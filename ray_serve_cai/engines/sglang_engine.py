"""
SGLang Engine for Ray Serve.

Launches SGLang's HTTP server as a subprocess inside the Ray actor, then
proxies OpenAI-compatible requests from the FastAPI app.  This approach
gives us SGLang's full native Prometheus metrics (TTFT, cache hit rate,
token throughput, queue depth, etc.) via --enable-metrics.

Architecture
------------
  Client request
    → nginx → Ray Serve → FastAPI (@serve.ingress)
      → httpx proxy → localhost:<sglang_port> (SGLang HTTP server)
        → SGLang engine (GPU inference)

  Prometheus scrape
    → /api/v1/metrics/apps → <route_prefix>/metrics
      → FastAPI proxies to localhost:<sglang_port>/metrics

Endpoints
---------
  POST /v1/chat/completions  — proxied to SGLang
  POST /v1/completions       — proxied to SGLang
  GET  /v1/models            — proxied to SGLang
  GET  /metrics              — proxied (native SGLang Prometheus metrics)
  GET  /health               — liveness probe

References:
  SGLang: https://docs.sglang.io/
  SGLang metrics: https://docs.sglang.io/references/production_metrics.html
  Ray example: https://github.com/ray-project/ray/blob/master/python/ray/llm/examples/sglang/
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from ray import serve
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)

# Default port for the internal SGLang HTTP server (not exposed externally).
_DEFAULT_SGLANG_PORT = 30000


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

_sglang_app = FastAPI(
    title="SGLang OpenAI-Compatible API",
    description=(
        "OpenAI-compatible inference API powered by SGLang and Ray Serve.\n\n"
        "Supports `/v1/chat/completions`, `/v1/completions`, `/v1/models`, "
        "and `/metrics` (native SGLang Prometheus metrics)."
    ),
    version="1.0.0",
    root_path_in_servers=True,
    openapi_tags=[
        {"name": "Chat",        "description": "Chat completion endpoints"},
        {"name": "Completions", "description": "Text completion endpoints"},
        {"name": "Models",      "description": "Model registry"},
        {"name": "Metrics",     "description": "Prometheus metrics"},
        {"name": "Health",      "description": "Liveness probe"},
    ],
)
_sglang_app.add_middleware(_RoutePathMiddleware)


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

@serve.deployment(
    name="sglang-deployment",
    num_replicas=1,
    ray_actor_options={},
    max_ongoing_requests=100,
)
@serve.ingress(_sglang_app)
class SGLangEngine:
    """
    Ray Serve deployment for SGLang.

    Launches SGLang's HTTP server as a subprocess and proxies requests.
    Native Prometheus metrics are available at /metrics.
    """

    def __init__(self, engine_config: Dict[str, Any]) -> None:
        logger.info("Initializing SGLang engine with config: %s", engine_config)

        self.model_name = engine_config.get("model", "unknown")
        self.tensor_parallel_size = engine_config.get("tensor_parallel_size", 1)
        self._sglang_port = engine_config.get("sglang_port", _DEFAULT_SGLANG_PORT)
        self._base_url = f"http://127.0.0.1:{self._sglang_port}"

        # Build sglang launch command using the isolated venv Python so sglang
        # is resolved from .venv-sglang, not the root venv.
        _sglang_venv = engine_config.get("venv_path", "/home/cdsw/.venv-sglang")
        _python_bin = f"{_sglang_venv}/bin/python"
        cmd = [
            _python_bin, "-m", "sglang.launch_server",
            "--model-path", engine_config["model"],
            "--port", str(self._sglang_port),
            "--host", "127.0.0.1",
            "--enable-metrics",
        ]

        # Map config keys to CLI flags
        if self.tensor_parallel_size > 1:
            cmd += ["--tp-size", str(self.tensor_parallel_size)]
        if engine_config.get("dtype"):
            cmd += ["--dtype", engine_config["dtype"]]
        if engine_config.get("trust_remote_code"):
            cmd += ["--trust-remote-code"]
        if engine_config.get("context_length"):
            cmd += ["--context-length", str(engine_config["context_length"])]
        if engine_config.get("mem_fraction_static"):
            cmd += ["--mem-fraction-static", str(engine_config["mem_fraction_static"])]
        if engine_config.get("quantization"):
            cmd += ["--quantization", engine_config["quantization"]]

        logger.info("Starting SGLang server: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Wait for server to be ready
        self._wait_for_server(timeout=300)
        logger.info("SGLang server ready on port %d", self._sglang_port)

    def _wait_for_server(self, timeout: int = 300) -> None:
        """Poll SGLang's health endpoint until it responds."""
        deadline = time.time() + timeout
        url = f"{self._base_url}/health"
        while time.time() < deadline:
            # Check process is still alive
            if self._process.poll() is not None:
                stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                raise RuntimeError(
                    f"SGLang server exited with code {self._process.returncode}.\n"
                    f"Output:\n{stdout[-2000:]}"
                )
            try:
                import urllib.request
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            time.sleep(2)
        raise TimeoutError(
            f"SGLang server did not become ready within {timeout}s"
        )

    def __del__(self):
        if hasattr(self, "_process") and self._process.poll() is None:
            logger.info("Shutting down SGLang server (pid=%d)", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()

    # ------------------------------------------------------------------
    # Proxy helpers
    # ------------------------------------------------------------------

    async def _proxy_json(self, path: str, request: Request) -> Response:
        """Proxy a JSON request to the SGLang server and return the response."""
        body = await request.body()
        async with httpx.AsyncClient(base_url=self._base_url, timeout=120) as client:
            resp = await client.request(
                method=request.method,
                url=path,
                content=body,
                headers={"Content-Type": "application/json"},
            )
            return JSONResponse(
                content=resp.json(),
                status_code=resp.status_code,
            )

    async def _proxy_stream(self, path: str, request: Request) -> StreamingResponse:
        """Proxy a streaming request to the SGLang server."""
        body = await request.body()

        async def _stream():
            async with httpx.AsyncClient(base_url=self._base_url, timeout=120) as client:
                async with client.stream(
                    method="POST",
                    url=path,
                    content=body,
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(
            content=_stream(),
            media_type="text/event-stream",
        )

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @_sglang_app.post("/v1/chat/completions", tags=["Chat"],
                      summary="Chat completion (OpenAI-compatible)",
                      response_model=None)
    async def chat_completion(self, request: Request):
        body = await request.json()
        raw_request = Request(scope=request.scope, receive=request.receive)
        # Re-create request with the body for proxy
        if body.get("stream"):
            return await self._proxy_stream("/v1/chat/completions", request)
        return await self._proxy_json("/v1/chat/completions", request)

    @_sglang_app.post("/v1/completions", tags=["Completions"],
                      summary="Text completion (OpenAI-compatible)",
                      response_model=None)
    async def completion(self, request: Request):
        body = await request.json()
        if body.get("stream"):
            return await self._proxy_stream("/v1/completions", request)
        return await self._proxy_json("/v1/completions", request)

    @_sglang_app.get("/v1/models", tags=["Models"],
                     summary="List available models",
                     response_model=None)
    async def list_models(self, request: Request):
        return await self._proxy_json("/v1/models", request)

    @_sglang_app.get("/metrics", tags=["Metrics"],
                     summary="Native SGLang Prometheus metrics",
                     response_model=None)
    async def metrics(self):
        """Proxy SGLang's native Prometheus metrics endpoint."""
        async with httpx.AsyncClient(base_url=self._base_url, timeout=10) as client:
            try:
                resp = await client.get("/metrics")
                return Response(
                    content=resp.content,
                    media_type="text/plain; version=0.0.4; charset=utf-8",
                    status_code=resp.status_code,
                )
            except Exception as exc:
                return Response(
                    content=f"# SGLang metrics unavailable: {exc}\n",
                    media_type="text/plain",
                    status_code=503,
                )

    @_sglang_app.get("/health", tags=["Health"],
                     summary="Liveness probe")
    async def health_check(self):
        alive = self._process.poll() is None
        return {
            "status": "healthy" if alive else "unhealthy",
            "model": self.model_name,
            "engine": "sglang",
            "tensor_parallel_size": self.tensor_parallel_size,
            "sglang_port": self._sglang_port,
            "sglang_pid": self._process.pid,
            "sglang_alive": alive,
        }


# ---------------------------------------------------------------------------
# Deployment factory
# ---------------------------------------------------------------------------

def create_sglang_deployment(
    engine_config: Dict[str, Any],
    num_replicas: int = 1,
    tensor_parallel_size: int = 1,
    use_cpu: bool = False,
    max_ongoing_requests: int = 100,
    gpu_fraction: Optional[float] = None,
    placement_group_bundles: Optional[List[Dict[str, float]]] = None,
    placement_group_strategy: Optional[str] = None,
    venv_path: Optional[str] = None,
    scheduling_resources: Optional[Dict[str, float]] = None,
    scheduling_env_vars: Optional[Dict[str, str]] = None,
) -> serve.Application:
    """Create an SGLang Ray Serve deployment."""
    logger.info("Creating SGLang deployment  replicas=%d  tp=%d  cpu=%s",
                num_replicas, tensor_parallel_size, use_cpu)

    if use_cpu:
        ray_actor_options: Dict[str, Any] = {"num_cpus": 4, "num_gpus": 0}
    elif tensor_parallel_size > 1:
        ray_actor_options = {
            "num_cpus": tensor_parallel_size,
            "num_gpus": tensor_parallel_size,
        }
    elif gpu_fraction is not None:
        ray_actor_options = {"num_cpus": 2, "num_gpus": gpu_fraction}
    else:
        ray_actor_options = {"num_cpus": 2, "num_gpus": 1}

    # ── Node affinity resolution ─────────────────────────────────────────────
    # scheduling_resources takes full precedence over the legacy node_type
    # shorthand.  Applied to GPU-bearing placement group bundles when a PG is in
    # play, or to the actor directly when it is not — a PG would otherwise reject
    # an actor whose resource request isn't a subset of its assigned bundle.
    node_type = engine_config.get("node_type")
    if scheduling_resources:
        _affinity: Dict[str, float] = dict(scheduling_resources)
    elif node_type:
        _affinity = {f"node_type:{node_type}": 0.001}
    else:
        _affinity = {}

    # Auto placement groups
    if placement_group_bundles is None and not use_cpu:
        if tensor_parallel_size > 1:
            placement_group_bundles = [
                {"GPU": float(tensor_parallel_size),
                 "CPU": float(tensor_parallel_size), **_affinity}
            ]
            placement_group_strategy = placement_group_strategy or "STRICT_PACK"
        elif gpu_fraction is not None and gpu_fraction < 1.0:
            placement_group_bundles = [{"GPU": gpu_fraction, "CPU": 2.0, **_affinity}]
            placement_group_strategy = placement_group_strategy or "PACK"
    elif placement_group_bundles is not None and _affinity:
        # Explicit bundles: merge affinity into GPU-bearing bundles (fallback to
        # all bundles if none carry a GPU) so scheduling.resources isn't dropped.
        _gpu_bundles = [b for b in placement_group_bundles if b.get("GPU", 0)]
        for _b in (_gpu_bundles or placement_group_bundles):
            _b.update(_affinity)
        logger.info("Merged scheduling resources into explicit bundles: %s", _affinity)

    # Actor-level affinity only when there is NO placement group (see vLLM note).
    if _affinity and placement_group_bundles is None:
        ray_actor_options.setdefault("resources", {})
        ray_actor_options["resources"].update(_affinity)
        logger.info("Pinning deployment via ray_actor_options resources: %s", _affinity)

    # Runtime env: venv + scheduling env_vars
    rt_env: Dict[str, Any] = {}
    if venv_path:
        rt_env["py_executable"] = f"{venv_path}/bin/python"
        # Propagate to engine_config so the SGLang subprocess launches from the same venv.
        engine_config["venv_path"] = venv_path
        logger.info("Using isolated venv: %s", venv_path)
    if scheduling_env_vars:
        rt_env["env_vars"] = scheduling_env_vars
        logger.info("Scheduling env_vars applied: %s", list(scheduling_env_vars.keys()))
    if rt_env:
        ray_actor_options["runtime_env"] = rt_env

    autoscaling = engine_config.get("autoscaling_config")
    opts: Dict[str, Any] = {
        "ray_actor_options": ray_actor_options,
        "max_ongoing_requests": max_ongoing_requests,
    }
    if autoscaling:
        opts["autoscaling_config"] = autoscaling
    else:
        opts["num_replicas"] = num_replicas
    if placement_group_bundles is not None:
        opts["placement_group_bundles"] = placement_group_bundles
        if placement_group_strategy:
            opts["placement_group_strategy"] = placement_group_strategy

    deployment = SGLangEngine.options(**opts)
    return deployment.bind(engine_config)
