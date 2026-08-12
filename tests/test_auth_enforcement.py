"""Integration tests for management-API auth enforcement.

Uses FastAPI's TestClient against the real app. CML identity resolution is
monkeypatched so no live cluster is needed. We assert the auth *gate*, not the
handler bodies — for the admin case we only require that the request gets past
auth (i.e. is not 401/403); the handler itself may 500 without a live Ray
cluster, which is fine.

Run: python -m pytest tests/test_auth_enforcement.py -o addopts=""
"""

import pytest
from fastapi.testclient import TestClient

from ray_serve_cai.management import app as app_module
from ray_serve_cai.management.auth import cml_identity as ci
from ray_serve_cai.management.auth.cml_identity import ROLE_ADMIN, ROLE_USER, Identity


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MANAGEMENT_AUTH_ENABLED", "true")
    ci.clear_cache()

    def _resolve(token):
        return {
            "admin-tok": Identity("root", ROLE_ADMIN),
            "user-tok": Identity("joe", ROLE_USER),
        }.get(token)

    # Patch the symbol used by the dependencies module.
    monkeypatch.setattr(
        "ray_serve_cai.management.auth.dependencies.resolve_caller", _resolve
    )
    # raise_server_exceptions=False: handlers that pass auth but then fail
    # (no live Ray/coordinator) surface as 500 rather than propagating, so we
    # can assert the auth gate (not 401/403) without a real cluster.
    return TestClient(app_module.app, raise_server_exceptions=False)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── Open endpoints (no auth) ───────────────────────────────────────────────────

def test_health_is_open(client):
    assert client.get("/api/health").status_code == 200


def test_metrics_is_open(client):
    # Left unauthenticated for Prometheus scraping. May 500 without Ray, but
    # must not be 401/403.
    assert client.get("/api/v1/metrics").status_code not in (401, 403)


# ── require_user on reads ──────────────────────────────────────────────────────

def test_list_applications_requires_token(client):
    assert client.get("/api/v1/applications").status_code == 401


def test_list_applications_allows_user(client):
    # Valid user passes auth; handler may 500 without Ray, but not 401/403.
    assert client.get("/api/v1/applications", headers=_auth("user-tok")).status_code not in (401, 403)


# ── require_admin on mutations ─────────────────────────────────────────────────

def test_deploy_requires_token(client):
    assert client.post("/api/v1/applications", json={"name": "x"}).status_code == 401


def test_deploy_forbidden_for_non_admin(client):
    r = client.post("/api/v1/applications", headers=_auth("user-tok"), json={"name": "x", "engine_type": "vllm", "model": "m"})
    assert r.status_code == 403


def test_deploy_passes_auth_for_admin(client):
    r = client.post("/api/v1/applications", headers=_auth("admin-tok"), json={"name": "x", "engine_type": "vllm", "model": "m"})
    assert r.status_code not in (401, 403)


def test_delete_node_forbidden_for_non_admin(client):
    assert client.delete("/api/v1/resources/nodes/app-1", headers=_auth("user-tok")).status_code == 403


def test_create_env_forbidden_for_non_admin(client):
    r = client.post("/api/v1/environments", headers=_auth("user-tok"), json={"name": "e", "packages": ["x"]})
    assert r.status_code == 403


def test_invalid_token_rejected(client):
    assert client.get("/api/v1/applications", headers=_auth("garbage")).status_code == 401
