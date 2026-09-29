"""Real local CPU Serve/worker autoscaling; only CAI application operations are replaced.

Opt in with RAY_LOCAL_AUTOSCALING_TEST=1. At most two one-CPU workers,
zero GPUs, 80 MiB object store per node. No CAI calls or credentials.
"""

import os
import time
from pathlib import Path

import pytest


@pytest.mark.skipif(
    os.environ.get("RAY_LOCAL_AUTOSCALING_TEST") != "1", reason="Opt-in local Ray processes"
)
def test_small_cpu_cluster_scales_real_serve_demand(tmp_path, monkeypatch):
    import asyncio

    import ray
    import requests
    from ray import serve
    from ray._raylet import GcsClient
    from ray.autoscaler.v2.sdk import get_cluster_resource_state
    from ray.cluster_utils import Cluster
    from ray.core.generated.autoscaler_pb2 import DrainNodeReason, NodeStatus

    from cai_integration.autoscaling.bootstrap import initialize_scaling
    from cai_integration.autoscaling.provider import CAIWorkerBackend
    from cai_integration.autoscaling.supervisor import reconcile
    from ray_serve_cai.autoscaling.policy import ClusterScalingPolicy
    from ray_serve_cai.autoscaling.store import ScalingStore

    assert ray.__version__ == "2.56.1"
    cluster = Cluster()
    release = tmp_path / "release-requests"
    started = time.monotonic()

    def checkpoint(message):
        print(f"LOCAL CPU [{time.monotonic() - started:.1f}s]: {message}", flush=True)

    class LocalApplications:
        """Replacement for the CAI API boundary, with real worker processes."""

        def __init__(self):
            self.records = {}
            self.nodes = {}
            self.created = 0
            self.attempts = 0
            self.deleted = 0
            self.reject_next = False

        def create_worker_node(self, **spec):
            assert spec["cpu"] == 1 and spec["gpus"] == 0
            self.attempts += 1
            if self.reject_next:
                self.reject_next = False
                response = requests.Response()
                response.status_code = 429
                raise requests.HTTPError("Simulated admission rejection", response=response)
            assert len(self.nodes) < 2, "Test must never exceed two local CPU workers"
            identity = spec["_worker_id"]
            ownership = spec["_autoscaling"]
            # Ray reads cloud identity while constructing its start command.
            with monkeypatch.context() as env:
                env.setenv("RAY_CLOUD_INSTANCE_ID", identity)
                env.setenv("RAY_NODE_TYPE_NAME", ownership["pool_id"])
                node = cluster.add_node(
                    num_cpus=1,
                    num_gpus=0,
                    memory=128 * 1024**2,
                    object_store_memory=80 * 1024**2,
                    resources={"node_type:cpu-worker": 1},
                )
            self.nodes[identity] = node
            self.records[identity] = {
                "worker_id": identity,
                "app_id": identity,
                "autoscaling": ownership,
            }
            self.created += 1
            return {"app_id": identity}

        def worker_records(self):
            return self.records.copy()

        def prepare_worker_retirement(self, record):
            assert record["app_id"] in self.nodes

        def cancel_worker_retirement(self, record):
            pass

        def list_applications(self):
            return [{"id": key} for key in self.nodes]

        def delete_application(self, identity):
            state = get_cluster_resource_state(client)
            nodes = [n for n in state.node_states if n.instance_id == identity]
            assert nodes and all(n.status == NodeStatus.DEAD for n in nodes)
            cluster.remove_node(self.nodes.pop(identity))
            self.deleted += 1

        def forget_worker(self, identity):
            self.records.pop(identity)

    try:
        cluster.add_node(
            num_cpus=0,
            num_gpus=0,
            memory=128 * 1024**2,
            object_store_memory=80 * 1024**2,
            include_dashboard=False,
            no_monitor=True,
            _system_config={"enable_autoscaler_v2": True},
        )
        ray.init(address=cluster.address, namespace="serve", log_to_driver=False)
        client = GcsClient(address=cluster.address)
        config = {
            "worker_runtime_identifier": "local-test-only",
            "worker_pools": [
                {
                    "id": "cpu",
                    "initial_workers": 1,
                    "min_workers": 1,
                    "idle_timeout_s": 60,
                    "worker_spec": {"cpu": 1, "memory": 4, "gpus": 0, "node_type": "cpu-worker"},
                }
            ],
        }
        store = ScalingStore(tmp_path / "scaling.json")
        initialize_scaling(config, store.path)
        assert store.read()["policy"]["max_workers"] is None
        service = LocalApplications()

        def new_backend():
            return CAIWorkerBackend(
                {"state_path": str(store.path), "gcs_address": cluster.address},
                store.read()["cluster_id"],
                service=service,
            )

        backend = new_backend()

        def tick():
            try:
                status = reconcile(store, client, backend, network_ready=False)
            except requests.HTTPError as exc:
                assert exc.response.status_code == 429
                status = {"state": "admission_rejected"}
            return status

        def until(predicate, message, timeout=60):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                status = tick()
                if predicate(status):
                    checkpoint(message)
                    return
                time.sleep(1)
            pytest.fail(f"Timed out: {message}; journal={store.read()}; last={status}")

        def update_policy(**changes):
            value = store.read()["policy"]
            value.update(changes)
            store.set_policy(ClusterScalingPolicy(**value))

        until(lambda _: service.created == 1, "initial pool created one CPU worker")
        until(
            lambda _: any(w["state"] == "joined" for w in store.read()["workers"].values()),
            "worker identity joined real GCS",
        )
        assert ray.cluster_resources().get("GPU", 0) == 0

        @serve.deployment(
            ray_actor_options={"num_cpus": 1, "resources": {"node_type:cpu-worker": 0.001}},
            autoscaling_config={
                "min_replicas": 1,
                "max_replicas": 2,
                "target_ongoing_requests": 1,
                "metrics_interval_s": 1,
                "look_back_period_s": 2,
                "upscale_delay_s": 0,
                "downscale_delay_s": 2,
            },
            max_ongoing_requests=2,
            graceful_shutdown_timeout_s=10,
        )
        class TinyService:
            def ping(self):
                return ray.get_runtime_context().get_node_id()

            async def __call__(self, release_path):
                while not Path(release_path).exists():
                    await asyncio.sleep(0.1)
                return ray.get_runtime_context().get_node_id()

        serve.start(http_options={"location": "HeadOnly", "host": "127.0.0.1", "port": 0})
        handle = serve.run(TinyService.bind(), name="tiny-cpu", route_prefix="/tiny")
        assert handle.ping.remote().result(timeout_s=30)
        tick()
        assert service.created == 1
        checkpoint("Serve replica reused initial worker")

        update_policy(mode="observe")
        responses = [handle.remote(str(release)) for _ in range(4)]
        until(
            lambda status: bool(status.get("proposed_launches")),
            "observe mode detected real Serve demand without creating a worker",
        )
        assert service.created == 1
        update_policy(mode="disabled", enabled=False)
        assert tick()["state"] == "disabled"
        assert service.created == 1
        update_policy(mode="full", enabled=True, max_workers=1)
        assert not tick()["proposed_launches"]
        assert service.created == 1
        checkpoint("disabled mode and optional one-worker budget prevented growth")

        update_policy(max_workers=None)
        service.reject_next = True
        until(
            lambda _: bool(store.read().get("admission")),
            "simulated rejection persisted retry backoff",
        )
        attempts = service.attempts
        backend = new_backend()  # A controller restart retains the backoff.
        for _ in range(3):
            tick()
            time.sleep(1)
        assert service.attempts == attempts
        until(
            lambda _: service.created == 2, "CPU demand grew cluster to two workers after backoff"
        )

        # Wait until both real replicas are serving the blocked requests.
        def two_replicas(_):
            deployment = serve.status().applications["tiny-cpu"].deployments["TinyService"]
            return (
                sum(
                    count
                    for state, count in deployment.replica_states.items()
                    if str(state).endswith("RUNNING")
                )
                == 2
            )

        until(two_replicas, "two live Serve replicas are running")
        release.touch()
        node_ids = [response.result(timeout_s=30) for response in responses]
        assert len(set(node_ids)) == 2
        checkpoint("four requests completed across both CPU workers")
        for node in service.nodes.values():
            accepted, _ = client.drain_node(
                node.node_id,
                DrainNodeReason.DRAIN_NODE_REASON_IDLE_TERMINATION,
                "must not drain a live replica",
                0,
            )
            assert not accepted
        checkpoint("Ray refused idle drain while replicas occupied the workers")

        # Real Serve downscaling and the real 60-second worker idle timeout.
        until(
            lambda _: service.deleted == 1,
            "Serve scaled down and controller retired one idle worker",
            timeout=130,
        )
        assert len(service.nodes) == 1
        assert handle.ping.remote().result(timeout_s=30)
        serve.delete("tiny-cpu")

        # Keep the empty worker beyond its actual idle timeout to verify the
        # retained minimum, rather than checking immediately after deletion.
        def baseline_is_idle(_):
            state = get_cluster_resource_state(client)
            return any(
                n.instance_id in service.nodes
                and n.status == NodeStatus.IDLE
                and n.idle_duration_ms >= 65000
                for n in state.node_states
            )

        until(
            baseline_is_idle,
            "minimum retained an empty worker beyond its idle timeout",
            timeout=100,
        )
        assert len(service.nodes) == 1 and service.deleted == 1
        value = store.read()["policy"]
        value["pools"][0]["min_workers"] = 0
        store.set_policy(ClusterScalingPolicy(**value))
        until(
            lambda _: service.deleted == 2,
            "lowered minimum allowed final idle worker retirement",
            timeout=100,
        )
        initialize_scaling(config, store.path)
        backend = new_backend()
        for _ in range(3):
            tick()
            time.sleep(1)
        assert service.created == 2 and service.nodes == {}
        assert ray.cluster_resources().get("CPU", 0) == 0
        checkpoint("bootstrap/controller restart did not recreate the one-time initial pool")
    finally:
        release.touch()
        try:
            if ray.is_initialized():
                serve.shutdown()
        finally:
            ray.shutdown()
            remaining_nodes = cluster.list_all_nodes()
            cluster.shutdown()
            assert all(not node.any_processes_alive() for node in remaining_nodes)
