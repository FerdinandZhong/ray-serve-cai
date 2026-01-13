"""
Base protocols and interfaces for LLM engines in Ray Serve.

This module defines the core abstractions that all LLM engines must implement
to be compatible with the ray-serve-cai framework. This plugin-based architecture
allows for easy integration of new engines (vLLM, SGLang, etc.) without modifying
the core orchestration logic.

Architecture:
- LLMEngineProtocol: Interface for the engine deployment class
- ConfigBuilderProtocol: Interface for configuration handling
- DeploymentFactoryProtocol: Interface for creating Ray Serve deployments

Usage:
    from engines.base import LLMEngineProtocol, ConfigBuilderProtocol

    class MyEngine(LLMEngineProtocol):
        @property
        def engine_type(self) -> str:
            return "my_engine"

        # ... implement other methods
"""

import logging
from typing import Dict, Any, Optional, Protocol, runtime_checkable
from starlette.requests import Request
from starlette.responses import JSONResponse
from ray import serve

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMEngineProtocol(Protocol):
    """
    Protocol that all LLM engines must implement.

    This defines the interface for engine deployment classes that handle
    inference requests. Engines must provide OpenAI-compatible endpoints
    and health check functionality.

    Properties:
        engine_type: Unique identifier for the engine (e.g., "vllm", "sglang")
        model_name: Name of the loaded model

    Methods:
        completion: Handle OpenAI-compatible completion requests
        chat_completion: Handle OpenAI-compatible chat completion requests
        list_models: Return list of available models
        health_check: Return engine health status
        __call__: Main request router
    """

    @property
    def engine_type(self) -> str:
        """
        Return the engine type identifier.

        Returns:
            Engine type string (e.g., "vllm", "sglang")
        """
        ...

    @property
    def model_name(self) -> str:
        """
        Return the name of the loaded model.

        Returns:
            Model name string
        """
        ...

    async def completion(self, request: Request) -> JSONResponse:
        """
        Handle OpenAI-compatible completion requests.

        Endpoint: POST /v1/completions

        Args:
            request: Starlette request object

        Returns:
            JSON response with completion result
        """
        ...

    async def chat_completion(self, request: Request) -> JSONResponse:
        """
        Handle OpenAI-compatible chat completion requests.

        Endpoint: POST /v1/chat/completions

        Args:
            request: Starlette request object

        Returns:
            JSON response with chat completion result
        """
        ...

    async def list_models(self, request: Request) -> JSONResponse:
        """
        List available models.

        Endpoint: GET /v1/models

        Args:
            request: Starlette request object

        Returns:
            JSON response with model list
        """
        ...

    async def health_check(self, request: Request) -> JSONResponse:
        """
        Health check endpoint.

        Endpoint: GET /health

        Args:
            request: Starlette request object

        Returns:
            JSON response with health status
        """
        ...

    async def __call__(self, request: Request):
        """
        Main request handler - routes to appropriate endpoint.

        Args:
            request: Starlette request object

        Returns:
            Response from the appropriate handler
        """
        ...


@runtime_checkable
class ConfigBuilderProtocol(Protocol):
    """
    Protocol for configuration builders.

    Configuration builders translate user configuration into engine-specific
    configuration dictionaries. Each engine implements its own builder to
    handle engine-specific parameters and defaults.

    Methods:
        build_config: Convert user config to engine config
        validate_config: Validate user configuration
        get_default_config: Return default configuration values
    """

    def build_config(self, user_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build engine configuration from user configuration.

        Args:
            user_config: User-provided configuration dictionary

        Returns:
            Engine-specific configuration dictionary

        Raises:
            ValueError: If configuration is invalid
        """
        ...

    def validate_config(self, user_config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        Validate user configuration.

        Args:
            user_config: User-provided configuration dictionary

        Returns:
            Tuple of (is_valid, error_message)
            - (True, None) if valid
            - (False, error_message) if invalid
        """
        ...

    def get_default_config(self) -> Dict[str, Any]:
        """
        Get default configuration values for this engine.

        Returns:
            Dictionary with default configuration values
        """
        ...


@runtime_checkable
class DeploymentFactoryProtocol(Protocol):
    """
    Protocol for deployment factories.

    Deployment factories create Ray Serve deployments with proper resource
    allocation and placement strategies. This handles engine-specific
    deployment requirements like tensor parallelism, GPU allocation, etc.

    Methods:
        create_deployment: Create a Ray Serve deployment
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
        Create Ray Serve deployment with proper resource allocation.

        Args:
            engine_config: Engine-specific configuration dictionary
            num_replicas: Number of deployment replicas
            tensor_parallel_size: Number of GPUs for tensor parallelism
            use_cpu: Whether to use CPU-only mode
            **kwargs: Additional engine-specific deployment options

        Returns:
            Configured Ray Serve application

        Raises:
            ValueError: If deployment configuration is invalid
        """
        ...


class EngineComponents:
    """
    Container for engine implementation components.

    This class holds all the components needed to use an engine:
    the engine class, config builder, and deployment factory.

    Attributes:
        engine_class: The engine deployment class
        config_builder: Configuration builder instance
        deployment_factory: Deployment factory instance
        engine_type: Unique engine identifier
    """

    def __init__(
        self,
        engine_class: type,
        config_builder: ConfigBuilderProtocol,
        deployment_factory: DeploymentFactoryProtocol,
        engine_type: str
    ):
        """
        Initialize engine components.

        Args:
            engine_class: The engine deployment class
            config_builder: Configuration builder instance
            deployment_factory: Deployment factory instance
            engine_type: Unique engine identifier
        """
        self.engine_class = engine_class
        self.config_builder = config_builder
        self.deployment_factory = deployment_factory
        self.engine_type = engine_type

    def __repr__(self) -> str:
        return f"EngineComponents(engine_type={self.engine_type})"
