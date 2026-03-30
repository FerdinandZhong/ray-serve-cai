"""Request models for management API."""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


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

    class Config:
        json_schema_extra = {
            "example": {
                "node_type": "t4-gpu-worker",
                "cpu": 16,
                "memory": 32,
                "gpus": 1,
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
    """Request to deploy a vLLM or SGLang model as a Ray Serve application."""

    name: str = Field(..., description="Application name (used as the Ray Serve app name)")
    engine_type: str = Field(
        default="vllm",
        description="Inference engine to use: 'vllm' or 'sglang'",
    )
    model: str = Field(
        ...,
        description="HuggingFace model ID or local path (e.g., 'meta-llama/Llama-3.1-8B-Instruct')",
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
    engine_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Extra engine-specific parameters passed to the config builder. "
            "vLLM examples: dtype, gpu_memory_utilization, max_model_len, "
            "trust_remote_code, enable_prefix_caching, download_dir."
        ),
    )

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
