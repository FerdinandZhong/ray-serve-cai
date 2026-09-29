"""Translate new-cluster configuration into the shared scaling policy."""

from ray_serve_cai.autoscaling.policy import ClusterScalingPolicy
from ray_serve_cai.autoscaling.store import ScalingStore


def bootstrap_policy(config):
    settings = dict(config.get("autoscaling") or {})
    enabled = settings.pop("enabled", True)
    limits = settings.pop("limits", None) or {}
    if not isinstance(limits, dict):
        raise ValueError("autoscaling.limits must be an object or null")
    allowed = {"max_workers", "max_gpus", "max_cpus", "max_memory_gb"}
    if set(limits) - allowed:
        raise ValueError("Unknown autoscaling limit")
    mode = settings.pop("mode", "full" if enabled else "disabled")
    pools = config.get("worker_pools") or []
    if pools and any(g.get("count", 0) for g in config.get("worker_groups") or []):
        raise ValueError("Use worker_pools or legacy counted worker_groups, not both")
    policy = ClusterScalingPolicy(enabled=enabled, mode=mode, pools=pools, **limits, **settings)
    for pool in policy.pools:
        if not pool.worker_spec.runtime_identifier:
            pool.worker_spec.runtime_identifier = config.get("worker_runtime_identifier")
        if not pool.worker_spec.runtime_identifier:
            raise ValueError("An initial worker pool requires a resolved worker runtime")
    return policy


def initialize_scaling(config, path=None):
    # Retry/recovery must never reset policy, initial counts, or ownership.
    return ScalingStore(path).set_policy(bootstrap_policy(config), initialize_only=True)
