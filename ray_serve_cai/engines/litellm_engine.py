"""
LiteLLM Engine for Ray Serve.

Launches LiteLLM's proxy server as a subprocess inside the Ray actor, then
proxies OpenAI-compatible requests from the FastAPI app.  This gives a single
unified endpoint for 100+ model providers (OpenAI, Anthropic, Bedrock, Azure,
Vertex AI, Ollama, local vLLM, etc.).

Architecture
------------
  Client request
    → nginx → Ray Serve → FastAPI (@serve.ingress)
      → httpx proxy → localhost:<litellm_port> (LiteLLM proxy)
        → LiteLLM router → provider (OpenAI / Bedrock / Anthropic / ...)

  Prometheus scrape
    → /api/v1/metrics/apps → <route_prefix>/metrics
      → proxied to localhost:<litellm_port>/metrics (if available)

Endpoints
---------
  POST /v1/chat/completions  — proxied to LiteLLM
  POST /v1/completions       — proxied to LiteLLM
  GET  /v1/models            — proxied to LiteLLM
  GET  /metrics              — proxied (LiteLLM Prometheus metrics)
  GET  /health               — liveness probe

Config
------
engine_config must contain:
  model_list (list)  — [{model_name, litellm_params: {model, api_key, ...}}]
  litellm_settings (dict, optional) — passed through to LiteLLM config YAML

References:
  LiteLLM: https://docs.litellm.ai/
  LiteLLM config: https://docs.litellm.ai/docs/proxy/configs
"""

import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from ray import serve
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Receive, Scope, Send

logger = logging.getLogger(__name__)

_DEFAULT_LITELLM_PORT = 4000


# ---------------------------------------------------------------------------
# ASGI middleware — strips root_path from scope["path"]
# ---------------------------------------------------------------------------

class _RoutePathMiddleware:
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

_litellm_app = FastAPI(
    title="LiteLLM OpenAI-Compatible API",
    description=(
        "OpenAI-compatible API gateway powered by LiteLLM and Ray Serve.\n\n"
        "Routes requests to 100+ model providers via a single interface."
    ),
    version="1.0.0",
    root_path_in_servers=True,
)
_litellm_app.add_middleware(_RoutePathMiddleware)


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

@serve.deployment(
    name="litellm-deployment",
    num_replicas=1,
    ray_actor_options={},
    max_ongoing_requests=200,
)
@serve.ingress(_litellm_app)
class LiteLLMEngine:
    """
    Ray Serve deployment for LiteLLM.

    Launches LiteLLM's proxy server as a subprocess and proxies requests.
    """

    def __init__(self, engine_config: Dict[str, Any]) -> None:
        logger.info("Initializing LiteLLM engine")

        self._port = engine_config.get("litellm_port", _DEFAULT_LITELLM_PORT)
        self._base_url = f"http://127.0.0.1:{self._port}"

        # Write LiteLLM config YAML to a temp file
        litellm_cfg: Dict[str, Any] = {
            "model_list": engine_config["model_list"],
        }
        if engine_config.get("litellm_settings"):
            litellm_cfg["litellm_settings"] = engine_config["litellm_settings"]

        # Use a named temp file so the subprocess can read it after open()
        self._config_file = tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            prefix="litellm_config_",
            delete=False,
        )
        yaml.dump(litellm_cfg, self._config_file)
        self._config_file.flush()
        config_path = self._config_file.name
        logger.info("LiteLLM config written to %s", config_path)

        cmd = [
            sys.executable, "-m", "litellm.proxy.server",
            "--config", config_path,
            "--port", str(self._port),
            "--host", "127.0.0.1",
            "--detailed_debug",
        ]

        logger.info("Starting LiteLLM proxy: %s", " ".join(cmd))
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self._wait_for_server(timeout=120)
        logger.info("LiteLLM proxy ready on port %d", self._port)

    def _wait_for_server(self, timeout: int = 120) -> None:
        """Poll /health until LiteLLM responds."""
        deadline = time.time() + timeout
        url = f"{self._base_url}/health"
        while time.time() < deadline:
            if self._process.poll() is not None:
                stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                raise RuntimeError(
                    f"LiteLLM proxy exited with code {self._process.returncode}.\n"
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
        raise TimeoutError(f"LiteLLM proxy did not become ready within {timeout}s")

    def __del__(self) -> None:
        if hasattr(self, "_process") and self._process.poll() is None:
            logger.info("Shutting down LiteLLM proxy (pid=%d)", self._process.pid)
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        # Clean up temp config file
        if hasattr(self, "_config_file"):
            try:
                Path(self._config_file.name).unlink(missing_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Proxy helpers
    # ------------------------------------------------------------------

    async def _proxy_json(self, path: str, request: Request) -> Response:
        body = await request.body()
        async with httpx.AsyncClient(base_url=self._base_url, timeout=120) as client:
            resp = await client.request(
                method=request.method,
                url=path,
                content=body,
                headers={"Content-Type": "application/json"},
            )
            return JSONResponse(content=resp.json(), status_code=resp.status_code)

    async def _proxy_stream(self, path: str, request: Request) -> StreamingResponse:
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

        return StreamingResponse(content=_stream(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @_litellm_app.post("/v1/chat/completions", response_model=None)
    async def chat_completion(self, request: Request):
        body = await request.json()
        if body.get("stream"):
            return await self._proxy_stream("/v1/chat/completions", request)
        return await self._proxy_json("/v1/chat/completions", request)

    @_litellm_app.post("/v1/completions", response_model=None)
    async def completion(self, request: Request):
        body = await request.json()
        if body.get("stream"):
            return await self._proxy_stream("/v1/completions", request)
        return await self._proxy_json("/v1/completions", request)

    @_litellm_app.get("/v1/models", response_model=None)
    async def list_models(self, request: Request):
        return await self._proxy_json("/v1/models", request)

    @_litellm_app.get("/metrics", response_model=None)
    async def metrics(self):
        """Proxy LiteLLM Prometheus metrics (available when prometheus_url is configured)."""
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
                    content=f"# LiteLLM metrics unavailable: {exc}\n",
                    media_type="text/plain",
                    status_code=503,
                )

    @_litellm_app.get("/health")
    async def health_check(self):
        alive = self._process.poll() is None
        return {
            "status": "healthy" if alive else "unhealthy",
            "engine": "litellm",
            "litellm_port": self._port,
            "litellm_pid": self._process.pid if hasattr(self, "_process") else None,
            "litellm_alive": alive,
        }
