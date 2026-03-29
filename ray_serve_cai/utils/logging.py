"""
Centralized logging setup for Ray Serve applications.

Call setup_serve_logging(app_name) once at application startup (e.g. in the
FastAPI lifespan handler).  It attaches two file handlers to the "ray.serve"
logger — which Ray Serve already uses for all replica/controller output:

    /tmp/ray/session_latest/logs/serve/{app_name}_info.log   (INFO+)
    /tmp/ray/session_latest/logs/serve/{app_name}_err.log    (ERROR+)

Ray's own logs also land in the same serve/ directory so all output is in one
place.  The symlink /tmp/ray/session_latest always points to the active session.
"""

import logging
import os
from pathlib import Path


# Ray Serve always writes into this directory (symlink to the current session).
_SERVE_LOG_DIR = Path("/tmp/ray/session_latest/logs/serve")

_FORMATTER = logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s -- %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


class _LevelFilter(logging.Filter):
    """Accept only records at exactly (or above) a given level."""
    def __init__(self, max_level: int):
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


def setup_serve_logging(app_name: str) -> None:
    """
    Attach INFO-file and ERROR-file handlers to the Ray Serve logger.

    Args:
        app_name: Used as the log-file prefix, e.g. "management-api".
                  Must be a filesystem-safe string.

    File layout:
        <serve_log_dir>/<app_name>_info.log   — INFO and WARNING
        <serve_log_dir>/<app_name}_err.log    — ERROR and CRITICAL
    """
    log_dir = _SERVE_LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fall back to a local directory if the Ray path is not available yet.
        log_dir = Path("/tmp") / "ray_serve_logs"
        log_dir.mkdir(parents=True, exist_ok=True)

    serve_logger = logging.getLogger("ray.serve")

    # Avoid adding duplicate handlers on hot-reload / multiple calls.
    existing_paths = {
        h.baseFilename
        for h in serve_logger.handlers
        if isinstance(h, logging.FileHandler)
    }

    info_path = str(log_dir / f"{app_name}_info.log")
    err_path  = str(log_dir / f"{app_name}_err.log")

    if info_path not in existing_paths:
        info_handler = logging.FileHandler(info_path)
        info_handler.setLevel(logging.INFO)
        info_handler.addFilter(_LevelFilter(logging.WARNING))   # INFO + WARNING only
        info_handler.setFormatter(_FORMATTER)
        serve_logger.addHandler(info_handler)

    if err_path not in existing_paths:
        err_handler = logging.FileHandler(err_path)
        err_handler.setLevel(logging.ERROR)
        err_handler.setFormatter(_FORMATTER)
        serve_logger.addHandler(err_handler)

    # Ensure the logger propagates at least at INFO level.
    if serve_logger.level == logging.NOTSET or serve_logger.level > logging.INFO:
        serve_logger.setLevel(logging.INFO)

    serve_logger.info(
        f"File logging configured for '{app_name}': "
        f"{info_path}  |  {err_path}"
    )
