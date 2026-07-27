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


def _install_fake_serve_context(monkeypatch, *, fail_times: int):
    """Install a fake ray.serve.context whose _get_global_client fails N times."""
    calls = {"get": 0, "set_none": 0}

    def _get_global_client(_health_check_controller=False, raise_if_no_controller_running=True):
        calls["get"] += 1
        if calls["get"] <= fail_times:
            raise RuntimeError("cached controller has died")
        return object()  # healthy client

    def _set_global_client(client):
        if client is None:
            calls["set_none"] += 1

    fake_ctx = types.ModuleType("ray.serve.context")
    fake_ctx._get_global_client = _get_global_client
    fake_ctx._set_global_client = _set_global_client
    monkeypatch.setitem(sys.modules, "ray.serve.context", fake_ctx)
    return calls


def test_refresh_serve_client_reconnects_on_stale(monkeypatch):
    """A stale cached handle triggers a reset + reconnect."""
    calls = _install_fake_serve_context(monkeypatch, fail_times=1)
    RayService()._refresh_serve_client()
    assert calls["set_none"] == 1, "stale handle should be dropped via _set_global_client(None)"
    assert calls["get"] == 2, "should retry after dropping the stale handle"


def test_refresh_serve_client_no_op_when_healthy(monkeypatch):
    """A healthy cached handle needs no reset."""
    calls = _install_fake_serve_context(monkeypatch, fail_times=0)
    RayService()._refresh_serve_client()
    assert calls["get"] == 1
    assert calls["set_none"] == 0


def test_refresh_serve_client_degrades_if_api_missing(monkeypatch):
    """If the private context API is gone (Ray version drift), degrade quietly."""
    broken = types.ModuleType("ray.serve.context")  # no _get_global_client attr
    monkeypatch.setitem(sys.modules, "ray.serve.context", broken)
    # Must not raise.
    RayService()._refresh_serve_client()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-o", "addopts=", "-q"]))
