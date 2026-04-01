"""Application management API endpoints."""

from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from ..models.requests import DeployApplicationRequest, DeployModelRequest
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


@router.post("/model", response_model=Dict[str, Any])
async def deploy_model(
    request: DeployModelRequest,
    coordinator: CoordinatorService = Depends(get_coordinator)
):
    """
    Deploy a vLLM or SGLang model as a Ray Serve application.

    The model is loaded by the chosen inference engine and served via
    OpenAI-compatible endpoints under `route_prefix`:

    - `POST {route_prefix}/v1/completions`
    - `POST {route_prefix}/v1/chat/completions`
    - `GET  {route_prefix}/v1/models`
    - `GET  {route_prefix}/health`

    Use `tensor_parallel_size > 1` to split a large model across multiple GPUs
    on a single worker node.  To spread replicas across multiple worker nodes,
    increase `num_replicas` and ensure enough GPU-equipped nodes are registered
    in the cluster.

    The application name must be unique within Ray Serve.  To update a running
    model, call this endpoint again with the same name — Ray Serve will perform
    a rolling update.
    """
    try:
        result = coordinator.ray_service.deploy_model(
            name=request.name,
            engine_type=request.engine_type,
            model=request.model,
            route_prefix=request.route_prefix,
            num_replicas=request.num_replicas,
            tensor_parallel_size=request.tensor_parallel_size,
            use_cpu=request.use_cpu,
            gpu_fraction=request.gpu_fraction,
            engine_config=request.engine_config,
            placement_group_bundles=request.placement_group_bundles,
            placement_group_strategy=request.placement_group_strategy,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/engines", response_model=Dict[str, Any])
async def list_engines(coordinator: CoordinatorService = Depends(get_coordinator)):
    """
    List available inference engines and their registration status.

    Returns the engines that are installed and available for model deployment
    (e.g., 'vllm', 'sglang').  Only registered engines can be used with the
    POST /applications/model endpoint.
    """
    try:
        import ray_serve_cai.engines  # noqa: F401 — triggers registration
        from ray_serve_cai.engines.registry import get_registry

        registry = get_registry()
        engines = registry.list_engines()
        default = registry.get_default_engine()
        return {
            "engines": engines,
            "default_engine": default,
            "total": len(engines),
        }
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
