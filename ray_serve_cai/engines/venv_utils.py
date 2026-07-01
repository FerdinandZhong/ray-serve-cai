"""
Shared virtual-environment resolution for engine deployment factories.

Each engine deployment runs its Ray actor under an isolated venv via the
`py_executable` runtime_env (see docs/ISOLATED_ENV_DESIGN.md).  Historically
each config builder hardcoded its own venv path (``/home/cdsw/.venv-vllm``
etc.), which fused three separate concerns — the engine implementation, its
dependency environment, and the package set — behind a single literal.

This module decouples them.  A deployment may name *any* environment via
``venv_name``; the engine's own name is only the default.  This unlocks:

  * multiple versions of one engine   (venv_name="vllm-013" vs "vllm-023")
  * one environment shared by engines  (compatible deps in a single venv)

Environments are materialised as ``/home/cdsw/.venv-<name>`` on shared NFS,
created either by the setup_*_env.py jobs or the POST /api/v1/environments API.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Base directory and prefix for all managed venvs.  The prefix is enforced so
# a caller-supplied venv_name can never point py_executable at an arbitrary
# filesystem location.
VENV_BASE = "/home/cdsw"
VENV_PREFIX = ".venv-"

# A venv name must be a single bare segment: letters, digits, dot, dash,
# underscore.  No slashes, no "..", no leading dot — this blocks path traversal
# and keeps the on-disk layout flat and predictable.
_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_venv_name(name: str) -> None:
    """Raise ValueError if *name* is not a safe, flat venv segment."""
    if not name or not _VALID_NAME.match(name) or ".." in name:
        raise ValueError(
            f"Invalid venv name {name!r}: must be a bare name like 'vllm' or "
            "'vllm-013' (letters, digits, '.', '-', '_'; no '/' or '..')."
        )


def venv_dir_for(name: str) -> str:
    """Return the absolute venv directory for a validated *name*."""
    validate_venv_name(name)
    return str(Path(VENV_BASE) / f"{VENV_PREFIX}{name}")


def resolve_venv_path(engine_config: dict, default_name: str) -> str | None:
    """Resolve the venv path for a deployment.

    Reads ``venv_name`` from *engine_config*, falling back to *default_name*
    (normally the engine type).  Returns the venv directory as a string, or
    ``None`` when the *default* env is absent (graceful fallback to the root
    venv, e.g. a dev box running everything in one environment).

    Fail-fast rule: if the caller *explicitly* names a venv that does not
    exist, raise ValueError so the API returns a clear 400 instead of the
    actor dying with an opaque ModuleNotFoundError at startup.
    """
    explicit = engine_config.get("venv_name")
    name = explicit or default_name
    path = Path(venv_dir_for(name))

    if path.exists():
        return str(path)

    if explicit:
        raise ValueError(
            f"Requested venv_name={name!r} not found at {path}. "
            "Create it first via POST /api/v1/environments, or omit venv_name "
            f"to use the default '{default_name}' environment."
        )

    # Default env missing — preserve historical graceful fallback.
    logger.info(
        "Default venv '%s' not found at %s — actor will run in the root venv",
        name, path,
    )
    return None
