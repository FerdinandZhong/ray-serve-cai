"""Exercise Ray's real scheduler with synthetic resources, no cloud mutations."""

import pytest
from ray.autoscaler.v2.instance_manager.config import NodeTypeConfig
from ray.autoscaler.v2.scheduler import ResourceDemandScheduler, SchedulingRequest
from ray.autoscaler.v2.schema import AutoscalerInstance
from ray.autoscaler.v2.utils import ResourceRequestUtil
from ray.core.generated.autoscaler_pb2 import GangResourceRequest, NodeState, NodeStatus
from ray.core.generated.instance_manager_pb2 import Instance, NodeKind

SHAPE = {"CPU": 16, "GPU": 1, "accelerator_type:L40S": 1, "node_type:gpu-worker": 1}


def node(index, *, pending=False, manual=False):
    identity = str(index)
    im = (
        None
        if manual
        else Instance(
            instance_id=identity,
            cloud_instance_id=identity,
            instance_type="l40s",
            status=Instance.ALLOCATED if pending else Instance.RAY_RUNNING,
            node_kind=NodeKind.WORKER,
        )
    )
    live = (
        None
        if pending
        else NodeState(
            node_id=identity.encode(),
            instance_id=identity,
            ray_node_type_name="manual" if manual else "l40s",
            status=NodeStatus.RUNNING,
            total_resources=SHAPE,
            available_resources=SHAPE,
        )
    )
    return AutoscalerInstance(cloud_instance_id=identity, im_instance=im, ray_node=live)


def schedule(existing, *, strict=False, accelerator="L40S"):
    bundles = [
        {"CPU": 4},
        {"GPU": 1, f"accelerator_type:{accelerator}": 0.001},
        {"GPU": 1, f"accelerator_type:{accelerator}": 0.001},
    ]
    constraints = (
        [(ResourceRequestUtil.PlacementConstraintType.AFFINITY, "pg", "replica")]
        if strict
        else None
    )
    gang = GangResourceRequest(requests=[ResourceRequestUtil.make(b, constraints) for b in bundles])
    return ResourceDemandScheduler().schedule(
        SchedulingRequest(
            disable_launch_config_check=True,
            node_type_configs={
                "l40s": NodeTypeConfig(
                    name="l40s", min_worker_nodes=0, max_worker_nodes=4, resources=SHAPE
                )
            },
            max_num_nodes=4,
            current_instances=existing,
            gang_resource_requests=[gang],
        )
    )


@pytest.mark.parametrize(
    "existing,expected",
    [([], 2), ([node(1)], 1), ([node(1), node(2)], 0), ([node(1), node(2, pending=True)], 0)],
)
def test_reuses_capacity_and_launch_commitments_for_whole_tp_replica(existing, expected):
    reply = schedule(existing)
    assert sum(r.count for r in reply.to_launch) == expected
    assert not reply.infeasible_gang_resource_requests
    assert not reply.to_terminate


def test_strict_pack_cannot_be_satisfied_by_two_separate_one_gpu_workers():
    reply = schedule([], strict=True)
    assert reply.infeasible_gang_resource_requests
    assert not reply.to_launch


def test_wrong_accelerator_is_not_free_compatible_capacity():
    reply = schedule([node(1), node(2)], accelerator="H100")
    assert reply.infeasible_gang_resource_requests
    assert not reply.to_launch


def test_inventory_adapter_counts_unmanaged_capacity_without_adopting_it():
    from ray.core.generated.autoscaler_pb2 import ClusterResourceState

    from ray_serve_cai.autoscaling.planner import plan_capacity
    from ray_serve_cai.autoscaling.policy import ClusterScalingPolicy

    policy = ClusterScalingPolicy(
        enabled=True,
        mode="full",
        max_workers=2,
        max_gpus=2,
        pools=[
            {
                "id": "l40s",
                "max_workers": 2,
                "worker_spec": {
                    "cpu": 16,
                    "memory": 64,
                    "gpus": 1,
                    "node_type": "gpu-worker",
                    "accelerator_type": "L40S",
                },
            }
        ],
    )
    gang = GangResourceRequest(
        requests=[
            ResourceRequestUtil.make(b)
            for b in [
                {"CPU": 4},
                {"GPU": 1, "accelerator_type:L40S": 0.001},
                {"GPU": 1, "accelerator_type:L40S": 0.001},
            ]
        ]
    )
    cluster = ClusterResourceState(
        node_states=[node(1, manual=True).ray_node, node(2, manual=True).ray_node],
        pending_gang_resource_requests=[gang],
    )
    cluster.node_states.append(
        NodeState(
            node_id=b"head",
            status=NodeStatus.RUNNING,
            total_resources={"CPU": 4, "node:__internal_head__": 1},
            available_resources={"CPU": 4, "node:__internal_head__": 1},
        )
    )
    data = {"policy": policy.model_dump(), "workers": {}}
    reply = plan_capacity(data, cluster)
    assert not reply.to_launch
    assert not reply.to_terminate
    assert not reply.infeasible_gang_resource_requests
    assert data["workers"] == {}


@pytest.mark.parametrize("cap,expected", [(None, 12), (2, 2), (0, 0)])
def test_uncapped_pools_plan_from_demand_and_optional_budget(cap, expected):
    from ray.core.generated.autoscaler_pb2 import ClusterResourceState, ResourceRequestByCount

    from ray_serve_cai.autoscaling.planner import plan_capacity
    from ray_serve_cai.autoscaling.policy import ClusterScalingPolicy

    policy = ClusterScalingPolicy(
        enabled=True,
        mode="full",
        max_gpus=cap,
        pools=[{"id": "gpu", "worker_spec": {"cpu": 4, "memory": 8, "gpus": 1}}],
    )
    cluster = ClusterResourceState(
        pending_resource_requests=[
            ResourceRequestByCount(request=ResourceRequestUtil.make({"GPU": 1}), count=12)
        ]
    )
    reply = plan_capacity({"policy": policy.model_dump(), "workers": {}}, cluster)
    assert sum(r.count for r in reply.to_launch) == expected
    cluster.ClearField("pending_resource_requests")
    assert not plan_capacity({"policy": policy.model_dump(), "workers": {}}, cluster).to_launch
