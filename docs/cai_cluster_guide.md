# CAI-Based Ray Cluster Guide

This guide explains how to use Cloudera Machine Learning (CML) Applications to create distributed Ray clusters for running vLLM and other inference engines.

## Overview

The CAI cluster feature allows you to:
- **Create distributed Ray clusters** using CML Applications as nodes
- **Scale horizontally** by adding worker nodes
- **Efficient GPU allocation** - Head node has NO GPUs, only workers get GPUs
- **Leverage CML resources** including GPUs, CPUs, and memory
- **Manage clusters** through simple CLI commands

### Architecture

```
┌─────────────────────────────────────────────────────┐
│                    CML Project                       │
│                                                      │
│  ┌──────────────────┐         ┌─────────────────┐  │
│  │   CAI App 1      │         │   CAI App 2     │  │
│  │                  │         │                 │  │
│  │   Ray Head       │◄────────┤  Ray Worker     │  │
│  │   Node           │         │   Node          │  │
│  │                  │         │                 │  │
│  │  Port: 6379      │         │  GPUs: YES ✓    │  │
│  │  Dashboard: 8265 │         │                 │  │
│  │  GPUs: NO ✗      │         │                 │  │
│  └──────────────────┘         └─────────────────┘  │
│          │                           │              │
│          │                           │              │
│  ┌───────▼──────────┐        ┌──────▼──────────┐  │
│  │   CAI App 3      │        │   CAI App 4     │  │
│  │                  │        │                 │  │
│  │  Ray Worker      │        │  Ray Worker     │  │
│  │   Node           │        │   Node          │  │
│  │                  │        │                 │  │
│  │  GPUs: YES ✓     │        │  GPUs: YES ✓    │  │
│  └──────────────────┘        └─────────────────┘  │
│                                                     │
└─────────────────────────────────────────────────────┘

Key Design:
- Head node: Cluster coordination, no GPUs needed
- Worker nodes: Actual computation, allocated GPUs
```

## Prerequisites

1. **CML Access**: You need access to a CML instance with:
   - API access enabled
   - A project where you can create applications
   - Sufficient resource quotas (CPU, memory, GPUs)

2. **API Key**: Generate a CML API key from your profile settings

3. **Dependencies**: Install required packages:
   ```bash
   pip install ray[serve]
   pip install pyyaml requests
   ```

4. **Caikit Library**: The caikit Python library must be available:
   ```bash
   # If in the same parent directory
   export PYTHONPATH=/path/to/caikit:$PYTHONPATH

   # Or install it
   cd /path/to/caikit && pip install -e .
   ```

## Quick Start

### 1. Create Configuration File

Create a YAML configuration file (e.g., `my_cluster.yaml`):

```yaml
cai:
  # CML instance URL
  host: https://ml.example.cloudera.site

  # API key (or set CML_API_KEY env var)
  api_key: your-api-key-here

  # Project ID
  project_id: project-abc123

  # Number of worker nodes
  num_workers: 2

  # Worker node resources (GPUs allocated here)
  resources:
    cpu: 16
    memory: 64
    num_gpus: 1  # GPUs for worker nodes only

  # Optional: Head node resources (no GPUs)
  # Defaults to same as workers if not specified
  head_resources:
    cpu: 8
    memory: 32
```

See `examples/cai_cluster_config.yaml` for more options.

**Important:** Head node always gets 0 GPUs, regardless of configuration. GPUs are only allocated to worker nodes for computation.

### 2. Start the Cluster

```bash
# Using config file
python -m ray_serve_cai.launch_cluster --config my_cluster.yaml start-cai

# Or set API key via environment
export CML_API_KEY=your-api-key
python -m ray_serve_cai.launch_cluster --config my_cluster.yaml start-cai
```

The command will:
1. Create a CAI application as Ray head node (0 GPUs - coordination only)
2. Wait for the head node to start
3. Create CAI applications as Ray worker nodes
4. Connect workers to the head node
5. Return the cluster address

Output:
```
🚀 Starting Ray cluster on CAI...
   Head node: 8CPU, 32GB RAM, 0GPU
   Workers: 2 nodes, 16CPU, 64GB RAM, 1GPU each
📝 Creating head node script...
✅ Uploaded script: ray_head_launcher.py
🎯 Creating head node application...
✅ Head node application created: app-head-123
⏳ Waiting for head node to start...
✅ Head node ready: ray-cluster-head.ml.example.com:6379
🔧 Creating 2 worker node(s)...
   Creating worker 1/2...
   ✅ Worker 1 created: app-worker-456
   Creating worker 2/2...
   ✅ Worker 2 created: app-worker-789
⏳ Waiting for workers to start...
   ✅ Worker 1 ready
   ✅ Worker 2 ready
============================================================
✅ Ray cluster started successfully!
   Head address: ray-cluster-head.ml.example.com:6379
   Head resources: 8CPU, 32GB RAM, 0GPU
   Workers: 2 nodes
   Worker resources: 16CPU, 64GB RAM, 1GPU each
   Total nodes: 3
============================================================
```

### 3. Check Cluster Status

```bash
python -m ray_serve_cai.launch_cluster status-cai
```

Output:
```
✅ CAI Ray cluster is running

Head node:
  ID: app-head-123
  Status: running
  Address: ray-cluster-head.ml.example.com:6379

Worker nodes: 2
  Worker 1: running (ID: app-worker-456)
  Worker 2: running (ID: app-worker-789)

Total nodes: 3
```

### 4. Use the Cluster

Connect your Ray applications to the cluster:

```python
import ray

# Connect to CAI cluster
ray.init(address="ray://ray-cluster-head.ml.example.com:6379")

# Or use the full address from cluster info
ray.init(address="ray://your-head-address:6379")

# Your Ray code here
@ray.remote
def my_task():
    return "Hello from Ray on CAI!"

result = ray.get(my_task.remote())
print(result)
```

### 5. Stop the Cluster

```bash
python -m ray_serve_cai.launch_cluster stop-cai
```

This will stop and delete all CAI applications (head and workers).

## Configuration Reference

### CAI Section

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `host` | string | Yes | CML instance URL (e.g., `https://ml.example.com`) |
| `api_key` | string | Yes* | CML API key (* or use `CML_API_KEY` env var) |
| `project_id` | string | Yes | CML project ID where apps will be created |
| `num_workers` | integer | No | Number of worker nodes (default: 1) |
| `resources.cpu` | integer | No | CPU cores per **worker** node (default: 16) |
| `resources.memory` | integer | No | Memory in GB per **worker** node (default: 64) |
| `resources.num_gpus` | integer | No | GPUs per **worker** node (default: 0). **Head node always has 0 GPUs** |
| `head_resources.cpu` | integer | No | CPU cores for **head** node (defaults to resources.cpu) |
| `head_resources.memory` | integer | No | Memory in GB for **head** node (defaults to resources.memory) |
| `runtime_identifier` | string | No | Docker runtime identifier (uses project default if not set) |

**Key Points:**
- `resources.*` parameters apply to **worker nodes only**
- `head_resources.*` are optional - head node defaults to same CPU/memory as workers
- **Head node ALWAYS gets 0 GPUs**, regardless of any configuration
- GPUs are only allocated to worker nodes for actual computation

### Ray Section (Optional)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `port` | integer | 6379 | Ray GCS server port |
| `dashboard_port` | integer | 8265 | Ray dashboard port |

## Usage Examples

### Development Cluster (No GPUs)

```yaml
cai:
  host: https://ml-dev.example.com
  project_id: dev-project-123
  num_workers: 1
  resources:  # Worker resources
    cpu: 8
    memory: 32
    num_gpus: 0
  head_resources:  # Smaller head node for dev
    cpu: 4
    memory: 16
```

### Production Cluster (With GPUs)

```yaml
cai:
  host: https://ml-prod.example.com
  project_id: prod-project-456
  num_workers: 4
  resources:  # Worker resources (with GPUs)
    cpu: 16
    memory: 64
    num_gpus: 2
  head_resources:  # Head node (no GPUs needed)
    cpu: 8
    memory: 32
```

### Large Training Cluster

```yaml
cai:
  host: https://ml-train.example.com
  project_id: train-project-789
  num_workers: 8
  resources:  # Worker resources (with GPUs for training)
    cpu: 32
    memory: 128
    num_gpus: 4
  head_resources:  # Head node (coordination only)
    cpu: 16
    memory: 64
```

## Deploying vLLM on CAI Cluster

Once your CAI cluster is running, you can deploy vLLM models:

```python
from ray_serve_cai.ray_backend import RayBackend

# Initialize backend
backend = RayBackend()

# Connect to CAI cluster
await backend.initialize_ray(address="ray://your-head-address:6379")

# Deploy vLLM model
await backend.start_model(
    vllm_config={
        "model": "meta-llama/Llama-2-7b-hf",
        "tensor_parallel_size": 2,
        "engine": "vllm"
    }
)
```

## Troubleshooting

### Cluster Fails to Start

**Symptom**: Head node or workers fail to reach "running" status

**Solutions**:
1. Check CML quotas - ensure sufficient resources
2. Verify API key has correct permissions
3. Check project ID is correct
4. Review CML application logs in the UI

### Workers Can't Connect to Head

**Symptom**: Workers start but don't join the cluster

**Solutions**:
1. Verify network connectivity between CAI applications
2. Check firewall rules allow traffic on Ray ports
3. Ensure head node is fully started before workers
4. Review Ray logs in the applications

### Out of Resources

**Symptom**: Applications fail with resource errors

**Solutions**:
1. Reduce `num_workers`
2. Reduce `cpu`, `memory`, or `num_gpus` per node
3. Contact CML admin to increase quotas
4. Use smaller models or configurations

### Import Error: caikit Not Found

**Symptom**: `Failed to import caikit library`

**Solutions**:
1. Ensure caikit package is installed or in PYTHONPATH
2. Install caikit: `pip install /path/to/caikit`
3. Or add to path: `export PYTHONPATH=/path/to/caikit:$PYTHONPATH`

## Advanced Features

### Custom Runtime

Specify a custom Docker runtime:

```yaml
cai:
  runtime_identifier: docker.repo.example.com/custom/runtime:latest
  # ... other config
```

### Monitoring

Access Ray Dashboard at:
```
http://your-head-node-url:8265
```

The dashboard shows:
- Cluster resource usage
- Running tasks and actors
- Node status
- Application metrics

### Scaling

To scale the cluster:
1. Stop the current cluster: `stop-cai`
2. Update `num_workers` in config
3. Start new cluster: `start-cai`

Note: Dynamic scaling (adding/removing workers without restart) is not yet supported.

### Security

Best practices:
1. **Never commit API keys** to version control
2. **Use environment variables** for sensitive data:
   ```bash
   export CML_API_KEY=your-key
   ```
3. **Rotate API keys** regularly
4. **Use project-scoped keys** with minimal permissions
5. **Enable authentication** on Ray Serve endpoints

## API Reference

### CAIClusterManager

Main class for managing CAI-based Ray clusters:

```python
from ray_serve_cai.cai_cluster import CAIClusterManager

manager = CAIClusterManager(
    cml_host="https://ml.example.com",
    cml_api_key="your-key",
    project_id="project-123",
    verbose=True
)

# Start cluster
cluster_info = manager.start_cluster(
    num_workers=2,
    cpu=16,
    memory=64,
    num_gpus=1
)

# Get status
status = manager.get_status()

# Stop cluster
import asyncio
asyncio.run(manager.stop_cluster())
```

## Limitations

1. **No dynamic scaling**: Must restart cluster to change worker count
2. **Single project**: All nodes must be in the same CML project
3. **No persistence**: Cluster state is not saved between restarts
4. **Manual cleanup**: Failed applications may need manual deletion from CML UI

## Next Steps

- Learn about [vLLM deployment](./vllm_deployment.md)
- Explore [engine configuration](./engine_config.md)
- Read about [Ray Serve integration](./ray_serve.md)

## Support

For issues or questions:
1. Check the [troubleshooting section](#troubleshooting)
2. Review CML application logs
3. Open an issue on GitHub
