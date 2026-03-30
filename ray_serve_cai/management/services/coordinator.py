"""Coordinator service for managing the relationship between Ray and CAI."""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

from .ray_service import RayService
from .cai_service import CAIService
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
        Get Ray nodes enriched with CML application information.

        Returns:
            List of enriched node information
        """
        ray_nodes = self.ray_service.get_nodes()
        state = self.load_state()
        node_mapping = state.get("node_mapping", {})

        enriched_nodes = []
        for node in ray_nodes:
            node_id = node.get("NodeID")
            node_info = {
                "node_id": node_id,
                "node_name": node.get("NodeName", ""),
                "node_type": _detect_node_type(node.get("Resources", {})),
                "alive": node.get("Alive", False),
                "resources": node.get("Resources", {}),
                "resources_used": node.get("ResourcesUsed", {}),
                "cml_app_id": None,
                "cml_app_name": None,
            }

            # Add CML mapping if available
            if node_id in node_mapping:
                mapping = node_mapping[node_id]
                node_info["cml_app_id"] = mapping.get("cml_app_id")
                node_info["cml_app_name"] = mapping.get("cml_app_name")

            enriched_nodes.append(node_info)

        return enriched_nodes

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
    ) -> Dict[str, Any]:
        """Add a new worker node, register it in the resource map, and track the mapping."""
        result = self.cai_service.create_worker_node(
            node_type=node_type, cpu=cpu, memory=memory, gpus=gpus,
            runtime_identifier=runtime_identifier,
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

    def remove_worker_node(self, app_id: str) -> Dict[str, Any]:
        """Remove a worker node, unregister it from the resource map, and clean up mapping."""
        state = self.load_state()
        node_mapping = state.get("node_mapping", {})

        ray_node_id = None
        for nid, mapping in node_mapping.items():
            if mapping.get("cml_app_id") == app_id:
                ray_node_id = nid
                break

        result = self.cai_service.delete_worker_node(app_id)
        self.resource_map.unregister_worker(app_id)

        if ray_node_id:
            self.remove_node_mapping(ray_node_id)
            logger.info(f"Removed node mapping for Ray node: {ray_node_id}")

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
        result = self.cai_service.delete_worker_node(app_id)
        self.resource_map.release(app_id)
        return result

    def get_resource_map(self) -> Dict[str, Any]:
        """Return the current resource capacity summary."""
        return self.resource_map.get_summary()
