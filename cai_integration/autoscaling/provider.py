"""Durable CAI lifecycle backend using the existing worker creation path.

Instance IDs are durable launch-operation IDs, not pod IPs. All creates are
journaled before contacting CAI. Ambiguous outcomes block further creates.
"""

import os
import time
import uuid

from ray.autoscaler.tags import TAG_RAY_LAUNCH_REQUEST, TAG_RAY_USER_NODE_TYPE

from ray_serve_cai.autoscaling.lifecycle import creation_rejection, lifecycle_lock
from ray_serve_cai.autoscaling.policy import ClusterScalingPolicy, active_workers, initial_remaining
from ray_serve_cai.autoscaling.store import ScalingStore


class CAIWorkerBackend:
    def __init__(self, provider_config, cluster_name, *, service=None, snapshot=None):
        self.provider_config = provider_config
        self.cluster_name = cluster_name
        self.store = ScalingStore(provider_config["state_path"])
        self.snapshot = snapshot or self._snapshot
        if service is None:
            from ray_serve_cai.management.services.cai_service import CAIService

            domain = os.environ.get("CDSW_DOMAIN", "")
            service = CAIService(
                os.environ.get("CML_PROJECT_ID") or os.environ.get("CDSW_PROJECT_ID"),
                os.environ.get("CML_HOST") or f"https://{domain}",
            )
        self.service = service

    def _snapshot(self):
        # Ray's extension exports this at runtime but omits it from its stubs.
        from ray._raylet import GcsClient  # type: ignore[attr-defined]
        from ray.autoscaler.v2.sdk import get_cluster_resource_state
        from ray.core.generated.autoscaler_pb2 import NodeStatus

        state = get_cluster_resource_state(GcsClient(address=self.provider_config["gcs_address"]))
        return [
            {
                "instance_id": n.instance_id,
                "node_id": n.node_id.hex(),
                "status": NodeStatus.Name(n.status),
                "ip": n.node_ip_address,
            }
            for n in state.node_states
        ]

    def _records(self):
        data = self.store.read()
        if data.get("cluster_id") != self.cluster_name:
            raise RuntimeError("Autoscaling cluster ownership mismatch")
        return data["workers"]

    def non_terminated_nodes(self, tag_filters):
        # Journaled pending/unknown launches remain visible to avoid duplicating
        # capacity. Ray handles the pending resource demand and in-flight nodes.
        return [
            key
            for key, record in self._records().items()
            if record["state"] not in {"terminated", "rejected"}
            and all(record["tags"].get(k) == v for k, v in tag_filters.items())
        ]

    def is_terminated(self, node_id):
        return self._records()[node_id]["state"] == "terminated"

    def launch_batch(self, node_config, tags, count, *, purpose="demand"):
        with lifecycle_lock(self.store.path.parent):
            return self._create(node_config, tags, count, purpose=purpose)

    def _create(self, node_config, tags, count, *, purpose):
        if purpose not in {"demand", "initial"}:
            raise ValueError("Invalid launch purpose")
        request_id = tags.get(TAG_RAY_LAUNCH_REQUEST)
        pool_id = tags.get(TAG_RAY_USER_NODE_TYPE)
        if not request_id or not pool_id:
            raise ValueError("Missing launch operation identity")
        # One durable operation ID covers the complete batch.
        if count < 1 or count > 1000:
            raise ValueError("Invalid launch batch")
        for index in range(count):
            instance_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"{self.cluster_name}/{request_id}/{pool_id}/{index}"
            ).hex
            with self.store.transaction() as data:
                policy = ClusterScalingPolicy(**data["policy"])
                if policy.mode not in {"scale_up_only", "full"}:
                    raise RuntimeError("Worker creation is paused")
                if data["cluster_id"] != self.cluster_name:
                    raise RuntimeError("Autoscaling cluster ownership mismatch")
                existing = data["workers"].get(instance_id)
                if existing:
                    if existing["state"] in {"creating", "launch_unknown"}:
                        raise RuntimeError("Unresolved CAI create; reconcile before retry")
                    continue
                active = active_workers(data)
                if any(w["state"] in {"creating", "launch_unknown"} for w in active):
                    raise RuntimeError("An unresolved launch blocks further provisioning")
                pool = next((p for p in policy.pools if p.id == pool_id), None)
                if pool is None or node_config["worker_spec"] != pool.worker_spec.model_dump():
                    raise RuntimeError("Stale pool configuration; wait for reconciliation")
                retry = data.get("admission", {}).get(pool_id, {})
                if retry.get("retry_at", 0) > time.time():
                    raise RuntimeError("CAI admission retry is in backoff")
                if purpose == "initial" and not initial_remaining(data, pool.model_dump()):
                    raise RuntimeError("Initial pool request has already been fulfilled")
                if (policy.max_workers is not None and len(active) >= policy.max_workers) or (
                    pool.max_workers is not None
                    and sum(w["pool_id"] == pool_id for w in active) >= pool.max_workers
                ):
                    raise RuntimeError("Worker limit reached")
                # A lowered pool maximum does not remove existing workers.
                # Include their original shapes before admitting another pool.
                for resource, limit in (
                    ("gpus", policy.max_gpus),
                    ("cpu", policy.max_cpus),
                    ("memory", policy.max_memory_gb),
                ):
                    if limit is None:
                        continue
                    committed = sum(w["spec"].get(resource, 0) or 0 for w in active)
                    requested = getattr(pool.worker_spec, resource) or 0
                    if committed + requested > limit:
                        raise RuntimeError(f"Worker {resource} budget reached")
                # Refuse to race the separate head recovery job.
                self._check_recovery()
                data["workers"][instance_id] = {
                    "state": "creating",
                    "pool_id": pool_id,
                    "tags": tags,
                    "worker_id": instance_id,
                    "app_id": None,
                    "created_at": time.time(),
                    "spec": pool.worker_spec.model_dump(),
                    "purpose": purpose,
                }
                self.store.event(data, "launch_worker", worker_id=instance_id, pool_id=pool_id)
            try:
                result = self.service.create_worker_node(
                    **pool.worker_spec.model_dump(),
                    _worker_id=instance_id,
                    _autoscaling={
                        "cluster_id": self.cluster_name,
                        "pool_id": pool_id,
                        "instance_id": instance_id,
                    },
                )
            except Exception as exc:
                rejected = creation_rejection(exc)
                with self.store.transaction() as data:
                    state = "rejected" if rejected else "launch_unknown"
                    data["workers"][instance_id]["state"] = state
                    if rejected:
                        prior = data.setdefault("admission", {}).get(pool_id, {})
                        attempts = prior.get("attempts", 0) + 1
                        data["admission"][pool_id] = {
                            "attempts": attempts,
                            "retry_at": time.time() + min(30 * 2 ** min(attempts - 1, 4), 300),
                            "http_status": rejected,
                        }
                    self.store.event(data, state, worker_id=instance_id, http_status=rejected)
                raise
            with self.store.transaction() as data:
                data["workers"][instance_id].update(state="starting", app_id=result["app_id"])
                data.setdefault("admission", {}).pop(pool_id, None)

    def _check_recovery(self):

        if (self.store.path.parent / ".recovery_lock").exists():
            raise RuntimeError("Head recovery is in progress")

    def prepare_retirement(self, identity):
        record = self._records()[identity]
        current = self.service.worker_records().get(record["worker_id"])
        if not current or current.get("app_id") != record["app_id"]:
            raise RuntimeError("Worker identity changed before idle drain")
        if current.get("autoscaling", {}).get("cluster_id") != self.cluster_name:
            raise RuntimeError("Worker ownership changed before idle drain")
        self.service.prepare_worker_retirement(current)
        return current

    def recover_recorded_launches(self):
        """Close a crash window after CAIService saved a successful create.

        A create with no confirmed app ID remains unknown, never replayed.
        """
        with lifecycle_lock(self.store.path.parent):
            return self._recover_recorded_launches()

    def _recover_recorded_launches(self):
        records = self.service.worker_records()
        with self.store.transaction() as data:
            for identity, worker in data["workers"].items():
                if worker["state"] not in {"creating", "launch_unknown"}:
                    continue
                current = records.get(identity)
                if (
                    current
                    and current.get("status") == "launch_rejected"
                    and current.get("autoscaling", {}).get("cluster_id") == self.cluster_name
                ):
                    worker["state"] = "rejected"
                    data.setdefault("admission", {})[worker["pool_id"]] = {
                        "attempts": 1,
                        "retry_at": time.time() + 30,
                    }
                    continue
                if (
                    current
                    and current.get("app_id")
                    and current.get("autoscaling", {}).get("cluster_id") == self.cluster_name
                ):
                    worker.update(state="starting", app_id=current["app_id"])
                    self.store.event(data, "launch_reconciled", worker_id=identity)

    def terminate_node(self, node_id):
        with lifecycle_lock(self.store.path.parent):
            return self._terminate(node_id)

    def _terminate(self, node_id):
        with self.store.transaction() as data:
            if data.get("cluster_id") != self.cluster_name:
                raise RuntimeError("Autoscaling cluster ownership mismatch")
            policy = ClusterScalingPolicy(**data["policy"])
            if policy.mode != "full":
                raise RuntimeError("Worker termination is disabled")
            record = data["workers"].get(node_id)
            if record is None:
                raise RuntimeError("Refusing deletion of an unowned worker")
            if record["state"] == "terminated":
                return
            self._check_recovery()
            # Ray performs the idle drain. Never force a busy/unknown raylet down
            # merely because an earlier reconciliation selected this instance.
            nodes = [n for n in self.snapshot() if n["instance_id"] == node_id]
            if not nodes or any(n["status"] != "DEAD" for n in nodes):
                self.store.event(data, "retain_busy_worker", worker_id=node_id)
                raise RuntimeError("Worker has not been confirmed stopped by Ray")
            current = self.service.worker_records().get(record["worker_id"])
            if not current or current.get("app_id") != record["app_id"]:
                raise RuntimeError("CAI worker identity changed; deletion blocked")
            if current.get("autoscaling", {}).get("cluster_id") != self.cluster_name:
                raise RuntimeError("CAI record ownership mismatch")
            if not record["app_id"]:
                raise RuntimeError("CAI create outcome remains unresolved")
            record["state"] = "terminating"
            app_id = record["app_id"]
        live = {app["id"] for app in self.service.list_applications()}
        if app_id in live:
            self.service.delete_application(app_id)
        # Confirm eventual deletion before freeing quota or losing identity.
        live = {app["id"] for app in self.service.list_applications()}
        if app_id in live:
            raise RuntimeError("CAI deletion is pending confirmation")
        with self.store.transaction() as data:
            data["workers"][node_id]["state"] = "terminated"
            self.store.event(data, "delete_drained_worker", worker_id=node_id, app_id=app_id)
        self.service.forget_worker(app_id)
