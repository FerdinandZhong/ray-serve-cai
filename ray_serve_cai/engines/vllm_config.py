"""
vLLM Engine Configuration Builder
Converts user configuration to vLLM engine arguments
"""

import logging
from typing import Dict, Any, Optional
from ray import serve

logger = logging.getLogger(__name__)


def build_vllm_engine_config(user_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build vLLM engine configuration from user configuration.

    Translates the playground's configuration format into vLLM's
    AsyncEngineArgs format.

    Args:
        user_config: User configuration dictionary containing vLLM parameters

    Returns:
        Dictionary with engine configuration for vLLM AsyncEngineArgs

    References:
        - vLLM AsyncEngineArgs: https://docs.vllm.ai/en/stable/api/engine_args.html
    """
    engine_config = {}

    # Core parameters
    model_source = user_config.get('model_source') or user_config.get('model')
    if not model_source:
        raise ValueError("Model name is required")
    engine_config['model'] = model_source

    # Data type
    use_cpu = user_config.get('use_cpu', False)
    if use_cpu and user_config.get('dtype', 'auto') == 'auto':
        # For CPU mode, default to bfloat16
        engine_config['dtype'] = 'bfloat16'
    else:
        engine_config['dtype'] = user_config.get('dtype', 'auto')

    # Max model length (optional)
    if user_config.get('max_model_len'):
        engine_config['max_model_len'] = user_config['max_model_len']

    # Trust remote code
    if user_config.get('trust_remote_code', False):
        engine_config['trust_remote_code'] = True

    # GPU-specific parameters
    if not use_cpu:
        # Tensor parallelism
        tensor_parallel_size = user_config.get('tensor_parallel_size', 1)
        engine_config['tensor_parallel_size'] = tensor_parallel_size

        # GPU memory utilization
        engine_config['gpu_memory_utilization'] = user_config.get('gpu_memory_utilization', 0.9)

        # GPU device selection (for subprocess mode compatibility)
        if user_config.get('gpu_device'):
            # Note: Ray handles GPU allocation, but we can pass this for reference
            logger.info(f"GPU device specification: {user_config['gpu_device']}")

        # Distributed executor backend
        if tensor_parallel_size > 1:
            # Use Ray for multi-GPU tensor parallelism
            engine_config['distributed_executor_backend'] = 'ray'
            logger.info("Using Ray distributed executor for tensor parallelism")

    # CPU-specific parameters
    if use_cpu:
        # Explicitly set device to CPU
        # Note: vLLM may not have a direct 'device' arg in AsyncEngineArgs
        # This is handled by vLLM's internal logic
        logger.info("CPU mode enabled")

    # Download directory
    if user_config.get('download_dir'):
        engine_config['download_dir'] = user_config['download_dir']

    # Load format
    engine_config['load_format'] = user_config.get('load_format', 'auto')

    # HuggingFace token for gated models
    # Note: Set as environment variable HF_TOKEN instead
    if user_config.get('hf_token'):
        import os
        os.environ['HF_TOKEN'] = user_config['hf_token']
        os.environ['HUGGING_FACE_HUB_TOKEN'] = user_config['hf_token']
        logger.info("HuggingFace token configured")

    # Logging configuration
    if user_config.get('disable_log_stats', False):
        engine_config['disable_log_stats'] = True

    # Prefix caching
    if user_config.get('enable_prefix_caching', False):
        engine_config['enable_prefix_caching'] = True

    # Custom chat template
    if user_config.get('custom_chat_template'):
        # vLLM accepts chat_template as a string
        engine_config['chat_template'] = user_config['custom_chat_template']

    logger.info(f"Built vLLM engine config: {engine_config}")

    return engine_config


def validate_vllm_config(user_config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Validate user configuration for vLLM engine.

    Args:
        user_config: User configuration dictionary

    Returns:
        Tuple of (is_valid, error_message)
        - (True, None) if valid
        - (False, error_message) if invalid
    """
    # Check required fields
    model = user_config.get('model_source') or user_config.get('model')
    if not model:
        return False, "Model name is required"

    # Validate tensor parallel size
    tensor_parallel_size = user_config.get('tensor_parallel_size', 1)
    if not isinstance(tensor_parallel_size, int) or tensor_parallel_size < 1:
        return False, "tensor_parallel_size must be a positive integer"

    # Validate GPU memory utilization
    gpu_mem_util = user_config.get('gpu_memory_utilization', 0.9)
    if not isinstance(gpu_mem_util, (int, float)) or not (0 < gpu_mem_util <= 1):
        return False, "gpu_memory_utilization must be between 0 and 1"

    # Validate dtype
    valid_dtypes = ['auto', 'half', 'float16', 'bfloat16', 'float', 'float32']
    dtype = user_config.get('dtype', 'auto')
    if dtype not in valid_dtypes:
        return False, f"dtype must be one of {valid_dtypes}"

    return True, None


# ============================================================================
# Class-based implementations for protocol support
# ============================================================================


class VLLMConfigBuilder:
    """
    Configuration builder for vLLM engine.

    Implements ConfigBuilderProtocol for the engine registry.
    Wraps the legacy build_vllm_engine_config() function.
    """

    def build_config(self, user_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build vLLM engine configuration from user configuration.

        Args:
            user_config: User-provided configuration dictionary

        Returns:
            Engine-specific configuration dictionary

        Raises:
            ValueError: If configuration is invalid
        """
        # Validate first
        is_valid, error_msg = self.validate_config(user_config)
        if not is_valid:
            raise ValueError(f"Invalid vLLM configuration: {error_msg}")

        # Use legacy function for backward compatibility
        return build_vllm_engine_config(user_config)

    def validate_config(self, user_config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate user configuration.

        Args:
            user_config: User-provided configuration dictionary

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Use legacy function for backward compatibility
        return validate_vllm_config(user_config)

    def get_default_config(self) -> Dict[str, Any]:
        """
        Get default configuration values for vLLM engine.

        Returns:
            Dictionary with default configuration values
        """
        return {
            'dtype': 'auto',
            'tensor_parallel_size': 1,
            'gpu_memory_utilization': 0.9,
            'load_format': 'auto',
            'trust_remote_code': False,
            'disable_log_stats': False,
            'enable_prefix_caching': False,
        }


class VLLMDeploymentFactory:
    """
    Deployment factory for vLLM engine.

    Implements DeploymentFactoryProtocol for the engine registry.
    Creates Ray Serve deployments with proper resource allocation.
    """

    def create_deployment(
        self,
        engine_config: Dict[str, Any],
        num_replicas: int = 1,
        tensor_parallel_size: int = 1,
        use_cpu: bool = False,
        **kwargs
    ) -> serve.Application:
        """
        Create vLLM deployment with proper placement group configuration.

        This function sets up the Ray Serve deployment with appropriate
        resource allocation and placement strategy for tensor parallelism.

        Args:
            engine_config: Configuration dictionary for vLLM engine
            num_replicas: Number of deployment replicas
            tensor_parallel_size: Number of GPUs for tensor parallelism
            use_cpu: Whether to use CPU-only mode
            **kwargs: Additional deployment options (ignored for vLLM)

        Returns:
            Configured Ray Serve application

        References:
            - Placement groups: https://docs.ray.io/en/latest/serve/llm/user-guides/cross-node-parallelism.html
            - vLLM distributed: https://docs.vllm.ai/en/stable/serving/distributed_serving.html
        """
        # Import here to avoid circular dependency
        from .vllm_engine import VLLMEngine

        logger.info(f"Creating vLLM deployment with tensor_parallel_size={tensor_parallel_size}")

        # Calculate resource requirements
        if use_cpu:
            # CPU mode - no GPU needed
            ray_actor_options = {
                "num_cpus": 4,  # Adjust based on needs
                "num_gpus": 0,
            }
        elif tensor_parallel_size > 1:
            # Multi-GPU with tensor parallelism
            # Use placement group for proper GPU allocation
            # Each bundle gets exactly 1 GPU (Ray constraint)
            placement_group_bundles = [
                {"GPU": 1, "CPU": 1} for _ in range(tensor_parallel_size)
            ]

            ray_actor_options = {
                "num_cpus": tensor_parallel_size,
                "num_gpus": tensor_parallel_size,
                "placement_group_bundles": placement_group_bundles,
                "placement_group_strategy": "PACK",  # Pack on same node if possible
            }

            logger.info(f"Using placement group with {tensor_parallel_size} bundles (1 GPU each)")
        else:
            # Single GPU
            ray_actor_options = {
                "num_cpus": 2,
                "num_gpus": 1,
            }

        # Create deployment with configured options
        deployment = VLLMEngine.options(
            num_replicas=num_replicas,
            ray_actor_options=ray_actor_options,
        )

        # Bind engine config
        return deployment.bind(engine_config)
