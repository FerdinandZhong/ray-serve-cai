"""Application management API endpoints."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from ..auth import Identity, require_admin
from ..models.requests import DeployApplicationRequest
from ..models.responses import ApplicationInfo, ApplicationsListResponse
from ..services.coordinator import CoordinatorService

router = APIRouter(prefix="/api/v1/applications", tags=["applications"])


def get_coordinator() -> CoordinatorService:
    """Dependency to get coordinator service."""
    from ..app import get_coordinator_service
    return get_coordinator_service()


@router.post("/model", include_in_schema=False)
async def deploy_model_compat():
    """
    Permanent redirect to the unified POST /applications endpoint.

    This path was removed in favour of POST /api/v1/applications which now
    handles both engine-registry and raw Ray Serve deployments via a
    discriminated union body.  Update your client to use that endpoint.
    """
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/v1/applications", status_code=308)


@router.post("", response_model=Dict[str, Any], dependencies=[Depends(require_admin)])
async def deploy_application(
    request: DeployApplicationRequest,
    coordinator: CoordinatorService = Depends(get_coordinator),
    identity: Identity = Depends(require_admin),
):
    """
    Deploy a Ray Serve application.

    Exactly one of ``engine_type`` or ``import_path`` must be provided:

    **Engine-registry path** (``engine_type`` set):
    Deploy a model or service using a registered inference engine (vLLM, SGLang,
    LiteLLM, YOLO, MCP, or a custom engine).  Engine-specific parameters go in
    ``engine_config``; ``model`` is required for vLLM and SGLang.

    **Raw Ray Serve path** (``import_path`` set):
    Import and deploy any ``@serve.deployment``-decorated class or bound
    ``serve.Application`` from a Python module on the head node's PYTHONPATH.
    Use ``ray_actor_options`` for resource requirements.

    **Scheduling** (both paths):
    Use the ``scheduling`` field for explicit Ray actor resource constraints,
    custom placement group bundle layouts, and actor env vars (e.g.
    ``VLLM_RAY_PER_WORKER_GPUS``, ``VLLM_RAY_BUNDLE_INDICES``).  The legacy
    ``node_type`` shorthand auto-expands to
    ``scheduling.resources = {"node_type:<value>": 0.001}`` when ``scheduling``
    is not provided.

    The application name must be unique within Ray Serve.  Re-deploying with
    the same name performs a rolling update.
    """
    try:
        if request.engine_type:
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
                placement_group_bundles=None,   # resolved via scheduling
                placement_group_strategy=None,  # resolved via scheduling
                node_type=request.node_type,
                multi_node=request.multi_node,
                autoscaling_config=request.autoscaling_config,
                venv_name=request.venv_name,
                scheduling=request.scheduling,
            )
        else:
            # Raw Ray Serve import path.
            # Merge scheduling constraints into ray_actor_options before dispatch.
            actor_opts: Dict[str, Any] = dict(request.ray_actor_options or {})
            if request.scheduling:
                sc = request.scheduling
                if sc.resources:
                    actor_opts.setdefault("resources", {})
                    actor_opts["resources"].update(sc.resources)
                if sc.env_vars:
                    rt = dict(actor_opts.get("runtime_env") or {})
                    rt["env_vars"] = {**rt.get("env_vars", {}), **sc.env_vars}
                    actor_opts["runtime_env"] = rt

            result = coordinator.ray_service.deploy_application(
                name=request.name,
                import_path=request.import_path,
                route_prefix=request.route_prefix,
                num_replicas=request.num_replicas,
                ray_actor_options=actor_opts or None,
            )

        # Persist deploy intent (audit + recovery redeploy). Best-effort: a
        # store failure must not fail an otherwise-successful deployment.
        try:
            coordinator.deployment_store.record(
                name=request.name,
                route_prefix=request.route_prefix,
                engine_type=request.engine_type,
                model=request.model,
                venv_name=request.venv_name,
                num_replicas=request.num_replicas,
                tensor_parallel_size=request.tensor_parallel_size,
                request=request.model_dump(mode="json", exclude_none=True),
                deployer=identity.username,
            )
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "Failed to persist deploy intent for '%s'", request.name, exc_info=True
            )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{app_name}", response_model=Dict[str, Any], dependencies=[Depends(require_admin)])
async def delete_application(
    app_name: str,
    coordinator: CoordinatorService = Depends(get_coordinator),
):
    """Undeploy a Ray Serve application. Stops all replicas."""
    try:
        result = coordinator.ray_service.delete_application(app_name)
        coordinator.deployment_store.remove(app_name)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/intents", response_model=Dict[str, Any])
async def list_deploy_intents(coordinator: CoordinatorService = Depends(get_coordinator)):
    """Return persisted deploy intent, annotated with live-vs-drift status.

    Intent (engine_type, venv, original request, deployer) is what Ray does not
    retain; ``live`` is cross-checked against the current Ray Serve
    applications so drift (deleted out-of-band, or awaiting recovery) is
    visible. Ray remains the status authority.
    """
    try:
        try:
            live_names = {a["name"] for a in coordinator.ray_service.list_applications()}
        except Exception:
            live_names = set()
        records = coordinator.deployment_store.reconcile(live_names)
        return {"deployments": records, "total": len(records)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("", response_model=ApplicationsListResponse)
async def list_applications(coordinator: CoordinatorService = Depends(get_coordinator)):
    """List all Ray Serve applications currently deployed."""
    try:
        apps = coordinator.ray_service.list_applications()
        return ApplicationsListResponse(
            applications=[ApplicationInfo(**app) for app in apps],
            total_applications=len(apps),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{app_name}", response_model=ApplicationInfo)
async def get_application(
    app_name: str,
    coordinator: CoordinatorService = Depends(get_coordinator),
):
    """Get detailed information about a specific application."""
    try:
        app = coordinator.ray_service.get_application_status(app_name)
        if not app:
            raise HTTPException(status_code=404, detail=f"Application {app_name} not found")
        return ApplicationInfo(**app)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
