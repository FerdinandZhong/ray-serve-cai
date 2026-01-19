"""Application management API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from ..models.requests import DeployApplicationRequest
from ..models.responses import ApplicationInfo, ApplicationsListResponse
from ..services.coordinator import CoordinatorService

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


def get_coordinator() -> CoordinatorService:
    """Dependency to get coordinator service."""
    from ..app import get_coordinator_service
    return get_coordinator_service()


@router.post("", response_model=Dict[str, Any])
async def deploy_application(
    request: DeployApplicationRequest,
    coordinator: CoordinatorService = Depends(get_coordinator)
):
    """
    Deploy a new Ray Serve application.

    The application will be imported from the specified import_path and deployed
    with the given configuration.
    """
    try:
        result = coordinator.ray_service.deploy_application(
            name=request.name,
            import_path=request.import_path,
            route_prefix=request.route_prefix,
            num_replicas=request.num_replicas,
            ray_actor_options=request.ray_actor_options
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{app_name}", response_model=Dict[str, Any])
async def delete_application(
    app_name: str,
    coordinator: CoordinatorService = Depends(get_coordinator)
):
    """
    Undeploy a Ray Serve application.

    This will stop all replicas and remove the application from Ray Serve.
    """
    try:
        result = coordinator.ray_service.delete_application(app_name)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=ApplicationsListResponse)
async def list_applications(coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    List all Ray Serve applications currently deployed.
    """
    try:
        apps = coordinator.ray_service.list_applications()

        return ApplicationsListResponse(
            applications=[ApplicationInfo(**app, num_replicas=1, route_prefix="/") for app in apps],
            total_applications=len(apps)
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{app_name}", response_model=ApplicationInfo)
async def get_application(
    app_name: str,
    coordinator: CoordinatorService = Depends(get_coordinator)
):
    """
    Get detailed information about a specific application.
    """
    try:
        app = coordinator.ray_service.get_application_status(app_name)
        if not app:
            raise HTTPException(status_code=404, detail=f"Application {app_name} not found")

        return ApplicationInfo(**app, num_replicas=1, route_prefix="/")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
