"""Service for Ray cluster operations."""

import concurrent.futures
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import ray
from ray import serve

if TYPE_CHECKING:
    from ..models.requests import SchedulingConfig

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

        # Allowlist check mirrors the guard in management/api/engines.py.
        # Only modules whose dotted prefix starts with a permitted value can be
        # imported — prevents arbitrary code execution via crafted import_path.
        import os as _os
        _allowed = [
            p.strip()
            for p in _os.environ.get(
                "ALLOWED_ENGINE_MODULES", "custom_engines,ray_serve_cai"
            ).split(",")
            if p.strip()
        ]
        module_part = import_path.rsplit(":", 1)[0]
        if not any(module_part == p or module_part.startswith(p + ".") for p in _allowed):
            raise ValueError(
                f"import_path module '{module_part}' is not in the allowlist "
                f"{_allowed}. Set ALLOWED_ENGINE_MODULES env var to add more prefixes."
            )

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
        model: Optional[str],
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
        venv_name: Optional[str] = None,
        scheduling: Optional["SchedulingConfig"] = None,
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

        # Inject the deployment-level venv selection so the factory's
        # resolve_venv_path() picks it up.  Kept out of build_config (which is
        # engine-model config) because the venv is a deployment concern.
        if venv_name:
            built_config["venv_name"] = venv_name

        # Resolve scheduling constraints.
        # Priority: explicit scheduling block > node_type shorthand.
        # scheduling.placement_group_bundles / strategy override the caller's
        # positional placement_group_bundles / placement_group_strategy args.
        if scheduling is not None:
            if scheduling.resources:
                built_config["scheduling_resources"] = scheduling.resources
            if scheduling.placement_group_bundles:
                placement_group_bundles = scheduling.placement_group_bundles
            if scheduling.placement_group_strategy:
                placement_group_strategy = scheduling.placement_group_strategy
            if scheduling.env_vars:
                built_config["scheduling_env_vars"] = scheduling.env_vars
        elif node_type:
            # Backward-compat shorthand: auto-expand into scheduling_resources.
            # node_type is ALSO kept in user_config (already in built_config via
            # build_vllm_engine_config) so multi-node bundle hints still work.
            built_config["scheduling_resources"] = {f"node_type:{node_type}": 0.001}

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

    def _refresh_serve_client(self) -> None:
        """Drop a stale cached Serve controller handle so ``serve.status()`` reconnects.

        ``serve.status()`` uses the cached global Serve client. On Ray 2.56.1
        ``_get_global_client()`` returns that cache **without a health check**,
        so when the Serve controller has restarted — common on CML; the
        controller actor is recreated — the cached handle still points at the
        dead actor. The subsequent ``client.get_serve_details()`` then raises
        ``RayActorError`` and ``serve.status()`` returns empty, so the caller
        reports zero applications even though apps are running.

        Ray 2.56.1 exposes ``_check_cached_client_alive()``: it pings the cached
        controller (``check_alive`` with a 5s timeout) and, if it is dead,
        clears the cache. Once cleared, the next ``_get_global_client()`` inside
        ``serve.status()`` reconnects to the live controller via ``_connect()``.
        This is a private Serve API, so the import is guarded: if it moves in a
        future Ray upgrade we skip the refresh and let ``serve.status()`` run as
        before rather than break listing.
        """
        try:
            from ray.serve.context import _check_cached_client_alive
        except Exception as e:  # noqa: BLE001 - private API moved; degrade gracefully
            logger.debug("Serve health-check API unavailable (%s); skipping refresh", e)
            return

        try:
            client, had_cached = _check_cached_client_alive()
            if client is None and had_cached:
                logger.warning(
                    "Cached Serve controller handle was stale; cleared so "
                    "serve.status() reconnects to the live controller."
                )
        except Exception as e:  # noqa: BLE001 - never let the health-check break listing
            logger.error("Serve controller health-check failed: %s", e)

    def list_applications(self) -> List[Dict[str, Any]]:
        """
        List all Ray Serve applications with real status, route prefix, and replica count.

        Returns:
            List of application information dicts including route_prefix and num_replicas
            sourced directly from Ray Serve.
        """
        self.connect()
        self._refresh_serve_client()

        try:
            status = serve.status()
        except Exception as e:
            # Do NOT silently swallow into an empty list — that masks controller
            # connectivity problems as "no applications deployed".
            logger.error("serve.status() failed: %s", e, exc_info=True)
            return []

        try:
            result = []
            for name, app_status in status.applications.items():
                # Sum replicas across all named deployments in this application.
                total_replicas: Optional[int] = None
                route_prefix: Optional[str] = None
                try:
                    deployments = app_status.deployments or {}
                    counts = [
                        d.replica_states
                        for d in deployments.values()
                        if hasattr(d, "replica_states") and d.replica_states
                    ]
                    if counts:
                        total_replicas = sum(
                            sum(s for s in rc.values() if isinstance(s, int))
                            for rc in counts
                        )
                except Exception:
                    pass

                # route_prefix is an attribute on the application status in Ray 2.x
                try:
                    route_prefix = getattr(app_status, "route_prefix", None)
                except Exception:
                    pass

                result.append({
                    "name": name,
                    "status": str(app_status.status),
                    "message": app_status.message or "",
                    # Ray 2.56.1: ApplicationStatusOverview.last_deployed_time_s
                    # is a Unix timestamp (float seconds).
                    "last_deployed_time": (
                        str(app_status.last_deployed_time_s)
                        if app_status.last_deployed_time_s
                        else None
                    ),
                    "route_prefix": route_prefix,
                    "num_replicas": total_replicas,
                })
            if not result:
                logger.warning(
                    "serve.status() reported no applications; if apps are "
                    "actually running the Serve controller connection may be "
                    "stale (see _refresh_serve_client)."
                )
            return result
        except Exception as e:
            logger.error(f"Failed to list applications: {e}", exc_info=True)
            return []

    def get_application_status(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a specific application.

        Matches by application name OR route prefix, since the two differ: a
        model deployed at route ``/qwen3-35b`` may carry the sanitized app name
        ``qwen3-35b-a3b-fp8``. Looking up either the name or the route prefix
        (with or without the leading slash) resolves it.

        Args:
            name: Application name or route prefix.

        Returns:
            Application status or None if not found
        """
        target = name.strip().lstrip("/")
        apps = self.list_applications()
        for app in apps:
            if app["name"] == name:
                return app
            route = (app.get("route_prefix") or "").strip().lstrip("/")
            if route and route == target:
                return app
        return None
