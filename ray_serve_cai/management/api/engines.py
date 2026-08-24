"""
Engine registration API.

POST /api/v1/engines/register  — dynamically load and register a custom engine
GET  /api/v1/engines/          — list all currently registered engine types

The allowlist is controlled by the ALLOWED_ENGINE_MODULES env var
(comma-separated module prefixes, default: "custom_engines,ray_serve_cai.engines").
"""

import importlib
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..auth import require_admin
from ...engines.registry import get_registry, register_engine

router = APIRouter(prefix="/api/v1/engines", tags=["Engines"])
logger = logging.getLogger(__name__)

_ALLOWED_PREFIXES = [
    p.strip()
    for p in os.environ.get(
        "ALLOWED_ENGINE_MODULES", "custom_engines,ray_serve_cai.engines"
    ).split(",")
    if p.strip()
]


class EngineRegisterRequest(BaseModel):
    engine_type: str
    module_path: str
    config_builder_class: str
    deployment_factory_class: str
    engine_class: Optional[str] = None


@router.post("/register", status_code=201, dependencies=[Depends(require_admin)])
async def register_engine_endpoint(
    body: EngineRegisterRequest, request: Request
) -> dict:
    """
    Dynamically load a Python module and register its engine with the registry.

    The module must be importable from the server's PYTHONPATH and its path
    must start with one of the prefixes in ALLOWED_ENGINE_MODULES.
    """
    if not any(body.module_path.startswith(p) for p in _ALLOWED_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail=f"Module '{body.module_path}' is not in the allowlist: {_ALLOWED_PREFIXES}",
        )

    try:
        mod = importlib.import_module(body.module_path)
    except ImportError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot import module '{body.module_path}': {exc}",
        )

    registry = get_registry()
    if body.engine_type in registry.list_engines():
        raise HTTPException(
            status_code=409,
            detail=f"Engine type '{body.engine_type}' is already registered",
        )

    try:
        builder = getattr(mod, body.config_builder_class)()
        factory = getattr(mod, body.deployment_factory_class)()
        engine_cls = (
            getattr(mod, body.engine_class)
            if body.engine_class
            else type(body.engine_type, (), {})
        )
    except AttributeError as exc:
        raise HTTPException(status_code=400, detail=f"Class not found in module: {exc}")

    register_engine(body.engine_type, engine_cls, builder, factory)

    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "AUDIT engine_register  module=%s  type=%s  ip=%s",
        body.module_path,
        body.engine_type,
        client_ip,
    )

    return {"registered": body.engine_type}


@router.get("")
async def list_engines_endpoint() -> dict:
    """List all currently registered engine types and the default engine."""
    registry = get_registry()
    return {"engines": registry.list_engines(), "default_engine": registry.get_default_engine()}
