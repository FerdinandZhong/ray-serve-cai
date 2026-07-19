"""
Shared utilities for building Ray Serve engine apps.

Provides three helpers that every engine can use:
  create_engine_app  — FastAPI factory that strips root_path from scope["path"]
  mount_health       — register GET /health with standard response
  mount_metrics      — mount prometheus_client ASGI app at /metrics
"""

from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI
from starlette.types import Receive, Scope, Send


def load_engine_symbols(engine_label: str, specs: List[Tuple[str, str]]) -> list:
    """Import ``(module_path, attr)`` pairs at call-time — inside the actor.

    Engine modules are imported on the head node for registration, where the
    heavy engine library (vllm, etc.) is intentionally absent. Any symbol
    resolved at module top-level there falls back to ``None``, and once the
    deployment is pickled that ``None`` reaches the replica — even though the
    actor's own venv (``.venv-<engine>``) has the real object. Calling this from
    ``__init__`` binds the symbols from THIS process's environment at runtime,
    sidestepping the pickled ``None``. See docs/ISOLATED_ENV_DESIGN.md.

    Args:
        engine_label: Human-readable label for the error message (e.g.
            ``"vLLM (.venv-vllm)"``).
        specs: ``(module_path, attribute_name)`` pairs to resolve, in order.

    Returns:
        The resolved attributes, in the same order as ``specs``.

    Raises:
        RuntimeError: if any import or attribute lookup fails, with the original
            error chained — surfaces the real cause instead of a downstream
            ``'NoneType' object is not callable``.
    """
    import importlib

    resolved: list = []
    try:
        for module_path, attr in specs:
            resolved.append(getattr(importlib.import_module(module_path), attr))
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            f"{engine_label} failed to import inside this actor's venv: {exc!r}"
        ) from exc
    return resolved


def create_engine_app(title: str, **fastapi_kwargs: Any) -> FastAPI:
    """
    Return a FastAPI instance whose ASGI call strips the root_path prefix.

    Ray Serve sets scope["root_path"] to the deployment's route prefix,
    which causes FastAPI's router to never match any route.  This wrapper
    removes the prefix from scope["path"] before dispatching so that routes
    like /health and /metrics resolve correctly regardless of mount prefix.
    """
    app = FastAPI(title=title, **fastapi_kwargs)
    _orig_call = app.__call__

    async def _call(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            root = scope.get("root_path", "")
            if root and scope["path"].startswith(root):
                scope = dict(scope)
                scope["path"] = scope["path"][len(root):] or "/"
        await _orig_call(scope, receive, send)

    app.__call__ = _call  # type: ignore[method-assign]
    return app


def mount_health(
    app: FastAPI,
    engine_type: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Register GET /health → {"status": "healthy", "engine": engine_type, ...extra}."""

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"status": "healthy", "engine": engine_type, **(extra or {})}


def mount_metrics(app: FastAPI, registry: Any = None) -> None:
    """Mount prometheus_client make_asgi_app at /metrics."""
    from prometheus_client import REGISTRY, make_asgi_app

    metrics_app = make_asgi_app(registry=registry or REGISTRY)
    app.mount("/metrics", metrics_app)
