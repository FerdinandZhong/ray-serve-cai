# Ray Cluster Setup Guide for vLLM Playground

This guide explains how to launch and manage Ray clusters for use with vLLM Playground.

## Quick Start

### 1. Install Dependencies

```bash
# Install Ray with Serve support
pip install "ray[serve]>=2.9.0"

# Install vLLM
pip install vllm

# Install optional dependencies
pip install aiohttp pyyaml
```

### 2. Launch Local Cluster

```bash
# Navigate to ray_serve directory
cd vllm_playground/ray_serve

# Start cluster with default settings
python launch_cluster.py start

# Or with custom config
python launch_cluster.py --config configs/local_cpu.yaml start
```

### 3. Use with vLLM Playground

```bash
# Get cluster address
CLUSTER_ADDRESS=$(python launch_cluster.py get-address)

# Start vLLM playground with Ray backend
# (Integration with main app coming soon)
```

## Configuration Files

We provide several pre-configured YAML files for different scenarios:

### Local Configurations

#### 1. Basic Local Cluster ([configs/local_basic.yaml](configs/local_basic.yaml:1-1))

```bash
python launch_cluster.py --config configs/local_basic.yaml start
```

**Use case**: Quick testing and development
- Auto-detects system resources
- Dashboard enabled at http://localhost:8265
- Minimal configuration

#### 2. GPU Cluster ([configs/local_gpu.yaml](configs/local_gpu.yaml:1-1))

```bash
python launch_cluster.py --config configs/local_gpu.yaml start
```

**Use case**: Production-grade GPU inference
- Explicitly configured GPU resources
- Optimized object store for large models
- Supports multi-GPU tensor parallelism

**Key settings**:
```yaml
resources:
  num_cpus: 16
  num_gpus: 2
  object_store_memory: 10737418240  # 10 GB
```

#### 3. CPU-Only Cluster ([configs/local_cpu.yaml](configs/local_cpu.yaml:1-1))

```bash
python launch_cluster.py --config configs/local_cpu.yaml start
```

**Use case**: Development without GPU
- CPU-only inference
- Smaller object store
- Good for testing with small models

### Multi-Node Configuration

#### Cloud Deployment ([configs/multi_node_example.yaml](configs/multi_node_example.yaml:1-1))

```bash
# Requires cloud credentials configured
python launch_cluster.py --config configs/multi_node_example.yaml start-autoscaler
```

**Use case**: Production multi-node deployment
- Auto-scaling cluster
- Cloud provider support (AWS, GCP, Azure)
- Multiple worker nodes with GPUs

## Cluster Management Commands

### Start Cluster

```bash
# With default settings
python launch_cluster.py start

# With specific config
python launch_cluster.py --config configs/local_gpu.yaml start

# With verbose logging
python launch_cluster.py --config configs/local_gpu.yaml --verbose start
```

### Check Status

```bash
python launch_cluster.py status
```

Output example:
```
✅ Ray cluster is running
Resources:
- CPUs: 16/16
- GPUs: 2/2
- Memory: 32.0 GB/64.0 GB
```

### Get Cluster Address

```bash
python launch_cluster.py get-address
```

Output: `127.0.0.1:6379`

Use this address to connect vLLM playground to the cluster.

### Stop Cluster

```bash
python launch_cluster.py stop
```

## Custom Configuration

Create your own YAML configuration file:

```yaml
cluster:
  # Ray head node port (default: 6379)
  port: 6379

  # Dashboard configuration
  dashboard:
    enabled: true
    port: 8265

  # Resource configuration
  resources:
    num_cpus: 32      # Total CPUs
    num_gpus: 4       # Total GPUs
    object_store_memory: 21474836480  # 20 GB

  # Block terminal (keep running in foreground)
  block: false
```

Then launch:
```bash
python launch_cluster.py --config my_custom_config.yaml start
```

## Integration with vLLM Playground

### Option 1: Automatic Connection

vLLM Playground will automatically detect and connect to running Ray clusters:

```python
from vllm_playground.ray_serve import ray_backend

# Auto-connects to local cluster if running
result = await ray_backend.start_model(config)
```

### Option 2: Explicit Connection

Specify cluster address explicitly:

```python
# Connect to specific cluster
await ray_backend.initialize_ray(address="192.168.1.100:6379")

# Then start model
await ray_backend.start_model(config)
```

### Option 3: Environment Variable

```bash
export RAY_ADDRESS="127.0.0.1:6379"
python -m vllm_playground.cli serve --ray
```

## Resource Planning

### GPU Requirements

| Model Size | Tensor Parallel | GPUs Needed | Object Store |
|-----------|----------------|-------------|--------------|
| 7B        | 1              | 1x A100     | 8 GB         |
| 13B       | 1              | 1x A100     | 12 GB        |
| 70B       | 4              | 4x A100     | 40 GB        |
| 70B       | 8              | 8x A100     | 80 GB        |

### CPU Requirements

- Minimum: 2 CPUs per GPU
- Recommended: 4-8 CPUs per GPU
- For CPU-only: 8-16 CPUs for small models

### Memory Requirements

**Object Store Memory**:
- Rule of thumb: Model size × 1.5
- Example: 7B model → ~10 GB object store
- Set to 30-40% of system RAM

**System RAM**:
- Minimum: 2x model size
- Recommended: 3-4x model size
- Example: 7B model → 32 GB RAM

## Troubleshooting

### Cluster Won't Start

**Issue**: "Address already in use"
```bash
# Stop existing cluster first
python launch_cluster.py stop

# Or use different port
# Edit config: port: 6380
```

**Issue**: "No GPU detected"
```bash
# Check GPU availability
nvidia-smi

# Use CPU config instead
python launch_cluster.py --config configs/local_cpu.yaml start
```

### Connection Issues

**Issue**: "Cannot connect to cluster"
```bash
# Verify cluster is running
python launch_cluster.py status

# Check firewall rules for port 6379
sudo ufw allow 6379
```

**Issue**: "Dashboard not accessible"
```bash
# Check dashboard port
# Default: http://localhost:8265

# If using remote cluster
ssh -L 8265:localhost:8265 user@remote-host
```

### Resource Issues

**Issue**: "Out of memory" errors
```bash
# Increase object store memory in config
resources:
  object_store_memory: 21474836480  # 20 GB

# Or reduce model size/batch size
```

**Issue**: "GPU memory full"
```bash
# Check GPU memory usage
nvidia-smi

# Reduce gpu_memory_utilization in vLLM config
# From 0.9 to 0.7 or 0.8
```

## Advanced Usage

### Multiple Clusters

Run multiple clusters on different ports:

```yaml
# cluster1.yaml
cluster:
  port: 6379
  dashboard:
    port: 8265

# cluster2.yaml
cluster:
  port: 6380
  dashboard:
    port: 8266
```

### Remote Cluster Access

1. Start cluster on remote machine:
```bash
# On remote: 192.168.1.100
python launch_cluster.py start
```

2. Connect from vLLM playground:
```python
await ray_backend.initialize_ray(address="192.168.1.100:6379")
```

### SSH Tunneling

For secure remote access:
```bash
# On local machine
ssh -L 6379:localhost:6379 -L 8265:localhost:8265 user@remote-host

# Now connect to localhost:6379
```

## Monitoring

### Ray Dashboard

Access at http://localhost:8265 (default)

Features:
- Resource utilization (CPU, GPU, Memory)
- Active tasks and actors
- Logs and metrics
- Cluster topology

### Command Line Monitoring

```bash
# Detailed status
ray status

# Monitor resources
watch -n 1 ray status

# View logs
ray logs
```

## Best Practices

1. **Resource Allocation**:
   - Don't over-allocate GPUs (leave headroom for system)
   - Set object store to 30-40% of RAM
   - Use placement groups for tensor parallelism

2. **Configuration Management**:
   - Use version control for configs
   - Document custom configurations
   - Test configs before production use

3. **Security**:
   - Use firewall rules for remote clusters
   - Enable authentication for production
   - Use SSH tunneling for remote access

4. **Monitoring**:
   - Check dashboard regularly
   - Monitor GPU utilization
   - Set up alerts for resource issues

## See Also

- [Ray Backend README](README.md) - Ray backend architecture
- [Ray Documentation](https://docs.ray.io/en/latest/cluster/getting-started.html)
- [vLLM Documentation](https://docs.vllm.ai/en/stable/)
