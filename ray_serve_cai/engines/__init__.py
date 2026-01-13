"""
Engine implementations for Ray Serve
Supports multiple LLM engines (vLLM, SGLang, etc.)
"""

import logging

# Import protocols and registry
from .base import (
    LLMEngineProtocol,
    ConfigBuilderProtocol,
    DeploymentFactoryProtocol,
    EngineComponents,
)
from .registry import get_registry, register_engine, EngineRegistry

# Import vLLM engine components
from .vllm_engine import VLLMEngine, create_vllm_deployment
from .vllm_config import (
    build_vllm_engine_config,
    validate_vllm_config,
    VLLMConfigBuilder,
    VLLMDeploymentFactory,
)

logger = logging.getLogger(__name__)

# Register vLLM engine on module import
try:
    register_engine(
        engine_type="vllm",
        engine_class=VLLMEngine,
        config_builder=VLLMConfigBuilder(),
        deployment_factory=VLLMDeploymentFactory(),
        set_as_default=True  # vLLM is the default engine
    )
    logger.info("✅ Registered vLLM engine as default")
except Exception as e:
    logger.warning(f"Failed to register vLLM engine: {e}")

# Try to register SGLang engine (optional, fail gracefully)
try:
    from .sglang_engine import SGLangEngine
    from .sglang_config import SGLangConfigBuilder, SGLangDeploymentFactory

    register_engine(
        engine_type="sglang",
        engine_class=SGLangEngine,
        config_builder=SGLangConfigBuilder(),
        deployment_factory=SGLangDeploymentFactory(),
        set_as_default=False
    )
    logger.info("✅ Registered SGLang engine")
except ImportError:
    logger.debug("SGLang engine not available (optional dependency)")
except Exception as e:
    logger.warning(f"Failed to register SGLang engine: {e}")

__all__ = [
    # Legacy exports (backward compatibility)
    'VLLMEngine',
    'create_vllm_deployment',
    'build_vllm_engine_config',
    'validate_vllm_config',
    # New protocol exports
    'LLMEngineProtocol',
    'ConfigBuilderProtocol',
    'DeploymentFactoryProtocol',
    'EngineComponents',
    # Registry exports
    'get_registry',
    'register_engine',
    'EngineRegistry',
    # Class-based exports
    'VLLMConfigBuilder',
    'VLLMDeploymentFactory',
]
