"""Opt-in real local Ray drain test, no CAI applications or hardware GPUs.

RAY_LOCAL_AUTOSCALING_TEST=1 python -m pytest tests/test_autoscaling_local_ray.py
"""

import os
import time

import pytest


@pytest.mark.skipif(
    os.environ.get("RAY_LOCAL_AUTOSCALING_TEST") != "1", reason="Opt-in local Ray processes"
)
def test_real_ray_rejects_busy_drain_then_stops_idle_worker():
    import ray
    from ray._raylet import GcsClient
    from ray.cluster_utils import Cluster
    from ray.core.generated.autoscaler_pb2 import DrainNodeReason
    from ray.util.placement_group import placement_group, remove_placement_group

    cluster = Cluster()
    try:
        cluster.add_node(
            num_cpus=0,
            include_dashboard=False,
            object_store_memory=80 * 1024**2,
            _system_config={"enable_autoscaler_v2": True},
        )
        worker = cluster.add_node(num_cpus=1, object_store_memory=80 * 1024**2)
        ray.init(address=cluster.address, namespace="autoscaling-drain-smoke")

        @ray.remote(num_cpus=1)
        class Busy:
            def ready(self):
                return True

        actor = Busy.remote()
        assert ray.get(actor.ready.remote(), timeout=30)
        client = GcsClient(address=cluster.address)
        reason = DrainNodeReason.DRAIN_NODE_REASON_IDLE_TERMINATION
        accepted, _ = client.drain_node(worker.node_id, reason, "test busy", 0)
        assert accepted is False
        ray.kill(actor)
        del actor
        # An empty placement group still reserves capacity for a whole replica.
        # Removing the parent actor alone is not sufficient proof of emptiness.
        group = placement_group([{"CPU": 1}], strategy="STRICT_PACK")
        ray.get(group.ready(), timeout=30)
        accepted, _ = client.drain_node(worker.node_id, reason, "test reserved bundle", 0)
        assert accepted is False
        remove_placement_group(group)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            accepted, _ = client.drain_node(worker.node_id, reason, "test idle", 0)
            if accepted:
                break
            time.sleep(0.5)
        assert accepted
        while time.monotonic() < deadline:
            if any(n["NodeID"] == worker.node_id and not n["Alive"] for n in ray.nodes()):
                break
            time.sleep(0.5)
        else:
            pytest.fail("Accepted idle drain did not stop the worker")
    finally:
        ray.shutdown()
        cluster.shutdown()
