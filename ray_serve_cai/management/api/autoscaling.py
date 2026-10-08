"""Policy API; does not create workers inside request handlers."""

import time

from fastapi import APIRouter, Depends, HTTPException

from ray_serve_cai.autoscaling.policy import ClusterScalingPolicy
from ray_serve_cai.autoscaling.store import ScalingStore

from ..auth import require_admin

router = APIRouter(prefix="/api/v1/cluster/autoscaling", tags=["cluster"])


def get_store():
    return ScalingStore()


@router.get("")
def get_policy(store: ScalingStore = Depends(get_store)):
    data = store.read()
    return {"policy": data["policy"], "revision": data["revision"]}


@router.put("", dependencies=[Depends(require_admin)])
def put_policy(policy: ClusterScalingPolicy, store: ScalingStore = Depends(get_store)):
    try:
        return store.set_policy(policy)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/status")
def get_status(store: ScalingStore = Depends(get_store)):
    data = store.read()
    status = data["status"]
    fresh = time.time() - status.get("at", 0) < 30
    return {
        "revision": data["revision"],
        "mode": data["policy"]["mode"],
        "supervisor_fresh": fresh,
        "status": status,
        "workers": data["workers"],
        "admission": data.get("admission", {}),
        "consolidation": "not_implemented",
    }


@router.get("/events")
def get_events(store: ScalingStore = Depends(get_store)):
    return {"events": store.read()["events"]}
