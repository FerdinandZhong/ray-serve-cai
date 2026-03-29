"""
Ray Worker Node Info Server.

Runs on CDSW_APP_PORT inside the worker CML application pod.
Serves two purposes:
  1. Keeps the CML application in "running" state (CML requires a live process on the app port).
  2. Exposes node metadata so the Management API can retrieve the worker's IP address,
     node type, and Ray connection details without querying the Ray GCS directly.

Endpoints:
  GET /health  — liveness probe used by CML / Management API
  GET /info    — node metadata (IP, node_type, head_address, hostname, resources)
"""

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


if __name__ == "__main__":
    port = int(os.environ.get("CDSW_APP_PORT", 8100))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
