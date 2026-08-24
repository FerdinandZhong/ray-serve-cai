"""Head-node recovery orchestrator.

Runs from a separate CML Job pod (the head is dead, so the management API can't
recover itself). Collaborators are injected so the whole flow is unit-testable
without CML or Ray:

    cml               object with restart_application(app_id) / stop_application(app_id)
                      / list_applications() (CAIService.manager)
    cai_service       has create_worker_node(node_type=...) to rebuild workers
    deployment_store  DeploymentStore — the redeploy source of truth
    http              object with .get(url)/.post(url, json, headers) (requests-like)

Sequence (each phase checkpointed before the next; see recovery_state):
    RESTARTING_HEAD    -> restart the head CML application (idempotent on resume)
    WAITING_HEAD       -> poll head /api/health + /api/v1/cluster/gcs-address
    UPDATING_INFO      -> rewrite ray_cluster_info.json head_address atomically
    REBUILDING_WORKERS -> delete stale ray-* workers, recreate from worker_groups
    REDEPLOYING        -> re-issue each stored deploy request to the new head
    COMPLETE           -> clear state
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

from .recovery_state import PHASES, RecoveryState

logger = logging.getLogger(__name__)


class RecoveryOrchestrator:
    def __init__(
        self,
        *,
        cml: Any,
        cai_service: Any,
        deployment_store: Any,
        cluster_info_path: Path,
        http: Any,
        service_token: str | None = None,
        state: RecoveryState | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_s: float = 10.0,
        head_timeout_s: float = 600.0,
        dry_run: bool = False,
    ):
        self.cml = cml
        self.cai_service = cai_service
        self.deployment_store = deployment_store
        self.cluster_info_path = Path(cluster_info_path)
        self.http = http
        self.service_token = service_token
        self.state = state or RecoveryState()
        self.sleep = sleep
        self.poll_interval_s = poll_interval_s
        self.head_timeout_s = head_timeout_s
        self.dry_run = dry_run

    # ── Helpers ────────────────────────────────────────────────────────────

    def _cluster_info(self) -> dict[str, Any]:
        with open(self.cluster_info_path) as f:
            return json.load(f)

    def _past(self, phase: str) -> bool:
        """True if the persisted phase is at/after ``phase`` (resume skip)."""
        cur = self.state.phase()
        if cur is None:
            return False
        return PHASES.index(cur) >= PHASES.index(phase)

    # ── Phases ─────────────────────────────────────────────────────────────

    def restart_head(self, info: dict[str, Any]) -> None:
        if self._past("RESTARTING_HEAD"):
            logger.info("Resume: head restart already issued; skipping")
            return
        app_id = info.get("head_app_id")
        if not app_id:
            raise RuntimeError("ray_cluster_info.json has no head_app_id")
        self.state.set_phase("RESTARTING_HEAD", head_app_id=app_id)
        if self.dry_run:
            logger.info("[dry-run] would restart head CML app %s", app_id)
            return
        if not self.cml.restart_application(app_id):
            raise RuntimeError(f"CML restart_application returned False for head {app_id}")

    def wait_for_head(self, info: dict[str, Any]) -> str:
        """Poll until the new head serves /api/health and a GCS address."""
        self.state.set_phase("WAITING_HEAD")
        base = (info.get("head_url") or info.get("management_api_url") or "").rstrip("/")
        if not base:
            raise RuntimeError("ray_cluster_info.json has no head_url/management_api_url")
        if self.dry_run:
            logger.info("[dry-run] would poll %s/api/health for readiness", base)
            return info.get("head_address", "")
        # Elapsed-time loop (no wall-clock reads) keeps the poll deterministic.
        waited = 0.0
        while waited <= self.head_timeout_s:
            health = self.http.get(f"{base}/api/health")
            if getattr(health, "status_code", 0) == 200:
                gcs = self.http.get(f"{base}/api/v1/cluster/gcs-address")
                if getattr(gcs, "status_code", 0) == 200:
                    addr = gcs.json().get("gcs_address") or gcs.json().get("address") or ""
                    if addr:
                        logger.info("New head is live; gcs=%s", addr)
                        return addr
            self.sleep(self.poll_interval_s)
            waited += self.poll_interval_s
        raise TimeoutError(f"head did not become ready within {self.head_timeout_s}s")

    def update_cluster_info(self, new_head_address: str) -> None:
        if self._past("UPDATING_INFO") or not new_head_address:
            return
        self.state.set_phase("UPDATING_INFO", head_address=new_head_address)
        if self.dry_run:
            logger.info("[dry-run] would set head_address=%s in cluster info", new_head_address)
            return
        info = self._cluster_info()
        info["head_address"] = new_head_address
        tmp = self.cluster_info_path.with_suffix(self.cluster_info_path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(info, f, indent=2)
        os.replace(tmp, self.cluster_info_path)
        logger.info("Updated ray_cluster_info.json head_address=%s", new_head_address)

    def rebuild_workers(self, info: dict[str, Any]) -> dict[str, int]:
        self.state.set_phase("REBUILDING_WORKERS")
        head_name = info.get("head_app_name", "ray-cluster-head")
        deleted = 0
        # Delete stale worker CML apps (their raylets point at the dead GCS).
        try:
            for app in self.cml.list_applications():
                name = app.get("name", "")
                if name.startswith("ray-") and name != head_name:
                    if self.dry_run:
                        logger.info("[dry-run] would delete stale worker %s", name)
                    else:
                        self.cml.stop_application(app["id"])
                    deleted += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Listing/deleting stale workers failed: %s", e)
        # Recreate workers from the persisted group definitions.
        created = 0
        for group in info.get("worker_groups", []):
            for _ in range(int(group.get("count", 0))):
                if self.dry_run:
                    logger.info("[dry-run] would create worker node_type=%s", group.get("node_type"))
                else:
                    self.cai_service.create_worker_node(node_type=group["node_type"])
                created += 1
        logger.info("Workers rebuilt: deleted=%d created=%d", deleted, created)
        return {"deleted": deleted, "created": created}

    def redeploy(self, info: dict[str, Any]) -> dict[str, int]:
        self.state.set_phase("REDEPLOYING")
        base = (info.get("management_api_url") or info.get("head_url") or "").rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
        ok = fail = 0
        for rec in self.deployment_store.all_records():
            body = rec.get("request") or {}
            if not body:
                continue
            if self.dry_run:
                logger.info("[dry-run] would redeploy %s", rec.get("name"))
                ok += 1
                continue
            try:
                resp = self.http.post(f"{base}/api/v1/applications", json=body, headers=headers)
                if getattr(resp, "status_code", 500) < 400:
                    ok += 1
                else:
                    fail += 1
                    logger.warning("Redeploy of %s failed: HTTP %s", rec.get("name"), resp.status_code)
            except Exception as e:  # noqa: BLE001
                fail += 1
                logger.warning("Redeploy of %s raised: %s", rec.get("name"), e)
        logger.info("Redeploy complete: ok=%d fail=%d", ok, fail)
        return {"ok": ok, "fail": fail}

    # ── Driver ─────────────────────────────────────────────────────────────

    def run(self, owner: str = "recovery-job") -> dict[str, Any]:
        if not self.state.acquire_lock(owner):
            return {"status": "skipped", "reason": "another recovery run holds the lock"}
        try:
            if self.state.is_resumable():
                logger.warning("Resuming recovery from phase %s", self.state.phase())
            else:
                self.state.set_phase("DETECTED")
            info = self._cluster_info()

            self.restart_head(info)
            new_addr = self.wait_for_head(info)
            self.update_cluster_info(new_addr)
            workers = self.rebuild_workers(info)
            deployed = self.redeploy(info)

            self.state.set_phase("COMPLETE")
            self.state.clear()
            return {
                "status": "recovered",
                "head_address": new_addr,
                "workers": workers,
                "deployments": deployed,
                "dry_run": self.dry_run,
            }
        finally:
            self.state.release_lock()
