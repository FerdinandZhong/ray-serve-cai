"""API routers for management endpoints."""

from .resources import router as resources_router
from .applications import router as applications_router
from .cluster import router as cluster_router

__all__ = ["resources_router", "applications_router", "cluster_router"]
