"""
vLLM Engine for Ray Serve
Leverages vLLM's built-in OpenAI-compatible request handlers.

Import paths and constructor signatures differ across vLLM versions:

  v0.13.x  — flat layout under vllm/entrypoints/openai/
  v0.18.0  — subdirectory layout; OpenAIServingChat / OpenAIServingCompletion
              require an `openai_serving_render` argument (OpenAIServingRender),
              built from renderer_from_config() + get_io_processor().
  newest   — renderers/ refactor renames that argument to `online_renderer`
              (class OnlineRenderer), built from the live engine's model_config
              + renderer (no io_processor/model_registry). See
              _build_online_renderer / _build_renderer_for.

All layouts are handled via try/except on imports and signature inspection
(_build_renderer_for picks the correct kwarg name + renderer class).

References:
  vLLM OpenAI Server: https://docs.vllm.ai/en/stable/serving/openai_compatible_server.html
  Ray Placement Groups: https://docs.ray.io/en/latest/serve/llm/user-guides/cross-node-parallelism.html
"""
# PEP-563: all annotations are lazy strings so FastAPI route type-hints
# (CompletionRequest, ChatCompletionRequest) are never evaluated at
# class-definition time on the head node, where vllm is not installed.
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from ray import serve
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

# ---------------------------------------------------------------------------
# vllm imports — deferred to avoid ImportError on the head node (root venv).
# All symbols used at class-definition time (type hints in @app.post handlers)
# are protected by `from __future__ import annotations` above — they are
# stored as strings and never evaluated until the actor runs in .venv-vllm.
# ---------------------------------------------------------------------------
# Core engine imports — these must succeed in the actor venv (.venv-vllm).
# On the head node (no vLLM) they fall back to None stubs so the module stays
# importable for engine registration.
try:
    from vllm import AsyncLLMEngine
    from vllm.engine.arg_utils import AsyncEngineArgs
    _VLLM_AVAILABLE = True
    _VLLM_IMPORT_ERROR: Optional[BaseException] = None
except ImportError as exc:
    AsyncLLMEngine = None           # type: ignore[assignment,misc]
    AsyncEngineArgs = None          # type: ignore[assignment,misc]
    _VLLM_AVAILABLE = False
    # Preserve the real reason. On the head node this is expected (no vLLM);
    # inside the .venv-vllm actor it is the root cause of an otherwise cryptic
    # "'NoneType' object is not callable" at AsyncEngineArgs(**engine_config).
    _VLLM_IMPORT_ERROR = exc

# Serving-layer classes (OpenAIServing*/BaseModelPath) and the OpenAI request
# models (CompletionRequest/ChatCompletionRequest) are intentionally NOT bound
# at module level. They live only in the actor venv (.venv-vllm); on the head
# the names would be None, and — critically — the FastAPI routes below are built
# at import time on the head, then cloudpickled to the replica by
# @serve.ingress. If a route annotated its body with a head-None class, FastAPI
# would resolve it to None and mis-route the JSON body as a query param (422).
# So these are loaded lazily inside the actor via _load_vllm_serving() /
# _load_vllm_protocol(), and request bodies are validated at request time.

logger = logging.getLogger(__name__)


def _load_vllm_serving():
    """Deferred, version-adaptive import of the vLLM OpenAI serving-layer classes.

    Same head-side-None rationale as the core classes (see load_engine_symbols):
    these are used inside the actor (VLLMEngine.__init__ and _build_serving_render),
    but the module-level globals are None on the vLLM-less head and that None rides
    the deployment pickle into the replica. The import paths differ across vLLM
    layouts, so try the 0.18+ subdirectory layout first, then the 0.13.x flat
    layout. See docs/ISOLATED_ENV_DESIGN.md.

    Returns (OpenAIServingCompletion, OpenAIServingChat, OpenAIServingModels,
    BaseModelPath).
    """
    try:
        try:  # vLLM 0.14+/0.18+ — subdirectory layout
            from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
            from vllm.entrypoints.openai.completion.serving import OpenAIServingCompletion
            from vllm.entrypoints.openai.models.protocol import BaseModelPath
            from vllm.entrypoints.openai.models.serving import OpenAIServingModels
        except ImportError:  # vLLM 0.13.x — flat layout
            from vllm.entrypoints.openai.serving_chat import OpenAIServingChat
            from vllm.entrypoints.openai.serving_completion import OpenAIServingCompletion
            from vllm.entrypoints.openai.serving_models import (
                BaseModelPath,
                OpenAIServingModels,
            )
    except ImportError as exc:
        raise RuntimeError(
            "vLLM OpenAI serving-layer classes failed to import inside this "
            f"actor's venv (.venv-vllm): {exc!r}"
        ) from exc
    return OpenAIServingCompletion, OpenAIServingChat, OpenAIServingModels, BaseModelPath


def _load_vllm_protocol():
    """Deferred, version-adaptive import of the OpenAI request models.

    Same head-side-None rationale as _load_vllm_serving: CompletionRequest /
    ChatCompletionRequest are None on the vLLM-less head. The FastAPI route
    handlers must NOT annotate their body with these globals — the routes are
    built at module import time on the head (where the names resolve to None),
    then @serve.ingress cloudpickles that broken app to the replica. Instead we
    resolve the real classes here, inside the actor, and validate the JSON body
    at request time. Returns (CompletionRequest, ChatCompletionRequest).
    """
    try:
        try:  # vLLM 0.14+/0.18+ — subdirectory layout
            from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
            from vllm.entrypoints.openai.completion.protocol import CompletionRequest
        except ImportError:  # vLLM 0.13.x — flat layout
            from vllm.entrypoints.openai.protocol import (
                ChatCompletionRequest,
                CompletionRequest,
            )
    except ImportError as exc:
        raise RuntimeError(
            "vLLM OpenAI protocol models failed to import inside this actor's "
            f"venv (.venv-vllm): {exc!r}"
        ) from exc
    return CompletionRequest, ChatCompletionRequest


def _validate_request(cls, payload: dict):
    """Build a vLLM request model from a raw JSON payload (pydantic v2/v1)."""
    if hasattr(cls, "model_validate"):
        return cls.model_validate(payload)
    return cls(**payload)  # pydantic v1 fallback


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


def _extract_serving_kwargs(engine_config: Dict[str, Any]) -> Dict[str, Any]:
    """Pop vLLM FrontendArgs (tool calling / reasoning) out of *engine_config*
    and map them to the serving-layer constructor kwargs, exactly as vLLM's own
    ``api_server.init_app_state`` does:

        enable_auto_tool_choice -> enable_auto_tools
        tool_call_parser        -> tool_parser
        reasoning_parser        -> reasoning_parser  (+ enable_reasoning for <0.9)

    These are FrontendArgs, NOT AsyncEngineArgs, so they must be removed before
    ``AsyncEngineArgs(**engine_config)`` or the engine rejects them. In vLLM
    0.26 ``OpenAIServingChat`` builds its own output parser from these
    (``self.parser_cls = ParserManager.get_parser(tool_parser, reasoning_parser,
    enable_auto_tools)``) and gates ``tool_calls`` on
    ``self.enable_auto_tools`` + ``parser_cls.tool_parser_cls`` — so they must be
    passed to the chat serving class, not only the renderer.

    Mutates *engine_config* (pops the keys) and returns the serving kwargs.
    """
    serving_kwargs: Dict[str, Any] = {}
    if engine_config.pop("enable_auto_tool_choice", False):
        serving_kwargs["enable_auto_tools"] = True
    if (tp := engine_config.pop("tool_call_parser", None)):
        serving_kwargs["tool_parser"] = tp
    if (rp := engine_config.pop("reasoning_parser", None)):
        serving_kwargs["reasoning_parser"] = rp
        # Older vLLM (0.6-0.8) gated reasoning behind a separate boolean; newer
        # versions infer it from reasoning_parser. Set both — the unsupported
        # one is filtered out by _accepted_kwargs at the call site.
        serving_kwargs["enable_reasoning"] = True
    return serving_kwargs


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
                          chat_template: Optional[str] = None,
                          tool_kwargs: Optional[Dict[str, Any]] = None):
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

    # Runtime-bind BaseModelPath (module global is None on the head).
    _, _, _, BaseModelPath = _load_vllm_serving()

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

    kw: Dict[str, Any] = {
        "model_config":                 model_config,
        "renderer":                     renderer,
        "io_processor":                 io_processor,
        "model_registry":               model_registry,
        "request_logger":               None,
        "chat_template":                chat_template,
        "chat_template_content_format": content_format,
    }
    if tool_kwargs:
        # Defensive only: tool/reasoning parsers actually run in
        # OpenAIServingChat, not the renderer. _accepted_kwargs drops these
        # here (the renderer doesn't declare them) — they are applied on
        # OpenAIServingChat in VLLMEngine.__init__.
        kw.update(tool_kwargs)
    return OpenAIServingRender(**_accepted_kwargs(OpenAIServingRender, kw))


def _build_online_renderer(engine, engine_args: AsyncEngineArgs, model_name: str,
                           chat_template: Optional[str] = None,
                           tool_kwargs: Optional[Dict[str, Any]] = None):
    """
    Build OnlineRenderer for the newest vLLM layout (the ``online_renderer`` kwarg).

    OnlineRenderer replaced OpenAIServingRender in the renderers/ refactor. It
    wraps the live engine's ``model_config`` + ``renderer`` (mirroring vLLM's own
    ``init_app_state``), so no io_processor / model_registry plumbing is needed.
    When the engine client doesn't expose ``.renderer`` (e.g. a CPU render build)
    we fall back to ``renderer_from_config(vllm_config)``, matching vLLM's
    ``init_render_app_state``.
    """
    from vllm.renderers.online_renderer import OnlineRenderer

    model_config = getattr(engine, "model_config", None)
    renderer = getattr(engine, "renderer", None)
    if model_config is None or renderer is None:
        from vllm.renderers import renderer_from_config
        vllm_config = engine_args.create_engine_config()
        if model_config is None:
            model_config = vllm_config.model_config
        if renderer is None:
            renderer = renderer_from_config(vllm_config)

    # chat_template_content_format: accept the enum or fall back to "auto".
    try:
        from vllm.entrypoints.chat_utils import ChatTemplateContentFormatOption
        content_format = ChatTemplateContentFormatOption("auto")
    except Exception:
        content_format = "auto"  # type: ignore[assignment]

    kw: Dict[str, Any] = {
        "model_config":                 model_config,
        "renderer":                     renderer,
        "request_logger":               None,
        "chat_template":                chat_template,
        "chat_template_content_format": content_format,
    }
    if tool_kwargs:
        # Defensive only: tool/reasoning parsers actually run in
        # OpenAIServingChat, not the renderer. _accepted_kwargs drops these
        # here (OnlineRenderer doesn't declare them) — they are applied on
        # OpenAIServingChat in VLLMEngine.__init__.
        kw.update(tool_kwargs)
    return OnlineRenderer(**_accepted_kwargs(OnlineRenderer, kw))


def _build_renderer_for(cls, *, engine, engine_args: AsyncEngineArgs,
                        model_name: str, chat_template: Optional[str] = None,
                        tool_kwargs: Optional[Dict[str, Any]] = None):
    """
    Return ``(kwarg_name, renderer_obj)`` for the renderer argument that
    ``cls.__init__`` expects, or ``(None, None)`` when it takes no renderer.

    vLLM renamed this argument across versions (check newest name first):
      - newest       → ``online_renderer``      (OnlineRenderer)
      - v0.18.0      → ``openai_serving_render`` (OpenAIServingRender)
      - v0.13.x flat → none
    """
    if _requires_param(cls, "online_renderer"):
        return "online_renderer", _build_online_renderer(
            engine, engine_args, model_name, chat_template, tool_kwargs=tool_kwargs,
        )
    if _requires_param(cls, "openai_serving_render"):
        return "openai_serving_render", _build_serving_render(
            engine_args=engine_args, model_name=model_name, chat_template=chat_template,
            tool_kwargs=tool_kwargs,
        )
    return None, None


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


class _ApiKeyMiddleware:
    """Enforce a static bearer API key on model endpoints when configured.

    The expected key is read from ``VLLM_API_KEY`` in the replica environment at
    request time (deliver it per-deployment via ``scheduling.env_vars`` or set
    it on the replica actor). Behaviour:

    - **Fail-open**: if ``VLLM_API_KEY`` is unset/empty, all requests are allowed
      — preserves the prior open behaviour so existing deployments don't break.
    - ``/health`` is always exempt so Serve / K8s liveness probes need no key.
    - Otherwise the request must carry ``Authorization: Bearer <VLLM_API_KEY>``.

    Plain ASGI class (not a FastAPI/Starlette BaseHTTPMiddleware) so the module
    app remains cloudpickle-safe for @serve.ingress — same constraint as
    _RoutePathMiddleware above.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            expected = os.environ.get("VLLM_API_KEY", "").strip()
            path = scope.get("path", "") or ""
            # endswith so the check is agnostic to any root_path prefix.
            if expected and not path.endswith("/health"):
                headers = dict(scope.get("headers") or [])
                auth = headers.get(b"authorization", b"").decode("latin-1")
                token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
                if token != expected:
                    await self._reject(send)
                    return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = (
            b'{"error":{"message":"Invalid or missing API key",'
            b'"type":"authentication_error","code":"invalid_api_key"}}'
        )
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


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
# API-key gate (outermost): rejects unauthenticated calls before routing when
# VLLM_API_KEY is set on the replica; no-op otherwise.
_vllm_app.add_middleware(_ApiKeyMiddleware)

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

        # Bind vLLM's core classes at runtime, inside the actor. The module-level
        # globals cannot be used: this module is imported on the vLLM-less head
        # for registration, so they fall back to None and that None reaches the
        # replica via the deployment pickle. See load_engine_symbols().
        from .engine_utils import load_engine_symbols

        AsyncLLMEngine, AsyncEngineArgs = load_engine_symbols(
            "vLLM (.venv-vllm)",
            [("vllm", "AsyncLLMEngine"), ("vllm.engine.arg_utils", "AsyncEngineArgs")],
        )

        try:
            import os
            import sys

            # FlashInfer JIT-compiles CUDA kernels at first use by shelling out to
            # `ninja` (a console script installed into this venv's bin/). Ray's
            # py_executable swaps the interpreter but NOT PATH, so the venv bin is
            # not searched and the EngineCore subprocess dies with
            # `FileNotFoundError: 'ninja'`. Prepend the venv bin so ninja (and any
            # other venv console tool) resolves. This must happen here — before
            # the engine core subprocess starts — and cannot be done via the
            # deploy payload (PATH is denylisted in SchedulingConfig.env_vars).
            _venv_bin = os.path.dirname(sys.executable)
            if _venv_bin and _venv_bin not in os.environ.get("PATH", "").split(os.pathsep):
                os.environ["PATH"] = _venv_bin + os.pathsep + os.environ.get("PATH", "")
                logger.info("Prepended %s to PATH (FlashInfer JIT needs ninja)", _venv_bin)

            # attention_backend must be set as an env var before the engine
            # starts — vLLM's EngineCore subprocess inherits it from us.
            attention_backend = engine_config.pop("attention_backend", None)
            if attention_backend:
                os.environ["VLLM_ATTENTION_BACKEND"] = attention_backend
                logger.info("Set VLLM_ATTENTION_BACKEND=%s", attention_backend)

            # Serving-layer flags (tool calling / reasoning). See
            # _extract_serving_kwargs for the FrontendArgs->serving mapping and
            # why these must be popped before AsyncEngineArgs. They are forwarded
            # to BOTH the renderer (input validation) and OpenAIServingChat
            # (output extraction) below; _accepted_kwargs drops any the installed
            # vLLM doesn't declare.
            serving_kwargs: Dict[str, Any] = _extract_serving_kwargs(engine_config)

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

            # Bind serving-layer classes at runtime (module globals are None on
            # the head; see _load_vllm_serving). Locals shadow the globals below.
            (
                OpenAIServingCompletion,
                OpenAIServingChat,
                OpenAIServingModels,
                BaseModelPath,
            ) = _load_vllm_serving()

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
            # The renderer argument was introduced in v0.18.0 as
            # `openai_serving_render` (OpenAIServingRender) and later renamed to
            # `online_renderer` (OnlineRenderer) in the renderers/ refactor.
            # Detect which name the constructor wants, build the matching
            # renderer, and pass it under that name. When neither is present
            # we're on the v0.13.x flat layout (no renderer arg at all).
            render_kw, renderer_obj = _build_renderer_for(
                OpenAIServingCompletion,
                engine=self.engine,
                engine_args=self.engine_args,
                model_name=self.model_name,
                chat_template=engine_config.get("chat_template"),
                tool_kwargs=serving_kwargs,
            )

            try:
                from vllm.entrypoints.chat_utils import ChatTemplateContentFormatOption
                content_format = ChatTemplateContentFormatOption("auto")
            except Exception:
                content_format = "auto"  # type: ignore[assignment]

            if render_kw:
                logger.info("Detected vLLM renderer layout — passing '%s'", render_kw)
                self.openai_serving_completion = OpenAIServingCompletion(
                    **_accepted_kwargs(OpenAIServingCompletion, {
                        "engine_client":  self.engine,
                        "models":         self.openai_serving_models,
                        render_kw:        renderer_obj,
                        "request_logger": None,
                    })
                )
                self.openai_serving_chat = OpenAIServingChat(
                    **_accepted_kwargs(OpenAIServingChat, {
                        "engine_client":                self.engine,
                        "models":                       self.openai_serving_models,
                        "response_role":                "assistant",
                        render_kw:                      renderer_obj,
                        "request_logger":               None,
                        "chat_template":                engine_config.get("chat_template"),
                        "chat_template_content_format": content_format,
                        # REQUIRED for output tool-call/reasoning extraction. In
                        # vLLM 0.26 OpenAIServingChat builds its OWN parser:
                        # self.parser_cls = ParserManager.get_parser(tool_parser,
                        # reasoning_parser, enable_auto_tools) and gates tool_calls
                        # on self.enable_auto_tools + parser_cls.tool_parser_cls
                        # (chat_completion/serving.py:152-159, 867-945). The
                        # renderer only validates INPUT (raises the 400); without
                        # these on the chat class, tool calls leak into `content`
                        # with tool_calls=null. So they must be set on BOTH the
                        # renderer (above, via tool_kwargs) and here.
                        **serving_kwargs,
                    })
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
                        # Flat layout has no renderer — tool/reasoning args live
                        # on the chat serving class here.
                        **serving_kwargs,
                    })
                )

            if serving_kwargs:
                logger.info("vLLM serving flags: %s", sorted(serving_kwargs))

            # Bind the OpenAI request models for in-handler body validation.
            # The FastAPI routes can't annotate these (None on the head; see
            # _load_vllm_protocol), so we validate the JSON body at request time.
            (
                self._completion_request_cls,
                self._chat_request_cls,
            ) = _load_vllm_protocol()

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

    # These POST handlers take the Starlette Request and parse the body manually
    # (the vLLM body models are None on the head — see _load_vllm_protocol).
    #
    # CRITICAL: annotations are assigned as REAL class objects, not written
    # inline as `request: Request`. Under `from __future__ import annotations`
    # inline hints are stringized; @serve.ingress then cloudpickles this app to
    # the replica, and cloudpickle rebuilds each endpoint's __globals__ from the
    # names used in the *code body* only — dropping annotation-only names like
    # Request/Response. On the replica get_type_hints("Request") NameErrors,
    # FastAPI falls back to treating `request` as a query param (→ 422 on every
    # call) and OpenAPI schema-gen 500s. A concrete class object needs no
    # get_type_hints resolution and pickles by reference, so it survives intact.
    async def chat_completion(self, request):
        try:
            payload = await request.json()
            body = _validate_request(self._chat_request_cls, payload)
        except Exception as exc:
            logger.warning("Invalid chat completion request: %s", exc)
            return JSONResponse({"error": f"invalid request: {exc}"}, status_code=422)
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

    chat_completion.__annotations__ = {"request": Request, "return": Response}
    chat_completion = _vllm_app.post(
        "/v1/chat/completions", tags=["Chat"],
        summary="Chat completion (OpenAI-compatible)", response_model=None,
    )(chat_completion)

    async def completion(self, request):
        try:
            payload = await request.json()
            body = _validate_request(self._completion_request_cls, payload)
        except Exception as exc:
            logger.warning("Invalid completion request: %s", exc)
            return JSONResponse({"error": f"invalid request: {exc}"}, status_code=422)
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

    completion.__annotations__ = {"request": Request, "return": Response}
    completion = _vllm_app.post(
        "/v1/completions", tags=["Completions"],
        summary="Text completion (OpenAI-compatible)", response_model=None,
    )(completion)

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
    scheduling_resources: Optional[Dict[str, float]] = None,
    scheduling_env_vars: Optional[Dict[str, str]] = None,
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

    # ── Node affinity resolution ─────────────────────────────────────────────
    # scheduling_resources (explicit SchedulingConfig) takes full precedence
    # over the legacy node_type shorthand.  The resolved affinity is applied to
    # GPU-bearing placement group bundles when a placement group is in play, or
    # to the actor directly when it is not — a placement group would otherwise
    # reject an actor whose resource request isn't a subset of its assigned
    # bundle (Ray allocates actor resources FROM the bundle).
    node_type = engine_config.get("node_type")
    if scheduling_resources:
        _affinity: Dict[str, float] = dict(scheduling_resources)
    elif node_type:
        _affinity = {f"node_type:{node_type}": 0.001}
    else:
        _affinity = {}

    # ── Placement group defaults ────────────────────────────────────────────
    # placement_group_bundles / placement_group_strategy are top-level
    # deployment options (NOT inside ray_actor_options — Ray Serve blocks them
    # there).  Auto-generate sensible defaults when the caller omits them.
    if placement_group_bundles is None and not use_cpu:
        if tensor_parallel_size > 1 and multi_node:
            # Full scheduler↔executor separation:
            #   bundle 0 : {CPU:4}          ← VLLMEngine actor (scheduler, no GPU)
            #   bundle 1..tp : {GPU:1, ...} ← one RayWorkerWrapper per TP shard
            #
            # vLLM auto-discovers GPU bundles by scanning bundle_specs for
            # non-zero GPU entries (skips bundle 0), so ranks 0..tp-1 land in
            # bundles 1..tp automatically — no VLLM_RAY_BUNDLE_INDICES needed.
            #
            # Affinity goes into the executor bundles so all shards land on the
            # right nodes.  Bundle 0 stays label-free so the scheduler can run
            # anywhere (e.g. the head node), which is required for cross-node TP.
            engine_bundle: Dict[str, float] = {"CPU": 4.0}
            executor_bundle: Dict[str, float] = {"GPU": 1.0, **_affinity}
            placement_group_bundles = [engine_bundle] + [
                dict(executor_bundle) for _ in range(tensor_parallel_size)
            ]
            placement_group_strategy = placement_group_strategy or "PACK"
            logger.info(
                "Auto placement group (multi-node): PACK [engine{CPU:4}] + "
                "%d×[executor{GPU:1%s}] for TP=%d",
                tensor_parallel_size,
                "".join(f",{k}" for k in _affinity),
                tensor_parallel_size,
            )
        elif tensor_parallel_size > 1:
            # Single-node TP: all shards forced onto one node via STRICT_PACK.
            # Affinity lives in the bundle (not the actor) so the actor request
            # stays a subset of the bundle it is captured into.
            placement_group_bundles = [
                {"GPU": float(tensor_parallel_size),
                 "CPU": float(tensor_parallel_size), **_affinity}
            ]
            placement_group_strategy = placement_group_strategy or "STRICT_PACK"
            logger.info(
                "Auto placement group (single-node): STRICT_PACK bundle "
                "GPU=%d CPU=%d%s for TP=%d",
                tensor_parallel_size, tensor_parallel_size,
                "".join(f" +{k}" for k in _affinity), tensor_parallel_size,
            )
        elif gpu_fraction is not None and gpu_fraction < 1.0:
            # Bin-pack fractional replicas onto the same node's GPU pool.
            # The actor needs num_cpus=2 (see ray_actor_options above), so
            # the bundle CPU must be at least 2 to satisfy Ray's constraint
            # that actor resources must be a subset of the first bundle.
            placement_group_bundles = [{"GPU": gpu_fraction, "CPU": 2.0, **_affinity}]
            placement_group_strategy = placement_group_strategy or "PACK"
            logger.info(
                "Auto placement group: PACK bundle for gpu_fraction=%.2f%s",
                gpu_fraction, "".join(f" +{k}" for k in _affinity),
            )
    elif placement_group_bundles is not None and _affinity:
        # Caller supplied bundles explicitly.  Merge affinity into GPU-bearing
        # bundles so scheduling.resources isn't silently dropped, while leaving
        # CPU-only (scheduler) bundles untouched.  If no bundle has a GPU, apply
        # to all of them as a fallback.
        _gpu_bundles = [b for b in placement_group_bundles if b.get("GPU", 0)]
        for _b in (_gpu_bundles or placement_group_bundles):
            _b.update(_affinity)
        logger.info("Merged scheduling resources into explicit bundles: %s", _affinity)

    # ── Actor-level affinity (only when there is NO placement group) ──────────
    # With a placement group active the affinity is carried by the GPU bundles
    # above; injecting it onto the actor too would require the actor's assigned
    # bundle to satisfy it and can make the actor unschedulable.
    if _affinity and placement_group_bundles is None:
        ray_actor_options.setdefault("resources", {})
        ray_actor_options["resources"].update(_affinity)
        logger.info("Pinning deployment via ray_actor_options resources: %s", _affinity)

    # ── Runtime env: venv + scheduling env_vars ──────────────────────────────
    rt_env: Dict[str, Any] = {}
    if venv_path:
        rt_env["py_executable"] = f"{venv_path}/bin/python"
        logger.info("Using isolated venv: %s", venv_path)
    if scheduling_env_vars:
        rt_env["env_vars"] = scheduling_env_vars
        logger.info("Scheduling env_vars applied: %s", list(scheduling_env_vars.keys()))
    if rt_env:
        ray_actor_options["runtime_env"] = rt_env

    # ── Build .options() kwargs ─────────────────────────────────────────────
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
        logger.info(
            "Placement group: strategy=%s  bundles=%s",
            placement_group_strategy or "PACK (default)",
            placement_group_bundles,
        )

    deployment = VLLMEngine.options(**opts)
    return deployment.bind(engine_config)
