"""Authentication and role-based access control for the management API.

Callers present their personal CML API key as an ``Authorization: Bearer``
token. :func:`cml_identity.resolve_caller` validates it against CML API v2 and
derives a role (``admin`` / ``user``) from the caller's CML project role. The
FastAPI dependencies :func:`dependencies.require_user` and
:func:`dependencies.require_admin` gate the management API routes.
"""

from .cml_identity import ROLE_ADMIN, ROLE_USER, Identity, resolve_caller
from .dependencies import require_admin, require_user

__all__ = [
    "Identity",
    "ROLE_ADMIN",
    "ROLE_USER",
    "resolve_caller",
    "require_admin",
    "require_user",
]
