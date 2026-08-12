"""Request models for management API."""

import re
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Environment variable keys that must never be injected into actor processes.
# These can be used for dynamic linker hijacking, Python startup code execution,
# or credential theft — all achievable before the actor's own code runs.
_ENV_VAR_DENYLIST = frozenset({
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    "LD_DEBUG",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "PYTHONSTARTUP",
    "PYTHONPATH",
    "PATH",
})


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


class DefineNodeTypeRequest(BaseModel):
    """Register a new worker group / node_type at runtime (no cluster relaunch).

    Once defined, scale it with POST /api/v1/resources/nodes (this endpoint only
    registers the shape; it does not launch workers unless count > 0 is later
    honoured by the caller).
    """

    node_type: str = Field(
        ...,
        description="Logical node type, e.g. 'l40-gpu-worker-12cpu'. Bare segment "
        "(letters, digits, '.', '-', '_') — used as a Ray resource key and "
        "launcher filename component.",
    )
    cpu: int = Field(..., ge=1, le=256, description="Default CPU cores per worker.")
    memory: int = Field(..., ge=4, le=1024, description="Default memory in GB per worker.")
    gpus: int = Field(default=0, ge=0, description="Default GPU count per worker.")
    accelerator_type: Optional[str] = Field(
        default=None,
        description="Ray accelerator_type label (matches nvidia-smi GPU name, e.g. 'L40S').",
    )
    node_label: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Kubernetes node-selector label(s) for pod placement, e.g. "
            "{'node.kubernetes.io/instance-type': 'g6e.4xlarge'}. Also auto-derived "
            "into a short-key Ray resource label. See AddNodeRequest for details."
        ),
    )
    runtime_identifier: Optional[str] = Field(
        default=None,
        description="Docker runtime identifier (uses cluster worker default when omitted).",
    )
    count: int = Field(
        default=0, ge=0,
        description="Workers to record for the group. Registration only — scale via POST /nodes.",
    )
    name: Optional[str] = Field(
        default=None,
        description="Group display name (defaults to node_type).",
    )

    @field_validator("node_type")
    @classmethod
    def _validate_node_type(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", v):
            raise ValueError(
                "node_type must be a bare segment: letters, digits, '.', '-', '_' only"
            )
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "node_type": "l40-gpu-worker-12cpu",
                "cpu": 12,
                "memory": 64,
                "gpus": 1,
                "accelerator_type": "L40S",
                "node_label": {"node.kubernetes.io/instance-type": "g6e.4xlarge"},
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
        default=False,
        description="Allow unauthenticated access to the application. Defaults to False; set True only for internal tooling.",
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


class SchedulingConfig(BaseModel):
    """
    Explicit Ray scheduling constraints for a deployment.

    Provides full user control over actor resource requirements, placement group
    bundles, and runtime environment variables — covering everything from simple
    node-type affinity to complex multi-actor fractional-GPU topologies.

    All fields are optional and compose:
    - ``resources`` pins the main actor to specific Ray nodes
    - ``placement_group_bundles`` overrides the auto-generated bundle layout
    - ``env_vars`` are merged with the venv runtime_env (not replacing it)
    """

    resources: Optional[Dict[str, float]] = Field(
        default=None,
        description=(
            "Ray resource affinity for this deployment's GPU work. Use 0.001 for "
            "soft affinity (a scheduling hint that does not consume capacity). "
            "Applied to whichever layer actually places the work: when a placement "
            "group is used (tensor parallelism or fractional GPU), these labels are "
            "merged into the GPU-bearing bundles so every shard lands on the target "
            "nodes; otherwise they are set on the deployment actor directly. "
            "Examples: "
            "{\"node_type:l40-gpu-worker\": 0.001} — pin to a specific worker group; "
            "{\"instance-group-id:ig-n4bsnv8r\": 0.001} — pin to a specific K8s node "
            "(requires that node was added with a matching node_label)."
        ),
    )
    placement_group_bundles: Optional[List[Dict[str, float]]] = Field(
        default=None,
        description=(
            "Per-bundle resource specs. When provided, overrides the auto-generated "
            "placement group entirely. Each dict is one bundle; keys are Ray resource "
            "names (including custom node-affinity labels), values are quantities. "
            "You may embed custom labels directly per bundle for fine-grained control; "
            "any `resources` above are additionally merged into the GPU-bearing bundles. "
            "Example (vLLM with fractional GPU + LMCache, matches right diagram): "
            "[{\"CPU\": 2.0, \"GPU\": 0.01, \"node_type:l40-gpu-worker\": 0.001}, "
            "{\"GPU\": 0.99}, {\"GPU\": 0.99}]"
        ),
    )
    placement_group_strategy: Optional[Literal["PACK", "STRICT_PACK", "SPREAD", "STRICT_SPREAD"]] = Field(
        default=None,
        description=(
            "Placement group scheduling strategy. "
            "PACK: bin-pack bundles onto as few nodes as possible (default). "
            "STRICT_PACK: all bundles must fit on a single node. "
            "SPREAD: spread across as many nodes as possible. "
            "STRICT_SPREAD: each bundle on a strictly different node."
        ),
    )
    env_vars: Optional[Dict[str, str]] = Field(
        default=None,
        description=(
            "Environment variables injected into the actor's runtime_env['env_vars']. "
            "Merged with (not replacing) the venv py_executable runtime_env. "
            "Use for engine-specific tuning parameters that must be set at actor "
            "startup before the engine initialises. "
            "Examples: "
            "{\"VLLM_RAY_PER_WORKER_GPUS\": \"0.99\", \"VLLM_RAY_BUNDLE_INDICES\": \"1,2\"} "
            "— fractional GPU executor layout for vLLM + LMCache; "
            "{\"HF_HOME\": \"/mnt/models\"} — override HuggingFace cache path."
        ),
    )

    @field_validator("env_vars")
    @classmethod
    def validate_env_vars(cls, v: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
        if v is None:
            return v
        blocked = _ENV_VAR_DENYLIST & set(v.keys())
        if blocked:
            raise ValueError(
                f"env_vars contains disallowed keys that could be used for "
                f"dynamic-linker or interpreter hijacking: {sorted(blocked)}. "
                f"Remove these keys from scheduling.env_vars."
            )
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "resources": {"node_type:l40-gpu-worker": 0.001},
                "placement_group_bundles": [
                    {"CPU": 2.0, "GPU": 0.01},
                    {"GPU": 0.99},
                    {"GPU": 0.99},
                ],
                "placement_group_strategy": "STRICT_PACK",
                "env_vars": {
                    "VLLM_RAY_PER_WORKER_GPUS": "0.99",
                    "VLLM_RAY_BUNDLE_INDICES": "1,2",
                },
            }
        }


class DeployApplicationRequest(BaseModel):
    """
    Unified request to deploy a Ray Serve application.

    Exactly one of ``engine_type`` (engine-registry path) or ``import_path``
    (raw Ray Serve import path) must be provided.

    Engine-registry path (vLLM, SGLang, LiteLLM, YOLO, MCP, custom):
        Set ``engine_type`` to the registered engine name.  Engine-specific
        parameters go in ``engine_config``.  ``model`` is required for vLLM
        and SGLang; other engines configure their target via ``engine_config``.

    Raw Ray Serve path:
        Set ``import_path`` to a ``module:attribute`` string.  The object must
        be an ``@serve.deployment``-decorated class or a bound ``serve.Application``.
        Use ``ray_actor_options`` for resource requirements and ``scheduling``
        for node targeting.

    Scheduling:
        The ``scheduling`` field provides full control over actor resources,
        placement group bundles, and runtime env vars for BOTH paths.
        When ``node_type`` is set without ``scheduling``, it auto-expands to
        ``scheduling.resources = {"node_type:<value>": 0.001}`` for backward
        compatibility.  When both are set, ``scheduling`` takes full precedence.

    .. note::
        ``placement_group_bundles`` and ``placement_group_strategy`` were
        top-level fields on the old ``DeployModelRequest`` (used with
        ``POST /applications/model``).  They must now be provided inside the
        ``scheduling`` sub-object.  Passing them at the top level raises 422
        due to ``extra="forbid"``.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "name": "llama3-8b",
                    "engine_type": "vllm",
                    "model": "meta-llama/Llama-3.1-8B-Instruct",
                    "route_prefix": "/llama3",
                    "tensor_parallel_size": 1,
                    "engine_config": {"dtype": "bfloat16", "gpu_memory_utilization": 0.9},
                    "scheduling": {"resources": {"node_type:l40-gpu-worker": 0.001}},
                },
                {
                    "name": "vllm-lmcache",
                    "engine_type": "vllm",
                    "model": "meta-llama/Llama-3.1-8B-Instruct",
                    "tensor_parallel_size": 2,
                    "scheduling": {
                        "resources": {"node_type:l40-gpu-worker": 0.001},
                        "placement_group_bundles": [
                            {"CPU": 2.0, "GPU": 0.01},
                            {"GPU": 0.99},
                            {"GPU": 0.99},
                        ],
                        "placement_group_strategy": "STRICT_PACK",
                        "env_vars": {
                            "VLLM_RAY_PER_WORKER_GPUS": "0.99",
                            "VLLM_RAY_BUNDLE_INDICES": "1,2",
                        },
                    },
                },
                {
                    "name": "litellm-proxy",
                    "engine_type": "litellm",
                    "route_prefix": "/litellm",
                    "engine_config": {
                        "model_list": [
                            {
                                "model_name": "gpt-4o",
                                "litellm_params": {
                                    "model": "openai/gpt-4o",
                                    "api_key": "os.environ/OPENAI_API_KEY",
                                },
                            }
                        ]
                    },
                },
                {
                    "name": "my-service",
                    "import_path": "my_module:app",
                    "route_prefix": "/svc",
                    "ray_actor_options": {"num_cpus": 2},
                    "scheduling": {"resources": {"node_type:cpu-worker": 0.001}},
                },
            ]
        },
    )

    name: str = Field(..., description="Ray Serve application name (must be unique within the cluster)")

    # ── Discriminator ──────────────────────────────────────────────────────────
    engine_type: Optional[str] = Field(
        default=None,
        description=(
            "Inference engine: 'vllm', 'sglang', 'litellm', 'yolo', 'mcp', or a "
            "custom registered type. Mutually exclusive with import_path."
        ),
    )
    import_path: Optional[str] = Field(
        default=None,
        description=(
            "Import path to a Ray Serve deployment: 'module:attribute'. "
            "The attribute must be an @serve.deployment class or a bound Application. "
            "Mutually exclusive with engine_type."
        ),
    )

    # ── Common ─────────────────────────────────────────────────────────────────
    route_prefix: str = Field(
        default="/",
        description="HTTP route prefix — all endpoints are served under this prefix",
    )
    num_replicas: int = Field(default=1, ge=1, description="Number of Ray Serve replicas")
    autoscaling_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Ray Serve autoscaling configuration. Mutually exclusive with num_replicas > 1. "
            "Example: {\"min_replicas\": 1, \"max_replicas\": 4, "
            "\"target_ongoing_requests\": 5}"
        ),
    )
    scheduling: Optional[SchedulingConfig] = Field(
        default=None,
        description=(
            "Explicit Ray scheduling constraints: actor resource requirements, "
            "placement group bundle layout, placement strategy, and actor env vars. "
            "Takes full precedence over node_type when both are provided."
        ),
    )

    # ── Engine-registry fields (engine_type path) ──────────────────────────────
    model: Optional[str] = Field(
        default=None,
        description=(
            "HuggingFace model ID or local path. Required for vLLM and SGLang. "
            "Not needed for LiteLLM, MCP, YOLO (use engine_config instead)."
        ),
    )
    tensor_parallel_size: int = Field(
        default=1,
        ge=1,
        description="Number of GPUs for tensor parallelism within each replica",
    )
    use_cpu: bool = Field(
        default=False,
        description="Run inference on CPU (no GPU required; for testing only)",
    )
    gpu_fraction: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Fractional GPU resource per replica (e.g. 0.5). "
            "Combine with gpu_memory_utilization in engine_config."
        ),
    )
    engine_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Engine-specific parameters passed to the config builder. "
            "vLLM: dtype, gpu_memory_utilization, max_model_len, trust_remote_code, etc. "
            "LiteLLM: model_list, litellm_settings, litellm_port. "
            "YOLO: model_path, confidence_threshold, etc."
        ),
    )
    multi_node: bool = Field(
        default=False,
        description=(
            "Enable cross-node tensor parallelism. When True and tensor_parallel_size > 1, "
            "shards may spread across separate worker nodes."
        ),
    )
    venv_name: Optional[str] = Field(
        default=None,
        description=(
            "Name of the isolated environment at /home/cdsw/.venv-<name>. "
            "Defaults to engine_type. Must already exist (create via POST /environments)."
        ),
    )
    node_type: Optional[str] = Field(
        default=None,
        description=(
            "Backward-compat shorthand: auto-expands to "
            "scheduling.resources={\"node_type:<value>\": 0.001}. "
            "Ignored when scheduling is also provided."
        ),
    )

    # ── Raw serve app fields (import_path path) ────────────────────────────────
    ray_actor_options: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Ray actor options for raw serve deployments (import_path path). "
            "scheduling.resources and scheduling.env_vars are merged in on top. "
            "Example: {\"num_cpus\": 2, \"num_gpus\": 1}"
        ),
    )

    # ── Validators ─────────────────────────────────────────────────────────────
    @field_validator("import_path")
    @classmethod
    def validate_import_path(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Must be module.submodule:attribute — no relative imports, no path traversal.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(
                "import_path must be in 'module:attribute' format "
                "(e.g. 'my_module:app'). Relative imports and path components are not allowed."
            )
        return v

    @model_validator(mode="after")
    def check_discriminator(self) -> "DeployApplicationRequest":
        has_engine = self.engine_type is not None
        has_import = self.import_path is not None
        if has_engine and has_import:
            raise ValueError(
                "engine_type and import_path are mutually exclusive — provide exactly one"
            )
        if not has_engine and not has_import:
            raise ValueError(
                "Exactly one of 'engine_type' or 'import_path' must be provided"
            )
        return self

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
                    "Set num_replicas=1 when using autoscaling_config."
                )
        return v



# Backward-compat alias — existing code that imports DeployModelRequest directly
# will continue to work; the unified model accepts all its fields.
DeployModelRequest = DeployApplicationRequest
