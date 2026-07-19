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
