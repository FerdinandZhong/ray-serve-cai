"""
SGLang Engine Configuration Builder and Deployment Factory.
"""

import logging
from typing import Any, Dict, Optional

from ray import serve

logger = logging.getLogger(__name__)


class SGLangConfigBuilder:
    """
    Configuration builder for the SGLang engine.

    Translates the user-facing deploy_model payload into the engine_config
    dict consumed by SGLangEngine.__init__().
    """

    def build_config(self, user_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build SGLang engine configuration.

        Recognised keys
        ----------------
        model (required)         — HuggingFace model ID or local path
        tensor_parallel_size     — GPUs per replica (default 1)
        dtype                    — Data type (auto, float16, bfloat16)
        trust_remote_code        — Allow custom model code (default False)
        context_length           — Max sequence length (default: model default)
        mem_fraction_static      — KV cache memory fraction (default 0.8)
        quantization             — Quantization method (e.g. "fp8")
        """
        is_valid, error_msg = self.validate_config(user_config)
        if not is_valid:
            raise ValueError(f"Invalid SGLang configuration: {error_msg}")

        model = user_config.get("model") or user_config.get("model_source")

        engine_config: Dict[str, Any] = {
            "model": model,
            "tensor_parallel_size": user_config.get("tensor_parallel_size", 1),
        }

        # Optional passthrough keys
        for key in ("dtype", "trust_remote_code", "context_length",
                     "mem_fraction_static", "quantization", "autoscaling_config"):
            val = user_config.get(key)
            if val is not None:
                engine_config[key] = val

        logger.info("Built SGLang config: model=%s  tp=%d",
                     model, engine_config["tensor_parallel_size"])
        return engine_config

    def validate_config(
        self, user_config: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        model = user_config.get("model") or user_config.get("model_source")
        if not model:
            return False, "model (HuggingFace model ID or local path) is required"

        tp = user_config.get("tensor_parallel_size", 1)
        if not isinstance(tp, int) or tp < 1:
            return False, "tensor_parallel_size must be a positive integer"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "tensor_parallel_size": 1,
            "trust_remote_code": False,
        }


class SGLangDeploymentFactory:
    """
    Deployment factory for the SGLang engine.

    Implements DeploymentFactoryProtocol for the engine registry.
    """

    def create_deployment(
        self,
        engine_config: Dict[str, Any],
        num_replicas: int = 1,
        tensor_parallel_size: int = 1,
        use_cpu: bool = False,
        max_ongoing_requests: int = 100,
        **kwargs,
    ) -> serve.Application:
        from .sglang_engine import create_sglang_deployment
        from .venv_utils import resolve_venv_path

        venv_path = resolve_venv_path(engine_config, default_name="sglang")

        scheduling_resources = engine_config.pop("scheduling_resources", None)
        scheduling_env_vars  = engine_config.pop("scheduling_env_vars", None)
        pg_bundles  = kwargs.get("placement_group_bundles")
        pg_strategy = kwargs.get("placement_group_strategy")

        return create_sglang_deployment(
            engine_config=engine_config,
            num_replicas=num_replicas,
            tensor_parallel_size=tensor_parallel_size,
            use_cpu=use_cpu,
            max_ongoing_requests=max_ongoing_requests,
            gpu_fraction=kwargs.get("gpu_fraction"),
            placement_group_bundles=pg_bundles,
            placement_group_strategy=pg_strategy,
            venv_path=venv_path,
            scheduling_resources=scheduling_resources,
            scheduling_env_vars=scheduling_env_vars,
        )
