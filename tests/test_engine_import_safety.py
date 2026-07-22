"""Regression guard: the engines package must be import-safe on the head node.

The head/controller runs a base venv that intentionally lacks the heavy engine
libraries (vllm, sglang, litellm, mcp) — those live in per-engine venvs
(.venv-<engine>) used only by the replica actors. Engine *registration* must
still succeed head-side, and engine modules must not resolve heavy symbols at
module top-level (or the resulting None gets pickled into the replica; see
docs/ISOLATED_ENV_DESIGN.md and load_engine_symbols()).

These tests simulate the head by blocking the heavy libs, then assert the
engines package still imports and registers. They require no Ray cluster.

Run with pytest (override the repo's --cov addopt if pytest-cov is absent):
    python -m pytest tests/test_engine_import_safety.py -o addopts=""
or standalone:
    python tests/test_engine_import_safety.py
"""
from __future__ import annotations

import contextlib
import os
import sys

import pytest

# Absent on the head node (installed only in their own .venv-<engine>).
# ultralytics/torch are NOT blocked: the base venv installs YOLO deps, so the
# head genuinely has them — blocking them would not simulate the real head.
_HEAD_ABSENT = ("vllm", "sglang", "litellm", "mcp")

# Engines that MUST register even with their lib absent (they have explicit
# stub-class fallbacks in engines/__init__.py). yolo/mcp legitimately skip when
# their lib is missing, so they are not asserted.
_STUB_FALLBACK_ENGINES = ("vllm", "sglang", "litellm")


class _BlockFinder:
    """meta_path finder that makes `import <blocked>` raise ModuleNotFoundError."""

    def __init__(self, blocked: tuple[str, ...]) -> None:
        self._blocked = tuple(blocked)

    def find_spec(self, name, path=None, target=None):  # noqa: ANN001
        if name.split(".")[0] in self._blocked:
            raise ModuleNotFoundError(f"blocked for head-safety test: {name}")
        return None  # defer to the normal finders


def _purge(blocked: tuple[str, ...]) -> dict:
    """Drop cached blocked libs + our engines package; return what was removed."""
    removed = {
        k: v
        for k, v in sys.modules.items()
        if k.split(".")[0] in blocked or k.startswith("ray_serve_cai.engines")
    }
    for k in removed:
        del sys.modules[k]
    return removed


@contextlib.contextmanager
def heavy_libs_blocked(blocked: tuple[str, ...] = _HEAD_ABSENT):
    saved = _purge(blocked)
    finder = _BlockFinder(blocked)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)
        # Re-purge anything imported under the block, then restore originals so
        # other tests see a clean, real module table.
        _purge(blocked)
        sys.modules.update(saved)


def test_engines_package_imports_without_heavy_libs():
    """Registration survives with all per-engine libs absent (the head case)."""
    with heavy_libs_blocked():
        import ray_serve_cai.engines as engines  # must NOT raise

        names = list(engines.get_registry().list_engines())

    for expected in _STUB_FALLBACK_ENGINES:
        assert expected in names, (
            f"engine {expected!r} failed to register head-side "
            f"(heavy libs blocked); registered={names}"
        )


def test_load_engine_symbols_success():
    from ray_serve_cai.engines.engine_utils import load_engine_symbols

    (getcwd,) = load_engine_symbols("test", [("os", "getcwd")])
    assert getcwd is os.getcwd


def test_load_engine_symbols_missing_raises():
    from ray_serve_cai.engines.engine_utils import load_engine_symbols

    with pytest.raises(RuntimeError):
        load_engine_symbols("test-engine", [("definitely_absent_pkg_xyz", "Thing")])


def _build_ingress_like_app():
    """A FastAPI app shaped like management/app.py: router + CORS + lifespan.

    fastapi>=0.137 retains a router graph holding a threading.Lock, so
    cloudpickling this app is exactly what fails inside @serve.ingress.
    """
    from contextlib import asynccontextmanager

    from fastapi import APIRouter, FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    @asynccontextmanager
    async def _lifespan(app):  # noqa: ANN001
        yield

    app = FastAPI(title="ingress-pickle-test", lifespan=_lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"])
    router = APIRouter()

    @router.get("/ping")
    async def _ping():
        return {"ok": True}

    app.include_router(router)
    return app


def test_fastapi_app_cloudpickles_with_lock_reducer():
    """Guard against the serve.ingress 'cannot pickle _thread.lock' regression.

    install_lock_pickle_reducer() (called at ray_serve_cai import) must make a
    management-like FastAPI app cloudpickle-able, which is what @serve.ingress
    requires. If a future fastapi/Ray bump reintroduces an unpicklable object,
    this fails in CI instead of at deploy time.
    """
    cloudpickle = pytest.importorskip("ray.cloudpickle")

    from ray_serve_cai._serialization import install_lock_pickle_reducer

    install_lock_pickle_reducer()  # idempotent; import already ran it
    app = _build_ingress_like_app()

    reloaded = cloudpickle.loads(cloudpickle.dumps(app))
    assert reloaded.title == "ingress-pickle-test"


def test_build_renderer_for_picks_kwarg_by_signature(monkeypatch):
    """Guard the vLLM renderer-kwarg dispatch across versions.

    vLLM renamed the serving-layer renderer argument `openai_serving_render`
    (v0.18.0) → `online_renderer` (renderers/ refactor). _build_renderer_for
    must select the name the constructor actually declares; the original bug
    only knew `openai_serving_render`, so the newer constructor fell through to
    the no-renderer (v0.13.x) path and died with
    'missing 1 required keyword-only argument: online_renderer'.

    The two builders import vllm, so we stub them and assert only the dispatch.
    """
    with heavy_libs_blocked():
        from ray_serve_cai.engines import vllm_engine as ve

    monkeypatch.setattr(ve, "_build_online_renderer",
                        lambda *a, **k: "ONLINE", raising=True)
    monkeypatch.setattr(ve, "_build_serving_render",
                        lambda *a, **k: "SERVING", raising=True)

    class Newest:  # renderers/ refactor
        def __init__(self, engine_client, models, *, online_renderer,
                     request_logger):  # noqa: ANN001
            ...

    class V018:  # subdirectory layout
        def __init__(self, engine_client, models, *, openai_serving_render,
                     request_logger):  # noqa: ANN001
            ...

    class Flat:  # v0.13.x flat layout — no renderer arg
        def __init__(self, engine_client, models, request_logger=None):  # noqa: ANN001
            ...

    common = dict(engine=object(), engine_args=object(), model_name="m")

    assert ve._build_renderer_for(Newest, **common) == ("online_renderer", "ONLINE")
    assert ve._build_renderer_for(V018, **common) == ("openai_serving_render", "SERVING")
    assert ve._build_renderer_for(Flat, **common) == (None, None)


def test_vllm_post_routes_take_no_body_annotation():
    """Guard the serve.ingress body-annotation regression.

    The vLLM FastAPI routes are built at import time on the head (where vllm is
    absent) and cloudpickled to the replica by @serve.ingress. If a POST handler
    annotates its body with a vLLM request model, that name resolves to None on
    the head and FastAPI mis-routes the JSON body as a query param → 422 for
    every inference call. So /v1/chat/completions and /v1/completions must take
    only (self, request) and validate the body at request time.
    """
    import inspect

    with heavy_libs_blocked():
        from ray_serve_cai.engines import vllm_engine as ve

        by_path = {}
        for r in ve._vllm_app.routes:
            for p in getattr(r, "path", "") and [r.path] or []:
                by_path[p] = r

        for path in ("/v1/chat/completions", "/v1/completions"):
            route = by_path.get(path)
            assert route is not None, f"route {path} not registered"
            params = set(inspect.signature(route.endpoint).parameters) - {"self"}
            assert params == {"request"}, (
                f"{path} handler must take only (self, request); got {params}. "
                "A body-typed param reintroduces the head-None 422 bug."
            )
            # The `request` annotation MUST be a concrete class object, not a
            # PEP-563 string. A string relies on get_type_hints resolution, which
            # fails after @serve.ingress cloudpickles the app (annotation-only
            # names are dropped from the endpoint globals) → request becomes a
            # query param (422) and OpenAPI 500s. See vllm_engine handler comment.
            anno = route.endpoint.__annotations__.get("request")
            assert isinstance(anno, type), (
                f"{path} 'request' annotation must be a real class, got {anno!r}. "
                "Assign __annotations__ = {'request': Request, ...} explicitly."
            )


if __name__ == "__main__":
    # Standalone runner (no pytest/pytest-cov required).
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failures else 0)
