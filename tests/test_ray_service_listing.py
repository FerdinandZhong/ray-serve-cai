"""Regression tests for RayService application listing (no Ray cluster needed).

Covers the hotfix for `/api/v1/applications` returning an empty list while
apps are actually running:

  * `_refresh_serve_client` must drop a stale/dead cached Serve controller
    handle and reconnect (the Serve controller restarts on CML, and
    `serve.status()` reuses the cached client WITHOUT a health check).
  * `get_application_status` must match by route prefix as well as name,
    since a model at route `/qwen3-35b` may carry the app name
    `qwen3-35b-a3b-fp8`.

Run:
    python -m pytest tests/test_ray_service_listing.py -o addopts=""
"""
from __future__ import annotations

import sys
import types

import pytest

from ray_serve_cai.management.services.ray_service import RayService


def test_get_application_status_matches_by_name_and_route(monkeypatch):
    svc = RayService()
    apps = [
        {"name": "qwen3-35b-a3b-fp8", "route_prefix": "/qwen3-35b", "status": "RUNNING"},
        {"name": "management-api", "route_prefix": "/", "status": "RUNNING"},
    ]
    monkeypatch.setattr(svc, "list_applications", lambda: apps)

    # by exact name
    assert svc.get_application_status("qwen3-35b-a3b-fp8")["route_prefix"] == "/qwen3-35b"
    # by route prefix without leading slash (what a user naturally queries)
    assert svc.get_application_status("qwen3-35b")["name"] == "qwen3-35b-a3b-fp8"
    # by route prefix with leading slash
    assert svc.get_application_status("/qwen3-35b")["name"] == "qwen3-35b-a3b-fp8"
    # unknown
    assert svc.get_application_status("does-not-exist") is None


def _install_fake_serve_context(monkeypatch, *, alive: bool, had_cached: bool = True):
    """Install a fake ray.serve.context exposing Ray 2.56.1's _check_cached_client_alive.

    Mirrors the real primitive: it pings the cached controller and, when the
    handle is dead, clears the cache itself and returns ``(None, True)``. A
    live handle returns ``(client, True)``; an empty cache returns ``(None, False)``.
    """
    calls = {"check": 0}

    def _check_cached_client_alive():
        calls["check"] += 1
        if alive:
            return object(), True  # healthy cached client
        return None, had_cached    # stale (cache cleared internally) or absent

    fake_ctx = types.ModuleType("ray.serve.context")
    fake_ctx._check_cached_client_alive = _check_cached_client_alive
    monkeypatch.setitem(sys.modules, "ray.serve.context", fake_ctx)
    return calls


def test_refresh_serve_client_clears_stale_handle(monkeypatch):
    """A stale handle is reported as (None, True); the helper tolerates it
    (the cache is cleared inside _check_cached_client_alive) and does not raise."""
    calls = _install_fake_serve_context(monkeypatch, alive=False, had_cached=True)
    RayService()._refresh_serve_client()  # must not raise
    assert calls["check"] == 1


def test_refresh_serve_client_no_op_when_healthy(monkeypatch):
    """A healthy cached handle is checked once and left in place."""
    calls = _install_fake_serve_context(monkeypatch, alive=True)
    RayService()._refresh_serve_client()
    assert calls["check"] == 1


def test_refresh_serve_client_degrades_if_api_missing(monkeypatch):
    """If the private context API is gone (Ray version drift), degrade quietly."""
    broken = types.ModuleType("ray.serve.context")  # no _get_global_client attr
    monkeypatch.setitem(sys.modules, "ray.serve.context", broken)
    # Must not raise.
    RayService()._refresh_serve_client()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-o", "addopts=", "-q"]))
