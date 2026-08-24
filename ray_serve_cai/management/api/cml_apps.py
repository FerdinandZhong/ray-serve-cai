"""CML application lifecycle endpoints.

Manages CML applications that run workloads on the Ray cluster — distinct from
Ray Serve application deployment (api/applications.py) and worker-node
management (api/resources.py).
"""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from ..auth import require_admin
from ..models.requests import LaunchCaiApplicationRequest
from ..services.coordinator import CoordinatorService

router = APIRouter(prefix="/api/v1/cml-apps", tags=["cml-apps"])


def get_coordinator() -> CoordinatorService:
    """Dependency to get coordinator service."""
    from ..app import get_coordinator_service
    return get_coordinator_service()


@router.post("", response_model=Dict[str, Any], dependencies=[Depends(require_admin)])
async def launch_cai_application(
    request: LaunchCaiApplicationRequest,
    coordinator: CoordinatorService = Depends(get_coordinator),
):
    """
    Validate cluster capacity, launch a CML application, and record the allocation.

    Returns 400 if the cluster lacks sufficient resources.
    """
    try:
        result = coordinator.launch_cai_application(
            name=request.name,
            script=request.script,
            cpu=request.cpu,
            memory=request.memory,
            gpus=request.gpus,
            runtime_identifier=request.runtime_identifier,
            environment=request.environment,
            bypass_authentication=request.bypass_authentication,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{app_id}", response_model=Dict[str, Any], dependencies=[Depends(require_admin)])
async def remove_cai_application(
    app_id: str,
    coordinator: CoordinatorService = Depends(get_coordinator),
):
    """Stop a CML application and release its resources back to the cluster pool."""
    try:
        return coordinator.remove_cai_application(app_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
