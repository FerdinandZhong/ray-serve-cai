"""Service for Ray cluster operations."""

import concurrent.futures
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
        Deploy a Ray Serve application from an import path.

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
            # Parse import path (module.submodule:ClassName or module:app_handle)
            module_path, attr_name = import_path.rsplit(":", 1)

            import importlib
            module = importlib.import_module(module_path)
            obj = getattr(module, attr_name)

            # obj may be an already-bound serve.Application or an unbound
            # @serve.deployment class — handle both.
            if isinstance(obj, serve.Application):
                app = obj
            else:
                # Unbound deployment class: apply options then bind.
                opts: Dict[str, Any] = {"num_replicas": num_replicas}
                if ray_actor_options:
                    opts["ray_actor_options"] = ray_actor_options
                app = obj.options(**opts).bind()

            serve.run(app, name=name, route_prefix=route_prefix)

            logger.info(f"Deployed application: {name}")
            return {
                "status": "deployed",
                "name": name,
                "route_prefix": route_prefix,
            }

        except Exception as e:
            logger.error(f"Failed to deploy application {name}: {e}")
            raise

    def deploy_model(
        self,
        name: str,
        engine_type: str,
        model: str,
        route_prefix: str = "/",
        num_replicas: int = 1,
        tensor_parallel_size: int = 1,
        use_cpu: bool = False,
        gpu_fraction: Optional[float] = None,
        engine_config: Optional[Dict[str, Any]] = None,
        placement_group_bundles: Optional[List[Dict[str, float]]] = None,
        placement_group_strategy: Optional[str] = None,
        node_type: Optional[str] = None,
        multi_node: bool = False,
        autoscaling_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Deploy a vLLM or SGLang model as a Ray Serve application.

        Uses the engine registry to build the correct deployment for the
        requested engine type, then calls serve.run() with the application.

        Args:
            name: Ray Serve application name.
            engine_type: Engine identifier — "vllm" or "sglang".
            model: HuggingFace model ID or local path.
            route_prefix: HTTP route prefix for the deployment.
            num_replicas: Number of Ray Serve replicas.
            tensor_parallel_size: GPUs for intra-replica tensor parallelism.
            use_cpu: Run in CPU-only mode (no GPU required).
            engine_config: Extra engine-specific kwargs (dtype,
                gpu_memory_utilization, max_model_len, etc.).

        Returns:
            Dict with status, name, engine_type, model, route_prefix.
        """
        self.connect()

        # Import triggers engine registration in engines/__init__.py
        import ray_serve_cai.engines  # noqa: F401
        from ray_serve_cai.engines.registry import get_registry

        registry = get_registry()
        if not registry.is_registered(engine_type):
            available = registry.list_engines()
            raise ValueError(
                f"Engine '{engine_type}' is not registered. "
                f"Available engines: {available}"
            )

        user_config: Dict[str, Any] = {
            "model": model,
            "tensor_parallel_size": tensor_parallel_size,
            "use_cpu": use_cpu,
            "route_prefix": route_prefix,
            **(engine_config or {}),
        }
        if node_type:
            user_config["node_type"] = node_type
        if autoscaling_config:
            user_config["autoscaling_config"] = autoscaling_config

        config_builder = registry.get_config_builder(engine_type)
        built_config = config_builder.build_config(user_config)

        deployment_factory = registry.get_deployment_factory(engine_type)
        app = deployment_factory.create_deployment(
            engine_config=built_config,
            num_replicas=num_replicas,
            tensor_parallel_size=tensor_parallel_size,
            use_cpu=use_cpu,
            gpu_fraction=gpu_fraction,
            placement_group_bundles=placement_group_bundles,
            placement_group_strategy=placement_group_strategy,
            multi_node=multi_node,
        )

        # serve.run() blocks until the deployment is healthy, which can take
        # minutes for large models.  Run it in a thread and wait up to 30 s.
        # If it isn't ready in time, return "deploying" — the deployment
        # continues in the background and can be tracked via the Ray dashboard
        # or GET /api/v1/applications/{name}.
        _DEPLOY_TIMEOUT = 30
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(serve.run, app, name=name, route_prefix=route_prefix)
        executor.shutdown(wait=False)  # thread keeps running after timeout

        try:
            future.result(timeout=_DEPLOY_TIMEOUT)
            deploy_status = "deployed"
        except concurrent.futures.TimeoutError:
            deploy_status = "deploying"

        logger.info(
            f"Model application submitted: {name}  status={deploy_status}  "
            f"[engine:{engine_type}  model:{model}  tp:{tensor_parallel_size}]"
        )
        return {
            "status": deploy_status,
            "name": name,
            "engine_type": engine_type,
            "model": model,
            "route_prefix": route_prefix,
            "num_replicas": num_replicas,
            "tensor_parallel_size": tensor_parallel_size,
        }

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
