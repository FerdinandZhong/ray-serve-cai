"""Ray's bundle scheduler with complete inventory, including manual capacity.

Synthetic planning records do not adopt workers or write scheduling state to Ray.
"""

import copy

from ray.autoscaler.v2.instance_manager.config import NodeTypeConfig
from ray.autoscaler.v2.scheduler import ResourceDemandScheduler, SchedulingRequest
from ray.autoscaler.v2.schema import AutoscalerInstance
from ray.core.generated.autoscaler_pb2 import NodeStatus
from ray.core.generated.instance_manager_pb2 import Instance, NodeKind

from .policy import ClusterScalingPolicy, active_workers, worker_resources


def plan_capacity(data, cluster_state):
    policy = ClusterScalingPolicy(**data["policy"])
    active = active_workers(data)
    # Ray requires finite planning bounds. Recompute an upper bound from unmet
    # demand each cycle; this is not a configured or persistent pool ceiling.
    demand = sum(r.count for r in cluster_state.pending_resource_requests)
    demand += sum(len(g.requests) for g in cluster_state.pending_gang_resource_requests)
    demand += sum(
        r.count for c in cluster_state.cluster_resource_constraints for r in c.resource_requests
    )
    types = {}
    for pool in policy.pools:
        current = sum(w["pool_id"] == pool.id for w in active)
        ceiling = (
            pool.max_workers
            if pool.max_workers is not None
            else current + demand + pool.min_workers
        )
        for resource, cap in (
            (None, policy.max_workers),
            ("gpus", policy.max_gpus),
            ("cpu", policy.max_cpus),
            ("memory", policy.max_memory_gb),
        ):
            size = (getattr(pool.worker_spec, resource) or 0) if resource else 1
            if cap is not None and size:
                used = sum((w["spec"].get(resource, 0) or 0) if resource else 1 for w in active)
                ceiling = min(ceiling, current + max(0, (cap - used) // size))
        types[pool.id] = NodeTypeConfig(
            name=pool.id,
            min_worker_nodes=pool.min_workers,
            max_worker_nodes=max(ceiling, current, pool.min_workers),
            idle_timeout_s=pool.idle_timeout_s if policy.mode == "full" else 10**12,
            resources=worker_resources(pool.worker_spec),
        )
    instances = []
    observed = set()
    for original in cluster_state.node_states:
        if original.status == NodeStatus.DEAD:
            continue
        node = copy.deepcopy(original)
        record = data["workers"].get(node.instance_id)
        owned = record is not None and record["state"] not in {
            "terminated",
            "terminating",
            "rejected",
        }
        identity = node.instance_id if owned else "unmanaged-" + node.node_id.hex()
        kind = (
            NodeKind.HEAD if "node:__internal_head__" in node.total_resources else NodeKind.WORKER
        )
        if owned:
            node_type = record["pool_id"]
            observed.add(identity)
        else:
            node_type = identity
            types[node_type] = NodeTypeConfig(
                name=node_type,
                min_worker_nodes=1,
                max_worker_nodes=1,
                idle_timeout_s=10**12,
                resources=dict(node.total_resources),
            )
        node.ray_node_type_name = node_type
        node.instance_id = identity
        instances.append(
            AutoscalerInstance(
                cloud_instance_id=identity,
                ray_node=node,
                im_instance=Instance(
                    instance_id=identity,
                    cloud_instance_id=identity,
                    instance_type=node_type,
                    status=Instance.RAY_RUNNING,
                    node_kind=kind,
                ),
            )
        )
    for identity, record in data["workers"].items():
        if identity in observed or record["state"] in {"terminated", "terminating", "rejected"}:
            continue
        # Unresolved launches are commitments and block further CAI provisioning.
        if any(
            n.instance_id == identity and n.status == NodeStatus.DEAD
            for n in cluster_state.node_states
        ):
            continue
        instances.append(
            AutoscalerInstance(
                cloud_instance_id=identity,
                im_instance=Instance(
                    instance_id=identity,
                    cloud_instance_id=identity,
                    instance_type=record["pool_id"],
                    status=Instance.ALLOCATED,
                    node_kind=NodeKind.WORKER,
                ),
            )
        )
    return ResourceDemandScheduler().schedule(
        SchedulingRequest(
            disable_launch_config_check=True,
            node_type_configs=types,
            current_instances=instances,
            resource_requests=list(cluster_state.pending_resource_requests),
            gang_resource_requests=list(cluster_state.pending_gang_resource_requests),
            cluster_resource_constraints=list(cluster_state.cluster_resource_constraints),
        )
    )
