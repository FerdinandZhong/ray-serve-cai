#!/usr/bin/env python3
"""
CAI Job Entry Point for Launching Ray Cluster

This Python script serves as the entry point for CAI jobs.
It activates the virtual environment via bash wrapper and launches the Ray cluster.

This pattern is required by CAI:
- CAI jobs require Python entry points
- We use subprocess to call bash wrapper scripts
- Bash wrapper handles venv activation cleanly

Usage as CAI Job:
  script: "cai_integration/launch_ray_cluster_job.py"

Or manually:
  python cai_integration/launch_ray_cluster_job.py
"""

import subprocess
import sys
from pathlib import Path


def main():
    """Execute the Ray cluster launch via bash wrapper."""
    # Get cai_integration directory
    # Handle case where __file__ might not be available in some CAI execution contexts
    try:
        script_dir = Path(__file__).resolve().parent
        project_root = script_dir.parent
    except (NameError, AttributeError):
        # Fallback: assume we're in project root and cai_integration is a subdirectory
        project_root = Path.cwd()
        script_dir = project_root / "cai_integration"

    # Path to bash wrapper script (in cai_integration)
    bash_wrapper = script_dir / "launch_ray_cluster.sh"

    if not bash_wrapper.exists():
        print(f"❌ Error: Bash wrapper not found at {bash_wrapper}")
        print("Expected location: cai_integration/launch_ray_cluster.sh")
        return 1

    print("=" * 70)
    print("🚀 Ray Cluster Launch (CAI Job Entry Point)")
    print("=" * 70)
    print(f"\n📝 Executing: bash {bash_wrapper.name}")
    print(f"   Project root: {project_root}")
    print()

    try:
        # Execute bash wrapper script
        # Pass through any command line arguments
        result = subprocess.run(
            ["bash", str(bash_wrapper)] + sys.argv[1:],
            cwd=str(project_root)
        )

        return result.returncode
    except Exception as e:
        print(f"\n❌ Error executing bash wrapper: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
