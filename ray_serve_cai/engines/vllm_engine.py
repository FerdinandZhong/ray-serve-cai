"""
vLLM Engine for Ray Serve
Leverages vLLM's built-in OpenAI-compatible request handlers
Supports proper placement group handling for tensor parallelism

References:
- Ray Serve LLM vLLM: https://docs.ray.io/en/latest/serve/llm/user-guides/vllm-compatibility.html
- vLLM OpenAI Server: https://docs.vllm.ai/en/stable/serving/openai_compatible_server/
- Ray Placement Groups: https://docs.ray.io/en/latest/serve/llm/user-guides/cross-node-parallelism.html
"""

import logging
from typing import Dict, Any, Optional

from ray import serve
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from vllm import AsyncLLMEngine
from vllm.engine.arg_utils import AsyncEngineArgs

# vLLM API changed between versions:
# - v0.13.x: flat structure (serving_completion, serving_chat, serving_models, protocol)
# - v0.14+/v0.15+: subdirectory structure (completion/serving, chat_completion/serving, etc.)
# Try new structure first, fall back to old structure
try:
    # vLLM 0.14+/0.15+ (new subdirectory structure)
    from vllm.entrypoints.openai.completion.serving import OpenAIServingCompletion
    from vllm.entrypoints.openai.chat_completion.serving import OpenAIServingChat
    from vllm.entrypoints.openai.models.serving import (
        OpenAIServingModels,
        BaseModelPath,
    )
    from vllm.entrypoints.openai.completion.protocol import CompletionRequest
    from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
except ImportError:
    # vLLM 0.13.x (old flat structure)
    from vllm.entrypoints.openai.serving_completion import OpenAIServingCompletion
    from vllm.entrypoints.openai.serving_chat import OpenAIServingChat
    from vllm.entrypoints.openai.serving_models import (
        OpenAIServingModels,
        BaseModelPath,
    )
    from vllm.entrypoints.openai.protocol import (
        CompletionRequest,
        ChatCompletionRequest,
    )

logger = logging.getLogger(__name__)


@serve.deployment(
    name="vllm-deployment",
    # Default options - will be overridden during deployment creation
    num_replicas=1,
    ray_actor_options={}
)
class VLLMEngine:
    """
    Ray Serve deployment for vLLM engine with OpenAI-compatible API.

    Reuses vLLM's built-in OpenAI request handlers for maximum compatibility
    and feature parity with vLLM's native OpenAI server.

    Provides endpoints:
    - POST /v1/completions - Text completion
    - POST /v1/chat/completions - Chat completion
    - GET /v1/models - List models
    - GET /health - Health check
    """

    def __init__(self, engine_config: Dict[str, Any]):
        """
        Initialize vLLM engine with OpenAI-compatible serving layer.

        Args:
            engine_config: Configuration dictionary for vLLM engine
        """
        logger.info(f"Initializing vLLM engine with config: {engine_config}")

        try:
            # Create engine args from config
            self.engine_args = AsyncEngineArgs(**engine_config)

            # Initialize async engine
            # vLLM will automatically use Ray for distributed execution
            # if tensor_parallel_size > 1 and Ray is available
            self.engine = AsyncLLMEngine.from_engine_args(self.engine_args)

            # Store model configuration
            self.model_name = engine_config.get('model', 'unknown')
            self.tensor_parallel_size = engine_config.get('tensor_parallel_size', 1)

            # Get model config from engine
            model_config = self.engine.model_config

            # Create OpenAIServingModels - handles model registry and LoRA support
            base_model_path = BaseModelPath(
                name=self.model_name,
                model_path=model_config.model,
            )
            self.openai_serving_models = OpenAIServingModels(
                engine_client=self.engine,
                base_model_paths=[base_model_path],
            )

            # Initialize OpenAI-compatible serving handlers
            # Completion handler
            self.openai_serving_completion = OpenAIServingCompletion(
                engine_client=self.engine,
                models=self.openai_serving_models,
                request_logger=None,
                return_tokens_as_token_ids=False,
                enable_prompt_tokens_details=False,
                enable_force_include_usage=False,
                log_error_stack=False,
            )

            # Chat completion handler
            self.openai_serving_chat = OpenAIServingChat(
                engine_client=self.engine,
                models=self.openai_serving_models,
                response_role="assistant",
                request_logger=None,
                return_tokens_as_token_ids=False,
                log_error_stack=False,
            )

            logger.info(f"✅ vLLM engine initialized successfully")
            logger.info(f"   Model: {self.model_name}")
            logger.info(f"   Tensor Parallel Size: {self.tensor_parallel_size}")

        except Exception as e:
            logger.error(f"❌ Failed to initialize vLLM engine: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    @property
    def engine_type(self) -> str:
        """
        Return the engine type identifier.

        Returns:
            Engine type string: "vllm"
        """
        return "vllm"

    async def completion(self, request: Request) -> JSONResponse:
        """
        Handle OpenAI-compatible completion requests using vLLM's handler.

        Endpoint: POST /v1/completions

        Args:
            request: Starlette request object

        Returns:
            JSON response with completion result
        """
        try:
            # Parse request using vLLM's request model
            request_dict = await request.json()
            completion_request = CompletionRequest(**request_dict)

            # Use vLLM's serving handler to process the request
            response = await self.openai_serving_completion.create_completion(
                completion_request, raw_request=request
            )

            return response

        except Exception as e:
            logger.error(f"Error in completion: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return JSONResponse(
                content={'error': str(e)},
                status_code=500
            )

    async def chat_completion(self, request: Request) -> JSONResponse:
        """
        Handle OpenAI-compatible chat completion requests using vLLM's handler.

        Endpoint: POST /v1/chat/completions

        Args:
            request: Starlette request object

        Returns:
            JSON response with chat completion result
        """
        try:
            # Parse request using vLLM's request model
            request_dict = await request.json()
            chat_request = ChatCompletionRequest(**request_dict)

            # Use vLLM's serving handler to process the request
            # This handles chat template application, tokenization, etc.
            response = await self.openai_serving_chat.create_chat_completion(
                chat_request, raw_request=request
            )

            return response

        except Exception as e:
            logger.error(f"Error in chat completion: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return JSONResponse(
                content={'error': str(e)},
                status_code=500
            )

    async def list_models(self, request: Request) -> JSONResponse:
        """
        List available models.

        Endpoint: GET /v1/models

        Args:
            request: Starlette request object

        Returns:
            JSON response with model list
        """
        # Use vLLM's model list handler
        models = await self.openai_serving_chat.show_available_models()
        return JSONResponse(content=models.model_dump())

    async def health_check(self, request: Request) -> JSONResponse:
        """
        Health check endpoint.

        Endpoint: GET /health

        Args:
            request: Starlette request object

        Returns:
            JSON response with health status
        """
        return JSONResponse(content={
            'status': 'healthy',
            'model': self.model_name,
            'engine': 'vllm',
            'tensor_parallel_size': self.tensor_parallel_size,
        })

    async def __call__(self, request: Request):
        """
        Main request handler - routes to appropriate endpoint.

        Args:
            request: Starlette request object

        Returns:
            Response from the appropriate handler
        """
        path = request.url.path
        method = request.method

        # Route based on path and method
        if path == "/v1/completions" and method == "POST":
            return await self.completion(request)
        elif path == "/v1/chat/completions" and method == "POST":
            return await self.chat_completion(request)
        elif path == "/v1/models" and method == "GET":
            return await self.list_models(request)
        elif path == "/health" and method == "GET":
            return await self.health_check(request)
        else:
            return JSONResponse(
                content={'error': f'Unknown endpoint: {method} {path}'},
                status_code=404
            )


def create_vllm_deployment(
    engine_config: Dict[str, Any],
    num_replicas: int = 1,
    tensor_parallel_size: int = 1,
    use_cpu: bool = False
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

    Returns:
        Configured Ray Serve application

    References:
        - Placement groups: https://docs.ray.io/en/latest/serve/llm/user-guides/cross-node-parallelism.html
        - vLLM distributed: https://docs.vllm.ai/en/stable/serving/distributed_serving.html
    """
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
