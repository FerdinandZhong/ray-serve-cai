"""Service for CML/CAI operations."""

import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
import logging

# Add parent directory to path for imports
try:
    script_dir = Path(__file__).parent.parent.parent
except NameError:
    script_dir = Path.cwd()

sys.path.insert(0, str(script_dir))

from ray_serve_cai.cai_cluster import CAIClusterManager

logger = logging.getLogger(__name__)


class CAIService:
    """Handles CML/CAI platform operations."""

    def __init__(self, project_id: str, cml_host: str = None, api_key: str = None):
        """
        Initialize CAI service.

        Args:
            project_id: CML project ID
            cml_host: CML host URL (defaults to CML_HOST env var)
            api_key: CML API key (defaults to CML_API_KEY env var)
        """
        self.project_id = project_id
        self.cml_host = cml_host or os.environ.get("CML_HOST")
        self.api_key = api_key or os.environ.get("CML_API_KEY")

        if not self.cml_host or not self.api_key:
            raise ValueError("CML_HOST and CML_API_KEY must be provided or set in environment")

        # Initialize CAI cluster manager
        self.manager = CAIClusterManager(
            project_id=self.project_id,
            cml_host=self.cml_host,
            api_key=self.api_key
        )

    def create_worker_node(
        self,
        cpu: int = 4,
        memory: int = 16,
        node_type: str = "worker"
    ) -> Dict[str, Any]:
        """
        Create a new worker node as a CML application.

        Args:
            cpu: CPU cores
            memory: Memory in GB
            node_type: Type of node (worker, gpu-worker)

        Returns:
            Created application information
        """
        try:
            # Generate unique worker name
            import time
            worker_name = f"ray-{node_type}-{int(time.time())}"

            # Get head node address from cluster info
            cluster_info_path = Path("/home/cdsw/ray_cluster_info.json")
            if not cluster_info_path.exists():
                raise RuntimeError("Cluster info not found. Ensure Ray cluster is running.")

            import json
            with open(cluster_info_path) as f:
                cluster_info = json.load(f)

            head_address = cluster_info.get("head_address")
            if not head_address:
                raise RuntimeError("Head address not found in cluster info")

            # Create worker application using CAI cluster manager
            # Note: This uses the existing launch_worker method
            app_info = self.manager.launch_worker(
                head_address=head_address,
                cpu=cpu,
                memory=memory,
                name=worker_name
            )

            logger.info(f"Created worker node: {worker_name}")
            return {
                "status": "success",
                "app_id": app_info.get("id"),
                "app_name": worker_name,
                "cpu": cpu,
                "memory": memory,
                "node_type": node_type
            }

        except Exception as e:
            logger.error(f"Failed to create worker node: {e}")
            raise

    def delete_worker_node(self, app_id: str) -> Dict[str, Any]:
        """
        Delete a worker node (stop CML application).

        Args:
            app_id: CML application ID

        Returns:
            Deletion status
        """
        try:
            # Stop the application
            self.manager.stop_application(app_id)

            logger.info(f"Deleted worker node: {app_id}")
            return {"status": "success", "app_id": app_id}

        except Exception as e:
            logger.error(f"Failed to delete worker node {app_id}: {e}")
            raise

    def list_applications(self) -> list:
        """
        List all CML applications in the project.

        Returns:
            List of application information
        """
        try:
            apps = self.manager.list_applications()
            return apps
        except Exception as e:
            logger.error(f"Failed to list CML applications: {e}")
            return []

    def get_application(self, app_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific CML application.

        Args:
            app_id: Application ID

        Returns:
            Application information or None
        """
        try:
            return self.manager.get_application(app_id)
        except Exception as e:
            logger.error(f"Failed to get application {app_id}: {e}")
            return None
