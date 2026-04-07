#!/usr/bin/env python3
"""
CAI Application launcher for the standalone YOLO API server (no Ray Serve).

Runs the FastAPI YOLO detection server directly via uvicorn, suitable for
benchmarking against the Ray Serve dynamic-batching YOLO engine.

Environment Variables:
    CDSW_APP_PORT       CAI-injected port (default: 8100)
    MODEL_PATH          Path to YOLO .pt weights (default: /home/cdsw/models/Yolo8n_finetuned/best.pt)
    BACKEND             'ultralytics' or 'onnx' (default: ultralytics)
    CONF_THRESHOLD      Detection confidence threshold (default: 0.25)
    IOU_THRESHOLD       NMS IoU threshold (default: 0.45)
    DEVICE              Torch device — '0' for GPU, 'cpu' for CPU (default: cpu)

Usage (CAI Application):
    Set script to benchmark_scripts/launch_yolo_standalone.py

Usage (local):
    python benchmark_scripts/launch_yolo_standalone.py
"""

import os
import subprocess
import sys


def _ensure_deps():
    """Install missing packages."""
    missing = []
    for pkg in ["fastapi", "uvicorn", "ultralytics", "PIL"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        deps = ["fastapi", "uvicorn[standard]", "ultralytics", "Pillow", "python-multipart"]
        print(f"Installing missing packages: {deps}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet"] + deps
        )
        print("Dependencies installed.")


def main():
    _ensure_deps()

    port = int(os.environ.get("CDSW_APP_PORT", "8100"))
    model_path = os.environ.get("MODEL_PATH", "/home/cdsw/models/Yolo8n_finetuned/best.pt")
    backend = os.environ.get("BACKEND", "ultralytics")
    conf = os.environ.get("CONF_THRESHOLD", "0.25")
    iou = os.environ.get("IOU_THRESHOLD", "0.45")
    device = os.environ.get("DEVICE", "cpu")
    host = "127.0.0.1"

    print("=" * 60)
    print("YOLO Standalone API Server (benchmark baseline)")
    print("=" * 60)
    print(f"  Port:       {port}")
    print(f"  Model:      {model_path}")
    print(f"  Backend:    {backend}")
    print(f"  Conf:       {conf}")
    print(f"  IoU:        {iou}")
    print(f"  Device:     {device}")
    print()
    print("Endpoints:")
    print(f"  Detect:     http://{host}:{port}/v1/detect")
    print(f"  Health:     http://{host}:{port}/health")
    print(f"  Swagger:    http://{host}:{port}/docs")
    print()

    # Set env vars for the YOLO server startup event
    os.environ["MODEL_PATH"] = model_path
    os.environ["BACKEND"] = backend
    os.environ["CONF_THRESHOLD"] = conf
    os.environ["IOU_THRESHOLD"] = iou
    os.environ["DEVICE"] = device

    try:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        project_root = os.getcwd()

    server_script = os.path.join(project_root, "benchmark_scripts", "yolo_api_server.py")

    if os.environ.get("_SERVER_MODE"):
        # Inside the subprocess — safe to import and run uvicorn directly
        sys.path.insert(0, os.path.dirname(server_script))
        import uvicorn
        from yolo_api_server import app
        uvicorn.run(app, host=host, port=port)
    else:
        # CAI/Jupyter parent — spawn subprocess to avoid event loop conflicts
        cmd = [sys.executable, __file__]
        env = {**os.environ, "_SERVER_MODE": "1"}
        print(f"Spawning server subprocess...")
        print()
        try:
            process = subprocess.Popen(
                cmd,
                stdout=sys.stdout,
                stderr=sys.stderr,
                cwd=project_root,
                env=env,
            )
            exit_code = process.wait()
            if exit_code != 0:
                print(f"\nERROR: Server exited with code {exit_code}")
                sys.exit(exit_code)
        except KeyboardInterrupt:
            print("\n\nShutting down server...")
            if "process" in locals():
                process.terminate()
                process.wait()
            sys.exit(0)
        except Exception as exc:
            print(f"\nERROR: Failed to start server: {exc}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    main()
