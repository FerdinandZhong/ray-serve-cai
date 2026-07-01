"""
Generic MCP (Model Context Protocol) Engine for Ray Serve.

Dynamically imports a user-provided Python module containing a FastMCP instance
and mounts it behind a FastAPI app with @serve.ingress.  This allows any set of
MCP tools to be deployed as a scalable Ray Serve application with Swagger UI.

Usage:
    Deploy via the management API with engine_type="mcp" and engine_config:
      {
        "mcp_module": "ray_serve_cai.engines.mcps.weather_tools",
        "num_cpus": 0.2
      }

    The module must export a FastMCP instance (by convention named ``mcp``).

Endpoints (after deployment with route_prefix e.g. /weather-mcp):
    POST  /mcp      — MCP streamable HTTP endpoint (handled by FastMCP)
    GET   /health   — liveness probe
    GET   /docs     — Swagger UI

Reference:
    https://github.com/ray-project/ray/blob/master/python/ray/llm/examples/sglang/modules/sglang_engine.py
"""
from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import FastAPI
from ray import serve
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_fastmcp(module) -> Any:
    """
    Find the FastMCP instance in a module.

    Looks for an attribute named ``mcp`` first, then scans all attributes
    for the first FastMCP instance.
    """
    from mcp.server.fastmcp import FastMCP

    # Convention: module-level ``mcp``
    obj = getattr(module, "mcp", None)
    if isinstance(obj, FastMCP):
        return obj

    # Fallback: first FastMCP found
    for name in dir(module):
        obj = getattr(module, name, None)
        if isinstance(obj, FastMCP):
            logger.info("Found FastMCP instance as %s.%s", module.__name__, name)
            return obj

    raise ValueError(
        f"Module {module.__name__!r} does not export a FastMCP instance. "
        "Define one as: mcp = FastMCP('name', stateless_http=True)"
    )


# ---------------------------------------------------------------------------
# FastAPI app — path stripping + lifespan for MCP session manager
# ---------------------------------------------------------------------------

# Stored at module level so @serve.ingress can reference it.
# The lifespan callback reads engine_config from the MCPEngine instance
# via app.state (set during __init__).
_engine_config_store: Dict[str, Any] = {}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Mount the MCP app and run its session manager."""
    engine_config = _engine_config_store.get("config", {})
    mcp_module_path = engine_config.get("mcp_module", "")

    if not mcp_module_path:
        logger.warning("No mcp_module configured — MCP endpoint will not be available")
        yield
        return

    logger.info("Importing MCP module: %s", mcp_module_path)
    module = importlib.import_module(mcp_module_path)
    mcp_instance = _find_fastmcp(module)

    logger.info("Mounting MCP app from %s (server=%s)",
                mcp_module_path, mcp_instance.name)
    app.mount("/mcp", mcp_instance.streamable_http_app())

    async with mcp_instance.session_manager.run():
        logger.info("MCP session manager started")
        yield
    logger.info("MCP session manager stopped")


class _RoutePathMiddleware:
    """Strip ASGI root_path prefix from scope path before FastAPI routing."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            root_path: str = scope.get("root_path", "")
            path: str = scope.get("path", "")
            if root_path and path.startswith(root_path):
                remainder = path[len(root_path):]
                if remainder == "" or remainder.startswith("/"):
                    scope = dict(scope)
                    scope["path"] = remainder or "/"
        await self.app(scope, receive, send)


_mcp_app = FastAPI(
    title="MCP Tool Server",
    description="MCP (Model Context Protocol) tool server powered by Ray Serve.",
    version="1.0.0",
    root_path_in_servers=True,
    lifespan=_lifespan,
    openapi_tags=[
        {"name": "Health", "description": "Liveness probe"},
    ],
)
_mcp_app.add_middleware(_RoutePathMiddleware)


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

@serve.deployment(
    name="mcp-server",
    num_replicas=1,
    ray_actor_options={"num_cpus": 0.2},
)
@serve.ingress(_mcp_app)
class MCPEngine:
    """
    Ray Serve deployment for MCP tool servers.

    Loads a user-provided FastMCP module and serves it behind FastAPI
    with Swagger UI and health checks.
    """

    def __init__(self, engine_config: Dict[str, Any]) -> None:
        logger.info("Initializing MCP engine with config: %s", engine_config)
        self._mcp_module = engine_config.get("mcp_module", "")
        # Store config so the lifespan callback can read it.
        _engine_config_store["config"] = engine_config

        # ── Ray Prometheus metrics ───────────────────────────────────────
        # Auto-exported on Ray's metrics port (9090) alongside system metrics.
        from ray.util.metrics import Counter, Histogram
        self._m_requests = Counter(
            "mcp_requests_total",
            description="Total MCP requests",
            tag_keys=("method", "status"),
        )
        self._m_request_latency = Histogram(
            "mcp_request_seconds",
            description="MCP request latency",
            boundaries=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0, 10.0],
        )
        self._m_tool_calls = Counter(
            "mcp_tool_calls_total",
            description="Tool calls by tool name",
            tag_keys=("tool_name",),
        )
        self._m_tool_latency = Histogram(
            "mcp_tool_seconds",
            description="Tool execution latency",
            tag_keys=("tool_name",),
            boundaries=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0],
        )
        logger.info("MCP Ray metrics initialized")

    @property
    def engine_type(self) -> str:
        return "mcp"

    @_mcp_app.get("/health", tags=["Health"], summary="Liveness probe")
    async def health_check(self):
        return {
            "status": "healthy",
            "engine": "mcp",
            "mcp_module": self._mcp_module,
        }


# ---------------------------------------------------------------------------
# Deployment factory
# ---------------------------------------------------------------------------

def create_mcp_deployment(
    engine_config: Dict[str, Any],
    num_replicas: int = 1,
    use_cpu: bool = True,
    venv_path: Optional[str] = None,
    scheduling_resources: Optional[Dict[str, float]] = None,
    scheduling_env_vars: Optional[Dict[str, str]] = None,
    **kwargs,
) -> serve.Application:
    """
    Create an MCP Ray Serve deployment.

    Args:
        engine_config: Must contain ``mcp_module`` (dotted module path).
        num_replicas: Number of replicas (default 1).
        use_cpu: Always True for MCP (no GPU needed).
        scheduling_resources: Optional Ray resource constraints for actor placement.
        scheduling_env_vars: Optional env vars merged into the actor's runtime_env.
        **kwargs: Ignored.

    Returns:
        Bound Ray Serve application.
    """
    num_cpus = engine_config.get("num_cpus", 0.2)
    ray_actor_options: Dict[str, Any] = {"num_cpus": num_cpus, "num_gpus": 0}

    rt_env: Dict[str, Any] = {}
    if venv_path:
        rt_env["py_executable"] = f"{venv_path}/bin/python"
        logger.info("Using isolated venv: %s", venv_path)
    if scheduling_env_vars:
        rt_env["env_vars"] = scheduling_env_vars
        logger.info("Scheduling env_vars applied: %s", list(scheduling_env_vars.keys()))
    if rt_env:
        ray_actor_options["runtime_env"] = rt_env
    if scheduling_resources:
        ray_actor_options.setdefault("resources", {})
        ray_actor_options["resources"].update(scheduling_resources)
        logger.info("Scheduling resources applied: %s", scheduling_resources)

    opts: Dict[str, Any] = {
        "num_replicas": num_replicas,
        "ray_actor_options": ray_actor_options,
    }

    # Optional autoscaling (overrides num_replicas)
    autoscaling = engine_config.get("autoscaling_config")
    if autoscaling:
        opts["autoscaling_config"] = autoscaling
        opts.pop("num_replicas", None)

    logger.info("Creating MCP deployment: replicas=%s  cpus=%s  module=%s",
                num_replicas, num_cpus, engine_config.get("mcp_module"))

    deployment = MCPEngine.options(**opts)
    return deployment.bind(engine_config)
