"""Worker capacity policy. Replica targets remain owned by Ray Serve."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ray_serve_cai.management.models.requests import AddNodeRequest


class WorkerPool(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    min_workers: int = Field(default=0, ge=0)
    initial_workers: int = Field(default=0, ge=0)
    max_workers: int | None = Field(default=None, ge=0)
    idle_timeout_s: int = Field(default=900, ge=60)
    worker_spec: AddNodeRequest

    @model_validator(mode="after")
    def check_pool(self):
        if (
            self.max_workers is not None
            and max(self.min_workers, self.initial_workers) > self.max_workers
        ):
            raise ValueError("initial_workers and min_workers must not exceed max_workers")
        if self.worker_spec.cpu is None or self.worker_spec.memory is None:
            raise ValueError("Autoscaling pools require explicit cpu and memory")
        reserved = {"CPU", "GPU", "memory", "object_store_memory"}
        if reserved.intersection(self.worker_spec.ray_labels or {}):
            raise ValueError("ray_labels must not override CPU, GPU, or memory capacity")
        if self.id == "head":
            raise ValueError("Pool ID head is reserved")
        return self


class ClusterScalingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    mode: Literal["disabled", "observe", "scale_up_only", "full"] = "disabled"
    max_workers: int | None = Field(default=None, ge=0)
    max_gpus: int | None = Field(default=None, ge=0)
    max_cpus: int | None = Field(default=None, ge=0)
    max_memory_gb: int | None = Field(default=None, ge=0)
    startup_timeout_s: int = Field(default=1800, ge=60)
    max_launch_batch: int = Field(default=2, ge=1, le=1000)
    pools: list[WorkerPool] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_limits(self):
        if self.enabled != (self.mode != "disabled"):
            raise ValueError("enabled must be false exactly when mode is disabled")
        if len({p.id for p in self.pools}) != len(self.pools):
            raise ValueError("Pool IDs must be unique")
        # Pool maxima need not add up to a global cap. The backend admits each
        # launch against actual commitments. Only requested baseline capacity
        # must fit optional budgets before accepting the policy.
        baselines = [(p, max(p.initial_workers, p.min_workers)) for p in self.pools]
        budgets = {
            "workers": (sum(n for p, n in baselines), self.max_workers),
            "gpus": (sum(n * (p.worker_spec.gpus or 0) for p, n in baselines), self.max_gpus),
            "cpus": (sum(n * p.worker_spec.cpu for p, n in baselines), self.max_cpus),
            "memory_gb": (sum(n * p.worker_spec.memory for p, n in baselines), self.max_memory_gb),
        }
        for key, (total, limit) in budgets.items():
            if limit is not None and total > limit:
                raise ValueError(f"Initial/minimum pool capacity exceeds max_{key}")
        return self


def worker_resources(spec: AddNodeRequest) -> dict:
    """Match launcher's explicit logical CPU/GPU/custom resources.

    Do not advertise pod RAM as Ray schedulable memory: object store/runtime
    overhead makes them different. Memory-constrained pending tasks therefore
    require a separately calibrated provider shape, not a guessed value.
    """
    resources = {
        "CPU": spec.cpu,
        "GPU": spec.gpus or 0,
        f"node_type:{spec.node_type or 'worker'}": 1,
    }
    if spec.accelerator_type:
        resources[f"accelerator_type:{spec.accelerator_type}"] = 1
    for key, value in (spec.node_label or {}).items():
        resources[f"{key.split('/')[-1]}:{value}"] = 1
    resources.update(spec.ray_labels or {})
    return resources


def active_workers(data):
    return [w for w in data["workers"].values() if w["state"] not in {"terminated", "rejected"}]


def initial_remaining(data, pool):
    issued = sum(
        w["pool_id"] == pool["id"] and w.get("purpose") == "initial" and w["state"] != "rejected"
        for w in data["workers"].values()
    )
    return max(0, pool.get("initial_workers", 0) - issued)
