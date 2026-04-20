"""
Cluster Resource Map.

Tracks the total capacity contributed by worker nodes and the resources
consumed by CML applications launched through the Management API.

Layout of /home/cdsw/ray_resource_map.json
───────────────────────────────────────────
{
  "workers": {
    "<worker_app_id>": {
      "app_id":   "...",
      "app_name": "ray-workers-1234",
      "node_type": "t4-gpu-worker",
      "cpu": 16, "memory": 32, "gpus": 1
    },
    ...
  },
  "applications": {
    "<app_id>": {
      "app_id":   "...",
      "app_name": "my-ray-app",
      "cpu": 4, "memory": 8, "gpus": 1
    },
    ...
  }
}

Derived at query time (never stored):
  total     = sum of workers
  allocated = sum of applications
  available = total - allocated
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_MAP_PATH = Path("/home/cdsw/ray_resource_map.json")


class ResourceMap:
    """Persistent resource capacity tracker for the Ray cluster."""

    def __init__(self, path: Path = _MAP_PATH):
        self._path = path

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
                    data.setdefault("workers", {})
                    data.setdefault("applications", {})
                    return data
            except Exception as e:
                logger.error(f"Failed to load resource map: {e}")
        return {"workers": {}, "applications": {}}

    def _save(self, data: Dict[str, Any]) -> None:
        data["last_updated"] = datetime.now(timezone.utc).isoformat()
        try:
            with open(self._path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save resource map: {e}")

    # ── Worker registration ───────────────────────────────────────────────────

    def register_worker(
        self,
        app_id: str,
        app_name: str,
        node_type: str,
        cpu: int,
        memory: int,
        gpus: int = 0,
    ) -> None:
        """Add a worker node to the capacity pool."""
        data = self._load()
        data["workers"][app_id] = {
            "app_id":   app_id,
            "app_name": app_name,
            "node_type": node_type,
            "cpu":    cpu,
            "memory": memory,
            "gpus":   gpus,
        }
        self._save(data)
        logger.info(f"Registered worker {app_name} [{app_id}]: "
                    f"{cpu} CPU, {memory} GB, {gpus} GPU")

    def unregister_worker(self, app_id: str) -> None:
        """Remove a worker node from the capacity pool."""
        data = self._load()
        removed = data["workers"].pop(app_id, None)
        if removed:
            self._save(data)
            logger.info(f"Unregistered worker [{app_id}]")
        else:
            logger.warning(f"Worker [{app_id}] not found in resource map")

    # ── Application allocation ────────────────────────────────────────────────

    def allocate(
        self,
        app_id: str,
        app_name: str,
        cpu: int,
        memory: int,
        gpus: int = 0,
    ) -> None:
        """Record resource consumption for a newly launched application."""
        data = self._load()
        data["applications"][app_id] = {
            "app_id":   app_id,
            "app_name": app_name,
            "cpu":    cpu,
            "memory": memory,
            "gpus":   gpus,
        }
        self._save(data)
        logger.info(f"Allocated resources for {app_name} [{app_id}]: "
                    f"{cpu} CPU, {memory} GB, {gpus} GPU")

    def release(self, app_id: str) -> None:
        """Release resources when an application is removed."""
        data = self._load()
        removed = data["applications"].pop(app_id, None)
        if removed:
            self._save(data)
            logger.info(f"Released resources for app [{app_id}]")
        else:
            logger.warning(f"App [{app_id}] not found in resource map")

    # ── Reconciliation ────────────────────────────────────────────────────────

    def sync(self, live_app_ids: set) -> int:
        """
        Remove stale entries whose app_id no longer exists in CML.

        Args:
            live_app_ids: Set of CML application IDs that currently exist.

        Returns:
            Number of stale entries removed.
        """
        data = self._load()
        removed = 0

        for section in ("workers", "applications"):
            stale = [
                aid for aid in data[section]
                if aid not in live_app_ids
            ]
            for aid in stale:
                entry = data[section].pop(aid)
                logger.info(
                    "Pruned stale %s entry: %s [%s]",
                    section.rstrip("s"),
                    entry.get("app_name", "?"),
                    aid,
                )
                removed += 1

        if removed:
            self._save(data)

        return removed

    # ── Capacity queries ──────────────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Return total / allocated / available resource counts."""
        data = self._load()

        total = _sum_resources(data["workers"].values())
        allocated = _sum_resources(data["applications"].values())
        available = {
            "cpu":    total["cpu"]    - allocated["cpu"],
            "memory": total["memory"] - allocated["memory"],
            "gpus":   total["gpus"]   - allocated["gpus"],
        }
        return {
            "workers":      list(data["workers"].values()),
            "applications": list(data["applications"].values()),
            "total":     total,
            "allocated": allocated,
            "available": available,
            "last_updated": data.get("last_updated"),
        }

    def validate(self, cpu: int, memory: int, gpus: int = 0) -> None:
        """
        Raise ValueError if the cluster lacks capacity for the requested resources.

        Args:
            cpu:    Required CPU cores.
            memory: Required memory in GB.
            gpus:   Required GPUs.

        Raises:
            ValueError: With a human-readable message listing what's short.
        """
        summary = self.get_summary()
        available = summary["available"]
        total = summary["total"]

        if total["cpu"] == 0 and total["gpus"] == 0:
            raise ValueError(
                "No worker nodes are registered in the resource map. "
                "Add worker nodes before launching applications."
            )

        shortfalls = []
        if available["cpu"] < cpu:
            shortfalls.append(
                f"CPU: requested {cpu}, available {available['cpu']} "
                f"(total {total['cpu']})"
            )
        if available["memory"] < memory:
            shortfalls.append(
                f"memory: requested {memory} GB, available {available['memory']} GB "
                f"(total {total['memory']} GB)"
            )
        if gpus > 0 and available["gpus"] < gpus:
            shortfalls.append(
                f"GPU: requested {gpus}, available {available['gpus']} "
                f"(total {total['gpus']})"
            )

        if shortfalls:
            raise ValueError(
                "Insufficient cluster resources:\n  " + "\n  ".join(shortfalls)
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sum_resources(entries) -> Dict[str, int]:
    cpu = memory = gpus = 0
    for e in entries:
        cpu    += e.get("cpu",    0)
        memory += e.get("memory", 0)
        gpus   += e.get("gpus",   0)
    return {"cpu": cpu, "memory": memory, "gpus": gpus}
