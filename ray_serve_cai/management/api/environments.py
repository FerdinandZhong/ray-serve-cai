"""
Environment (venv) management API.

GET  /api/v1/environments          — list all isolated venvs and their status
POST /api/v1/environments          — create a new venv (name + packages)
GET  /api/v1/environments/{name}   — status of a single venv

An "environment" is an isolated virtual env at /home/cdsw/.venv-<name> on
shared NFS.  Engine deployments reference one by `venv_name` (see
engines/venv_utils.py).  Creation runs `uv venv` + `uv pip install` which can
take many minutes for heavy engines (vLLM), so POST kicks the work off in a
background thread and returns 202; poll GET to see when it becomes ready.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_admin
from ...engines.venv_utils import VENV_BASE, VENV_PREFIX, validate_venv_name, venv_dir_for

router = APIRouter(prefix="/api/v1/environments", tags=["Environments"])
logger = logging.getLogger(__name__)

# In-process record of background creations: name -> {status, error, packages}.
# The management API is a single deployment, so a module-level dict is a fine
# place to track in-flight work.  Ready/failed states are also re-derived from
# disk on every list so a restart never loses already-created venvs.
_CREATIONS: Dict[str, Dict] = {}
_LOCK = threading.Lock()


class CreateEnvironmentRequest(BaseModel):
    name: str = Field(
        ...,
        description="Environment name → /home/cdsw/.venv-<name>. Bare segment "
        "(letters, digits, '.', '-', '_'); e.g. 'vllm-013'.",
    )
    packages: List[str] = Field(
        ...,
        min_length=1,
        description="pip requirement strings, e.g. ['vllm==0.13.0', 'ninja'].",
    )
    python: Optional[str] = Field(
        default=None,
        description="Python interpreter for `uv venv`. Defaults to the base "
        "venv's interpreter (the runtime python) so the engine actor matches "
        "the cluster head. Only set this if that interpreter exists in the "
        "runtime image — a missing version triggers a standalone download and "
        "a head/actor version split.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "vllm-013",
                "packages": ["vllm==0.13.0", "ninja"],
            }
        }


def _import_setup():
    """Import setup_engine_venv/is_venv_ready from cai_integration lazily.

    The helper lives in the project's cai_integration package; ensure the
    project dir is importable (it normally is via PYTHONPATH on the head node).
    """
    project_dir = os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    from cai_integration.setup_environment import is_venv_ready, setup_engine_venv
    return setup_engine_venv, is_venv_ready


def _is_ready(name: str) -> bool:
    try:
        _, is_venv_ready = _import_setup()
        return bool(is_venv_ready(venv_dir_for(name)))
    except Exception:
        return Path(venv_dir_for(name)).exists()


def _run_creation(name: str, packages: List[str], python: Optional[str]) -> None:
    """Background worker: create the venv and record the outcome."""
    try:
        setup_engine_venv, _ = _import_setup()
        ok = setup_engine_venv(name, packages, venv_base=VENV_BASE, python=python)
        with _LOCK:
            _CREATIONS[name] = {
                "status": "ready" if ok else "failed",
                "error": None if ok else "venv not ready after install",
                "packages": packages,
            }
        logger.info("Environment creation %s: %s", name, "ready" if ok else "failed")
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _CREATIONS[name] = {"status": "failed", "error": str(exc), "packages": packages}
        logger.exception("Environment creation failed for %s", name)


def _discover_disk_venvs() -> List[str]:
    """Return names of all .venv-<name> dirs currently on disk."""
    base = Path(VENV_BASE)
    if not base.exists():
        return []
    out = []
    for p in base.glob(f"{VENV_PREFIX}*"):
        if p.is_dir():
            out.append(p.name[len(VENV_PREFIX):])
    return sorted(out)


@router.get("")
async def list_environments() -> dict:
    """List all environments — on-disk venvs plus any in-flight creations."""
    names = set(_discover_disk_venvs())
    with _LOCK:
        names.update(_CREATIONS.keys())

    envs = []
    for name in sorted(names):
        ready = _is_ready(name)
        with _LOCK:
            rec = _CREATIONS.get(name)
        status = "ready" if ready else (rec["status"] if rec else "unknown")
        envs.append({
            "name": name,
            "path": venv_dir_for(name),
            "ready": ready,
            "status": status,
            "packages": (rec or {}).get("packages"),
            "error": (rec or {}).get("error"),
        })
    return {"environments": envs, "count": len(envs)}


@router.get("/{name}")
async def get_environment(name: str) -> dict:
    try:
        validate_venv_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ready = _is_ready(name)
    with _LOCK:
        rec = _CREATIONS.get(name)
    if not ready and rec is None:
        raise HTTPException(status_code=404, detail=f"Environment '{name}' not found")
    return {
        "name": name,
        "path": venv_dir_for(name),
        "ready": ready,
        "status": "ready" if ready else (rec["status"] if rec else "unknown"),
        "packages": (rec or {}).get("packages"),
        "error": (rec or {}).get("error"),
    }


@router.post("", status_code=202, dependencies=[Depends(require_admin)])
async def create_environment(body: CreateEnvironmentRequest) -> dict:
    """Create a new isolated venv in the background. Returns 202 immediately."""
    try:
        validate_venv_name(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if _is_ready(body.name):
        return {"name": body.name, "status": "ready", "detail": "already exists"}

    with _LOCK:
        rec = _CREATIONS.get(body.name)
        if rec and rec["status"] == "creating":
            raise HTTPException(
                status_code=409,
                detail=f"Environment '{body.name}' is already being created",
            )
        _CREATIONS[body.name] = {
            "status": "creating", "error": None, "packages": body.packages,
        }

    thread = threading.Thread(
        target=_run_creation,
        args=(body.name, body.packages, body.python),
        daemon=True,
    )
    thread.start()

    return {
        "name": body.name,
        "status": "creating",
        "path": venv_dir_for(body.name),
        "detail": "Creation started; poll GET /api/v1/environments/{name} for status.",
    }
