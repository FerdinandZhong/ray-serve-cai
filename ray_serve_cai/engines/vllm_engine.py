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
import inspect
import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from ray import serve
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

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


def _normalize_vllm_stream_result(result: Any, *, op_name: str) -> Any:
    """
    Ensure FastAPI / Ray Serve never try to json-encode a streaming payload.

    vLLM behavior varies by version:
      - Returns AsyncGenerator[str] (SSE lines) directly.
      - Returns Starlette StreamingResponse.

    Additionally, vLLM may use a different Starlette install than this app,
    so isinstance(..., StreamingResponse) can be False even when the object
    is stream-shaped.  Duck-type on ``body_iterator``.

    If we mis-route on ``body.stream`` and call the non-stream handler while
    the payload still has stream=True, vLLM still returns a generator — a
    single post-await normalization fixes that without relying on branch flags.
    """
    disconnect_log = f"{op_name} cancelled (client disconnect)"
    err_prefix = f"Exception during {op_name}"

    body_iter: Any = None
    stream_kw: Dict[str, Any]

    if inspect.isasyncgen(result):
        body_iter = result
        stream_kw = {
            "status_code": 200,
            "media_type": "text/event-stream",
        }
    else:
        maybe_iter = getattr(result, "body_iterator", None)
        if maybe_iter is not None:
            body_iter = maybe_iter
            stream_kw = {
                "status_code": getattr(result, "status_code", 200),
                "media_type": getattr(result, "media_type", None)
                or "text/event-stream",
            }
            hdrs = getattr(result, "headers", None)
            if hdrs is not None:
                stream_kw["headers"] = hdrs
        else:
            return result

    async def _logged_stream():
        try:
            async for chunk in body_iter:
                yield chunk
        except asyncio.CancelledError:
            logger.debug("%s", disconnect_log)
            raise
        except Exception as exc:
            logger.error("%s: %s", err_prefix, exc, exc_info=True)
            raise

    return StreamingResponse(content=_logged_stream(), **stream_kw)


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
# Ray Serve sets scope["root_path"] to the deployment's route_prefix but does
# NOT strip it from scope["path"].  FastAPI routes are registered without the
# prefix, so they would not match → 404.
#
# Must be a plain class (not a FastAPI subclass) because Ray Serve serializes
# the module-level FastAPI instance via @serve.ingress.  A FastAPI subclass
# breaks Ray's serializer ("cannot pickle '_thread.lock'").
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
# FastAPI app — provides Swagger UI at <route_prefix>/docs
# Defined at module level; @serve.ingress binds it to the deployment class.
#
# Streaming: all endpoints use response_model=None and the normaliser
# _normalize_vllm_stream_result() to ensure StreamingResponse is never
# JSON-encoded, regardless of middleware stack ordering.
# ---------------------------------------------------------------------------

_vllm_app = FastAPI(
    title="vLLM OpenAI-Compatible API",
    description=(
        "OpenAI-compatible inference API powered by vLLM and Ray Serve.\n\n"
        "Supports `/v1/chat/completions`, `/v1/completions`, `/v1/models`, "
        "and `/metrics` (Prometheus)."
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

            # Use Ray-native Prometheus metrics instead of prometheus_client.
            # RayPrometheusStatLogger wraps all vLLM metrics with
            # ray.util.metrics so they auto-export on Ray's metrics port
            # (9090) alongside system metrics — no FastAPI /metrics route
            # or attach_router needed.
            _stat_loggers = None
            try:
                from vllm.v1.metrics.ray_wrappers import RayPrometheusStatLogger
                _stat_loggers = [RayPrometheusStatLogger]
                logger.info("Using RayPrometheusStatLogger for vLLM metrics")
            except ImportError:
                logger.debug("RayPrometheusStatLogger not available (older vLLM)")

            self.engine = AsyncLLMEngine.from_engine_args(
                self.engine_args,
                stat_loggers=_stat_loggers,
            )

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
    # Endpoints
    #
    # Always await vLLM once, then normalize streams.  Do not branch on
    # body.stream before calling vLLM: optional/coerced ``stream`` can be
    # wrong; vLLM still returns an async generator when the request streams.
    # ------------------------------------------------------------------

    @_vllm_app.post("/v1/chat/completions", tags=["Chat"],
                    summary="Chat completion (OpenAI-compatible)",
                    response_model=None)
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

        return _normalize_vllm_stream_result(
            result, op_name="streaming chat completion"
        )

    @_vllm_app.post("/v1/completions", tags=["Completions"],
                    summary="Text completion (OpenAI-compatible)",
                    response_model=None)
    async def completion(
        self, body: CompletionRequest, request: Request
    ) -> Response:
        try:
            result = await self.openai_serving_completion.create_completion(
                body, raw_request=request
            )
        except Exception as exc:
            logger.error("Error in completion: %s", exc)
            import traceback; logger.error(traceback.format_exc())
            return JSONResponse({"error": str(exc)}, status_code=500)

        return _normalize_vllm_stream_result(
            result, op_name="streaming completion"
        )

    @_vllm_app.get("/v1/models", tags=["Models"],
                   summary="List available models",
                   response_model=None)
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
    gpu_fraction: Optional[float] = None,
    placement_group_bundles: Optional[List[Dict[str, float]]] = None,
    placement_group_strategy: Optional[str] = None,
    multi_node: bool = False,
    venv_path: Optional[str] = None,
) -> serve.Application:
    """
    Create a vLLM Ray Serve deployment with appropriate resource allocation.

    max_ongoing_requests controls how many concurrent HTTP connections (including
    long-lived streaming requests) each replica accepts.  vLLM's AsyncLLMEngine
    uses continuous batching so many requests can be in-flight simultaneously;
    this value should be at least as large as the engine's max_num_seqs.

    placement_group_bundles and placement_group_strategy are passed as top-level
    deployment options (not inside ray_actor_options, which Ray Serve blocks).
    When omitted, sensible defaults are auto-generated per scenario:
      - tensor_parallel_size > 1, multi_node=False (default)
          → [{GPU:tp, CPU:tp}] + STRICT_PACK
          (all TP shards forced onto one node; required for NVLink/PCIe)
      - tensor_parallel_size > 1, multi_node=True
          → [{GPU:1, CPU:1}] * tp + PACK
          (one bundle per shard, allows cross-node scheduling via NCCL)
      - gpu_fraction < 1          → [{GPU:gpu_fraction, CPU:1}]  +  PACK
        (bin-pack fractional replicas onto the same node's GPU pool)

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
        # Tensor parallelism splits one model across multiple whole GPUs.
        # gpu_fraction is incompatible here — each shard needs a full GPU.
        if gpu_fraction is not None:
            logger.warning(
                "gpu_fraction=%.2f is ignored when tensor_parallel_size=%d — "
                "each tensor-parallel shard requires one full GPU.",
                gpu_fraction, tensor_parallel_size,
            )
        if multi_node:
            # Cross-node TP: the deployment actor (bundle 0) is the scheduler
            # ONLY — no GPU.  vLLM's RayDistributedExecutor auto-discovers GPU
            # bundles by scanning placement_group.bundle_specs for non-zero GPU;
            # since bundle 0 has no GPU, it is skipped and all tp RayWorkerWrapper
            # actors land in bundles 1..tp (num_cpus=0, num_gpus=1 each).
            # This is a full scheduler↔executor separation.
            ray_actor_options = {"num_cpus": 4, "num_gpus": 0}
            logger.info(
                "Multi-node tensor-parallel deployment: scheduler-only actor in "
                "bundle 0, %d GPU worker(s) in bundles 1..%d",
                tensor_parallel_size, tensor_parallel_size,
            )
        else:
            # Single-node TP: actor holds all GPU/CPU resources in one bundle.
            # vLLM spawns internal Ray workers for TP shards on the same node.
            ray_actor_options = {
                "num_cpus": tensor_parallel_size,
                "num_gpus": tensor_parallel_size,
            }
            logger.info(
                "Single-node tensor-parallel deployment: %d GPU(s) per replica "
                "(vLLM spawns internal Ray workers for TP)",
                tensor_parallel_size,
            )
    elif gpu_fraction is not None:
        # Fractional GPU: multiple replicas share one physical GPU.
        ray_actor_options = {
            "num_cpus": 2,
            "num_gpus": gpu_fraction,
        }
        logger.info(
            "Fractional GPU allocation: %.2f GPU per replica  "
            "(combine with gpu_memory_utilization=%.2f in engine_config)",
            gpu_fraction, gpu_fraction,
        )
    else:
        ray_actor_options = {"num_cpus": 2, "num_gpus": 1}

    # ── Placement group defaults ────────────────────────────────────────────
    # placement_group_bundles / placement_group_strategy are top-level
    # deployment options (NOT inside ray_actor_options — Ray Serve blocks them
    # there).  Auto-generate sensible defaults when the caller omits them.
    # ── Node type resource hint ──────────────────────────────────────────────
    # Resolved here so it can be embedded in placement group bundles (multi-node)
    # or injected into ray_actor_options (single-node / non-TP).
    node_type = engine_config.get("node_type")

    if placement_group_bundles is None and not use_cpu:
        if tensor_parallel_size > 1 and multi_node:
            # Full scheduler↔executor separation:
            #   bundle 0 : {CPU:4}       ← VLLMEngine actor (scheduler only, no GPU)
            #   bundle 1..tp : {GPU:1}   ← one RayWorkerWrapper per TP shard
            #
            # vLLM auto-discovers GPU bundles by scanning bundle_specs for
            # non-zero GPU entries (skips bundle 0), so ranks 0..tp-1 land in
            # bundles 1..tp automatically — no VLLM_RAY_BUNDLE_INDICES needed.
            #
            # node_type hint goes into worker bundles so all shards land on
            # nodes of the correct hardware type.  Actor bundle omits it since
            # the scheduler has no GPU requirement and can run on the head node.
            node_hint = {f"node_type:{node_type}": 0.001} if node_type else {}
            engine_bundle: Dict[str, float] = {"CPU": 4.0}
            executor_bundle: Dict[str, float] = {"GPU": 1.0, **node_hint}
            placement_group_bundles = [engine_bundle] + [
                dict(executor_bundle) for _ in range(tensor_parallel_size)
            ]
            placement_group_strategy = placement_group_strategy or "PACK"
            logger.info(
                "Auto placement group (multi-node): PACK [engine_bundle{CPU:4}] + "
                "%d×[executor_bundle{GPU:1}%s] for TP=%d",
                tensor_parallel_size,
                f",node_type:{node_type}" if node_type else "",
                tensor_parallel_size,
            )
        elif tensor_parallel_size > 1:
            # Single-node TP: all shards forced onto one node via STRICT_PACK.
            # The actor holds all GPU/CPU resources in a single bundle.
            placement_group_bundles = [
                {"GPU": float(tensor_parallel_size), "CPU": float(tensor_parallel_size)}
            ]
            placement_group_strategy = placement_group_strategy or "STRICT_PACK"
            logger.info(
                "Auto placement group (single-node): STRICT_PACK bundle GPU=%d CPU=%d for TP=%d",
                tensor_parallel_size, tensor_parallel_size, tensor_parallel_size,
            )
        elif gpu_fraction is not None and gpu_fraction < 1.0:
            # Bin-pack fractional replicas onto the same node's GPU pool.
            # The actor needs num_cpus=2 (see ray_actor_options above), so
            # the bundle CPU must be at least 2 to satisfy Ray's constraint
            # that actor resources must be a subset of the first bundle.
            placement_group_bundles = [{"GPU": gpu_fraction, "CPU": 2.0}]
            placement_group_strategy = placement_group_strategy or "PACK"
            logger.info(
                "Auto placement group: PACK bundle for gpu_fraction=%.2f",
                gpu_fraction,
            )

    # ── Node type targeting ──────────────────────────────────────────────────
    # For multi-node TP the hint is already embedded in each placement group
    # bundle (see above).  For all other cases inject into ray_actor_options.
    if node_type and not (multi_node and tensor_parallel_size > 1):
        ray_actor_options.setdefault("resources", {})
        ray_actor_options["resources"][f"node_type:{node_type}"] = 0.001
        logger.info("Pinning deployment to node_type=%r via ray_actor_options", node_type)

    if venv_path:
        ray_actor_options["runtime_env"] = {"virtualenv": venv_path}
        logger.info("Using isolated venv: %s", venv_path)

    # ── Build .options() kwargs ─────────────────────────────────────────────
    opts: Dict[str, Any] = {
        "num_replicas": num_replicas,
        "ray_actor_options": ray_actor_options,
        "max_ongoing_requests": max_ongoing_requests,
    }
    if placement_group_bundles is not None:
        opts["placement_group_bundles"] = placement_group_bundles
        if placement_group_strategy:
            opts["placement_group_strategy"] = placement_group_strategy
        logger.info(
            "Placement group: strategy=%s  bundles=%s",
            placement_group_strategy or "PACK (default)",
            placement_group_bundles,
        )

    deployment = VLLMEngine.options(**opts)
    return deployment.bind(engine_config)
