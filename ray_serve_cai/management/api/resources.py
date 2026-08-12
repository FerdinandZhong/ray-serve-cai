"""Resource management API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from ..auth import require_admin
from ..models.requests import AddNodeRequest, DefineNodeTypeRequest
from ..models.responses import NodeInfo, NodesListResponse, ResourceCapacity
from ..services.coordinator import CoordinatorService

router = APIRouter(prefix="/api/v1/resources", tags=["resources"])


def get_coordinator() -> CoordinatorService:
    """Dependency to get coordinator service."""
    from ..app import get_coordinator_service
    return get_coordinator_service()


@router.post("/nodes", response_model=Dict[str, Any], status_code=201, dependencies=[Depends(require_admin)])
async def add_node(request: AddNodeRequest, coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Add a new worker node to the cluster.

    This creates a new CML application that joins the Ray cluster as a worker node.
    """
    try:
        result = coordinator.add_worker_node(
            node_type=request.node_type,
            cpu=request.cpu,
            memory=request.memory,
            gpus=request.gpus,
            runtime_identifier=request.runtime_identifier,
            node_label=request.node_label,
            ray_labels=request.ray_labels,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/node-types", response_model=Dict[str, Any], status_code=201, dependencies=[Depends(require_admin)])
async def define_node_type(request: DefineNodeTypeRequest, coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Define a new worker node_type at runtime (no cluster relaunch).

    Registers a worker group so subsequent `POST /api/v1/resources/nodes` calls
    can launch workers of this type. Registration only — does not launch workers.
    """
    try:
        return coordinator.define_node_type(
            node_type=request.node_type,
            cpu=request.cpu,
            memory=request.memory,
            gpus=request.gpus,
            accelerator_type=request.accelerator_type,
            node_label=request.node_label,
            runtime_identifier=request.runtime_identifier,
            count=request.count,
            name=request.name,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/node-types", response_model=Dict[str, Any])
async def list_node_types(coordinator: CoordinatorService = Depends(get_coordinator)):
    """List worker groups (node_types) known to the running cluster."""
    try:
        groups = coordinator.list_node_types()
        return {"node_types": groups, "count": len(groups)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/node-types/{node_type}", response_model=Dict[str, Any], dependencies=[Depends(require_admin)])
async def remove_node_type(node_type: str, coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Remove a worker node_type definition.

    The rendered launcher script is left on disk. Existing running workers of this
    type are not affected; this only removes the ability to launch new ones.
    """
    try:
        return coordinator.remove_node_type(node_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/allocation", response_model=Dict[str, Any])
async def get_resource_allocation(coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    Return this API's tracked resource allocation map.

    Shows workers registered through this API, their reserved CPU/memory/GPU,
    and remaining capacity based on those tracked allocations.  This reflects
    what the management API thinks is running — not the live Ray view.
    Use GET /resources/capacity for the live Ray cluster view.
    """
    try:
        return coordinator.get_resource_map()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/nodes/{app_id}", response_model=Dict[str, Any], dependencies=[Depends(require_admin)])
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


@router.get("/workers", response_model=Dict[str, Any], deprecated=True)
async def list_workers(coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    **Deprecated** — use ``GET /api/v1/resources/nodes`` instead.

    ``GET /nodes`` now returns each node with ``app_id``, ``app_name``, and
    ``cml_status`` merged in, so there is no need to call this endpoint
    separately to resolve the CML app ID before a DELETE.

    This endpoint will be removed in a future release.
    """
    try:
        workers = coordinator.get_worker_apps()
        return {
            "workers": workers,
            "count": len(workers),
            "deprecated": "Use GET /api/v1/resources/nodes — it now includes app_id and cml_status.",
        }
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
