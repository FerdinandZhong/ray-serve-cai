"""
ray-serve-cai: Ray Serve orchestration for LLM inference in Cloudera AI

A production-ready package for deploying and managing LLM inference engines
(vLLM, SGLang, etc.) on Ray Serve, optimized for Cloudera AI environments.

Key Features:
- Multi-engine support with plugin architecture
- OpenAI-compatible API endpoints
- Automatic Ray cluster detection and management
- Tensor parallelism with placement groups
- YAML-based configuration
- Health monitoring and logging

Basic Usage:
    from ray_serve_cai import RayBackend

    backend = RayBackend()
    await backend.initialize_ray()

    config = {
        'model': 'meta-llama/Llama-2-7b-hf',
        'tensor_parallel_size': 2
    }

    result = await backend.start_model(config, engine='vllm')

For more information:
- Documentation: docs/README.md
- Quickstart: docs/QUICKSTART.md
- API Reference: docs/API.md
"""

__version__ = "0.1.0"

from .ray_backend import RayBackend, ray_backend

# Import engine components for advanced usage
from .engines import (
    get_registry,
    register_engine,
    EngineRegistry,
    LLMEngineProtocol,
    ConfigBuilderProtocol,
    DeploymentFactoryProtocol,
)

__all__ = [
    # Main backend
    'RayBackend',
    'ray_backend',
    # Engine registry
    'get_registry',
    'register_engine',
    'EngineRegistry',
    # Protocols
    'LLMEngineProtocol',
    'ConfigBuilderProtocol',
    'DeploymentFactoryProtocol',
    # Version
    '__version__',
]
