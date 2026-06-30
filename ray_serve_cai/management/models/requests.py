"""Request models for management API."""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, field_validator


class AddNodeRequest(BaseModel):
    """Request to add a new worker node to the cluster."""

    node_type: str = Field(default="worker", description="Type of node (worker, gpu-worker)")
    cpu: Optional[int] = Field(default=None, ge=1, le=256, description="CPU cores override (uses group default when omitted)")
    memory: Optional[int] = Field(default=None, ge=4, le=1024, description="Memory in GB override (uses group default when omitted)")
    gpus: Optional[int] = Field(default=None, ge=0, description="GPU count override (uses group default when omitted)")
    runtime_identifier: Optional[str] = Field(
        default=None,
        description="Docker runtime identifier override (uses group default from ray_cluster_info.json when omitted)",
    )
    node_label: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Kubernetes node-selector label(s) used to steer the worker pod onto a "
            "specific K8s node.  The first entry is used as NODE_SELECTOR_KEY/VALUE "
            "for CML pod placement (K8s layer).  Each entry is also auto-derived into "
            "a short-key Ray resource label so actors can target the same node "
            "(Ray layer) — e.g. 'liftie.cloudera.com/instance-group-id': 'ig-n4bsnv8r' "
            "becomes Ray resource 'instance-group-id:ig-n4bsnv8r=1'. "
            "Overrides the node_label baked into the worker group at cluster-start time. "
            "The correct label key depends on your infra provider: "
            "Cloudera/Liftie uses 'liftie.cloudera.com/instance-group-id', "
            "EKS uses 'node.kubernetes.io/instance-type', "
            "NVIDIA GFD uses 'nvidia.com/gpu.product'."
        ),
    )
    ray_labels: Optional[Dict[str, float]] = Field(
        default=None,
        description=(
            "Additional Ray resource labels merged into ray start --resources "
            "on top of the auto-derived ones.  Values must be >= 0. "
            "Use for ad-hoc actor scheduling affinity not covered by node_label "
            "auto-derivation, e.g. {\"zone:us-east-1d\": 1}."
        ),
    )

    class Config:
        json_schema_extra = {
            "example": {
                "node_type": "l40-gpu-worker",
                "cpu": 16,
                "memory": 64,
                "gpus": 1,
                "node_label": {
                    "liftie.cloudera.com/instance-group-id": "ig-n4bsnv8r",
                },
                "ray_labels": {
                    "zone:us-east-1d": 1,
                },
            }
        }


class LaunchCaiApplicationRequest(BaseModel):
    """Request to launch a generic CML application."""

    name: str = Field(..., description="CML application name")
    script: str = Field(..., description="Script path to run (relative to /home/cdsw)")
    cpu: int = Field(..., ge=1, le=256, description="CPU cores")
    memory: int = Field(..., ge=1, le=1024, description="Memory in GB")
    gpus: int = Field(default=0, ge=0, description="Number of GPUs")
    runtime_identifier: Optional[str] = Field(
        default=None,
        description="Docker runtime identifier (uses cluster default when omitted)",
    )
    environment: Optional[Dict[str, str]] = Field(
        default=None,
        description="Environment variables injected into the application",
    )
    bypass_authentication: bool = Field(
        default=True,
        description="Allow unauthenticated access to the application",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "my-ray-app",
                "script": "my_ray_app.py",
                "cpu": 4,
                "memory": 16,
                "gpus": 1,
                "environment": {"MY_VAR": "value"},
            }
        }


class DeployApplicationRequest(BaseModel):
    """Request to deploy a Ray Serve application."""

    name: str = Field(..., description="Application name")
    import_path: str = Field(..., description="Import path to the application (e.g., 'myapp:app')")
    route_prefix: str = Field(default="/", description="HTTP route prefix for the application")
    num_replicas: int = Field(default=1, ge=1, description="Number of replicas")
    ray_actor_options: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Ray actor options (num_cpus, num_gpus, etc.)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "name": "my-service",
                "import_path": "my_module:deployment",
                "route_prefix": "/api",
                "num_replicas": 2,
                "ray_actor_options": {"num_cpus": 1}
            }
        }


class DeployModelRequest(BaseModel):
    """Request to deploy an engine as a Ray Serve application."""

    name: str = Field(..., description="Application name (used as the Ray Serve app name)")
    engine_type: str = Field(
        default="vllm",
        description="Inference engine: 'vllm', 'sglang', 'litellm', 'yolo', 'mcp', or a custom type",
    )
    model: Optional[str] = Field(
        default=None,
        description=(
            "HuggingFace model ID or local path. Required for vLLM and SGLang. "
            "Not needed for LiteLLM, MCP, YOLO, or custom engines (use engine_config instead)."
        ),
    )
    route_prefix: str = Field(
        default="/",
        description="HTTP route prefix — all OpenAI-compatible endpoints are served under this prefix",
    )
    num_replicas: int = Field(default=1, ge=1, description="Number of Ray Serve replicas")
    tensor_parallel_size: int = Field(
        default=1,
        ge=1,
        description="Number of GPUs to use for tensor parallelism within each replica",
    )
    use_cpu: bool = Field(
        default=False,
        description="Run inference on CPU (no GPU required; slower, for testing only)",
    )
    gpu_fraction: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Fractional GPU resource allocated per replica (e.g. 0.5 to fit two "
            "replicas on one GPU). Defaults to 1.0 when not set. Must be combined "
            "with a matching gpu_memory_utilization in engine_config so vLLM only "
            "uses that fraction of VRAM."
        ),
    )
    engine_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Extra engine-specific parameters passed to the config builder. "
            "vLLM examples: dtype, gpu_memory_utilization, max_model_len, "
            "trust_remote_code, enable_prefix_caching, download_dir."
        ),
    )
    placement_group_bundles: Optional[List[Dict[str, float]]] = Field(
        default=None,
        description=(
            "Placement group bundles — one dict per bundle specifying resource "
            "requirements (e.g. [{\"GPU\": 1, \"CPU\": 1}, {\"GPU\": 1, \"CPU\": 1}]). "
            "Ray Serve creates one placement group per replica using these bundles. "
            "When omitted, sensible defaults are auto-generated: STRICT_PACK bundles "
            "for tensor_parallel_size > 1, PACK bundles for fractional GPU replicas."
        ),
    )
    placement_group_strategy: Optional[str] = Field(
        default=None,
        description=(
            "Placement group scheduling strategy. "
            "PACK: bin-pack bundles onto as few nodes as possible (default). "
            "STRICT_PACK: all bundles must fit on a single node — required for "
            "tensor parallelism across GPUs on the same machine. "
            "SPREAD: spread bundles across as many nodes as possible. "
            "STRICT_SPREAD: each bundle on a strictly different node — use for "
            "cross-node tensor parallelism."
        ),
    )

    node_type: Optional[str] = Field(
        default=None,
        description=(
            "Target worker node type for deployment placement (e.g. 'l40-gpu-worker'). "
            "Pins replicas to Ray nodes with a matching 'node_type:<value>' resource label."
        ),
    )
    multi_node: bool = Field(
        default=False,
        description=(
            "Enable cross-node tensor parallelism. When True and tensor_parallel_size > 1, "
            "placement group uses one bundle per GPU shard with PACK strategy, allowing "
            "shards to spread across separate worker nodes connected via NCCL. "
            "When False (default), all shards are forced onto a single node (STRICT_PACK). "
            "Requires NCCL inter-node connectivity between worker nodes."
        ),
    )
    autoscaling_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Ray Serve autoscaling configuration. When set, num_replicas is ignored. "
            "Example: {\"min_replicas\": 1, \"max_replicas\": 4, "
            "\"target_ongoing_requests\": 5, \"upscale_delay_s\": 30, "
            "\"downscale_delay_s\": 300}"
        ),
    )

    @field_validator("placement_group_strategy")
    @classmethod
    def validate_strategy(cls, v: Optional[str]) -> Optional[str]:
        valid = {"PACK", "STRICT_PACK", "SPREAD", "STRICT_SPREAD"}
        if v is not None and v not in valid:
            raise ValueError(
                f"placement_group_strategy must be one of {sorted(valid)}, got '{v}'"
            )
        return v

    @field_validator("autoscaling_config")
    @classmethod
    def validate_autoscaling_config(
        cls, v: Optional[Dict[str, Any]], info
    ) -> Optional[Dict[str, Any]]:
        if v is not None:
            num_replicas = info.data.get("num_replicas", 1)
            if num_replicas > 1:
                raise ValueError(
                    "autoscaling_config and num_replicas > 1 are mutually exclusive. "
                    "Set num_replicas=1 (the default) when using autoscaling_config."
                )
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "name": "llama3-8b",
                "engine_type": "vllm",
                "model": "meta-llama/Llama-3.1-8B-Instruct",
                "route_prefix": "/llama3",
                "num_replicas": 1,
                "tensor_parallel_size": 1,
                "engine_config": {
                    "dtype": "bfloat16",
                    "gpu_memory_utilization": 0.9,
                    "max_model_len": 8192,
                },
            }
        }
