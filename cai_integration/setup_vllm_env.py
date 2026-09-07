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

    # Verify `ninja` is actually RESOLVABLE (not merely installed). FlashInfer /
    # torch.compile shell out to the `ninja` binary at engine startup. Some
    # `ninja` wheels install the real binary only into the package's BIN_DIR and
    # skip the <venv>/bin console-script shim, so PATH lookups fail with
    # `FileNotFoundError: 'ninja'` even though the package IS installed (and thus
    # passes the _PRESENCE_CRITICAL check). Catch + repair that here at build
    # time rather than at deploy time.
    if not _ensure_ninja_resolvable(venv_python, _VENV_DIR):
        sys.exit(1)


def _ensure_ninja_resolvable(venv_python: str, venv_dir: str) -> bool:
    """Ensure the `ninja` binary is on PATH for the venv; repair a missing shim.

    Returns True if ninja is (now) resolvable, False if it is genuinely absent
    (caller should fail the job). Mirrors the runtime BIN_DIR fallback in
    vllm_engine.VLLMEngine.__init__.
    """
    import json
    import subprocess

    probe = (
        "import json, os, shutil\n"
        "info = {'which': shutil.which('ninja'), 'bin_dir': None, 'bin_exists': False}\n"
        "try:\n"
        "    import ninja\n"
        "    bd = getattr(ninja, 'BIN_DIR', None)\n"
        "    info['bin_dir'] = bd\n"
        "    if bd:\n"
        "        p = os.path.join(bd, 'ninja')\n"
        "        info['bin_exists'] = os.path.exists(p) or os.path.exists(p + '.exe')\n"
        "except Exception as e:\n"
        "    info['error'] = repr(e)\n"
        "print(json.dumps(info))\n"
    )
    res = subprocess.run([venv_python, "-c", probe], capture_output=True, text=True)
    try:
        info = json.loads((res.stdout or "").strip().splitlines()[-1])
    except Exception:
        print(f"⚠️  ninja probe failed: {res.stderr[:200] or res.stdout[:200]}")
        return False

    if info.get("which"):
        print(f"✅ ninja resolvable on PATH: {info['which']}")
        return True

    bin_dir = info.get("bin_dir")
    if info.get("bin_exists") and bin_dir:
        # Shim-missing case: create <venv>/bin/ninja -> <BIN_DIR>/ninja so every
        # consumer (torch, FlashInfer) resolves it via PATH, not just our engine.
        src = os.path.join(bin_dir, "ninja")
        dst = os.path.join(venv_dir, "bin", "ninja")
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.islink(dst) or os.path.exists(dst):
                os.remove(dst)
            os.symlink(src, dst)
            print(f"🔧 ninja shim missing from venv bin — linked {dst} -> {src}")
            return True
        except Exception as e:
            print(f"❌ Failed to create ninja shim {dst} -> {src}: {e}")
            return False

    print(
        "❌ ninja is not installed/resolvable in .venv-vllm "
        f"(probe: {info}). FlashInfer/torch.compile will fail at startup. "
        "Rerun with SETUP_FORCE_RECREATE=1 to rebuild the venv."
    )
    return False


if __name__ == "__main__":
    main()
