"""API routers for management endpoints."""

from .resources import router as resources_router
from .applications import router as applications_router
from .cluster import router as cluster_router
from .cml_apps import router as cml_apps_router
from .metrics import router as metrics_router
from .engines import router as engines_router
from .environments import router as environments_router

__all__ = [
    "resources_router",
    "applications_router",
    "cluster_router",
    "cml_apps_router",
    "metrics_router",
    "engines_router",
    "environments_router",
]
