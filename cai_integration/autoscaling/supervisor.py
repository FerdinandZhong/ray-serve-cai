"""One reconciliation loop using Ray's scheduler and CAI worker lifecycle."""

import fcntl
import os
import signal
import time
import uuid

import ray
from google.protobuf.json_format import MessageToDict
from ray._raylet import GcsClient  # type: ignore[attr-defined]  # Missing in Ray extension stubs.
from ray.autoscaler.tags import TAG_RAY_LAUNCH_REQUEST, TAG_RAY_USER_NODE_TYPE
from ray.autoscaler.v2.sdk import get_cluster_resource_state
from ray.core.generated.autoscaler_pb2 import DrainNodeReason, NodeStatus

from ray_serve_cai.autoscaling.lifecycle import lifecycle_lock
from ray_serve_cai.autoscaling.planner import plan_capacity
from ray_serve_cai.autoscaling.policy import initial_remaining
from ray_serve_cai.autoscaling.store import ScalingStore

from .provider import CAIWorkerBackend


def check_runtime():
    if ray.__version__ != "2.56.1":
        raise RuntimeError("CAI autoscaling adapter requires Ray 2.56.1")
    if os.environ.get("RAY_CAI_AUTOSCALING") != "1":
        raise RuntimeError("Head must start with RAY_CAI_AUTOSCALING=1 and no other monitor")


def reconcile(store, client, backend, *, network_ready=False):
    data = store.read()
    mode = data["policy"]["mode"]
    status = {"at": time.time(), "state": mode}
    if mode == "disabled":
        return status
    status["quota_discovery"] = "unavailable; CAI admission is authoritative"
    status["admission"] = data.get("admission", {})
    if not data["policy"]["pools"]:
        status.update(
            state="waiting_for_pools", reason="Add worker pools to enable automatic provisioning"
        )
        return status
    if mode != "observe":
        backend.recover_recorded_launches()
    cluster = get_cluster_resource_state(client)
    # Persist observations separately from intent. This also lets restart
    # distinguish never-joined launches from workers that later failed.
    joined = {n.instance_id for n in cluster.node_states if n.status != NodeStatus.DEAD}
    with store.transaction() as latest:
        for identity in joined:
            worker = latest["workers"].get(identity)
            if worker and worker["state"] == "starting":
                worker["state"] = "joined"
                store.event(latest, "worker_joined", worker_id=identity)
    data = store.read()
    reply = plan_capacity(data, cluster)
    status.update(
        resources=MessageToDict(cluster, preserving_proto_field_name=True),
        proposed_launches=[{"pool_id": r.instance_type, "count": r.count} for r in reply.to_launch],
        proposed_drains=[r.instance_id for r in reply.to_terminate],
        infeasible_gangs=len(reply.infeasible_gang_resource_requests),
        infeasible_requests=len(reply.infeasible_resource_requests),
        initial_remaining={p["id"]: initial_remaining(data, p) for p in data["policy"]["pools"]},
    )
    if mode == "observe":
        return status
    stalled = [
        identity
        for identity, worker in data["workers"].items()
        if worker["state"] in {"creating", "launch_unknown", "starting"}
        and time.time() - worker["created_at"] > data["policy"]["startup_timeout_s"]
    ]
    if stalled:
        status.update(
            state="blocked",
            reason="Worker startup deadline exceeded; reconcile uncertain launches",
            workers=stalled,
        )
        return status
    # Initial resources were explicitly requested by the cluster creator. They
    # may be provisioned before the operator verifies GPU networking. This is a
    # one-time request, not a minimum or a bypass for future demand growth.
    initial = [(pool, initial_remaining(data, pool)) for pool in data["policy"]["pools"]]
    initial = [(pool, count) for pool, count in initial if count]
    status["initial_remaining"] = {p["id"]: count for p, count in initial}
    if initial:
        budget = data["policy"]["max_launch_batch"]
        for pool, count in initial:
            if budget <= 0:
                break
            if data.get("admission", {}).get(pool["id"], {}).get("retry_at", 0) > time.time():
                continue
            count = min(count, budget)
            backend.launch_batch(
                {"worker_spec": pool["worker_spec"]},
                {TAG_RAY_LAUNCH_REQUEST: uuid.uuid4().hex, TAG_RAY_USER_NODE_TYPE: pool["id"]},
                count,
                purpose="initial",
            )
            budget -= count
        status["state"] = "initializing_pools"
        return status
    launch_budget = data["policy"]["max_launch_batch"]
    for request in reply.to_launch:
        if launch_budget <= 0:
            break
        pool = next((p for p in data["policy"]["pools"] if p["id"] == request.instance_type), None)
        if pool is None:
            raise RuntimeError("Planner attempted to provision an unmanaged node shape")
        if data.get("admission", {}).get(pool["id"], {}).get("retry_at", 0) > time.time():
            status.update(
                state="waiting_for_admission",
                reason="CAI rejected a create; retry backoff is active",
            )
            continue
        if pool["worker_spec"].get("gpus", 0) and not network_ready:
            status.update(
                state="blocked",
                reason="Network readiness for newly created GPU pods has not been configured",
            )
            continue
        backend.launch_batch(
            {"worker_spec": pool["worker_spec"]},
            {TAG_RAY_LAUNCH_REQUEST: uuid.uuid4().hex, TAG_RAY_USER_NODE_TYPE: pool["id"]},
            min(request.count, launch_budget),
        )
        launch_budget -= min(request.count, launch_budget)
    if mode == "full":
        for request in reply.to_terminate:
            identity = request.instance_id
            if identity not in data["workers"]:
                continue
            # GCS does the atomic idle check; a previous snapshot isn't a fence.
            with lifecycle_lock(store.path.parent):
                backend._check_recovery()
                if store.read()["policy"]["mode"] != "full":
                    break
                nodes = [
                    n
                    for n in cluster.node_states
                    if n.instance_id == identity and n.status != NodeStatus.DEAD
                ]
                if len(nodes) != 1 or nodes[0].status != NodeStatus.IDLE:
                    continue
                current_worker = backend.prepare_retirement(identity)
                with store.transaction() as latest:
                    latest["workers"][identity]["state"] = "drain_requested"
                accepted, reason = client.drain_node(
                    nodes[0].node_id.hex(),
                    DrainNodeReason.DRAIN_NODE_REASON_IDLE_TERMINATION,
                    "CAI autoscaling idle worker",
                    0,
                )
                with store.transaction() as latest:
                    store.event(
                        latest,
                        "idle_drain_accepted" if accepted else "idle_drain_rejected",
                        worker_id=identity,
                        reason=reason,
                    )
                    if accepted:
                        latest["workers"][identity]["state"] = "draining"
                    else:
                        latest["workers"][identity]["state"] = "joined"
                        backend.service.cancel_worker_retirement(current_worker)
        for identity, worker in store.read()["workers"].items():
            if worker["state"] not in {
                "drain_requested",
                "draining",
                "terminating",
                "joined",
                "starting",
            }:
                continue
            nodes = [n for n in cluster.node_states if n.instance_id == identity]
            if nodes and all(n.status == NodeStatus.DEAD for n in nodes):
                backend.terminate_node(identity)
    return status


def run():
    store = ScalingStore()
    store.path.parent.mkdir(parents=True, exist_ok=True)
    lock = store.path.with_suffix(".leader.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError("Another autoscaling supervisor holds the leader lock") from exc
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    backend = None
    try:
        while not stopping:
            try:
                data = store.read()
                if data["policy"]["mode"] == "disabled":
                    status = {"at": time.time(), "state": "disabled"}
                else:
                    check_runtime()
                    gcs = (store.path.parent / "ray_gcs_address").read_text().strip()
                    if backend is None:
                        backend = CAIWorkerBackend(
                            {"state_path": str(store.path), "gcs_address": gcs}, data["cluster_id"]
                        )
                    status = reconcile(
                        store,
                        GcsClient(address=gcs),
                        backend,
                        network_ready=os.environ.get("RAY_AUTOSCALING_NETWORK_READY") == "1",
                    )
            except Exception as exc:
                status = {
                    "at": time.time(),
                    "state": "blocked",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            with store.transaction() as data:
                previous = data["status"]
                if (previous.get("state"), previous.get("reason")) != (
                    status.get("state"),
                    status.get("reason"),
                ):
                    store.event(
                        data, "supervisor_state", state=status["state"], reason=status.get("reason")
                    )
                data["status"] = status
            for _ in range(5):
                if stopping:
                    break
                time.sleep(1)
    finally:
        lock.close()


if __name__ == "__main__":
    run()
