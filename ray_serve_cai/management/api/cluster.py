"""Cluster information and health API endpoints."""

import json
import os
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
async def get_cluster_info(coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Get cluster configuration and connection information.

    This includes Ray head address, dashboard URL, and management API URL.
    """
    try:
        cluster_info_path = Path("/home/cdsw/ray_cluster_info.json")
        if not cluster_info_path.exists():
            raise HTTPException(
                status_code=503,
                detail="Cluster info not available. Ensure Ray cluster is running."
            )

        with open(cluster_info_path) as f:
            cluster_data = json.load(f)

        # Read the version from the already-connected Ray instance via RayService
        # rather than calling ray.init() directly (which bypasses the shared
        # connection lifecycle and may connect to a different cluster).
        try:
            import ray
            coordinator.ray_service.connect()
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


@router.get("/gcs-address")
async def get_gcs_address():
    """
    Return the Ray GCS address (internal pod IP + port) for this head node.

    Workers must connect to this address — the public CAI domain only exposes
    port 443 and cannot be used for direct Ray GCS connections on port 6379.
    """
    gcs_file = Path("/home/cdsw/ray_gcs_address")
    if gcs_file.exists():
        address = gcs_file.read_text().strip()
    else:
        # Fallback: construct from CDSW_IP_ADDRESS if file not yet written
        pod_ip = os.environ.get("CDSW_IP_ADDRESS")
        ray_port = os.environ.get("RAY_PORT", "6379")
        if not pod_ip:
            raise HTTPException(
                status_code=503,
                detail="GCS address not available yet — head node may still be starting",
            )
        address = f"{pod_ip}:{ray_port}"

    return {"gcs_address": address}
