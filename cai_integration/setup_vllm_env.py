#!/usr/bin/env python3
"""
CML Job: Create isolated vLLM virtual environment.

Creates /home/cdsw/.venv-vllm with ray[serve] + vllm + ninja.
Uses fcntl.flock so multiple CML pods can run this concurrently on NFS
without corrupting the venv.

Designed to run AFTER setup_environment.py (base env) and BEFORE
launch_ray_cluster_job.py.
"""

import os
import sys

# Ensure the project root is on the path so we can import from cai_integration.
sys.path.insert(0, os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"))

from cai_integration.setup_environment import setup_engine_venv  # noqa: E402

VLLM_PACKAGES = [
    "ray[serve]==2.55.1",
    "protobuf>=5.29.6,<7.0",
    "fastapi==0.138.0",
    "vllm>=0.13.0",
    "ninja",   # required by FlashInfer JIT on older GPUs (e.g. T4/SM7.5)
]


def main():
    print("=" * 70)
    print("🔧 Setting up vLLM isolated environment")
    print("=" * 70)

    success = setup_engine_venv("vllm", VLLM_PACKAGES)

    if not success:
        print("❌ vLLM venv setup failed")
        sys.exit(1)

    # Verify vllm is importable
    venv_python = "/home/cdsw/.venv-vllm/bin/python"
    import subprocess
    result = subprocess.run(
        [venv_python, "-c",
         "import importlib.metadata; print(importlib.metadata.version('vllm'))"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"✅ vLLM {result.stdout.strip()} verified in .venv-vllm")
    else:
        print(f"⚠️  vLLM import check failed: {result.stderr[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
