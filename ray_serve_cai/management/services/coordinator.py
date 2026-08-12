"""Coordinator service for managing the relationship between Ray and CAI."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from .cai_service import CAIService
from .deployment_store import DeploymentStore
from .ray_service import RayService
from .resource_map import ResourceMap

logger = logging.getLogger(__name__)

# Ray 2.x automatically assigns "node:__internal_head__" to the head node.
# Worker nodes self-register a free-form "node_type:<label>" custom resource
# via --resources at ray start time (see ray_worker_launcher.py.j2).
# The label suffix is defined in WorkerGroupConfig.node_type and flows from
# the cluster YAML — no static registry is needed here.
#
# Examples of worker labels that are detected automatically:
#   "node_type:cpu-worker"
#   "node_type:gpu-worker"
#   "node_type:t4_gpu_node_single"
#   "node_type:l40_gpu_node_2_gpus"
_HEAD_NODE_LABEL    = "node:__internal_head__"
_WORKER_LABEL_PREFIX = "node_type:"


def _detect_node_type(resources: Dict[str, Any]) -> str:
    """Return the logical node type for a Ray node based on its resource labels.

    Detection order:
      1. "node:__internal_head__"  → "head"      (Ray built-in, head only)
      2. "node_type:<label>"       → "<label>"   (set by worker launcher)
      3. fallback                  → "worker"
    """
    if _HEAD_NODE_LABEL in resources:
        return "head"
    for key in resources:
        if key.startswith(_WORKER_LABEL_PREFIX):
            return key[len(_WORKER_LABEL_PREFIX):]
    return "worker"


class CoordinatorService:
    """Coordinates operations between Ray cluster and CML/CAI platform."""

    def __init__(self, ray_service: RayService, cai_service: CAIService):
        """
        Initialize coordinator service.

        Args:
            ray_service: Ray service instance
            ray_service: CAI service instance
        """
        self.ray_service = ray_service
        self.cai_service = cai_service
        self.resource_map = ResourceMap()
        self.deployment_store = DeploymentStore()
        self.state_file = Path("/home/cdsw/cluster_state.json")

    def load_state(self) -> Dict[str, Any]:
        """
        Load cluster state from disk.

        Returns:
            State dictionary
        """
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

        return {"node_mapping": {}, "applications": {}}

    def save_state(self, state: Dict[str, Any]):
        """
        Save cluster state to disk.

        Args:
            state: State dictionary to save
        """
        try:
            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def add_node_mapping(self, ray_node_id: str, cml_app_id: str, cml_app_name: str):
        """
        Record mapping between Ray node and CML application.

        Args:
            ray_node_id: Ray node ID
            cml_app_id: CML application ID
            cml_app_name: CML application name
        """
        state = self.load_state()
        state["node_mapping"][ray_node_id] = {
            "cml_app_id": cml_app_id,
            "cml_app_name": cml_app_name
        }
        self.save_state(state)

    def remove_node_mapping(self, ray_node_id: str):
        """
        Remove node mapping.

        Args:
            ray_node_id: Ray node ID
        """
        state = self.load_state()
        if ray_node_id in state["node_mapping"]:
            del state["node_mapping"][ray_node_id]
            self.save_state(state)

    def get_enriched_nodes(self) -> List[Dict[str, Any]]:
        """
        Get Ray nodes enriched with CML application identity.

        Joins Ray node data with the persisted node_mapping (ray_node_id →
        cml_app_id/cml_app_name) and live CML application status so that
        callers get a single unified view with the correct app_id for deletion.

        Returns:
            List of enriched node information
        """
        ray_nodes = self.ray_service.get_nodes()
        state = self.load_state()
        node_mapping = state.get("node_mapping", {})

        # Build app_id → live status from CML in one call.
        cml_status_by_id: Dict[str, str] = {}
        try:
            for app in self.cai_service.list_applications():
                if "id" in app:
                    cml_status_by_id[app["id"]] = app.get("status", "unknown")
        except Exception as exc:
            logger.warning("Could not fetch CML app statuses for node enrichment: %s", exc)

        enriched_nodes = []
        for node in ray_nodes:
            node_id = node.get("NodeID", "")
            mapping = node_mapping.get(node_id, {})
            app_id = mapping.get("cml_app_id")
            node_info: Dict[str, Any] = {
                "node_id": node_id,
                "node_name": node.get("NodeName", ""),
                "node_type": _detect_node_type(node.get("Resources", {})),
                "alive": node.get("Alive", False),
                "resources": node.get("Resources", {}),
                "resources_used": node.get("ResourcesUsed", {}),
                "pod_name": node.get("NodeManagerHostname"),
                "app_id": app_id,
                "app_name": mapping.get("cml_app_name"),
                "cml_status": cml_status_by_id.get(app_id) if app_id else None,
            }
            enriched_nodes.append(node_info)

        return enriched_nodes

    def get_worker_apps(self) -> List[Dict[str, Any]]:
        """
        List CML worker applications with their IDs (for deletion).

        Returns live CML apps whose names start with "ray-" (worker pattern),
        filtered to running/starting status. Each entry includes the CML app_id
        that can be passed to DELETE /api/v1/resources/nodes/{app_id}.
        """
        try:
            live_apps = self.cai_service.list_applications()
        except Exception as exc:
            logger.error("Failed to list CML applications: %s", exc)
            return []

        workers = []
        for app in live_apps:
            name = app.get("name", "")
            status = app.get("status", "")
            # Worker apps follow naming pattern "ray-<group>-<timestamp>"
            if name.startswith("ray-") and name != self._head_app_name():
                workers.append({
                    "app_id":   app["id"],
                    "app_name": name,
                    "status":   status,
                })
        return workers

    def _head_app_name(self) -> str:
        """Return the head node's CML app name from cluster info."""
        try:
            info = self.cai_service._load_cluster_info()
            return info.get("head_app_name", "ray-cluster-head")
        except Exception:
            return "ray-cluster-head"

    def get_cluster_status(self) -> Dict[str, Any]:
        """
        Get comprehensive cluster status.

        Returns:
            Cluster status dictionary
        """
        # Get nodes
        nodes = self.ray_service.get_nodes()
        alive_nodes = sum(1 for n in nodes if n.get("Alive", False))

        # Get resources
        total_resources = self.ray_service.get_cluster_resources()
        available_resources = self.ray_service.get_available_resources()

        total_cpus = total_resources.get("CPU", 0)
        available_cpus = available_resources.get("CPU", 0)
        total_memory = total_resources.get("memory", 0) / (1024 ** 3)  # Convert to GB
        available_memory = available_resources.get("memory", 0) / (1024 ** 3)

        # Calculate utilization
        cpu_used = total_cpus - available_cpus
        utilization = (cpu_used / total_cpus * 100) if total_cpus > 0 else 0

        # Get applications
        applications = self.ray_service.list_applications()

        return {
            "healthy": alive_nodes == len(nodes),
            "total_nodes": len(nodes),
            "alive_nodes": alive_nodes,
            "total_applications": len(applications),
            "resources": {
                "total_cpus": total_cpus,
                "available_cpus": available_cpus,
                "total_memory": total_memory,
                "available_memory": available_memory,
                "total_gpus": total_resources.get("GPU", 0),
                "available_gpus": available_resources.get("GPU", 0),
                "utilization_percent": round(utilization, 2),
            }
        }

    def add_worker_node(
        self,
        node_type: str = "worker",
        cpu: int = None,
        memory: int = None,
        gpus: int = None,
        runtime_identifier: str = None,
        node_label: dict = None,
        ray_labels: dict = None,
    ) -> Dict[str, Any]:
        """Add a new worker node, register it in the resource map, and track the mapping."""
        result = self.cai_service.create_worker_node(
            node_type=node_type, cpu=cpu, memory=memory, gpus=gpus,
            runtime_identifier=runtime_identifier,
            node_label=node_label,
            ray_labels=ray_labels,
        )
        self.resource_map.register_worker(
            app_id=result["app_id"],
            app_name=result["app_name"],
            node_type=result["node_type"],
            cpu=result["cpu"],
            memory=result["memory"],
            gpus=result["gpus"],
        )
        logger.info(f"Worker node created and registered: {result.get('app_name')}")
        return result

    # ── Node-type (worker group) registry ──────────────────────────────────
    def define_node_type(self, **kwargs) -> Dict[str, Any]:
        """Register a new worker group / node_type at runtime (no relaunch)."""
        return self.cai_service.define_node_type(**kwargs)

    def list_node_types(self) -> list:
        """List worker groups (node_types) known to the cluster."""
        return self.cai_service.list_worker_groups()

    def remove_node_type(self, node_type: str) -> Dict[str, Any]:
        """Remove a worker group / node_type definition."""
        return self.cai_service.remove_worker_group(node_type)

    def remove_worker_node(self, app_id: str) -> Dict[str, Any]:
        """Remove a worker node, unregister it from the resource map, and clean up mapping.

        Local state (resource map, node mapping) is cleaned up regardless of whether
        the CML delete succeeds, so that a partially-deleted or already-gone application
        does not leave stale entries.  A warning is included in the response when the
        CML API call fails.
        """
        state = self.load_state()
        node_mapping = state.get("node_mapping", {})

        ray_node_id = None
        for nid, mapping in node_mapping.items():
            if mapping.get("cml_app_id") == app_id:
                ray_node_id = nid
                break

        # Attempt to stop the CML application.  Failure is non-fatal for local
        # state cleanup — the app may have already been deleted or crashed.
        cml_delete_warning = None
        try:
            self.cai_service.delete_application(app_id)
        except Exception as exc:
            cml_delete_warning = str(exc)
            logger.warning(
                "CML application delete failed for %s (will still clean up local state): %s",
                app_id, exc,
            )

        # Always clean up local state so stale entries don't linger.
        self.resource_map.unregister_worker(app_id)

        if ray_node_id:
            self.remove_node_mapping(ray_node_id)
            logger.info("Removed node mapping for Ray node: %s", ray_node_id)

        result: Dict[str, Any] = {"status": "success", "app_id": app_id}
        if ray_node_id:
            result["ray_node_id"] = ray_node_id
        if cml_delete_warning:
            result["status"] = "partial"
            result["warning"] = cml_delete_warning
        return result

    def launch_cai_application(
        self,
        name: str,
        script: str,
        cpu: int,
        memory: int,
        gpus: int = 0,
        runtime_identifier: str = None,
        environment: dict = None,
        bypass_authentication: bool = True,
    ) -> Dict[str, Any]:
        """
        Validate cluster capacity, launch a CML application, and record the allocation.

        Raises:
            ValueError: If the cluster lacks sufficient CPU / memory / GPU.
        """
        self.resource_map.validate(cpu=cpu, memory=memory, gpus=gpus)

        result = self.cai_service.launch_cai_application(
            name=name,
            script=script,
            cpu=cpu,
            memory=memory,
            gpus=gpus,
            runtime_identifier=runtime_identifier,
            environment=environment,
            bypass_authentication=bypass_authentication,
        )
        self.resource_map.allocate(
            app_id=result["app_id"],
            app_name=result["app_name"],
            cpu=cpu,
            memory=memory,
            gpus=gpus,
        )
        return result

    def remove_cai_application(self, app_id: str) -> Dict[str, Any]:
        """Stop a CML application and release its resources from the map."""
        result = self.cai_service.delete_application(app_id)
        self.resource_map.release(app_id)
        return result

    def get_resource_map(self) -> Dict[str, Any]:
        """Return the current resource capacity summary.

        Syncs the persisted map against live CML applications first so that
        stale entries (crashed pods, apps deleted outside the API) are pruned.
        Only apps with status "running" or "starting" are considered alive —
        stopped / failed / deleted apps are treated as gone.
        """
        try:
            live_apps = self.cai_service.list_applications()
            running_ids = {
                a["id"] for a in live_apps
                if "id" in a and a.get("status") in ("running", "starting", "scheduling")
            }
            pruned = self.resource_map.sync(running_ids)
            if pruned:
                logger.info("Resource map sync: pruned %d stale entries", pruned)
        except Exception as exc:
            logger.warning("Resource map sync failed (returning stale data): %s", exc)

        return self.resource_map.get_summary()
