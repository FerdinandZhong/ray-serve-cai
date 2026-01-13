# Ray Serve Backend for vLLM Playground

This module provides Ray-based orchestration for vLLM deployments, offering an alternative to container-based deployment.

## Architecture

```
ray_serve/
├── __init__.py              # Package exports
├── ray_backend.py           # Main orchestrator (start/stop/status)
├── launch_cluster.py        # Cluster launcher script
├── configs/                 # YAML configuration files
│   ├── local_basic.yaml     # Basic local cluster
│   ├── local_gpu.yaml       # GPU-optimized cluster
│   ├── local_cpu.yaml       # CPU-only cluster
│   └── multi_node_example.yaml  # Multi-node template
├── engines/
│   ├── __init__.py          # Engine exports
│   ├── vllm_engine.py       # vLLM Ray Serve deployment
│   └── vllm_config.py       # vLLM configuration builder
├── README.md                # This file
└── CLUSTER_SETUP.md         # Cluster setup guide
```

## Features

### Ray Backend Orchestrator ([ray_backend.py](ray_backend.py:1-1))

The `RayBackend` class provides:

- **Ray Cluster Management**:
  - Auto-detect existing Ray clusters
  - Connect to remote clusters via address
  - Start local clusters automatically
  - One-time availability check during initialization

- **Model Lifecycle**:
  - `start_model()` - Deploy vLLM with Ray Serve
  - `stop_model()` - Stop deployment
  - `get_status()` - Check deployment status
  - `get_logs()` - Retrieve deployment logs

- **Health Monitoring**:
  - Wait for deployment readiness
  - Health check endpoint integration

### vLLM Engine ([engines/vllm_engine.py](engines/vllm_engine.py:1-1))

The `VLLMEngine` deployment provides:

- **OpenAI-Compatible API**:
  - `POST /v1/completions` - Text completion
  - `POST /v1/chat/completions` - Chat completion
  - `GET /v1/models` - List models
  - `GET /health` - Health check

- **Key Features**:
  - Reuses vLLM's built-in OpenAI request handlers
  - Supports tensor parallelism via Ray placement groups
  - Proper GPU resource allocation
  - CPU mode support

### Configuration Builder ([engines/vllm_config.py](engines/vllm_config.py:1-1))

The config builder provides:

- **Configuration Translation**:
  - Converts user config to vLLM `AsyncEngineArgs`
  - Handles GPU/CPU mode differences
  - Sets up HuggingFace tokens
  - Validates configuration

### Cluster Launcher ([launch_cluster.py](launch_cluster.py:1-1))

The cluster launcher script provides:

- **Cluster Management Commands**:
  - `start` - Start local Ray cluster
  - `stop` - Stop Ray cluster
  - `status` - Check cluster status
  - `get-address` - Get cluster address
  - `start-autoscaler` - Start multi-node cluster with autoscaling

- **YAML Configuration Support**:
  - Pre-configured templates for different scenarios
  - Custom resource allocation
  - Dashboard configuration

See [CLUSTER_SETUP.md](CLUSTER_SETUP.md) for detailed setup instructions.

## Quick Start

### 1. Launch a Ray Cluster

```bash
# Start local cluster with GPU support
cd vllm_playground/ray_serve
python launch_cluster.py --config configs/local_gpu.yaml start

# Check cluster status
python launch_cluster.py status

# Get cluster address
python launch_cluster.py get-address
```

### 2. Deploy vLLM Model

## Usage

### Basic Usage

```python
from vllm_playground.ray_serve import ray_backend

# Start vLLM model
config = {
    'model': 'TinyLlama/TinyLlama-1.1B-Chat-v1.0',
    'tensor_parallel_size': 1,
    'port': 8000,
}

result = await ray_backend.start_model(config, wait_ready=True)
print(f"Model started: {result}")

# Check status
status = await ray_backend.get_status()
print(f"Status: {status}")

# Stop model
await ray_backend.stop_model()
```

### Connect to Existing Ray Cluster

```python
# Connect to remote cluster
init_result = await ray_backend.initialize_ray(address="192.168.1.100:6379")

# Then start model as usual
result = await ray_backend.start_model(config)
```

### Tensor Parallelism

```python
# Multi-GPU deployment
config = {
    'model': 'meta-llama/Llama-2-7b-chat-hf',
    'tensor_parallel_size': 2,  # Use 2 GPUs
    'gpu_memory_utilization': 0.9,
    'port': 8000,
}

result = await ray_backend.start_model(config)
```

## Ray Placement Groups

For tensor parallelism with multiple GPUs, the backend automatically configures Ray placement groups:

- **Single GPU** (`tensor_parallel_size=1`):
  - Simple resource allocation: 1 GPU, 2 CPUs

- **Multi-GPU** (`tensor_parallel_size>1`):
  - Creates placement group with bundles
  - Each bundle: 1 GPU, 1 CPU (Ray constraint)
  - Strategy: PACK (prefer same node)

Example configuration for 4-GPU tensor parallelism:

```python
placement_group_bundles = [
    {"GPU": 1, "CPU": 1},
    {"GPU": 1, "CPU": 1},
    {"GPU": 1, "CPU": 1},
    {"GPU": 1, "CPU": 1},
]
```

## Integration with vLLM Playground

The Ray backend integrates with the playground's existing infrastructure:

1. **Configuration Format**: Uses the same `VLLMConfig` structure
2. **API Compatibility**: Provides similar methods to `container_manager`
3. **Port Management**: Respects port configuration
4. **Health Checks**: Uses same readiness checking logic

## Dependencies

Required packages:
```bash
pip install ray[serve] vllm
```

Optional for health checks:
```bash
pip install aiohttp
```

## References

- [Ray Serve LLM Documentation](https://docs.ray.io/en/latest/serve/llm/index.html)
- [vLLM OpenAI Server](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/)
- [Ray Placement Groups](https://docs.ray.io/en/latest/serve/llm/user-guides/cross-node-parallelism.html)
- [vLLM Distributed Serving](https://docs.vllm.ai/en/stable/serving/distributed_serving/)

## Future Enhancements

### SGLang Engine Support

The architecture is designed to support multiple engines. To add SGLang:

1. Create `engines/sglang_engine.py` with similar structure
2. Create `engines/sglang_config.py` for configuration
3. Update `ray_backend.py` to support engine selection

Example structure:
```python
# engines/sglang_engine.py
@serve.deployment(name="sglang-deployment")
class SGLangEngine:
    # Similar to VLLMEngine but using SGLang
    pass
```

### Additional Features

- Auto-scaling based on request load
- Multi-model serving with model routing
- LoRA adapter support
- Metrics and monitoring integration
