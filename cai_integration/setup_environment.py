#!/usr/bin/env python3
"""
Setup Python environment for Ray cluster on CML.

This script:
1. Creates a Python virtual environment
2. Installs Ray and dependencies
3. Verifies installation

Run this as a CML job to prepare the environment for Ray cluster deployment.
"""

import os
import sys
import subprocess


def run_command(cmd, cwd=None):
    """Run a command and return success status."""
    print(f"Running: {cmd}")
    try:
        # Use shell=True to avoid subprocess inheriting pip config issues
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True
        )
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        if e.stderr:
            print(f"Error output: {e.stderr}")
        return False


def is_venv_ready(venv_dir):
    """Check if virtual environment exists and is properly configured."""
    if not os.path.exists(venv_dir):
        return False

    # Check if python executable exists in venv
    python_exe = os.path.join(venv_dir, "bin", "python")
    if not os.path.exists(python_exe):
        return False

    # Check if pyvenv.cfg exists (indicator of valid venv)
    pyvenv_cfg = os.path.join(venv_dir, "pyvenv.cfg")
    if not os.path.exists(pyvenv_cfg):
        return False

    return True


def main():
    """Main setup function."""
    print("=" * 70)
    print("🔧 Setting up Python environment for Ray cluster")
    print("=" * 70)

    # Change to project directory
    os.chdir("/home/cdsw")
    print(f"Working directory: {os.getcwd()}\n")

    venv_dir = "/home/cdsw/.venv"

    # Check if environment is already properly configured
    if is_venv_ready(venv_dir):
        print(f"✅ Virtual environment already exists at: {venv_dir}")
        print("   Verifying Ray installation...")

        # Check if Ray is installed
        check_ray = f"{venv_dir}/bin/python -c 'import ray; print(f\"Ray {{ray.__version__}}\")"
        result = subprocess.run(check_ray, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"✅ {result.stdout.strip()} is already installed")
            print("\n" + "=" * 70)
            print("✅ Environment already ready - skipped setup!")
            print("=" * 70)
            return
        else:
            print("⚠️  Ray not found, will reinstall...")

    # Create virtual environment
    print("\n📝 Creating Python virtual environment...")
    if os.path.exists(venv_dir):
        print(f"   Removing existing incomplete venv...")
        run_command(f"rm -rf {venv_dir}")

    if not run_command(f"python3 -m venv {venv_dir}"):
        print("❌ Failed to create virtual environment")
        sys.exit(1)

    print("✅ Virtual environment created\n")

    # Upgrade pip (using shell command to avoid --user issues)
    print("📦 Upgrading pip, setuptools, and wheel...")
    if not run_command(f"{venv_dir}/bin/pip install --upgrade pip setuptools wheel"):
        print("⚠️  Warning: Could not upgrade pip packages\n")
    else:
        print("✅ Pip upgraded successfully\n")

    # Install Ray and dependencies
    print("🚀 Installing Ray and dependencies...")

    ray_packages = [
        "ray[default]>=2.20.0",
        "ray[tune]",
        "numpy",
        "pandas",
        "scikit-learn",
        "matplotlib",
    ]

    for package in ray_packages:
        print(f"\n📦 Installing {package}...")
        if not run_command(f"{venv_dir}/bin/pip install {package}"):
            print(f"⚠️  Warning: Could not install {package}")

    # Verify Ray installation
    print("\n🔍 Verifying Ray installation...")
    check_ray = f"{venv_dir}/bin/python -c 'import ray; print(f\"Ray {{ray.__version__}}\")"
    result = subprocess.run(check_ray, shell=True, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✅ {result.stdout.strip()}")
    else:
        print(f"❌ Ray verification failed: {result.stderr}")
        sys.exit(1)

    # Test Ray basic functionality (optional)
    print("\n🧪 Testing Ray functionality...")
    test_script = """
import ray
@ray.remote
def test_function():
    return 'Ray is working!'
ray.init(address='auto', ignore_reinit_error=True)
result = ray.get(test_function.remote())
print(f'✅ {result}')
ray.shutdown()
"""
    test_cmd = f"{venv_dir}/bin/python -c \"{test_script}\""
    result = subprocess.run(test_cmd, shell=True, capture_output=True, text=True, timeout=30)

    if result.returncode == 0:
        print(result.stdout.strip())
    else:
        print(f"⚠️  Ray test skipped (expected if no cluster running): {result.stderr[:100]}")

    print("\n" + "=" * 70)
    print("✅ Environment setup complete!")
    print("=" * 70)
    print(f"\nVirtual environment: {venv_dir}")
    print(f"Python binary: {venv_dir}/bin/python")
    print(f"Pip binary: {venv_dir}/bin/pip")
    print("\nTo activate the environment manually:")
    print(f"  source {venv_dir}/bin/activate")
    print("\nNext step: Ray cluster will be launched by the next job")


if __name__ == "__main__":
    main()
