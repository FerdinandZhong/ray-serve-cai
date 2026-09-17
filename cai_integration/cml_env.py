"""Resolve CML and CAI Workbench connection variables without exposing secrets."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CMLConnection:
    """Connection values injected by GitHub Actions or a CAI Workbench."""

    host: Optional[str]
    api_key: Optional[str]
    project_id: Optional[str]


def _value(environment: Mapping[str, str], *names: str) -> Optional[str]:
    """Return the first non-empty named environment value, stripped."""
    for name in names:
        value = environment.get(name, "").strip()
        if value:
            return value
    return None


def resolve_cml_connection(
    environment: Optional[Mapping[str, str]] = None,
) -> CMLConnection:
    """Resolve legacy CML variables and their CAI Workbench counterparts.

    ``CML_HOST`` remains the explicit override. In a Workbench, CAI provides a
    domain rather than a full API URL, so ``CDSW_DOMAIN`` is converted to an
    HTTPS host. The function deliberately returns values only; callers must not
    log the API key.
    """
    env = environment if environment is not None else os.environ
    host = _value(env, "CML_HOST")
    if not host:
        domain = _value(env, "CDSW_DOMAIN")
        host = f"https://{domain}" if domain else None

    return CMLConnection(
        host=host,
        api_key=_value(env, "CML_API_KEY", "CDSW_APIV2_KEY"),
        project_id=_value(env, "CML_PROJECT_ID", "CDSW_PROJECT_ID"),
    )
