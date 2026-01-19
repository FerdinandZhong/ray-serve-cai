# Ray Cluster Management API

A FastAPI-based REST API for managing Ray clusters running on Cloudera Machine Learning (CML/CAI) platform.

## Overview

The Management API provides a unified interface to:
- **Resource Management**: Add/remove worker nodes dynamically
- **Application Management**: Deploy/undeploy Ray Serve applications
- **Cluster Monitoring**: View cluster status, resource utilization, and health

## Architecture

```
Management Application (Ray Serve Deployment)
├── Runs on Ray Head Node
├── FastAPI Server (via Ray Serve, Port 8080)
├── Ray Client Connection → Ray Cluster (local)
├── CML API Client → CML Platform
└── Coordinator Service (bridges Ray + CML)
```

The management app runs as a **Ray Serve deployment** on the Ray cluster and coordinates between:
- **Ray Cluster**: Uses Ray APIs to manage applications and view cluster state (running inside the cluster)
- **CML Platform**: Uses CML APIs to create/destroy worker node applications

## API Endpoints

### Resource Management

#### `POST /api/v1/resources/nodes/add`
Add a new worker node to the cluster.

**Request Body:**
```json
{
  "cpu": 8,
  "memory": 32,
  "node_type": "worker"
}
```

**Response:**
```json
{
  "status": "success",
  "app_id": "abc-123",
  "app_name": "ray-worker-1234567890",
  "cpu": 8,
  "memory": 32,
  "node_type": "worker"
}
```

#### `DELETE /api/v1/resources/nodes/{app_id}`
Remove a worker node from the cluster.

**Response:**
```json
{
  "status": "success",
  "app_id": "abc-123"
}
```

#### `GET /api/v1/resources/nodes`
List all nodes in the cluster with Ray and CML information.

**Response:**
```json
{
  "nodes": [
    {
      "node_id": "abc123",
      "node_name": "ray-head",
      "node_type": "head",
      "cml_app_id": "head-app-id",
      "cml_app_name": "ray-head",
      "alive": true,
      "resources": {"CPU": 8, "memory": 34359738368},
      "resources_used": {"CPU": 2, "memory": 4294967296}
    }
  ],
  "total_nodes": 2,
  "alive_nodes": 2
}
```

#### `GET /api/v1/resources/capacity`
Check available cluster resources.

**Response:**
```json
{
  "total_cpus": 16,
  "available_cpus": 12,
  "total_memory": 64.0,
  "available_memory": 48.0,
  "total_gpus": 0,
  "available_gpus": 0,
  "utilization_percent": 25.0
}
```

### Application Management

#### `POST /api/v1/applications`
Deploy a Ray Serve application.

**Request Body:**
```json
{
  "name": "my-service",
  "import_path": "my_module:deployment",
  "route_prefix": "/api",
  "num_replicas": 2,
  "ray_actor_options": {"num_cpus": 1}
}
```

**Response:**
```json
{
  "status": "success",
  "name": "my-service",
  "route_prefix": "/api"
}
```

#### `DELETE /api/v1/applications/{app_name}`
Undeploy a Ray Serve application.

**Response:**
```json
{
  "status": "success",
  "name": "my-service"
}
```

#### `GET /api/v1/applications`
List all Ray Serve applications.

**Response:**
```json
{
  "applications": [
    {
      "name": "my-service",
      "status": "RUNNING",
      "route_prefix": "/api",
      "num_replicas": 2,
      "message": "",
      "last_deployed_time": "2026-01-19T10:30:00"
    }
  ],
  "total_applications": 1
}
```

#### `GET /api/v1/applications/{app_name}`
Get detailed information about a specific application.

### Cluster Information

#### `GET /api/v1/cluster/status`
Get overall cluster health and status.

**Response:**
```json
{
  "healthy": true,
  "total_nodes": 2,
  "alive_nodes": 2,
  "total_applications": 1,
  "resources": {
    "total_cpus": 16,
    "available_cpus": 12,
    "total_memory": 64.0,
    "available_memory": 48.0,
    "total_gpus": 0,
    "available_gpus": 0,
    "utilization_percent": 25.0
  }
}
```

#### `GET /api/v1/cluster/info`
Get cluster configuration and connection details.

**Response:**
```json
{
  "head_address": "10.0.1.5:6379",
  "dashboard_url": "http://10.0.1.5:8265",
  "management_api_url": "https://management-api.cml.example.com",
  "ray_version": "2.20.0",
  "cluster_name": "ray-cluster"
}
```

### Health Check

#### `GET /health`
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "ray_connected": true,
  "cai_available": true
}
```

## Interactive Documentation

The API provides interactive Swagger UI documentation at:
- **Swagger UI**: `{management_api_url}/docs`
- **ReDoc**: `{management_api_url}/redoc`

## Configuration

The management app is configured through environment variables:

- `RAY_ADDRESS`: Ray cluster address (default: "auto")
- `CML_PROJECT_ID`: CML project ID
- `CML_HOST`: CML host URL
- `CML_API_KEY`: CML API key
- `CDSW_APP_PORT`: Port to run on (default: 8080)
- `CDSW_APP_HOST`: Host to bind to (default: 127.0.0.1)

## State Management

The management app maintains cluster state in `/home/cdsw/cluster_state.json` including:
- Node-to-CML-application mappings
- Application deployment history

## Deployment

The management app is automatically deployed when launching a Ray cluster via `launch_ray_cluster.py`. It:
1. Connects to the Ray cluster
2. Starts Ray Serve on the head node (port 8080)
3. Deploys the FastAPI app as a Ray Serve deployment (2 CPU, 4GB RAM)
4. Exposes the API on the head node
5. Registers the URL in cluster info

**Key Benefits of Ray Serve Deployment:**
- Runs directly on Ray cluster resources (no separate CML application needed)
- Uses Ray's load balancing and scaling
- Automatic failover if restarted
- Lower latency for Ray operations (local connection)

## Usage Example

```python
import requests

# Management API base URL
api_url = "https://ray-management-api.cml.example.com"

# Add a worker node
response = requests.post(
    f"{api_url}/api/v1/resources/nodes/add",
    json={"cpu": 8, "memory": 32, "node_type": "worker"}
)
print(response.json())

# Check cluster status
response = requests.get(f"{api_url}/api/v1/cluster/status")
print(response.json())

# Deploy an application
response = requests.post(
    f"{api_url}/api/v1/applications",
    json={
        "name": "sentiment-analysis",
        "import_path": "models.sentiment:deployment",
        "route_prefix": "/sentiment",
        "num_replicas": 3
    }
)
print(response.json())
```

## Development

To run locally for testing:

```bash
cd /home/cdsw
source .venv/bin/activate

export RAY_ADDRESS="auto"
export CML_PROJECT_ID="your-project-id"
export CML_HOST="https://ml.example.com"
export CML_API_KEY="your-api-key"

python -m ray_serve_cai.management.app
```

Access at: http://127.0.0.1:8080/docs

## Security Considerations

**Current Implementation:**
- No authentication (bypass_authentication=true)
- Suitable for internal/trusted networks

**Production Recommendations:**
- Enable CML authentication
- Add API key authentication
- Implement RBAC for operations
- Use HTTPS only
- Rate limiting
- Audit logging

## Troubleshooting

### Management API not accessible
- Check Ray Serve status: `ray.serve.status()`
- Verify port 8080 is accessible on head node
- Check Ray dashboard for deployment status
- Check logs: Ray Serve deployment logs in Ray dashboard

### Cannot add/remove nodes
- Verify CML_API_KEY has permissions
- Check CML quota limits
- Verify runtime_identifier is correct

### Ray operations failing
- Ensure Ray cluster is running
- Check RAY_ADDRESS environment variable
- Verify network connectivity to head node
