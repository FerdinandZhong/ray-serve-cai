"""FastAPI security dependencies for the management API.

``require_user`` gates read access to any valid CML caller; ``require_admin``
additionally requires the caller to hold the ``admin`` role. Attach
``require_user`` at router level and ``require_admin`` on mutating routes.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .cml_identity import Identity, resolve_caller

# auto_error=False so we can raise 401 with a WWW-Authenticate header ourselves.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid CML bearer token",
    headers={"WWW-Authenticate": "Bearer"},
)


def _identity_from(creds: HTTPAuthorizationCredentials | None) -> Identity:
    if creds is None or not creds.credentials:
        raise _UNAUTHENTICATED
    identity = resolve_caller(creds.credentials)
    if identity is None:
        raise _UNAUTHENTICATED
    return identity


async def require_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Identity:
    """Require any authenticated CML caller."""
    return _identity_from(creds)


async def require_admin(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Identity:
    """Require an authenticated caller with the ``admin`` role."""
    identity = _identity_from(creds)
    if not identity.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Admin role required for this operation; caller "
                f"'{identity.username}' has role '{identity.role}'"
            ),
        )
    return identity
