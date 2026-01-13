# ray-serve-cai

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ray Version](https://img.shields.io/badge/ray-2.48.0+-green.svg)](https://docs.ray.io/)

**Ray Serve orchestration for LLM inference in Cloudera AI**

A production-ready Python package for deploying and managing Large Language Model (LLM) inference engines on Ray Serve, optimized for Cloudera Machine Learning (CML) and Cloudera AI environments.

## Features

- 🔌 **Multi-Engine Support**: Plugin architecture supporting vLLM, SGLang, and custom engines
- 🎯 **OpenAI-Compatible API**: Drop-in replacement for OpenAI API clients
- ⚡ **Automatic Cluster Management**: Auto-detect and connect to existing Ray clusters
- 🌐 **CAI-Based Distributed Clusters**: Launch Ray clusters using CML Applications as nodes
- 🔧 **Tensor Parallelism**: Built-in support for multi-GPU inference with placement groups
- 📝 **YAML Configuration**: Simple, declarative cluster and model configuration
- 🏥 **Health Monitoring**: Built-in health checks and deployment status monitoring
- 🎛️ **Production-Ready**: Comprehensive logging, error handling, and lifecycle management

## Installation

### Basic Installation

```bash
pip install ray-serve-cai
```

### With vLLM Support

```bash
pip install ray-serve-cai[vllm]
```

### With All Engines

```bash
pip install ray-serve-cai[all]
```

### Development Installation

```bash
git clone https://github.com/cloudera/ray-serve-cai.git
cd ray-serve-cai
pip install -e ".[dev]"
```

## Quick Start

### Basic Usage

```python
import asyncio
from ray_serve_cai import RayBackend

async def main():
    # Initialize backend
    backend = RayBackend()
    await backend.initialize_ray()

    # Configure model
    config = {
        'model': 'meta-llama/Llama-2-7b-hf',
        'tensor_parallel_size': 2,
        'gpu_memory_utilization': 0.9,
    }

    # Start deployment
    result = await backend.start_model(config, engine='vllm')
    print(f"Model deployed: {result}")

    # Model is now available at http://localhost:8000/v1

if __name__ == "__main__":
    asyncio.run(main())
```

### Using Different Engines

```python
# Use vLLM (default)
await backend.start_model(config, engine='vllm')

# Use SGLang (when fully implemented)
await backend.start_model(config, engine='sglang')

# Engine auto-selected from config
config['engine'] = 'vllm'
await backend.start_model(config)
```

### OpenAI-Compatible API

Once deployed, use the standard OpenAI client:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"  # Not required for local deployment
)

response = client.chat.completions.create(
    model="meta-llama/Llama-2-7b-hf",
    messages=[
        {"role": "user", "content": "Hello, how are you?"}
    ]
)

print(response.choices[0].message.content)
```

### CAI-Based Distributed Clusters

Launch Ray clusters using CML Applications as nodes (1 head + N workers):

```bash
# Create configuration file
cat > cai_cluster.yaml <<EOF
cai:
  host: https://ml.example.cloudera.site
  api_key: your-api-key
  project_id: your-project-id
  num_workers: 2
  resources:
    cpu: 16
    memory: 64
    num_gpus: 1
EOF

# Start CAI cluster
python -m ray_serve_cai.launch_cluster --config cai_cluster.yaml start-cai

# Check cluster status
python -m ray_serve_cai.launch_cluster status-cai

# Connect to the cluster
import ray
ray.init(address="ray://cluster-head-address:6379")
```

See the [CAI Cluster Guide](docs/cai_cluster_guide.md) for detailed instructions.

## Documentation

- **[Quickstart Guide](docs/QUICKSTART.md)** - Get started in 5 minutes
- **[Installation Guide](docs/INSTALLATION.md)** - Detailed installation instructions
- **[Cluster Setup](docs/CLUSTER_SETUP.md)** - Configure Ray clusters
- **[CAI Cluster Guide](docs/cai_cluster_guide.md)** - Launch distributed clusters on CML
- **[API Reference](docs/API.md)** - Complete API documentation
- **[Developer Guide](docs/DEVELOPER_GUIDE.md)** - Adding custom engines
- **[Architecture](docs/architecture.md)** - System design and architecture

## Supported Engines

| Engine | Status | Description |
|--------|--------|-------------|
| **vLLM** | ✅ Stable | High-throughput LLM serving with PagedAttention |
| **SGLang** | 🚧 Planned | Fast serving with RadixAttention and structured generation |
| **Custom** | 🔌 Extensible | Implement your own engine using our protocols |

## Configuration

### YAML Configuration

```yaml
# config.yaml
cluster:
  name: "my-llm-cluster"
  ray_address: "auto"  # Auto-detect existing cluster

model:
  model_source: "meta-llama/Llama-2-7b-hf"
  tensor_parallel_size: 2
  gpu_memory_utilization: 0.9
  dtype: "auto"

deployment:
  port: 8000
  use_cpu: false
```

### Launch from YAML

```bash
python -m ray_serve_cai.launch_cluster --config config.yaml
```

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 RayBackend                      │
│         (Orchestration Layer)                   │
└────────────┬────────────────────────────────────┘
             │
             ├─► Engine Registry
             │   (Plugin Management)
             │
             ├─► vLLM Engine ──────► vLLM Runtime
             │   • ConfigBuilder
             │   • DeploymentFactory
             │
             └─► SGLang Engine ────► SGLang Runtime
                 • ConfigBuilder     (Future)
                 • DeploymentFactory
```

## Adding Custom Engines

Implement three protocols to add a new engine:

```python
from ray_serve_cai import (
    LLMEngineProtocol,
    ConfigBuilderProtocol,
    DeploymentFactoryProtocol,
    register_engine
)

class MyEngine(LLMEngineProtocol):
    @property
    def engine_type(self) -> str:
        return "my_engine"

    # Implement other protocol methods...

class MyConfigBuilder(ConfigBuilderProtocol):
    def build_config(self, user_config):
        # Convert user config to engine config
        pass

class MyDeploymentFactory(DeploymentFactoryProtocol):
    def create_deployment(self, engine_config, **kwargs):
        # Create Ray Serve deployment
        pass

# Register your engine
register_engine(
    engine_type="my_engine",
    engine_class=MyEngine,
    config_builder=MyConfigBuilder(),
    deployment_factory=MyDeploymentFactory()
)
```

See [Developer Guide](docs/DEVELOPER_GUIDE.md) for details.

## Use Cases

### Cloudera Machine Learning (CML)

Deploy LLMs in CML workspaces with automatic GPU allocation:

```python
# CML Session
backend = RayBackend()
await backend.initialize_ray(address="auto")  # Connects to CML Ray cluster
await backend.start_model(config)
```

### Multi-Node Clusters

Scale across multiple nodes with tensor parallelism:

```yaml
cluster:
  ray_address: "ray://head-node:10001"

model:
  tensor_parallel_size: 4  # Span 4 GPUs across nodes
```

### Local Development

Test locally before deploying:

```python
# Starts local Ray cluster automatically
backend = RayBackend()
await backend.initialize_ray()  # Local mode
```

## Performance

- **High Throughput**: Leverage vLLM's PagedAttention for 2-24x higher throughput
- **Low Latency**: Optimized request batching and continuous batching
- **Multi-GPU**: Efficient tensor parallelism across multiple GPUs
- **Resource Management**: Ray's placement groups for optimal GPU allocation

## Requirements

- Python 3.9+
- Ray[serve] >= 2.48.0
- For vLLM: vLLM >= 0.6.0
- For SGLang: sglang >= 0.1.0 (when available)
- CUDA 11.8+ (for GPU inference)

## Contributing

Contributions are welcome! Please see our [Contributing Guide](CONTRIBUTING.md) for details.

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

Apache License 2.0 - see [LICENSE](LICENSE) file for details.

## Acknowledgments

- **Ray Team** - For the excellent Ray Serve framework
- **vLLM Team** - For the high-performance inference engine
- **SGLang Team** - For innovative serving techniques
- **Cloudera** - For supporting open-source ML infrastructure

## Support

- 📖 [Documentation](docs/README.md)
- 🐛 [Issue Tracker](https://github.com/cloudera/ray-serve-cai/issues)
- 💬 [Discussions](https://github.com/cloudera/ray-serve-cai/discussions)

## Roadmap

- [x] vLLM engine support
- [x] Multi-engine plugin architecture
- [x] OpenAI-compatible API
- [x] Tensor parallelism support
- [ ] SGLang engine implementation
- [ ] LoRA adapter support
- [ ] Quantization support (FP8, INT8)
- [ ] Streaming responses
- [ ] Request queuing and prioritization
- [ ] Comprehensive metrics and monitoring
- [ ] Kubernetes deployment templates

---

**Made with ❤️ for the Cloudera AI community**
