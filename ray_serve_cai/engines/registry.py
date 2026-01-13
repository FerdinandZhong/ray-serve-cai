"""
Engine Registry for managing LLM engines.

This module provides a centralized registry for managing different LLM engines
(vLLM, SGLang, etc.). It implements a factory pattern for engine instantiation
and provides validation and discovery capabilities.

Usage:
    from engines.registry import get_registry, register_engine
    from engines.vllm_config import VLLMConfigBuilder, VLLMDeploymentFactory
    from engines.vllm_engine import VLLMEngine

    # Register an engine
    registry = get_registry()
    registry.register(
        engine_type="vllm",
        engine_class=VLLMEngine,
        config_builder=VLLMConfigBuilder(),
        deployment_factory=VLLMDeploymentFactory()
    )

    # Use the registry
    builder = registry.get_config_builder("vllm")
    config = builder.build_config(user_config)
"""

import logging
from typing import Dict, Optional, List
from .base import (
    EngineComponents,
    ConfigBuilderProtocol,
    DeploymentFactoryProtocol,
)

logger = logging.getLogger(__name__)


class EngineRegistry:
    """
    Central registry for LLM engines.

    This class manages the registration and retrieval of engine implementations.
    It provides a factory pattern for creating engine instances and accessing
    their configuration builders and deployment factories.

    Thread-safe singleton pattern ensures a single global registry instance.
    """

    def __init__(self):
        """Initialize the engine registry."""
        self._engines: Dict[str, EngineComponents] = {}
        self._default_engine: Optional[str] = None
        logger.info("Initialized engine registry")

    def register(
        self,
        engine_type: str,
        engine_class: type,
        config_builder: ConfigBuilderProtocol,
        deployment_factory: DeploymentFactoryProtocol,
        set_as_default: bool = False
    ) -> None:
        """
        Register a new engine.

        Args:
            engine_type: Unique identifier for the engine (e.g., "vllm", "sglang")
            engine_class: The engine deployment class
            config_builder: Configuration builder instance
            deployment_factory: Deployment factory instance
            set_as_default: Whether to set this as the default engine

        Raises:
            ValueError: If engine_type is already registered
        """
        if engine_type in self._engines:
            logger.warning(f"Engine '{engine_type}' is already registered. Overwriting.")

        components = EngineComponents(
            engine_class=engine_class,
            config_builder=config_builder,
            deployment_factory=deployment_factory,
            engine_type=engine_type
        )

        self._engines[engine_type] = components
        logger.info(f"Registered engine: {engine_type}")

        # Set as default if requested or if it's the first engine
        if set_as_default or self._default_engine is None:
            self._default_engine = engine_type
            logger.info(f"Set default engine: {engine_type}")

    def unregister(self, engine_type: str) -> None:
        """
        Unregister an engine.

        Args:
            engine_type: Engine type to unregister

        Raises:
            KeyError: If engine_type is not registered
        """
        if engine_type not in self._engines:
            raise KeyError(f"Engine '{engine_type}' is not registered")

        del self._engines[engine_type]
        logger.info(f"Unregistered engine: {engine_type}")

        # Clear default if it was the default engine
        if self._default_engine == engine_type:
            self._default_engine = list(self._engines.keys())[0] if self._engines else None
            if self._default_engine:
                logger.info(f"Updated default engine to: {self._default_engine}")

    def is_registered(self, engine_type: str) -> bool:
        """
        Check if an engine is registered.

        Args:
            engine_type: Engine type to check

        Returns:
            True if registered, False otherwise
        """
        return engine_type in self._engines

    def get_engine_components(self, engine_type: str) -> EngineComponents:
        """
        Get engine components by type.

        Args:
            engine_type: Engine type to retrieve

        Returns:
            EngineComponents for the specified engine

        Raises:
            KeyError: If engine_type is not registered
        """
        if engine_type not in self._engines:
            available = ", ".join(self._engines.keys())
            raise KeyError(
                f"Engine '{engine_type}' is not registered. "
                f"Available engines: {available}"
            )
        return self._engines[engine_type]

    def get_config_builder(self, engine_type: Optional[str] = None) -> ConfigBuilderProtocol:
        """
        Get configuration builder for an engine.

        Args:
            engine_type: Engine type, or None to use default engine

        Returns:
            ConfigBuilderProtocol instance

        Raises:
            ValueError: If no engine_type provided and no default set
            KeyError: If engine_type is not registered
        """
        engine_type = engine_type or self._default_engine
        if engine_type is None:
            raise ValueError("No engine type specified and no default engine set")

        components = self.get_engine_components(engine_type)
        return components.config_builder

    def get_deployment_factory(self, engine_type: Optional[str] = None) -> DeploymentFactoryProtocol:
        """
        Get deployment factory for an engine.

        Args:
            engine_type: Engine type, or None to use default engine

        Returns:
            DeploymentFactoryProtocol instance

        Raises:
            ValueError: If no engine_type provided and no default set
            KeyError: If engine_type is not registered
        """
        engine_type = engine_type or self._default_engine
        if engine_type is None:
            raise ValueError("No engine type specified and no default engine set")

        components = self.get_engine_components(engine_type)
        return components.deployment_factory

    def get_engine_class(self, engine_type: Optional[str] = None) -> type:
        """
        Get engine class for an engine.

        Args:
            engine_type: Engine type, or None to use default engine

        Returns:
            Engine class

        Raises:
            ValueError: If no engine_type provided and no default set
            KeyError: If engine_type is not registered
        """
        engine_type = engine_type or self._default_engine
        if engine_type is None:
            raise ValueError("No engine type specified and no default engine set")

        components = self.get_engine_components(engine_type)
        return components.engine_class

    def list_engines(self) -> List[str]:
        """
        List all registered engines.

        Returns:
            List of registered engine types
        """
        return list(self._engines.keys())

    def get_default_engine(self) -> Optional[str]:
        """
        Get the default engine type.

        Returns:
            Default engine type, or None if no default set
        """
        return self._default_engine

    def set_default_engine(self, engine_type: str) -> None:
        """
        Set the default engine.

        Args:
            engine_type: Engine type to set as default

        Raises:
            KeyError: If engine_type is not registered
        """
        if engine_type not in self._engines:
            available = ", ".join(self._engines.keys())
            raise KeyError(
                f"Engine '{engine_type}' is not registered. "
                f"Available engines: {available}"
            )

        self._default_engine = engine_type
        logger.info(f"Set default engine: {engine_type}")

    def __repr__(self) -> str:
        engines = ", ".join(self._engines.keys())
        return f"EngineRegistry(engines=[{engines}], default={self._default_engine})"


# Global registry instance
_global_registry: Optional[EngineRegistry] = None


def get_registry() -> EngineRegistry:
    """
    Get the global engine registry instance.

    Creates the registry if it doesn't exist (singleton pattern).

    Returns:
        Global EngineRegistry instance
    """
    global _global_registry
    if _global_registry is None:
        _global_registry = EngineRegistry()
    return _global_registry


def register_engine(
    engine_type: str,
    engine_class: type,
    config_builder: ConfigBuilderProtocol,
    deployment_factory: DeploymentFactoryProtocol,
    set_as_default: bool = False
) -> None:
    """
    Convenience function to register an engine in the global registry.

    Args:
        engine_type: Unique identifier for the engine
        engine_class: The engine deployment class
        config_builder: Configuration builder instance
        deployment_factory: Deployment factory instance
        set_as_default: Whether to set this as the default engine
    """
    registry = get_registry()
    registry.register(
        engine_type=engine_type,
        engine_class=engine_class,
        config_builder=config_builder,
        deployment_factory=deployment_factory,
        set_as_default=set_as_default
    )
