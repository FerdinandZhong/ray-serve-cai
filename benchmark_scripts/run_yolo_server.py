#!/usr/bin/env python3
"""
CAI Application entry point — runs the standalone YOLO API server.

This is the script that the CAI Application executes inside the pod.
It installs deps, then starts uvicorn with yolo_api_server:app.

Environment Variables (set by launch_yolo_standalone.py):
    MODEL_PATH, BACKEND, CONF_THRESHOLD, IOU_THRESHOLD, DEVICE
    CDSW_APP_PORT (injected by CAI, default 8100)
"""

import os
import subprocess
import sys


def _ensure_deps():
    missing = []
    for pkg in ["fastapi", "uvicorn", "ultralytics", "PIL"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        deps = ["fastapi", "uvicorn[standard]", "ultralytics", "Pillow", "python-multipart"]
        print(f"Installing: {deps}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + deps
        )


_ensure_deps()

port = int(os.environ.get("CDSW_APP_PORT", "8100"))
host = "127.0.0.1"

print("=" * 60)
print("YOLO Standalone API Server")
print("=" * 60)
print(f"  Port:   {port}")
print(f"  Model:  {os.environ.get('MODEL_PATH', '(default)')}")
print(f"  Device: {os.environ.get('DEVICE', '(default)')}")
print(f"  URL:    http://{host}:{port}/docs")
print("=" * 60)

# Run uvicorn pointing at yolo_api_server.py in the same directory
bench_dir = os.path.join(os.getcwd(), "benchmark_scripts")
cmd = [
    sys.executable, "-m", "uvicorn",
    "yolo_api_server:app",
    "--host", host,
    "--port", str(port),
    "--log-level", "info",
]

process = subprocess.run(cmd, cwd=bench_dir)
sys.exit(process.returncode)
