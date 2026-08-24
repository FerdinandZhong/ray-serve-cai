"""Offline unit tests for CML auth identity resolution and RBAC dependencies.

No live CML cluster required — all CML HTTP calls are monkeypatched.
Run: python -m pytest tests/test_auth_cml_identity.py -o addopts=""
"""

import pytest

from ray_serve_cai.management.auth import cml_identity as ci
from ray_serve_cai.management.auth.cml_identity import (
    ROLE_ADMIN,
    ROLE_USER,
    Identity,
    resolve_caller,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Baseline: auth enabled, CML configured, empty cache.
    monkeypatch.setenv("MANAGEMENT_AUTH_ENABLED", "true")
    monkeypatch.setenv("CML_HOST", "https://ml.example.com")
    monkeypatch.setenv("CML_API_KEY", "service-key")
    monkeypatch.setenv("CML_PROJECT_ID", "proj-1")
    monkeypatch.delenv("ADMIN_BOOTSTRAP", raising=False)
    ci.clear_cache()
    yield
    ci.clear_cache()


def _patch_cml(monkeypatch, username, admin):
    """Patch the two CML seams: permissions (RBAC) + best-effort username."""
    monkeypatch.setattr(ci, "_fetch_permissions", lambda token: {"admin": admin})
    monkeypatch.setattr(ci, "_fetch_username", lambda token: username)


def test_valid_admin_token_resolves_admin(monkeypatch):
    _patch_cml(monkeypatch, "alice", admin=True)
    identity = resolve_caller("tok-alice")
    assert identity == Identity(username="alice", role=ROLE_ADMIN)
    assert identity.is_admin


def test_valid_user_token_resolves_user(monkeypatch):
    _patch_cml(monkeypatch, "bob", admin=False)
    identity = resolve_caller("tok-bob")
    assert identity.username == "bob"
    assert identity.role == ROLE_USER
    assert not identity.is_admin


def test_invalid_token_returns_none(monkeypatch):
    # A non-200 from CML -> _fetch_permissions returns None -> caller rejected.
    monkeypatch.setattr(ci, "_fetch_permissions", lambda token: None)
    assert resolve_caller("bad") is None


def test_username_fallback_when_whoami_unavailable(monkeypatch):
    # CML v2 has no whoami; role still resolves and username degrades sanely.
    monkeypatch.setattr(ci, "_fetch_permissions", lambda token: {"admin": True})
    monkeypatch.setattr(ci, "_fetch_username", lambda token: None)
    identity = resolve_caller("tok-x")
    assert identity.role == ROLE_ADMIN
    assert identity.username == "cml-admin"


def test_empty_token_returns_none():
    assert resolve_caller("") is None


def test_bootstrap_allowlist_overrides_role(monkeypatch):
    # CML says non-admin, but the ADMIN_BOOTSTRAP allowlist wins.
    monkeypatch.setenv("ADMIN_BOOTSTRAP", "svc-account, carol")
    monkeypatch.setattr(ci, "_fetch_permissions", lambda token: {"admin": False})
    monkeypatch.setattr(ci, "_fetch_username", lambda token: "carol")
    identity = resolve_caller("tok-carol")
    assert identity.is_admin


def test_result_is_cached(monkeypatch):
    calls = {"n": 0}

    def _count(token):
        calls["n"] += 1
        return {"admin": False}

    monkeypatch.setattr(ci, "_fetch_permissions", _count)
    monkeypatch.setattr(ci, "_fetch_username", lambda token: "dave")
    resolve_caller("tok-dave")
    resolve_caller("tok-dave")
    assert calls["n"] == 1  # second call served from cache


def test_auth_disabled_returns_synthetic_admin(monkeypatch):
    monkeypatch.setenv("MANAGEMENT_AUTH_ENABLED", "false")
    identity = resolve_caller("anything-or-nothing")
    assert identity.is_admin
    assert identity.username == "auth-disabled"


# ── _fetch_permissions: the single RBAC seam ──────────────────────────────────
# Payloads mirror the LIVE response of GET /api/v2/projects/{id}, verified
# against the running instance: a caller-scoped `permissions` object.

def test_fetch_permissions_returns_perms_on_200(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return {
                "id": "proj-1",
                "permissions": {"read": True, "write": True, "admin": True, "operator": True},
            }

    monkeypatch.setattr(ci.requests, "get", lambda *a, **k: _Resp())
    perms = ci._fetch_permissions("tok")
    assert perms["admin"] is True


def test_fetch_permissions_none_on_non_200(monkeypatch):
    class _Resp:
        status_code = 401

        def json(self):
            return {"error": "invalid apikey"}

    monkeypatch.setattr(ci.requests, "get", lambda *a, **k: _Resp())
    assert ci._fetch_permissions("bad-token") is None


def test_fetch_permissions_missing_key_yields_empty_dict(monkeypatch):
    # 200 but no permissions object -> {} (treated as non-admin, not a crash).
    class _Resp:
        status_code = 200

        def json(self):
            return {"id": "proj-1"}

    monkeypatch.setattr(ci.requests, "get", lambda *a, **k: _Resp())
    assert ci._fetch_permissions("tok") == {}


def test_fetch_username_best_effort_none_on_non_200(monkeypatch):
    # v2 has no whoami; a non-200 must degrade quietly to None, not raise.
    class _Resp:
        status_code = 404

        def json(self):
            return {}

    monkeypatch.setattr(ci.requests, "get", lambda *a, **k: _Resp())
    assert ci._fetch_username("any-token") is None
