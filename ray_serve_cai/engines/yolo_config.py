"""
YOLO Engine Configuration Builder and Deployment Factory.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ray import serve

logger = logging.getLogger(__name__)


class YOLOConfigBuilder:
    """
    Configuration builder for the YOLO engine.

    Translates the user-facing deploy_model payload into the engine_config dict
    consumed by YOLOEngine.__init__().
    """

    def build_config(self, user_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build YOLO engine configuration from user configuration.

        Recognised user_config keys
        ----------------------------
        model (required)
            Path to the YOLO weights file (.pt) or an Ultralytics model ID
            (e.g. "yolov8n.pt").  Also accepted as model_path.

        conf_threshold (float, default 0.25)
            Minimum confidence score for a detection to be returned.

        iou_threshold (float, default 0.45)
            IoU threshold for Non-Maximum Suppression.

        device (str, default "cuda:0" / "cpu")
            Torch device string.  Inferred from use_cpu when omitted.

        max_batch_size (int, default 16)
            Maximum images per Ray Serve dynamic batch.  Higher values improve
            GPU utilisation at the cost of tail latency.

        batch_wait_timeout_s (float, default 0.05)
            Seconds Ray Serve waits to fill a batch before dispatching a
            partial batch.  Tune jointly with max_batch_size.

        num_gpus (int, default 1)
            GPUs allocated to each Ray actor replica.  Set to 0 for CPU.

        num_cpus (int, default 2)
            CPUs allocated to each Ray actor replica.
        """
        is_valid, error_msg = self.validate_config(user_config)
        if not is_valid:
            raise ValueError(f"Invalid YOLO configuration: {error_msg}")

        model_path = user_config.get("model_path") or user_config.get("model")
        use_cpu    = user_config.get("use_cpu", False)

        engine_config: Dict[str, Any] = {
            "model_path":           model_path,
            "conf_threshold":       user_config.get("conf_threshold", 0.25),
            "iou_threshold":        user_config.get("iou_threshold",  0.45),
            "device":               user_config.get("device", "cpu" if use_cpu else "cuda:0"),
            "max_batch_size":       int(user_config.get("max_batch_size", 16)),
            "batch_wait_timeout_s": float(user_config.get("batch_wait_timeout_s", 0.05)),
            "num_gpus":             int(user_config.get("num_gpus", 0 if use_cpu else 1)),
            "num_cpus":             int(user_config.get("num_cpus", 2)),
            # Optional: pin this deployment to a specific Ray node type.
            # When set, Ray adds "node_type:<value>" as a scheduling resource
            # requirement so replicas only land on nodes of that type.
            "node_type":            user_config.get("node_type"),
        }

        if user_config.get("autoscaling_config"):
            engine_config["autoscaling_config"] = user_config["autoscaling_config"]

        logger.info(
            f"Built YOLO engine config: model={model_path!r}  "
            f"device={engine_config['device']}  "
            f"max_batch_size={engine_config['max_batch_size']}  "
            f"batch_wait_timeout_s={engine_config['batch_wait_timeout_s']}"
        )
        return engine_config

    def validate_config(self, user_config: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate user configuration before building."""
        model = user_config.get("model_path") or user_config.get("model")
        if not model:
            return False, "model (path to YOLO .pt file) is required"

        conf = user_config.get("conf_threshold", 0.25)
        if not isinstance(conf, (int, float)) or not (0.0 < conf < 1.0):
            return False, "conf_threshold must be a float in (0, 1)"

        iou = user_config.get("iou_threshold", 0.45)
        if not isinstance(iou, (int, float)) or not (0.0 < iou < 1.0):
            return False, "iou_threshold must be a float in (0, 1)"

        max_bs = user_config.get("max_batch_size", 16)
        if not isinstance(max_bs, int) or max_bs < 1:
            return False, "max_batch_size must be a positive integer"

        timeout = user_config.get("batch_wait_timeout_s", 0.05)
        if not isinstance(timeout, (int, float)) or timeout < 0:
            return False, "batch_wait_timeout_s must be a non-negative number"

        return True, None

    def get_default_config(self) -> Dict[str, Any]:
        return {
            "conf_threshold":       0.25,
            "iou_threshold":        0.45,
            "max_batch_size":       16,
            "batch_wait_timeout_s": 0.05,
            "num_gpus":             1,
            "num_cpus":             2,
        }


class YOLODeploymentFactory:
    """
    Deployment factory for the YOLO engine.

    Creates a Ray Serve deployment whose @serve.batch parameters match the
    values in engine_config, by calling make_yolo_deployment() with the
    configured max_batch_size and batch_wait_timeout_s.
    """

    def create_deployment(
        self,
        engine_config: Dict[str, Any],
        num_replicas: int = 1,
        tensor_parallel_size: int = 1,   # unused for YOLO; accepted for interface compat
        use_cpu: bool = False,
        **kwargs,
    ) -> serve.Application:
        """
        Create a YOLO Ray Serve deployment.

        Args:
            engine_config:
                Output of YOLOConfigBuilder.build_config().
            num_replicas:
                Number of Ray Serve replicas.  Each replica loads its own copy
                of the model — horizontal scaling for higher throughput.
            tensor_parallel_size:
                Ignored.  YOLO models run on a single GPU per replica.
            use_cpu:
                Override device to CPU regardless of engine_config['device'].
            **kwargs:
                Ignored extra keyword arguments.

        Returns:
            Configured Ray Serve application ready for serve.run().
        """
        from .yolo_engine import make_yolo_deployment

        max_batch_size       = engine_config.get("max_batch_size", 16)
        batch_wait_timeout_s = engine_config.get("batch_wait_timeout_s", 0.05)
        num_gpus             = 0 if use_cpu else engine_config.get("num_gpus", 1)
        num_cpus             = engine_config.get("num_cpus", 2)
        node_type            = engine_config.get("node_type")

        # Each call to make_yolo_deployment() creates a fresh @serve.deployment
        # class whose @serve.batch decorator is bound to the requested params.
        YOLODeployment = make_yolo_deployment(
            max_batch_size=max_batch_size,
            batch_wait_timeout_s=batch_wait_timeout_s,
        )

        ray_actor_options: Dict[str, Any] = {
            "num_cpus": num_cpus,
            "num_gpus": num_gpus,
        }

        if node_type:
            ray_actor_options["resources"] = {f"node_type:{node_type}": 0.001}
            logger.info(f"Pinning deployment to node_type={node_type!r}")

        # Explicit scheduling resources (override node_type shorthand)
        scheduling_resources = engine_config.pop("scheduling_resources", None)
        scheduling_env_vars  = engine_config.pop("scheduling_env_vars", None)
        if scheduling_resources:
            ray_actor_options["resources"] = {}
            ray_actor_options["resources"].update(scheduling_resources)
            logger.info("Scheduling resources applied: %s", scheduling_resources)

        from .venv_utils import resolve_venv_path
        _vp = resolve_venv_path(engine_config, default_name="yolo")
        rt_env: Dict[str, Any] = {}
        if _vp:
            rt_env["py_executable"] = f"{_vp}/bin/python"
            logger.info("Using isolated venv: %s", _vp)
        if scheduling_env_vars:
            rt_env["env_vars"] = scheduling_env_vars
            logger.info("Scheduling env_vars applied: %s", list(scheduling_env_vars.keys()))
        if rt_env:
            ray_actor_options["runtime_env"] = rt_env

        logger.info(
            f"Creating YOLO deployment: replicas={num_replicas}  "
            f"num_gpus={num_gpus}  num_cpus={num_cpus}  "
            f"max_batch_size={max_batch_size}  "
            f"batch_wait_timeout_s={batch_wait_timeout_s}"
        )

        autoscaling = engine_config.get("autoscaling_config")
        deploy_opts: Dict[str, Any] = {"ray_actor_options": ray_actor_options}
        if autoscaling:
            deploy_opts["autoscaling_config"] = autoscaling
        else:
            deploy_opts["num_replicas"] = num_replicas

        return YOLODeployment.options(**deploy_opts).bind(engine_config)
