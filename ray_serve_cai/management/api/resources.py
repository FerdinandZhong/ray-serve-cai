"""Resource management API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from ..models.requests import AddNodeRequest
from ..models.responses import NodeInfo, NodesListResponse, ResourceCapacity
from ..services.coordinator import CoordinatorService

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])


def get_coordinator() -> CoordinatorService:
    """Dependency to get coordinator service."""
    from ..app import get_coordinator_service
    return get_coordinator_service()


@router.post("/nodes/add", response_model=Dict[str, Any])
async def add_node(request: AddNodeRequest, coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Add a new worker node to the cluster.

    This creates a new CML application that joins the Ray cluster as a worker node.
    """
    try:
        result = coordinator.add_worker_node(
            cpu=request.cpu,
            memory=request.memory,
            node_type=request.node_type
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/nodes/{app_id}", response_model=Dict[str, Any])
async def remove_node(app_id: str, coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Remove a worker node from the cluster.

    This stops the corresponding CML application and removes the node from Ray.
    """
    try:
        result = coordinator.remove_worker_node(app_id)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/nodes", response_model=NodesListResponse)
async def list_nodes(coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    List all nodes in the cluster with their Ray and CML information.
    """
    try:
        nodes = coordinator.get_enriched_nodes()
        alive_nodes = sum(1 for n in nodes if n.get("alive", False))

        return NodesListResponse(
            nodes=[NodeInfo(**node) for node in nodes],
            total_nodes=len(nodes),
            alive_nodes=alive_nodes
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/capacity", response_model=ResourceCapacity)
async def get_capacity(coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Check available cluster resources and capacity.
    """
    try:
        total_resources = coordinator.ray_service.get_cluster_resources()
        available_resources = coordinator.ray_service.get_available_resources()

        total_cpus = total_resources.get("CPU", 0)
        available_cpus = available_resources.get("CPU", 0)
        total_memory = total_resources.get("memory", 0) / (1024 ** 3)  # Convert to GB
        available_memory = available_resources.get("memory", 0) / (1024 ** 3)

        cpu_used = total_cpus - available_cpus
        utilization = (cpu_used / total_cpus * 100) if total_cpus > 0 else 0

        return ResourceCapacity(
            total_cpus=total_cpus,
            available_cpus=available_cpus,
            total_memory=total_memory,
            available_memory=available_memory,
            total_gpus=total_resources.get("GPU", 0),
            available_gpus=available_resources.get("GPU", 0),
            utilization_percent=round(utilization, 2)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
