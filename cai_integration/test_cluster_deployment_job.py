#!/usr/bin/env python3
"""
CAI Job Entry Point for Testing Cluster Deployment

This Python script serves as the entry point for CAI jobs testing cluster deployment.
It activates the virtual environment via bash wrapper and runs the test script.

This pattern is required by CAI:
- CAI jobs require Python entry points
- We use subprocess to call bash wrapper scripts
- Bash wrapper handles venv activation cleanly

Usage as CAI Job:
  script: "cai_integration/test_cluster_deployment_job.py"

Or manually:
  python cai_integration/test_cluster_deployment_job.py
"""

import subprocess
import sys
from pathlib import Path


def main():
    """Execute the cluster deployment test via bash wrapper."""
    # Get cai_integration directory
    script_dir = Path(__file__).parent
    project_root = script_dir.parent

    # Path to bash wrapper script (in cai_integration)
    bash_wrapper = script_dir / "test_cluster.sh"

    if not bash_wrapper.exists():
        print(f"❌ Error: Bash wrapper not found at {bash_wrapper}")
        print("Expected location: cai_integration/test_cluster.sh")
        return 1

    print("=" * 70)
    print("🧪 Cluster Deployment Test (CAI Job Entry Point)")
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
