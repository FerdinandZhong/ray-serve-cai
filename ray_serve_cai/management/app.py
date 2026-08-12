"""
Ray Cluster Management API Application.

This FastAPI application provides a REST API for managing Ray clusters running on CML/CAI.
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import resources_router, applications_router, cluster_router, cml_apps_router, metrics_router, engines_router, environments_router
from .auth import require_user
from .services import RayService, CAIService, CoordinatorService
from ..utils.logging import setup_serve_logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global service instances
ray_service = None
cai_service = None
coordinator_service = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global ray_service, cai_service, coordinator_service

    setup_serve_logging("management-api")
    logger.info("Initializing Management API services...")

    # Initialize Ray service
    ray_address = os.environ.get("RAY_ADDRESS", "auto")
    ray_service = RayService(ray_address=ray_address)

    # Initialize CAI service
    # CDSW_DOMAIN is the standard CML pod env var; CML_HOST overrides it when set.
    _domain = os.environ.get("CDSW_DOMAIN", "").strip()
    cml_host = os.environ.get("CML_HOST") or (f"https://{_domain}" if _domain else None)

    project_id = os.environ.get("CML_PROJECT_ID") or os.environ.get("CDSW_PROJECT_ID")
    if not project_id:
        logger.warning("CML_PROJECT_ID / CDSW_PROJECT_ID not set. CML operations may fail.")

    logger.info(f"  cml_host   : {cml_host or '(not set)'}")
    logger.info(f"  project_id : {project_id or '(not set)'}")

    try:
        cai_service = CAIService(project_id=project_id, cml_host=cml_host)
        logger.info("CAI service initialized")
    except Exception as e:
        logger.error(f"CAI service failed to initialize: {e}")
        cai_service = None

    # Initialize coordinator service
    if cai_service:
        coordinator_service = CoordinatorService(ray_service, cai_service)
        logger.info("Coordinator service initialized")
    else:
        logger.error(
            "Coordinator service NOT initialized — all /resources and /applications "
            "endpoints will return 500. Check CML_HOST/CDSW_DOMAIN and CML_API_KEY/CDSW_APIV2_KEY."
        )

    logger.info("Management API lifespan startup complete")

    yield

    logger.info("Shutting down Management API services...")


# Create FastAPI app
app = FastAPI(
    title="Ray Cluster Management API",
    description="REST API for managing Ray clusters on CML/CAI platform",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add CORS middleware.
# Origins are restricted to the CML domain by default; set MANAGEMENT_CORS_ORIGINS
# (comma-separated) to override. Falls back to the CML domain derived from
# CDSW_DOMAIN, or "*" only when nothing is configured (dev).
_cors_env = os.environ.get("MANAGEMENT_CORS_ORIGINS", "").strip()
if _cors_env:
    _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
elif os.environ.get("CDSW_DOMAIN", "").strip():
    _cors_origins = [f"https://{os.environ['CDSW_DOMAIN'].strip()}", f"https://*.{os.environ['CDSW_DOMAIN'].strip()}"]
else:
    _cors_origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers.
# Control-plane routers require an authenticated CML caller (require_user);
# individual mutating routes additionally require the admin role (see each
# router's route decorators). The metrics router is intentionally left open so
# Prometheus can scrape it without a bearer token.
_authed = [Depends(require_user)]
app.include_router(resources_router, dependencies=_authed)
app.include_router(applications_router, dependencies=_authed)
app.include_router(cml_apps_router, dependencies=_authed)
app.include_router(cluster_router, dependencies=_authed)
app.include_router(metrics_router)
app.include_router(engines_router, dependencies=_authed)
app.include_router(environments_router, dependencies=_authed)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Ray Cluster Management API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "ray_connected": ray_service._initialized if ray_service else False,
        "cai_available": cai_service is not None,
    }


def get_coordinator_service() -> CoordinatorService:
    """
    Get the global coordinator service instance.

    Used by API route dependencies.
    """
    if coordinator_service is None:
        raise RuntimeError("Coordinator service not initialized")
    return coordinator_service


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("CDSW_APP_PORT", 8080))
    host = os.environ.get("CDSW_APP_HOST", "127.0.0.1")

    logger.info(f"Starting Management API on {host}:{port}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info"
    )
