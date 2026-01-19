"""Cluster information and health API endpoints."""

import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends

from ..models.responses import ClusterStatus, ClusterInfo
from ..services.coordinator import CoordinatorService

router = APIRouter(prefix="/api/v1/cluster", tags=["cluster"])


def get_coordinator() -> CoordinatorService:
    """Dependency to get coordinator service."""
    from ..app import get_coordinator_service
    return get_coordinator_service()


@router.get("/status", response_model=ClusterStatus)
async def get_cluster_status(coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Get overall cluster health and status information.

    This includes node counts, application counts, and resource utilization.
    """
    try:
        status = coordinator.get_cluster_status()
        return ClusterStatus(**status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/info", response_model=ClusterInfo)
async def get_cluster_info():
    """
    Get cluster configuration and connection information.

    This includes Ray head address, dashboard URL, and management API URL.
    """
    try:
        # Load cluster info from file
        cluster_info_path = Path("/home/cdsw/ray_cluster_info.json")
        if not cluster_info_path.exists():
            raise HTTPException(
                status_code=503,
                detail="Cluster info not available. Ensure Ray cluster is running."
            )

        with open(cluster_info_path) as f:
            cluster_data = json.load(f)

        import ray
        try:
            ray.init(address="auto", ignore_reinit_error=True)
            ray_version = ray.__version__
        except Exception:
            ray_version = "unknown"

        return ClusterInfo(
            head_address=cluster_data.get("head_address", ""),
            dashboard_url=cluster_data.get("dashboard_url"),
            management_api_url=cluster_data.get("management_api_url", ""),
            ray_version=ray_version,
            cluster_name=cluster_data.get("cluster_name", "ray-cluster")
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
