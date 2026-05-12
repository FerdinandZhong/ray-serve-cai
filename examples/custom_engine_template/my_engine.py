"""
Custom engine template — copy this file to get started.

Steps:
  1. Copy this directory to your project (e.g. my_project/my_engine/).
  2. Edit MyEngine.__init__ to start your inference process / load your model.
  3. Add / remove routes as needed.
  4. Register at runtime via:

     POST /api/v1/engines/register
     {
       "engine_type": "my_engine",
       "module_path": "my_project.my_engine.my_engine",
       "config_builder_class": "MyEngineConfigBuilder",
       "deployment_factory_class": "MyEngineDeploymentFactory",
       "engine_class": "MyEngine"
     }

     The module must be on PYTHONPATH and its prefix must be in
     ALLOWED_ENGINE_MODULES (default includes "custom_engines").

  5. Deploy with:

     POST /api/v1/applications/deploy
     {
       "name": "my-engine",
       "engine_type": "my_engine",
       "route_prefix": "/my-engine",
       "num_replicas": 1,
       "engine_config": { "greeting": "hello" }
     }
"""

import logging
from typing import Any, Dict, Optional, Tuple

from fastapi.responses import JSONResponse
from ray import serve
from starlette.requests import Request

from ray_serve_cai.engines.base import ConfigBuilderProtocol, DeploymentFactoryProtocol
from ray_serve_cai.engines.engine_utils import create_engine_app, mount_health, mount_metrics

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FastAPI app (one instance shared across hot-reload cycles)
# ---------------------------------------------------------------------------
_app = create_engine_app(title="My Custom Engine")
mount_health(_app, engine_type="my_engine")
mount_metrics(_app)


@_app.post("/v1/chat/completions")
async def chat(request: Request) -> JSONResponse:
    body = await request.json()
    # TODO: replace with real inference
    return JSONResponse({"reply": f"echo: {body.get('messages', [])}"})


# ---------------------------------------------------------------------------
# Ray Serve deployment class
# ---------------------------------------------------------------------------
@serve.deployment
@serve.ingress(_app)
class MyEngine:
    def __init__(self, engine_config: Dict[str, Any]) -> None:
        self.greeting = engine_config.get("greeting", "hello")
        logger.info("MyEngine initialized with greeting=%r", self.greeting)

    async def __call__(self, request: Request):
        # FastAPI handles routing via @serve.ingress — this is the fallback.
        return JSONResponse({"greeting": self.greeting})


# ---------------------------------------------------------------------------
# ConfigBuilder — validates and normalises the engine_config dict
# ---------------------------------------------------------------------------
class MyEngineConfigBuilder:
    def build_config(self, user_config: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, err = self.validate_config(user_config)
        if not is_valid:
            raise ValueError(f"Invalid MyEngine config: {err}")
        return {"greeting": user_config.get("greeting", "hello")}

    def validate_config(self, user_config: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {"greeting": "hello"}


# ---------------------------------------------------------------------------
# DeploymentFactory — creates the Ray Serve application
# ---------------------------------------------------------------------------
class MyEngineDeploymentFactory:
    def create_deployment(
        self,
        engine_config: Dict[str, Any],
        num_replicas: int = 1,
        tensor_parallel_size: int = 1,
        use_cpu: bool = True,
        **kwargs,
    ) -> serve.Application:
        ray_actor_options: Dict[str, Any] = {"num_cpus": 1, "num_gpus": 0}

        # Wire per-engine venv if it was created by setup_environment.py
        from pathlib import Path
        _vp = "/home/cdsw/.venv-my_engine"
        if Path(_vp).exists():
            ray_actor_options["runtime_env"] = {"virtualenv": _vp}

        return MyEngine.options(
            num_replicas=num_replicas,
            ray_actor_options=ray_actor_options,
        ).bind(engine_config)
