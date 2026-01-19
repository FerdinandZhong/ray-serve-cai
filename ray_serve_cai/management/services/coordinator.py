"""Coordinator service for managing the relationship between Ray and CAI."""

import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

from .ray_service import RayService
from .cai_service import CAIService

logger = logging.getLogger(__name__)


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
                "node_type": "head" if node.get("NodeManagerAddress") == node.get("RayletSocketName") else "worker",
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

    def add_worker_node(self, cpu: int, memory: int, node_type: str = "worker") -> Dict[str, Any]:
        """
        Add a new worker node and track the mapping.

        Args:
            cpu: CPU cores
            memory: Memory in GB
            node_type: Type of node

        Returns:
            Worker creation result
        """
        # Create worker via CAI
        result = self.cai_service.create_worker_node(cpu, memory, node_type)

        # Note: We'll add the mapping when the Ray node appears
        # This is because there's a delay between CML app creation and Ray node registration
        # The mapping should ideally be done by polling or via a callback

        logger.info(f"Worker node creation initiated: {result.get('app_name')}")
        return result

    def remove_worker_node(self, app_id: str) -> Dict[str, Any]:
        """
        Remove a worker node and clean up mapping.

        Args:
            app_id: CML application ID

        Returns:
            Removal result
        """
        # Find the node mapping
        state = self.load_state()
        node_mapping = state.get("node_mapping", {})

        # Find Ray node ID by CML app ID
        ray_node_id = None
        for nid, mapping in node_mapping.items():
            if mapping.get("cml_app_id") == app_id:
                ray_node_id = nid
                break

        # Delete worker via CAI
        result = self.cai_service.delete_worker_node(app_id)

        # Remove mapping if found
        if ray_node_id:
            self.remove_node_mapping(ray_node_id)
            logger.info(f"Removed node mapping for Ray node: {ray_node_id}")

        return result
