"""
MCP Engine Configuration Builder and Deployment Factory.
"""

import logging
from typing import Any, Dict, Optional

from ray import serve

logger = logging.getLogger(__name__)


class MCPConfigBuilder:
    """
    Configuration builder for the generic MCP engine.

    Translates the user-facing deploy_model payload into the engine_config
    dict consumed by MCPEngine.__init__().
    """

    def build_config(self, user_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build MCP engine configuration from user configuration.

        Recognised keys
        ---------------
        mcp_module (required)
            Dotted Python module path that exports a FastMCP instance
            (e.g. "ray_serve_cai.engines.mcps.weather_tools").

        num_cpus (float, default 0.2)
            CPU allocation per replica.

        autoscaling_config (dict, optional)
            Ray Serve autoscaling configuration.
        """
        is_valid, error_msg = self.validate_config(user_config)
        if not is_valid:
            raise ValueError(f"Invalid MCP configuration: {error_msg}")

        return {
            "mcp_module":        user_config["mcp_module"],
            "num_cpus":          float(user_config.get("num_cpus", 0.2)),
            "autoscaling_config": user_config.get("autoscaling_config"),
        }

    def validate_config(
        self, user_config: Dict[str, Any]
    ) -> tuple[bool, Optional[str]]:
        if not user_config.get("mcp_module"):
            return False, "mcp_module (dotted Python module path) is required"
        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {"num_cpus": 0.2}


class MCPDeploymentFactory:
    """
    Deployment factory for the generic MCP engine.

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
        from .mcp_engine import create_mcp_deployment
        from .venv_utils import resolve_venv_path

        venv_path = resolve_venv_path(engine_config, default_name="mcp")

        scheduling_resources = engine_config.pop("scheduling_resources", None)
        scheduling_env_vars  = engine_config.pop("scheduling_env_vars", None)

        return create_mcp_deployment(
            engine_config=engine_config,
            num_replicas=num_replicas,
            use_cpu=use_cpu,
            venv_path=venv_path,
            scheduling_resources=scheduling_resources,
            scheduling_env_vars=scheduling_env_vars,
        )
