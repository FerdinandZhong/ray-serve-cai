"""Resolve a CML caller's identity and role from their bearer token.

Design
------
The caller sends their *personal* CML API key as ``Authorization: Bearer``.
We can't trust anything the client says about itself, so both token validity
and role are established in a **single** CML API v2 call made *with the
caller's own token*:

    GET /api/v2/projects/{CML_PROJECT_ID}

Verified live: the project response carries a ``permissions`` object scoped to
the *requesting* token, e.g. ``{"read": true, "write": true, "admin": true,
"business_user": true, "operator": true, "inherit": false}``. So one request:

1. **Validates the token** — a non-200 (401/403) means the key is invalid and
   the caller is rejected.
2. **Yields the role** — ``permissions.admin`` is the RBAC signal; True maps to
   ``ROLE_ADMIN``, otherwise ``ROLE_USER``. No whoami and no service key are
   needed, so the two lookups collapse into one caller-scoped call.

Why not the earlier whoami + collaborators approach? Live verification killed
both halves of it:
  * CML API **v2 has no whoami** — every ``/api/v2/{user,users,self,whoami}``
    candidate 404s, and the legacy ``/api/v1/users/self`` rejects a v2 key.
  * ``GET /projects/{id}/collaborators`` **omits the project owner**, so an
    owner (a de-facto admin) would have mapped to ``user`` — an admin lockout.

Username is best-effort only (audit/``deployer`` + ``ADMIN_BOOTSTRAP``
matching): CML v2 exposes no self-lookup for an API key, so ``_fetch_username``
tries an optional, env-overridable whoami (``CML_WHOAMI_PATH`` /
``CML_USERNAME_FIELD``) and, when that is unavailable, we fall back to a
role-derived placeholder. Security never depends on it — the admin gate is
``permissions.admin`` alone. An ``ADMIN_BOOTSTRAP`` allowlist still wins when a
username *is* resolvable, so an operator can pre-authorize themselves.

Auth can be disabled wholesale with ``MANAGEMENT_AUTH_ENABLED=false`` (returns a
synthetic admin identity) as an operational safety valve for dev or emergency
access.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_USER = "user"


@dataclass(frozen=True)
class Identity:
    """The resolved identity of an authenticated caller."""

    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


# ── Configuration (resolved lazily so tests can monkeypatch env) ───────────────


def _auth_enabled() -> bool:
    return os.environ.get("MANAGEMENT_AUTH_ENABLED", "true").strip().lower() not in (
        "false",
        "0",
        "no",
    )


def _cml_host() -> str | None:
    domain = os.environ.get("CDSW_DOMAIN", "").strip()
    host = os.environ.get("CML_HOST") or (f"https://{domain}" if domain else None)
    return host.rstrip("/") if host else None


def _project_id() -> str | None:
    return os.environ.get("CML_PROJECT_ID") or os.environ.get("CDSW_PROJECT_ID")


def _bootstrap_admins() -> set:
    raw = os.environ.get("ADMIN_BOOTSTRAP", "")
    return {u.strip() for u in raw.split(",") if u.strip()}


def _cache_ttl() -> float:
    try:
        return float(os.environ.get("CML_AUTH_CACHE_TTL", "60"))
    except ValueError:
        return 60.0


def _whoami_path() -> str:
    # Verified against the live instance: CML API **v2 has no whoami** — every
    # /api/v2/{user,users,self,whoami} candidate 404s. The legacy v1 self
    # endpoints DO exist (/api/v1/users/self and /api/v1/user return 401, not
    # 404, i.e. present-but-unauthorized for a non-control-plane token). The
    # success-body field still needs confirming with a valid CML API key;
    # override via CML_WHOAMI_PATH if the instance differs.
    return os.environ.get("CML_WHOAMI_PATH", "/api/v1/users/self")


def _username_field() -> str:
    return os.environ.get("CML_USERNAME_FIELD", "username")


# ── TTL cache: token -> (Identity, expiry_epoch) ──────────────────────────────

_cache: dict[str, tuple[Identity, float]] = {}
_cache_lock = threading.Lock()


def _cache_get(token: str) -> Identity | None:
    now = time.time()
    with _cache_lock:
        entry = _cache.get(token)
        if entry is None:
            return None
        identity, expiry = entry
        if expiry < now:
            _cache.pop(token, None)
            return None
        return identity


def _cache_put(token: str, identity: Identity) -> None:
    with _cache_lock:
        _cache[token] = (identity, time.time() + _cache_ttl())


def clear_cache() -> None:
    """Drop all cached identities (used by tests and on config change)."""
    with _cache_lock:
        _cache.clear()


# ── CML calls (single seam — the only code that knows CML's wire format) ───────


def _fetch_permissions(token: str) -> dict | None:
    """Validate the caller's token and return their effective project perms.

    Single source of truth for auth: ``GET /api/v2/projects/{id}`` with the
    caller's *own* token. A non-200 means the token is invalid/expired (caller
    rejected -> None). On 200, CML returns a ``permissions`` object scoped to
    the requesting token (verified live), e.g. ``{"read": ..., "admin": ...}``.
    """
    host = _cml_host()
    project = _project_id()
    if not (host and project):
        logger.error("CML host/project not configured (CML_HOST/CDSW_DOMAIN, CML_PROJECT_ID)")
        return None
    url = f"{host}/api/v2/projects/{project}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10,
        )
    except requests.RequestException as e:
        logger.error("CML project lookup failed: %s", e)
        return None
    if resp.status_code != 200:
        logger.warning("CML rejected caller token: HTTP %d for %s", resp.status_code, url)
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.error("CML project response was not JSON")
        return None
    perms = data.get("permissions")
    return perms if isinstance(perms, dict) else {}


def _fetch_username(token: str) -> str | None:
    """Best-effort caller username for audit/bootstrap — never gates auth.

    CML v2 has no self-lookup for an API key, so this is optional: it tries the
    env-overridable whoami path and returns None (quietly) when unavailable.
    """
    host = _cml_host()
    path = _whoami_path()
    if not (host and path):
        return None
    try:
        resp = requests.get(
            f"{host}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    return data.get(_username_field()) or data.get("name") or data.get("subject")


# ── Public entry point ─────────────────────────────────────────────────────────


def resolve_caller(token: str) -> Identity | None:
    """Resolve a bearer token to an :class:`Identity`, or None if invalid.

    Cached for ``CML_AUTH_CACHE_TTL`` seconds to avoid a CML round-trip per
    request.
    """
    if not _auth_enabled():
        return Identity(username="auth-disabled", role=ROLE_ADMIN)

    if not token:
        return None

    cached = _cache_get(token)
    if cached is not None:
        return cached

    # One caller-scoped call both validates the token and yields the role.
    perms = _fetch_permissions(token)
    if perms is None:
        return None
    role = ROLE_ADMIN if perms.get("admin") is True else ROLE_USER

    # Username is audit-only and best-effort; the admin gate above never
    # depends on it. A resolvable name in ADMIN_BOOTSTRAP still forces admin.
    username = _fetch_username(token)
    if username and username in _bootstrap_admins():
        role = ROLE_ADMIN
    if not username:
        username = "cml-admin" if role == ROLE_ADMIN else "cml-user"

    identity = Identity(username=username, role=role)
    _cache_put(token, identity)
    return identity
