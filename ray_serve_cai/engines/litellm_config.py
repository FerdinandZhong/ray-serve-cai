"""
LiteLLM Engine Configuration Builder and Deployment Factory.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from ray import serve

logger = logging.getLogger(__name__)


class LiteLLMConfigBuilder:
    """
    Configuration builder for the LiteLLM engine.

    Translates the user-facing deploy_model payload into the engine_config
    dict consumed by LiteLLMEngine.__init__().

    Recognised engine_config keys
    ------------------------------
    model_list (required)
        List of model definitions. Each entry must have:
          model_name (str)          — alias used in API calls
          litellm_params (dict)     — passed to LiteLLM, must include "model"
                                      e.g. {"model": "openai/gpt-4o",
                                            "api_key": "os.environ/OPENAI_API_KEY"}

    litellm_settings (dict, optional)
        Top-level LiteLLM settings written verbatim to the config YAML.
        Common keys: drop_params, request_timeout, num_retries.

    litellm_port (int, optional)
        Port for the internal LiteLLM proxy (default: 4000).
        Use a different port if 4000 is already in use on the worker.
    """

    def build_config(self, user_config: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, err = self.validate_config(user_config)
        if not is_valid:
            raise ValueError(f"Invalid LiteLLM configuration: {err}")

        cfg: Dict[str, Any] = {
            "model_list": user_config["model_list"],
        }

        if user_config.get("litellm_settings"):
            cfg["litellm_settings"] = user_config["litellm_settings"]

        if user_config.get("litellm_port"):
            cfg["litellm_port"] = int(user_config["litellm_port"])

        logger.info(
            "Built LiteLLM config: %d model(s)", len(cfg["model_list"])
        )
        return cfg

    def validate_config(
        self, user_config: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        model_list = user_config.get("model_list")
        if not model_list or not isinstance(model_list, list):
            return False, "model_list (list of model definitions) is required"

        for i, entry in enumerate(model_list):
            if not isinstance(entry, dict):
                return False, f"model_list[{i}] must be a dict"
            if not entry.get("model_name"):
                return False, f"model_list[{i}].model_name is required"
            params = entry.get("litellm_params", {})
            if not params.get("model"):
                return False, (
                    f"model_list[{i}].litellm_params.model is required "
                    "(e.g. 'openai/gpt-4o' or 'anthropic/claude-3-5-sonnet-20241022')"
                )

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "litellm_settings": {
                "drop_params": True,
                "request_timeout": 120,
            }
        }


class LiteLLMDeploymentFactory:
    """
    Deployment factory for the LiteLLM engine.

    Implements DeploymentFactoryProtocol for the engine registry.
    """

    def create_deployment(
        self,
        engine_config: Dict[str, Any],
        num_replicas: int = 1,
        tensor_parallel_size: int = 1,  # unused; accepted for interface compat
        use_cpu: bool = True,
        **kwargs,
    ) -> serve.Application:
        from pathlib import Path
        from .litellm_engine import LiteLLMEngine

        ray_actor_options: Dict[str, Any] = {"num_cpus": 1, "num_gpus": 0}

        _vp = "/home/cdsw/.venv-litellm"
        if Path(_vp).exists():
            ray_actor_options["runtime_env"] = {"virtualenv": _vp}
            logger.info("Using isolated venv: %s", _vp)

        logger.info(
            "Creating LiteLLM deployment: replicas=%d  models=%d",
            num_replicas,
            len(engine_config.get("model_list", [])),
        )

        return LiteLLMEngine.options(
            num_replicas=num_replicas,
            ray_actor_options=ray_actor_options,
        ).bind(engine_config)
