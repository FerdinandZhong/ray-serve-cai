"""Persistent record of Ray Serve deploy *intent*.

Ray/GCS is the ground truth for live application *status*; this store only
records the high-level intent Ray does not retain — engine_type, venv, the
original deploy request, and who deployed it. Two consumers:

1. **Audit / redeploy** — ``GET /api/v1/applications/intents`` and one-click
   redeploy of the same spec.
2. **Head-node recovery** — after a head restart, Ray has lost every Serve
   deployment; :meth:`all_records` provides the list to re-issue (see the
   recovery job).

This is deliberately *not* a source of truth for status: :meth:`reconcile`
cross-checks the file against a live application-name set and flags drift, but
never fabricates "running". Same flat-JSON-on-shared-NFS pattern as
``resource_map.py``; writes are atomic (tmp + ``os.replace``).

Layout of /home/cdsw/ray_serve_deployments.json
────────────────────────────────────────────────
{
  "deployments": {
    "<app_name>": {
      "name", "route_prefix", "engine_type", "model", "venv_name",
      "num_replicas", "tensor_parallel_size",
      "request": { ...original deploy request... },
      "deployer", "created_at", "updated_at"
    }, ...
  },
  "last_updated": "<iso8601>"
}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STORE_PATH = Path("/home/cdsw/ray_serve_deployments.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeploymentStore:
    """Persistent tracker of Ray Serve deploy intent."""

    def __init__(self, path: Path = _STORE_PATH):
        self._path = path

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
                    data.setdefault("deployments", {})
                    return data
            except Exception as e:
                logger.error("Failed to load deployment store: %s", e)
        return {"deployments": {}}

    def _save(self, data: dict[str, Any]) -> None:
        data["last_updated"] = _now()
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._path)  # atomic on POSIX/NFS
        except Exception as e:
            logger.error("Failed to save deployment store: %s", e)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # ── Mutations ────────────────────────────────────────────────────────────

    def record(
        self,
        name: str,
        *,
        route_prefix: str | None = None,
        engine_type: str | None = None,
        model: str | None = None,
        venv_name: str | None = None,
        num_replicas: int | None = None,
        tensor_parallel_size: int | None = None,
        request: dict[str, Any] | None = None,
        deployer: str | None = None,
    ) -> None:
        """Record (or update) the intent for a deployed application.

        Re-deploying the same ``name`` updates the record and preserves the
        original ``created_at``.
        """
        data = self._load()
        existing = data["deployments"].get(name, {})
        created = existing.get("created_at", _now())
        data["deployments"][name] = {
            "name": name,
            "route_prefix": route_prefix,
            "engine_type": engine_type,
            "model": model,
            "venv_name": venv_name,
            "num_replicas": num_replicas,
            "tensor_parallel_size": tensor_parallel_size,
            "request": request or {},
            "deployer": deployer,
            "created_at": created,
            "updated_at": _now(),
        }
        self._save(data)
        logger.info("Recorded deploy intent for '%s' (deployer=%s)", name, deployer)

    def remove(self, name: str) -> None:
        """Drop the intent record for an undeployed application."""
        data = self._load()
        if data["deployments"].pop(name, None) is not None:
            self._save(data)
            logger.info("Removed deploy intent for '%s'", name)

    # ── Queries ────────────────────────────────────────────────────────────

    def get(self, name: str) -> dict[str, Any] | None:
        return self._load()["deployments"].get(name)

    def all_records(self) -> list[dict[str, Any]]:
        """All intent records (used by the recovery job to redeploy)."""
        return list(self._load()["deployments"].values())

    def reconcile(self, live_names: set) -> list[dict[str, Any]]:
        """Return intent records annotated with live-vs-drift status.

        Ray remains the status authority: each record is tagged ``live`` when a
        matching application name is currently deployed, else ``drifted`` (the
        app was deleted out-of-band or hasn't been recovered yet). This never
        mutates the store — drift is surfaced, not silently reconciled.
        """
        out = []
        for rec in self._load()["deployments"].values():
            out.append({**rec, "live": rec["name"] in live_names})
        return out
