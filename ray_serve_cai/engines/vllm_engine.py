"""
vLLM Engine for Ray Serve
Leverages vLLM's built-in OpenAI-compatible request handlers.

Import paths and constructor signatures differ across vLLM versions:

  v0.13.x  — flat layout under vllm/entrypoints/openai/
  v0.18.0+ — subdirectory layout; OpenAIServingChat / OpenAIServingCompletion
              both require a new `openai_serving_render` argument built from
              renderer_from_config() + get_io_processor()

Both layouts are handled via try/except on imports and signature inspection.

References:
  vLLM OpenAI Server: https://docs.vllm.ai/en/stable/serving/openai_compatible_server.html
  Ray Placement Groups: https://docs.ray.io/en/latest/serve/llm/user-guides/cross-node-parallelism.html
"""

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from ray import serve
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from vllm import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs

# ---------------------------------------------------------------------------
# Version-aware imports
# vLLM 0.14+/0.18+ uses a subdirectory layout; 0.13.x uses flat files.
# ---------------------------------------------------------------------------
try:
    # vLLM 0.14+ / 0.18+ (subdirectory layout)
    from vllm.entrypoints.openai.completion.serving import OpenAIServingCompletion
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.entrypoints.openai.models.serving import OpenAIServingModels
    from vllm.entrypoints.openai.models.protocol import BaseModelPath
    from vllm.entrypoints.openai.completion.protocol import CompletionRequest
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
    _VLLM_NEW_LAYOUT = True
except ImportError:
    # vLLM 0.13.x (flat layout)
    from vllm.entrypoints.openai.serving_completion import OpenAIServingCompletion   # type: ignore[no-redef]
    from vllm.entrypoints.openai.serving_chat import OpenAIServingChat               # type: ignore[no-redef]
    from vllm.entrypoints.openai.serving_models import (                             # type: ignore[no-redef]
        OpenAIServingModels,
        BaseModelPath,
    )
    from vllm.entrypoints.openai.protocol import (                                   # type: ignore[no-redef]
        CompletionRequest,
        ChatCompletionRequest,
    )
    _VLLM_NEW_LAYOUT = False

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _accepted_kwargs(cls, kwargs: dict) -> dict:
    """Return only the kwargs that cls.__init__ actually accepts."""
    import inspect
    valid = set(inspect.signature(cls.__init__).parameters) - {"self"}
    return {k: v for k, v in kwargs.items() if k in valid}


def _requires_param(cls, param: str) -> bool:
    """Return True if cls.__init__ declares *param* (any default)."""
    import inspect
    return param in inspect.signature(cls.__init__).parameters


def _build_serving_render(engine_args: AsyncEngineArgs, model_name: str,
                          chat_template: Optional[str] = None):
    """
    Build OpenAIServingRender for vLLM v0.18.0+.

    OpenAIServingRender wraps the renderer (tokeniser + chat-template engine)
    and the io_processor (multi-modal input pre-processing).  Both are derived
    from VllmConfig which we build deterministically from AsyncEngineArgs.
    """
    from vllm.entrypoints.openai.models.serving import OpenAIModelRegistry
    from vllm.entrypoints.serve.render.serving import OpenAIServingRender
    from vllm.plugins.io_processors import get_io_processor
    from vllm.renderers import renderer_from_config

    # VllmConfig is built from engine args — no running engine needed.
    vllm_config = engine_args.create_engine_config()
    model_config = vllm_config.model_config

    base_model_path = BaseModelPath(name=model_name, model_path=model_config.model)
    model_registry = OpenAIModelRegistry(
        model_config=model_config,
        base_model_paths=[base_model_path],
    )

    renderer = renderer_from_config(vllm_config)
    io_processor = get_io_processor(
        vllm_config,
        renderer,
        getattr(model_config, "io_processor_plugin", None),
    )

    # chat_template_content_format: accept the enum or fall back to "auto".
    try:
        from vllm.entrypoints.chat_utils import ChatTemplateContentFormatOption
        content_format = ChatTemplateContentFormatOption("auto")
    except Exception:
        content_format = "auto"  # type: ignore[assignment]

    return OpenAIServingRender(
        model_config=model_config,
        renderer=renderer,
        io_processor=io_processor,
        model_registry=model_registry,
        request_logger=None,
        chat_template=chat_template,
        chat_template_content_format=content_format,
    )


# ---------------------------------------------------------------------------
# ASGI middleware — strips route_prefix from scope["path"] before routing.
#
# Ray Serve (starlette >= 0.33.0) sets scope["root_path"] to the deployment's
# route_prefix (e.g. "/paddleocr") but does NOT strip it from scope["path"],
# leaving scope["path"] = "/paddleocr/v1/chat/completions".  The FastAPI
# routes are registered without the prefix ("/v1/chat/completions"), so they
# would not match the full path → 404.
#
# Starlette 0.33.0+ handles this via get_route_path() in the Router, but the
# behaviour differs across deployment environments and Ray Serve versions.
# This middleware normalises the path unconditionally so routing is reliable.
# ---------------------------------------------------------------------------

class _RoutePathMiddleware:
    """Strip ASGI root_path prefix from scope path before FastAPI routing."""

    def __init__(self, app: ASGIApp) -> None:
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
                    # Keep root_path so Swagger UI server URL generation
                    # still uses the correct prefix.
        await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# FastAPI app — provides Swagger UI at <route_prefix>/docs
# Defined at module level; @serve.ingress binds it to the deployment class.
# root_path_in_servers=True tells FastAPI to use the ASGI root_path set by
# Ray Serve (the deployment's route_prefix) as the server base URL, so the
# "Try it out" button in Swagger UI sends requests to the correct path.
# ---------------------------------------------------------------------------
_vllm_app = FastAPI(
    title="vLLM OpenAI-Compatible API",
    description=(
        "OpenAI-compatible inference API powered by vLLM and Ray Serve.\n\n"
        "Supports `/v1/chat/completions`, `/v1/completions`, and `/v1/models`."
    ),
    version="1.0.0",
    root_path_in_servers=True,
    openapi_tags=[
        {"name": "Chat",        "description": "Chat completion endpoints"},
        {"name": "Completions", "description": "Text completion endpoints"},
        {"name": "Models",      "description": "Model registry"},
        {"name": "Health",      "description": "Liveness probe"},
    ],
)
# Apply the path-stripping middleware so all starlette/Ray Serve version
# combinations route correctly regardless of path-stripping behaviour.
_vllm_app.add_middleware(_RoutePathMiddleware)


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

@serve.deployment(
    name="vllm-deployment",
    num_replicas=1,
    ray_actor_options={},
    # Allow many concurrent streaming connections per replica.
    # vLLM's AsyncLLMEngine handles real concurrency internally via continuous
    # batching; this gate just needs to be high enough not to throttle it.
    # Default Ray Serve value is 5, which is far too low for streaming LLMs.
    # Reference: https://docs.ray.io/en/latest/serve/tutorials/streaming.html
    max_ongoing_requests=100,
)
@serve.ingress(_vllm_app)
class VLLMEngine:
    """
    Ray Serve deployment for vLLM with OpenAI-compatible API.

    Reuses vLLM's built-in OpenAI request handlers for maximum compatibility.
    Handles API differences between vLLM 0.13.x and 0.18.0+ transparently.

    Endpoints:
      POST /v1/completions        — text completion
      POST /v1/chat/completions   — chat completion
      GET  /v1/models             — model list
      GET  /health                — liveness probe
    """

    def __init__(self, engine_config: Dict[str, Any]) -> None:
        logger.info("Initializing vLLM engine with config: %s", engine_config)

        try:
            import os

            # attention_backend must be set as an env var before the engine
            # starts — vLLM's EngineCore subprocess inherits it from us.
            attention_backend = engine_config.pop("attention_backend", None)
            if attention_backend:
                os.environ["VLLM_ATTENTION_BACKEND"] = attention_backend
                logger.info("Set VLLM_ATTENTION_BACKEND=%s", attention_backend)

            self.engine_args = AsyncEngineArgs(**engine_config)
            self.engine = AsyncLLMEngine.from_engine_args(self.engine_args)

            self.model_name = engine_config.get("model", "unknown")
            self.tensor_parallel_size = engine_config.get("tensor_parallel_size", 1)

            model_config = self.engine.model_config

            # ── OpenAIServingModels ──────────────────────────────────────────
            base_model_path = BaseModelPath(
                name=self.model_name,
                model_path=model_config.model,
            )
            self.openai_serving_models = OpenAIServingModels(
                engine_client=self.engine,
                base_model_paths=[base_model_path],
            )

            # ── OpenAIServingCompletion / OpenAIServingChat ──────────────────
            # v0.18.0+ requires openai_serving_render; build it lazily only
            # when the parameter is actually declared in the constructor.
            if _requires_param(OpenAIServingCompletion, "openai_serving_render"):
                # vLLM 0.18.0+
                logger.info("Detected vLLM 0.18.0+ layout — building OpenAIServingRender")
                serving_render = _build_serving_render(
                    engine_args=self.engine_args,
                    model_name=self.model_name,
                    chat_template=engine_config.get("chat_template"),
                )

                self.openai_serving_completion = OpenAIServingCompletion(
                    engine_client=self.engine,
                    models=self.openai_serving_models,
                    openai_serving_render=serving_render,
                    request_logger=None,
                )

                try:
                    from vllm.entrypoints.chat_utils import ChatTemplateContentFormatOption
                    content_format = ChatTemplateContentFormatOption("auto")
                except Exception:
                    content_format = "auto"  # type: ignore[assignment]

                self.openai_serving_chat = OpenAIServingChat(
                    engine_client=self.engine,
                    models=self.openai_serving_models,
                    response_role="assistant",
                    openai_serving_render=serving_render,
                    request_logger=None,
                    chat_template=engine_config.get("chat_template"),
                    chat_template_content_format=content_format,
                )

            else:
                # vLLM 0.13.x — filter to only supported kwargs
                self.openai_serving_completion = OpenAIServingCompletion(
                    **_accepted_kwargs(OpenAIServingCompletion, {
                        "engine_client":                self.engine,
                        "models":                       self.openai_serving_models,
                        "request_logger":               None,
                        "return_tokens_as_token_ids":   False,
                        "enable_prompt_tokens_details": False,
                        "enable_force_include_usage":   False,
                        "log_error_stack":              False,
                    })
                )
                self.openai_serving_chat = OpenAIServingChat(
                    **_accepted_kwargs(OpenAIServingChat, {
                        "engine_client":                self.engine,
                        "models":                       self.openai_serving_models,
                        "response_role":                "assistant",
                        "request_logger":               None,
                        "return_tokens_as_token_ids":   False,
                        "log_error_stack":              False,
                    })
                )

            logger.info("✅ vLLM engine initialized  model=%s  tp=%d",
                        self.model_name, self.tensor_parallel_size)

        except Exception as exc:
            logger.error("❌ Failed to initialize vLLM engine: %s", exc)
            import traceback
            logger.error(traceback.format_exc())
            raise

    # ------------------------------------------------------------------
    # Engine type
    # ------------------------------------------------------------------

    @property
    def engine_type(self) -> str:
        return "vllm"

    # ------------------------------------------------------------------
    # Endpoints — decorated with _vllm_app routes so FastAPI generates
    # the OpenAPI schema and serves Swagger UI at <route_prefix>/docs.
    # FastAPI injects the parsed Pydantic body and the raw Request;
    # both are forwarded directly to vLLM's serving handlers.
    # Response is returned as-is (JSONResponse or StreamingResponse).
    # ------------------------------------------------------------------

    @_vllm_app.post("/v1/chat/completions", tags=["Chat"],
                    summary="Chat completion (OpenAI-compatible)")
    async def chat_completion(
        self, body: ChatCompletionRequest, request: Request
    ) -> Response:
        try:
            result = await self.openai_serving_chat.create_chat_completion(
                body, raw_request=request
            )
        except Exception as exc:
            logger.error("Error in chat completion: %s", exc)
            import traceback; logger.error(traceback.format_exc())
            return JSONResponse({"error": str(exc)}, status_code=500)

        # For streaming responses the generator runs after this handler returns,
        # so exceptions during streaming are invisible to the try/except above.
        # Wrap the body iterator to log the real cause before Ray Serve swallows
        # it with "raise e from None" in fetch_messages_from_queue.
        if isinstance(result, StreamingResponse):
            original_iter = result.body_iterator

            async def _logged_stream():
                try:
                    async for chunk in original_iter:
                        yield chunk
                except asyncio.CancelledError:
                    # Client disconnected mid-stream — normal, not an error.
                    logger.debug("Streaming chat completion cancelled (client disconnect)")
                    raise
                except Exception as exc:
                    logger.error("Exception during streaming chat completion: %s", exc,
                                 exc_info=True)
                    raise

            result.body_iterator = _logged_stream()

        return result

    @_vllm_app.post("/v1/completions", tags=["Completions"],
                    summary="Text completion (OpenAI-compatible)")
    async def completion(
        self, body: CompletionRequest, request: Request
    ) -> Response:
        try:
            return await self.openai_serving_completion.create_completion(
                body, raw_request=request
            )
        except Exception as exc:
            logger.error("Error in completion: %s", exc)
            import traceback; logger.error(traceback.format_exc())
            return JSONResponse({"error": str(exc)}, status_code=500)

    @_vllm_app.get("/v1/models", tags=["Models"],
                   summary="List available models")
    async def list_models(self) -> Response:
        models = await self.openai_serving_models.show_available_models()
        return JSONResponse(content=models.model_dump())

    @_vllm_app.get("/health", tags=["Health"],
                   summary="Liveness probe")
    async def health_check(self) -> dict:
        return {
            "status": "healthy",
            "model": self.model_name,
            "engine": "vllm",
            "tensor_parallel_size": self.tensor_parallel_size,
        }


# ---------------------------------------------------------------------------
# Deployment factory
# ---------------------------------------------------------------------------

def create_vllm_deployment(
    engine_config: Dict[str, Any],
    num_replicas: int = 1,
    tensor_parallel_size: int = 1,
    use_cpu: bool = False,
    max_ongoing_requests: int = 100,
) -> serve.Application:
    """
    Create a vLLM Ray Serve deployment with appropriate resource allocation.

    max_ongoing_requests controls how many concurrent HTTP connections (including
    long-lived streaming requests) each replica accepts.  vLLM's AsyncLLMEngine
    uses continuous batching so many requests can be in-flight simultaneously;
    this value should be at least as large as the engine's max_num_seqs.

    References:
      Streaming: https://docs.ray.io/en/latest/serve/tutorials/streaming.html
      Placement groups: https://docs.ray.io/en/latest/serve/llm/user-guides/cross-node-parallelism.html
      vLLM distributed: https://docs.vllm.ai/en/stable/serving/distributed_serving.html
    """
    logger.info("Creating vLLM deployment  replicas=%d  tp=%d  cpu=%s  max_ongoing=%d",
                num_replicas, tensor_parallel_size, use_cpu, max_ongoing_requests)

    if use_cpu:
        ray_actor_options: Dict[str, Any] = {"num_cpus": 4, "num_gpus": 0}
    elif tensor_parallel_size > 1:
        placement_group_bundles = [
            {"GPU": 1, "CPU": 1} for _ in range(tensor_parallel_size)
        ]
        ray_actor_options = {
            "num_cpus": tensor_parallel_size,
            "num_gpus": tensor_parallel_size,
            "placement_group_bundles": placement_group_bundles,
            "placement_group_strategy": "PACK",
        }
        logger.info("Using placement group with %d bundles (1 GPU each)", tensor_parallel_size)
    else:
        ray_actor_options = {"num_cpus": 2, "num_gpus": 1}

    deployment = VLLMEngine.options(
        num_replicas=num_replicas,
        ray_actor_options=ray_actor_options,
        max_ongoing_requests=max_ongoing_requests,
    )
    return deployment.bind(engine_config)
