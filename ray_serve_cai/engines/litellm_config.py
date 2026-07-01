"""
LiteLLM Engine Configuration Builder and Deployment Factory.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from ray import serve

logger = logging.getLogger(__name__)


class LiteLLMConfigBuilder:
    """
    Configuration builder for the LiteLLM engine.

    Translates the user-facing deploy_model payload into the engine_config
    dict consumed by LiteLLMEngine.__init__().

    Recognised engine_config keys
    ------------------------------
    model_list (optional)
        List of model definitions. When omitted the proxy starts with no
        pre-configured models; add them later via the LiteLLM /models API.
        Each entry must have:
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

    venv_path (str, optional)
        Absolute path to the Python virtualenv that contains the litellm
        package and its CLI binary (default: /home/cdsw/.venv-litellm).
        Ray activates this venv for the actor and the subprocess is launched
        from <venv_path>/bin/litellm.
    """

    def build_config(self, user_config: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, err = self.validate_config(user_config)
        if not is_valid:
            raise ValueError(f"Invalid LiteLLM configuration: {err}")

        cfg: Dict[str, Any] = {
            "model_list": user_config.get("model_list") or [],
        }

        if user_config.get("litellm_settings"):
            cfg["litellm_settings"] = user_config["litellm_settings"]

        if user_config.get("litellm_port"):
            cfg["litellm_port"] = int(user_config["litellm_port"])

        if user_config.get("venv_path"):
            cfg["venv_path"] = user_config["venv_path"]

        # server_root_path tells LiteLLM (via uvicorn) its mount prefix so the
        # Next.js UI generates correct asset paths.  Prefer an explicit value in
        # engine_config; fall back to the deployment's route_prefix.
        root_path = (
            user_config.get("server_root_path")
            or user_config.get("route_prefix")
        )
        if root_path and root_path != "/":
            cfg["server_root_path"] = root_path

        if user_config.get("autoscaling_config"):
            cfg["autoscaling_config"] = user_config["autoscaling_config"]

        logger.info(
            "Built LiteLLM config: %d model(s)", len(cfg["model_list"])
        )
        return cfg

    def validate_config(
        self, user_config: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        model_list = user_config.get("model_list")
        if model_list is not None and not isinstance(model_list, list):
            return False, "model_list must be a list"

        for i, entry in enumerate(model_list or []):
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
        from .litellm_engine import LiteLLMEngine
        from .venv_utils import resolve_venv_path, venv_dir_for

        # LiteLLM launches a subprocess from the venv, so it MUST have a valid
        # venv (unlike in-process engines, it cannot fall back to the root env).
        venv_path = resolve_venv_path(engine_config, default_name="litellm") \
            or venv_dir_for("litellm")
        # Propagate to engine_config so the actor's LiteLLM proxy subprocess
        # launches from the SAME venv (it reads engine_config["venv_path"]).
        engine_config["venv_path"] = venv_path
        scheduling_resources = engine_config.pop("scheduling_resources", None)
        scheduling_env_vars  = engine_config.pop("scheduling_env_vars", None)

        rt_env: Dict[str, Any] = {"py_executable": f"{venv_path}/bin/python"}
        if scheduling_env_vars:
            rt_env["env_vars"] = scheduling_env_vars
            logger.info("Scheduling env_vars applied: %s", list(scheduling_env_vars.keys()))

        ray_actor_options: Dict[str, Any] = {
            "num_cpus": 1,
            "num_gpus": 0,
            "runtime_env": rt_env,
        }
        if scheduling_resources:
            ray_actor_options.setdefault("resources", {})
            ray_actor_options["resources"].update(scheduling_resources)
            logger.info("Scheduling resources applied: %s", scheduling_resources)
        logger.info("Using isolated venv: %s", venv_path)

        logger.info(
            "Creating LiteLLM deployment: replicas=%d  models=%d",
            num_replicas,
            len(engine_config.get("model_list", [])),
        )

        autoscaling = engine_config.get("autoscaling_config")
        deploy_opts: Dict[str, Any] = {"ray_actor_options": ray_actor_options}
        if autoscaling:
            deploy_opts["autoscaling_config"] = autoscaling
        else:
            deploy_opts["num_replicas"] = num_replicas

        return LiteLLMEngine.options(**deploy_opts).bind(engine_config)
