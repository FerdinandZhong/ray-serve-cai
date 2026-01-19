"""Request models for management API."""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class AddNodeRequest(BaseModel):
    """Request to add a new worker node to the cluster."""

    cpu: int = Field(default=4, ge=1, le=32, description="CPU cores for the worker")
    memory: int = Field(default=16, ge=4, le=128, description="Memory in GB for the worker")
    node_type: str = Field(default="worker", description="Type of node (worker, gpu-worker)")

    class Config:
        json_schema_extra = {
            "example": {
                "cpu": 8,
                "memory": 32,
                "node_type": "worker"
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
