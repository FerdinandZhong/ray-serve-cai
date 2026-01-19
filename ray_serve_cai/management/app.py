"""
Ray Cluster Management API Application.

This FastAPI application provides a REST API for managing Ray clusters running on CML/CAI.
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import resources_router, applications_router, cluster_router
from .services import RayService, CAIService, CoordinatorService

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

    logger.info("Initializing Management API services...")

    # Initialize Ray service
    ray_address = os.environ.get("RAY_ADDRESS", "auto")
    ray_service = RayService(ray_address=ray_address)

    # Initialize CAI service
    project_id = os.environ.get("CML_PROJECT_ID")
    if not project_id:
        logger.warning("CML_PROJECT_ID not set. CML operations may fail.")

    try:
        cai_service = CAIService(project_id=project_id)
    except Exception as e:
        logger.error(f"Failed to initialize CAI service: {e}")
        cai_service = None

    # Initialize coordinator service
    if cai_service:
        coordinator_service = CoordinatorService(ray_service, cai_service)
    else:
        logger.warning("Coordinator service not initialized due to missing CAI service")

    logger.info("Management API services initialized successfully")

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

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(resources_router)
app.include_router(applications_router)
app.include_router(cluster_router)


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Ray Cluster Management API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/health")
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
