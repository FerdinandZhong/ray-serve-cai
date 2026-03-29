"""
Ray Worker Node Info Server.

Runs on CDSW_APP_PORT inside the worker CML application pod.
Serves two purposes:
  1. Keeps the CML application in "running" state (CML requires a live process on the app port).
  2. Exposes node metadata so the Management API can retrieve the worker's IP address,
     node type, and Ray connection details without querying the Ray GCS directly.

Endpoints:
  GET /       — root health probe (CML probes this path; returns 200)
  GET /health — liveness probe used by CML / Management API
  GET /info   — node metadata (IP, node_type, head_address, hostname, resources)
"""

import logging
import os
import socket

import uvicorn
from fastapi import FastAPI

app = FastAPI(title="Ray Worker Info", docs_url=None, redoc_url=None)

# These are injected as environment variables by the worker launcher script.
_NODE_TYPE   = os.environ.get("RAY_WORKER_NODE_TYPE", "unknown")
_HEAD_ADDR   = os.environ.get("RAY_HEAD_ADDRESS",     "unknown")
_WORKER_CPUS = os.environ.get("RAY_WORKER_CPUS",      "unknown")
_WORKER_MEM  = os.environ.get("RAY_WORKER_MEMORY_GB", "unknown")
_WORKER_GPUS = os.environ.get("RAY_WORKER_GPUS",      "0")


@app.get("/")
def root():
    """Root probe — CML hits this path; return 200 so it never logs a 404."""
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "healthy", "node_type": _NODE_TYPE}


@app.get("/info")
def info():
    _ip = os.environ.get("CDSW_IP_ADDRESS") or _resolve_ip()
    return {
        "ip":           _ip,
        "hostname":     socket.gethostname(),
        "node_type":    _NODE_TYPE,
        "head_address": _HEAD_ADDR,
        "resources": {
            "cpu":    _WORKER_CPUS,
            "memory": _WORKER_MEM,
            "gpus":   _WORKER_GPUS,
        },
    }


def _resolve_ip() -> str:
    try:
        # Connect to an external address to discover the outbound interface IP.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())


class _SuppressHealthProbe(logging.Filter):
    """Drop access-log lines for GET / and GET /health to silence CML probe noise."""

    _PATHS = ('"GET / ', '"GET /health ')

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p in msg for p in self._PATHS)


# Uvicorn log config — same as the built-in default but with the health-probe
# filter on the access handler so repeated CML probes don't flood pod logs.
LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "suppress_health_probe": {
            "()": "ray_serve_cai.worker_app._SuppressHealthProbe",
        },
    },
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "filters": ["suppress_health_probe"],
        },
    },
    "loggers": {
        "uvicorn":        {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error":  {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["access"],  "level": "INFO", "propagate": False},
    },
}


if __name__ == "__main__":
    port = int(os.environ.get("CDSW_APP_PORT", 8100))
    uvicorn.run(app, host="127.0.0.1", port=port, log_config=LOG_CONFIG)
