#!/usr/bin/env python3
"""
CML Job: Create isolated LiteLLM virtual environment.

Creates /home/cdsw/.venv-litellm with litellm[proxy] + pyyaml.
Uses fcntl.flock so multiple CML pods can run this concurrently on NFS
without corrupting the venv.

Designed to run AFTER setup_vllm_env.py and BEFORE launch_ray_cluster_job.py.

Force rebuild
-------------
Pass --force as a script argument, or set SETUP_FORCE_RECREATE=1 in the
CML job environment to delete and recreate the venv from scratch.
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

LITELLM_PACKAGES = _ENGINE_PACKAGES["litellm"]

_VENV_DIR = "/home/cdsw/.venv-litellm"


def main():
    parser = argparse.ArgumentParser(description="Set up LiteLLM isolated venv")
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Delete and recreate the venv even if it already exists",
    )
    args, _ = parser.parse_known_args()

    force = args.force or os.environ.get("SETUP_FORCE_RECREATE", "").strip() in ("1", "true", "yes")

    print("=" * 70)
    print("🔧 Setting up LiteLLM isolated environment")
    print("=" * 70)

    if force and os.path.exists(_VENV_DIR):
        print(f"⚠️  --force: removing existing venv at {_VENV_DIR}")
        shutil.rmtree(_VENV_DIR, ignore_errors=True)
        lock = f"{_VENV_DIR}.lock"
        if os.path.exists(lock):
            os.remove(lock)

    # Inherit the runtime python (via the base venv); see setup_vllm_env.py.
    success = setup_engine_venv("litellm", LITELLM_PACKAGES)

    if not success:
        print("❌ LiteLLM venv setup failed")
        sys.exit(1)

    # Verify litellm is importable
    import subprocess
    result = subprocess.run(
        [f"{_VENV_DIR}/bin/python", "-c",
         "import importlib.metadata; print(importlib.metadata.version('litellm'))"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"✅ LiteLLM {result.stdout.strip()} verified in .venv-litellm")
    else:
        print(f"⚠️  LiteLLM import check failed: {result.stderr[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
