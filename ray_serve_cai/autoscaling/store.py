"""Locked durable policy and operation journal shared by API and provider."""

import fcntl
import json
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .policy import ClusterScalingPolicy


class ScalingStore:
    def __init__(self, path=None):
        self.path = Path(path or "/home/cdsw/ray_autoscaling.json")

    def read(self):
        if not self.path.exists():
            return {
                "policy": ClusterScalingPolicy().model_dump(),
                "revision": 0,
                "workers": {},
                "events": [],
                "status": {},
            }
        # Corrupt state must stop mutations, never reset ownership to empty.
        return json.loads(self.path.read_text())

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            data = self.read()
            yield data
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".scaling-")
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(data, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)

    def set_policy(self, policy, *, initialize_only=False):
        policy = policy.model_copy(deep=True)
        for pool in policy.pools:
            if not pool.worker_spec.runtime_identifier:
                cluster_path = self.path.parent / "ray_cluster_info.json"
                default_runtime = (
                    json.loads(cluster_path.read_text()).get("worker_runtime_identifier")
                    if cluster_path.exists()
                    else None
                )
                if not default_runtime:
                    raise ValueError(
                        "Pool requires runtime_identifier or a configured cluster worker runtime"
                    )
                pool.worker_spec.runtime_identifier = default_runtime
        with self.transaction() as data:
            if initialize_only and data["revision"]:
                return {"policy": data["policy"], "revision": data["revision"]}
            active = [
                w for w in data["workers"].values() if w["state"] not in {"terminated", "rejected"}
            ]
            old = {p["id"]: p for p in data["policy"]["pools"]}
            new = {p.id: p.model_dump() for p in policy.pools}
            for key in old.keys() & new.keys():
                if old[key].get("initial_workers", 0) != new[key]["initial_workers"]:
                    raise ValueError(
                        "initial_workers is a one-time bootstrap setting; change min_workers to request ongoing capacity"
                    )
            for worker in active:
                pool_id = worker["pool_id"]
                if pool_id not in new or new[pool_id]["worker_spec"] != old[pool_id]["worker_spec"]:
                    raise ValueError(
                        "Cannot remove or change the launch spec of a pool with existing workers"
                    )
            data["policy"] = policy.model_dump()
            data["revision"] += 1
            data.setdefault("cluster_id", uuid.uuid4().hex)
            self.event(data, "policy_updated", revision=data["revision"], mode=policy.mode)
        return {"policy": data["policy"], "revision": data["revision"]}

    @staticmethod
    def event(data, action, **fields):
        data["events"].append({"at": time.time(), "action": action, **fields})
        data["events"] = data["events"][-500:]
