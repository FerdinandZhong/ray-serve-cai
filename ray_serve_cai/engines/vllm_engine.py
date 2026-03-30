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

import logging
from typing import Any, Dict, Optional

from ray import serve
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

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
# Deployment
# ---------------------------------------------------------------------------

@serve.deployment(
    name="vllm-deployment",
    num_replicas=1,
    ray_actor_options={},
)
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
    # Endpoint handlers
    # ------------------------------------------------------------------

    async def completion(self, request: Request) -> JSONResponse:
        """POST /v1/completions"""
        try:
            request_dict = await request.json()
            completion_request = CompletionRequest(**request_dict)
            return await self.openai_serving_completion.create_completion(
                completion_request, raw_request=request
            )
        except Exception as exc:
            logger.error("Error in completion: %s", exc)
            import traceback; logger.error(traceback.format_exc())
            return JSONResponse({"error": str(exc)}, status_code=500)

    async def chat_completion(self, request: Request) -> JSONResponse:
        """POST /v1/chat/completions"""
        try:
            request_dict = await request.json()
            chat_request = ChatCompletionRequest(**request_dict)
            return await self.openai_serving_chat.create_chat_completion(
                chat_request, raw_request=request
            )
        except Exception as exc:
            logger.error("Error in chat completion: %s", exc)
            import traceback; logger.error(traceback.format_exc())
            return JSONResponse({"error": str(exc)}, status_code=500)

    async def list_models(self, request: Request) -> JSONResponse:
        """GET /v1/models"""
        models = await self.openai_serving_models.show_available_models()
        return JSONResponse(content=models.model_dump())

    async def health_check(self, request: Request) -> JSONResponse:
        """GET /health"""
        return JSONResponse({
            "status": "healthy",
            "model": self.model_name,
            "engine": "vllm",
            "tensor_parallel_size": self.tensor_parallel_size,
        })

    # ------------------------------------------------------------------
    # Router
    # ------------------------------------------------------------------

    async def __call__(self, request: Request):
        path = request.url.path
        method = request.method

        if path == "/v1/completions" and method == "POST":
            return await self.completion(request)
        if path == "/v1/chat/completions" and method == "POST":
            return await self.chat_completion(request)
        if path == "/v1/models" and method == "GET":
            return await self.list_models(request)
        if path == "/health" and method == "GET":
            return await self.health_check(request)

        return JSONResponse(
            {"error": f"Unknown endpoint: {method} {path}"},
            status_code=404,
        )


# ---------------------------------------------------------------------------
# Deployment factory
# ---------------------------------------------------------------------------

def create_vllm_deployment(
    engine_config: Dict[str, Any],
    num_replicas: int = 1,
    tensor_parallel_size: int = 1,
    use_cpu: bool = False,
) -> serve.Application:
    """
    Create a vLLM Ray Serve deployment with appropriate resource allocation.

    References:
      Placement groups: https://docs.ray.io/en/latest/serve/llm/user-guides/cross-node-parallelism.html
      vLLM distributed: https://docs.vllm.ai/en/stable/serving/distributed_serving.html
    """
    logger.info("Creating vLLM deployment  replicas=%d  tp=%d  cpu=%s",
                num_replicas, tensor_parallel_size, use_cpu)

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
    )
    return deployment.bind(engine_config)
