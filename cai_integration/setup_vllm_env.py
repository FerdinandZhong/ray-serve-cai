#!/usr/bin/env python3
"""
CML Job: Create isolated vLLM virtual environment.

Creates /home/cdsw/.venv-vllm with ray[serve] + vllm + ninja.
Uses fcntl.flock so multiple CML pods can run this concurrently on NFS
without corrupting the venv.

Designed to run AFTER setup_environment.py (base env) and BEFORE
launch_ray_cluster_job.py.
"""

import argparse
import os
import shutil
import sys

# Ensure the project root is on the path so we can import from cai_integration.
sys.path.insert(0, os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"))

# Package set is defined once in setup_environment._ENGINE_PACKAGES so the CML
# job and the base-env registry never drift (see that module for rationale).
from cai_integration.setup_environment import (  # noqa: E402
    _ENGINE_PACKAGES,
    setup_engine_venv,
)

VLLM_PACKAGES = _ENGINE_PACKAGES["vllm"]

_VENV_DIR = "/home/cdsw/.venv-vllm"


def main():
    parser = argparse.ArgumentParser(description="Set up vLLM isolated venv")
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Delete and recreate the venv even if it already exists "
             "(also honoured via SETUP_FORCE_RECREATE=1)",
    )
    # parse_known_args so a stray Jupyter '-f <kernel.json>' arg is ignored.
    args, _ = parser.parse_known_args()

    force = args.force or os.environ.get("SETUP_FORCE_RECREATE", "").strip() in ("1", "true", "yes")

    print("=" * 70)
    print("🔧 Setting up vLLM isolated environment")
    print("=" * 70)

    if force and os.path.exists(_VENV_DIR):
        print(f"⚠️  --force: removing existing venv at {_VENV_DIR}")
        shutil.rmtree(_VENV_DIR, ignore_errors=True)
        lock = f"{_VENV_DIR}.lock"
        if os.path.exists(lock):
            os.remove(lock)

    # Inherit the runtime python (via the base venv) so the vLLM actor matches
    # the cluster head. Do NOT pin a version string: it triggers a standalone
    # download + head/actor version split on a runtime that lacks it.
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
