"""Cloudpickle compatibility shims for Ray Serve.

Ray Serve's ``@serve.ingress(app)`` cloudpickles the FastAPI app *at decoration
time* so it can ship the ASGI app to the replica actor. Since fastapi>=0.137
("preserve APIRouter/APIRoute instances" refactor: ``_openapi_routes_version``,
``_IncludedRouter``, ``APIRouter._get_routes_version()``), the retained router
graph transitively holds a ``threading.Lock``, which cloudpickle cannot
serialize::

    TypeError: cannot pickle '_thread.lock' object

That breaks the management-API deploy (scripts/deploy_ray_app.py) and every
module-level ``@serve.ingress`` (e.g. engines/mcp_engine.py).

A lock has no meaningful cross-process state, so the only correct
reconstruction on the replica side is a *fresh* lock. We register a copyreg
reducer to that effect instead of pinning fastapi back below 0.137 — this keeps
the pin free, needs no venv rebuilds, and fixes the management app and all
engines uniformly.
"""
from __future__ import annotations

import copyreg
import threading
import _thread

_INSTALLED = False


def install_lock_pickle_reducer() -> None:
    """Make ``_thread.lock``/``RLock`` cloudpickle-able as a fresh lock.

    Idempotent: safe to call from multiple import paths. Registers process-wide
    reducers so any cloudpickle/pickle of a lock reconstructs a new, unlocked
    primitive on load.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    copyreg.pickle(_thread.LockType, lambda _lock: (threading.Lock, ()))
    copyreg.pickle(type(threading.RLock()), lambda _lock: (threading.RLock, ()))
    _INSTALLED = True
