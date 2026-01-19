"""Service for Ray cluster operations."""

import ray
from ray import serve
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class RayService:
    """Handles Ray cluster and Ray Serve operations."""

    def __init__(self, ray_address: str = "auto"):
        """
        Initialize Ray service.

        Args:
            ray_address: Ray cluster address (default: "auto" for local discovery)
        """
        self.ray_address = ray_address
        self._initialized = False

    def connect(self):
        """Connect to Ray cluster."""
        if not self._initialized:
            try:
                ray.init(address=self.ray_address, ignore_reinit_error=True)
                self._initialized = True
                logger.info(f"Connected to Ray cluster at {self.ray_address}")
            except Exception as e:
                logger.error(f"Failed to connect to Ray: {e}")
                raise

    def get_nodes(self) -> List[Dict[str, Any]]:
        """
        Get all nodes in the Ray cluster.

        Returns:
            List of node information dictionaries
        """
        self.connect()
        nodes = ray.nodes()
        return nodes

    def get_cluster_resources(self) -> Dict[str, float]:
        """
        Get total cluster resources.

        Returns:
            Dictionary of resource types to quantities
        """
        self.connect()
        return ray.cluster_resources()

    def get_available_resources(self) -> Dict[str, float]:
        """
        Get currently available cluster resources.

        Returns:
            Dictionary of available resources
        """
        self.connect()
        return ray.available_resources()

    def deploy_application(
        self,
        name: str,
        import_path: str,
        route_prefix: str = "/",
        num_replicas: int = 1,
        ray_actor_options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Deploy a Ray Serve application.

        Args:
            name: Application name
            import_path: Import path to deployment (e.g., "module:deployment")
            route_prefix: HTTP route prefix
            num_replicas: Number of replicas
            ray_actor_options: Ray actor options dict

        Returns:
            Deployment status information
        """
        self.connect()

        try:
            # Start Ray Serve if not already running
            try:
                serve.start(detached=True, http_options={"host": "0.0.0.0"})
            except Exception:
                # Already running
                pass

            # Parse import path
            module_path, deployment_name = import_path.rsplit(":", 1)

            # Import the deployment
            import importlib
            module = importlib.import_module(module_path)
            deployment = getattr(module, deployment_name)

            # Configure and deploy
            deployment_config = {
                "name": name,
                "num_replicas": num_replicas,
                "route_prefix": route_prefix,
            }

            if ray_actor_options:
                deployment_config["ray_actor_options"] = ray_actor_options

            deployment.options(**deployment_config).deploy()

            logger.info(f"Deployed application: {name}")
            return {
                "status": "success",
                "name": name,
                "route_prefix": route_prefix
            }

        except Exception as e:
            logger.error(f"Failed to deploy application {name}: {e}")
            raise

    def delete_application(self, name: str) -> Dict[str, Any]:
        """
        Delete a Ray Serve application.

        Args:
            name: Application name

        Returns:
            Deletion status
        """
        self.connect()

        try:
            serve.delete(name)
            logger.info(f"Deleted application: {name}")
            return {"status": "success", "name": name}
        except Exception as e:
            logger.error(f"Failed to delete application {name}: {e}")
            raise

    def list_applications(self) -> List[Dict[str, Any]]:
        """
        List all Ray Serve applications.

        Returns:
            List of application information
        """
        self.connect()

        try:
            # Start Ray Serve if not running (read-only operation)
            try:
                serve.start(detached=True, http_options={"host": "0.0.0.0"})
            except Exception:
                pass

            apps = serve.status().applications
            return [
                {
                    "name": name,
                    "status": str(app_status.status),
                    "message": app_status.message or "",
                    "last_deployed_time": str(app_status.deployment_timestamp) if app_status.deployment_timestamp else None,
                }
                for name, app_status in apps.items()
            ]
        except Exception as e:
            logger.error(f"Failed to list applications: {e}")
            return []

    def get_application_status(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a specific application.

        Args:
            name: Application name

        Returns:
            Application status or None if not found
        """
        apps = self.list_applications()
        for app in apps:
            if app["name"] == name:
                return app
        return None
