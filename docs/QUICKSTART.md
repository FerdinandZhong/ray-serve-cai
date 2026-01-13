# Quick Start: vLLM with Ray Backend

This guide shows you how to deploy vLLM on a Ray cluster in under 5 minutes.

## Prerequisites

```bash
# Install vLLM Playground with Ray support
pip install vllm-playground[ray]

# Install vLLM
pip install vllm
```

## Method 1: Auto-Connect (Simplest)

The Ray backend will automatically detect and use any running Ray cluster.

### Step 1: Start Ray Cluster

```bash
cd vllm_playground/ray_serve

# For GPU
python launch_cluster.py --config configs/local_gpu.yaml start

# For CPU (testing)
python launch_cluster.py --config configs/local_cpu.yaml start
```

### Step 2: Deploy vLLM

```python
import asyncio
from vllm_playground.ray_serve import ray_backend

async def deploy():
    # Ray backend auto-detects the cluster
    config = {
        'model': 'TinyLlama/TinyLlama-1.1B-Chat-v1.0',
        'tensor_parallel_size': 1,
        'port': 8000,
    }

    result = await ray_backend.start_model(config, wait_ready=True)
    print(f"Model ready at: http://localhost:8000")

asyncio.run(deploy())
```

### Step 3: Test the Model

```bash
curl -X POST http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
    "prompt": "Hello, how are you?",
    "max_tokens": 50
  }'
```

## Method 2: Explicit Connection

Connect to a specific Ray cluster by address.

### Step 1: Get Cluster Address

```bash
# On the machine running Ray cluster
cd vllm_playground/ray_serve
python launch_cluster.py get-address
# Output: 127.0.0.1:6379
```

### Step 2: Connect and Deploy

```python
import asyncio
from vllm_playground.ray_serve import ray_backend

async def deploy():
    # Connect to specific cluster
    await ray_backend.initialize_ray(address="127.0.0.1:6379")

    # Deploy model
    config = {
        'model': 'TinyLlama/TinyLlama-1.1B-Chat-v1.0',
        'port': 8000,
    }

    await ray_backend.start_model(config, wait_ready=True)

asyncio.run(deploy())
```

## Method 3: Environment Variable

Set the Ray address as an environment variable.

### Step 1: Set Environment

```bash
# Set Ray address
export RAY_ADDRESS=127.0.0.1:6379

# Or for remote cluster
export RAY_ADDRESS=192.168.1.100:6379
```

### Step 2: Deploy Using Example Script

```bash
# Use the provided example script
python examples/ray_deployment_example.py
```

This will:
1. Connect to the Ray cluster at `$RAY_ADDRESS`
2. Deploy TinyLlama model
3. Wait for it to be ready
4. Run test queries

## Method 4: All-in-One Script

Use the complete example script with all features:

```bash
# Deploy model
python examples/ray_deployment_example.py

# Check status
python examples/ray_deployment_example.py --status

# Stop deployment
python examples/ray_deployment_example.py --stop
```

## Common Scenarios

### Scenario 1: Local Development (CPU)

```bash
# Terminal 1: Start CPU cluster
cd vllm_playground/ray_serve
python launch_cluster.py --config configs/local_cpu.yaml start

# Terminal 2: Deploy small model
python examples/ray_deployment_example.py
```

### Scenario 2: Single GPU Server

```bash
# Terminal 1: Start GPU cluster
cd vllm_playground/ray_serve
python launch_cluster.py --config configs/local_gpu.yaml start

# Terminal 2: Deploy larger model
python -c "
import asyncio
from vllm_playground.ray_serve import ray_backend

async def deploy():
    config = {
        'model': 'meta-llama/Llama-2-7b-chat-hf',
        'tensor_parallel_size': 1,
        'port': 8000,
        'hf_token': 'your_hf_token_here',  # For gated models
    }
    await ray_backend.start_model(config, wait_ready=True)

asyncio.run(deploy())
"
```

### Scenario 3: Multi-GPU Tensor Parallelism

```bash
# Start cluster with 4 GPUs
cd vllm_playground/ray_serve

# Edit configs/local_gpu.yaml to set num_gpus: 4
python launch_cluster.py --config configs/local_gpu.yaml start

# Deploy with tensor parallelism
python -c "
import asyncio
from vllm_playground.ray_serve import ray_backend

async def deploy():
    config = {
        'model': 'meta-llama/Llama-2-70b-chat-hf',
        'tensor_parallel_size': 4,  # Use 4 GPUs
        'port': 8000,
        'hf_token': 'your_hf_token_here',
    }
    await ray_backend.start_model(config, wait_ready=True)

asyncio.run(deploy())
"
```

### Scenario 4: Remote Ray Cluster

```bash
# On remote server (192.168.1.100)
cd vllm_playground/ray_serve
python launch_cluster.py --config configs/local_gpu.yaml start

# On local machine
export RAY_ADDRESS=192.168.1.100:6379
python examples/ray_deployment_example.py
```

## Using with OpenAI Client

Once deployed, you can use the OpenAI Python client:

```python
from openai import OpenAI

# Connect to vLLM deployment
client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"  # vLLM doesn't require real API key
)

# Text completion
response = client.completions.create(
    model="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
    prompt="Once upon a time",
    max_tokens=100
)
print(response.choices[0].text)

# Chat completion
response = client.chat.completions.create(
    model="TinyLlama/TinyLlama-1.1B-Chat-v1.0",
    messages=[
        {"role": "user", "content": "Tell me a joke"}
    ],
    max_tokens=100
)
print(response.choices[0].message.content)
```

## Checking Status

### Via Python

```python
import asyncio
from vllm_playground.ray_serve import ray_backend

async def check():
    status = await ray_backend.get_status()
    print(f"Running: {status['running']}")
    print(f"Status: {status['status']}")

asyncio.run(check())
```

### Via Ray Dashboard

Open http://localhost:8265 in your browser to see:
- Resource utilization (CPU, GPU, Memory)
- Active deployments
- Request metrics
- Logs

### Via Ray CLI

```bash
# Cluster status
ray status

# Detailed serve status
ray serve status
```

## Stopping Deployment

### Method 1: Python

```python
import asyncio
from vllm_playground.ray_serve import ray_backend

asyncio.run(ray_backend.stop_model())
```

### Method 2: Example Script

```bash
python examples/ray_deployment_example.py --stop
```

### Method 3: Stop Entire Cluster

```bash
cd vllm_playground/ray_serve
python launch_cluster.py stop
```

## Troubleshooting Quick Fixes

### Issue: Cannot connect to Ray cluster

```bash
# Check if cluster is running
ray status

# If no cluster, start one
cd vllm_playground/ray_serve
python launch_cluster.py start
```

### Issue: "Session name does not match"

This happens when Ray cluster state is stale. Fix:

```bash
# Stop existing cluster
ray stop

# Clean up (removes all Ray state)
rm -rf /tmp/ray

# Start fresh cluster
cd vllm_playground/ray_serve
python launch_cluster.py start
```

### Issue: Port already in use

```bash
# Use a different port
config = {
    'model': '...',
    'port': 8001,  # Changed from 8000
}
```

### Issue: Out of memory

```bash
# Reduce GPU memory utilization
config = {
    'model': '...',
    'gpu_memory_utilization': 0.7,  # Reduced from 0.9
}
```

## Next Steps

- **Full documentation**: See [CLUSTER_SETUP.md](CLUSTER_SETUP.md)
- **Configuration options**: See [README.md](README.md)
- **Advanced deployments**: See [configs/](configs/) for templates

## Quick Reference

```bash
# Start cluster
cd vllm_playground/ray_serve
python launch_cluster.py --config configs/local_gpu.yaml start

# Deploy model (auto-connect)
python examples/ray_deployment_example.py

# Check status
python examples/ray_deployment_example.py --status

# Test with curl
curl http://localhost:8000/health

# Stop deployment
python examples/ray_deployment_example.py --stop

# Stop cluster
python launch_cluster.py stop
```
