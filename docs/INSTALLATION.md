# Installation Guide for Ray Backend

This guide covers installing the Ray backend dependencies for vLLM Playground.

## Installation Options

### Option 1: Install with Ray Support (Recommended)

Install vLLM Playground with Ray backend support:

```bash
# Install from PyPI with Ray support
pip install vllm-playground[ray]
```

This installs:
- vLLM Playground core
- Ray[serve] >= 2.9.0
- PyYAML >= 6.0

### Option 2: Install from Source with Ray Support

```bash
# Clone the repository
git clone https://github.com/micytao/vllm-playground.git
cd vllm-playground

# Install with Ray support
pip install -e ".[ray]"
```

### Option 3: Install Everything (Development)

```bash
# Install all optional dependencies
pip install vllm-playground[all]

# Or from source
pip install -e ".[all]"
```

This includes:
- Ray backend (ray, pyyaml)
- Benchmarking tools (guidellm)
- Development tools (pytest, black, ruff)

### Option 4: Manual Installation

If you prefer to install dependencies separately:

```bash
# Install base vLLM Playground
pip install vllm-playground

# Then install Ray separately
pip install "ray[serve]>=2.9.0" pyyaml>=6.0
```

## Verifying Installation

Check that Ray is properly installed:

```bash
# Check Ray version
python -c "import ray; print(f'Ray version: {ray.__version__}')"

# Check Ray Serve
python -c "from ray import serve; print('Ray Serve is available')"

# Test cluster launcher
cd vllm_playground/ray_serve
python launch_cluster.py --help
```

## Dependencies

### Core Dependencies (Always Required)

```
fastapi>=0.104.0
uvicorn>=0.24.0
websockets>=12.0
aiohttp>=3.9.0
pydantic>=2.4.0
python-multipart>=0.0.6
numpy>=1.24.0
jinja2>=3.1.0
psutil>=5.9.0
requests>=2.31.0
```

### Ray Backend Dependencies (Optional)

```
ray[serve]>=2.9.0
pyyaml>=6.0
```

### Benchmark Dependencies (Optional)

```
guidellm>=0.3.1
```

### Development Dependencies (Optional)

```
pytest>=7.0.0
pytest-asyncio>=0.21.0
black>=23.0.0
ruff>=0.1.0
```

## Installing vLLM

vLLM itself is required for running models (not included in vllm-playground dependencies):

```bash
# Install vLLM (GPU)
pip install vllm

# Install vLLM (CPU - requires special build)
# See: https://docs.vllm.ai/en/stable/getting_started/cpu-installation.html
```

## System Requirements

### Minimum Requirements

- Python 3.9 or higher
- 8 GB RAM
- 20 GB disk space

### Recommended for GPU Mode

- Python 3.10 or 3.11
- 32 GB RAM (for 7B models)
- NVIDIA GPU with CUDA 12.1+
- 100 GB disk space (for model weights)

### Recommended for Ray Cluster

- Multiple GPUs or multiple nodes
- High-speed networking for multi-node
- 64+ GB RAM per node
- NFS or shared storage (optional, for multi-node)

## Platform-Specific Notes

### Linux

Standard installation works on most Linux distributions:

```bash
pip install vllm-playground[ray]
```

### macOS

Ray and vLLM work on macOS (ARM64 and x86_64):

```bash
# Install with Ray
pip install vllm-playground[ray]

# Note: GPU mode requires NVIDIA GPU (not available on macOS)
# Use CPU mode or connect to remote Linux cluster
```

### Windows

Ray and vLLM have limited Windows support:

```bash
# Use WSL2 (Windows Subsystem for Linux)
wsl --install

# Then follow Linux installation instructions
```

## Troubleshooting

### Issue: "No module named 'ray'"

**Solution**:
```bash
pip install "ray[serve]>=2.9.0"
```

### Issue: "ray.serve not found"

**Solution**: Install ray with serve support:
```bash
pip install "ray[serve]"  # Note the [serve] extra
```

### Issue: "Import error: yaml"

**Solution**:
```bash
pip install pyyaml
```

### Issue: "vLLM not found"

**Solution**: vLLM must be installed separately:
```bash
pip install vllm
```

### Issue: Ray version conflict

**Solution**: Upgrade Ray to >= 2.9.0:
```bash
pip install --upgrade "ray[serve]>=2.9.0"
```

## Next Steps

After installation:

1. **Launch a cluster**: See [CLUSTER_SETUP.md](CLUSTER_SETUP.md)
2. **Deploy a model**: See [README.md](README.md)
3. **Configure resources**: Check example configs in [configs/](configs/)

## Support

For issues and questions:

- GitHub Issues: https://github.com/micytao/vllm-playground/issues
- Ray Documentation: https://docs.ray.io/
- vLLM Documentation: https://docs.vllm.ai/
