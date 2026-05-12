#!/usr/bin/env python3
"""
CML Job: Create isolated LiteLLM virtual environment.

Creates /home/cdsw/.venv-litellm with litellm + pyyaml.
Uses fcntl.flock so multiple CML pods can run this concurrently on NFS
without corrupting the venv.

Designed to run AFTER setup_vllm_env.py and BEFORE launch_ray_cluster_job.py.
"""

import os
import sys

# Ensure the project root is on the path so we can import from cai_integration.
sys.path.insert(0, os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"))

from cai_integration.setup_environment import setup_engine_venv  # noqa: E402

LITELLM_PACKAGES = [
    "litellm>=1.83.0",
    "pyyaml>=6.0.3",  # used by litellm_engine.py to write the config YAML
]


def main():
    print("=" * 70)
    print("🔧 Setting up LiteLLM isolated environment")
    print("=" * 70)

    success = setup_engine_venv("litellm", LITELLM_PACKAGES)

    if not success:
        print("❌ LiteLLM venv setup failed")
        sys.exit(1)

    # Verify litellm is importable
    venv_python = "/home/cdsw/.venv-litellm/bin/python"
    import subprocess
    result = subprocess.run(
        [venv_python, "-c", "import litellm; print(litellm.__version__)"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print(f"✅ LiteLLM {result.stdout.strip()} verified in .venv-litellm")
    else:
        print(f"⚠️  LiteLLM import check failed: {result.stderr[:200]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
