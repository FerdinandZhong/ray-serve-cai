"""Response models for management API."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class NodeInfo(BaseModel):
    """Information about a Ray node."""

    node_id: str = Field(..., description="Ray node ID")
    node_name: str = Field(..., description="Node name")
    node_type: str = Field(..., description="Node type (head/worker)")
    cml_app_id: Optional[str] = Field(None, description="CML application ID if applicable")
    cml_app_name: Optional[str] = Field(None, description="CML application name")
    alive: bool = Field(..., description="Whether the node is alive")
    resources: Dict[str, float] = Field(..., description="Available resources")
    resources_used: Dict[str, float] = Field(..., description="Used resources")


class NodesListResponse(BaseModel):
    """Response for listing all nodes."""

    nodes: List[NodeInfo]
    total_nodes: int
    alive_nodes: int


class ApplicationInfo(BaseModel):
    """Information about a Ray Serve application."""

    name: str = Field(..., description="Application name")
    status: str = Field(..., description="Application status (RUNNING, DEPLOYING, UNHEALTHY)")
    route_prefix: str = Field(..., description="HTTP route prefix")
    num_replicas: int = Field(..., description="Number of replicas")
    message: Optional[str] = Field(None, description="Status message if any")
    last_deployed_time: Optional[datetime] = Field(None, description="Last deployment time")


class ApplicationsListResponse(BaseModel):
    """Response for listing all applications."""

    applications: List[ApplicationInfo]
    total_applications: int


class ResourceCapacity(BaseModel):
    """Cluster resource capacity information."""

    total_cpus: float = Field(..., description="Total CPU cores")
    available_cpus: float = Field(..., description="Available CPU cores")
    total_memory: float = Field(..., description="Total memory in GB")
    available_memory: float = Field(..., description="Available memory in GB")
    total_gpus: float = Field(default=0, description="Total GPUs")
    available_gpus: float = Field(default=0, description="Available GPUs")
    utilization_percent: float = Field(..., description="Overall utilization percentage")


class ClusterStatus(BaseModel):
    """Overall cluster health status."""

    healthy: bool = Field(..., description="Whether cluster is healthy")
    total_nodes: int = Field(..., description="Total number of nodes")
    alive_nodes: int = Field(..., description="Number of alive nodes")
    total_applications: int = Field(..., description="Total Ray Serve applications")
    resources: ResourceCapacity


class ClusterInfo(BaseModel):
    """Cluster configuration and connection information."""

    head_address: str = Field(..., description="Ray head node address")
    dashboard_url: Optional[str] = Field(None, description="Ray dashboard URL")
    management_api_url: str = Field(..., description="Management API URL")
    ray_version: str = Field(..., description="Ray version")
    cluster_name: str = Field(..., description="Cluster name")
