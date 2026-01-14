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
from pathlib import Path


def run_command(cmd, description=""):
    """Run a shell command and handle errors."""
    if description:
        print(f"➡️  {description}")
    print(f"   Running: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            print(f"   ✅ Success")
        return True
    except subprocess.CalledProcessError as e:
        print(f"   ❌ Failed: {e.stderr}")
        return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def main():
    """Main setup function."""
    print("=" * 70)
    print("🔧 Setting up Python environment for Ray cluster")
    print("=" * 70)

    # Determine venv location
    venv_path = Path("/home/cdsw/.venv")

    # Check if venv already exists
    if venv_path.exists():
        print(f"\n✅ Virtual environment already exists at {venv_path}")

        # Verify Ray is installed
        try:
            result = subprocess.run(
                [str(venv_path / "bin" / "python"), "-c", "import ray; print(ray.__version__)"],
                capture_output=True,
                text=True,
                check=True
            )
            print(f"✅ Ray {result.stdout.strip()} is already installed")
            print("\n✅ Environment setup complete!")
            return 0
        except Exception:
            print("⚠️  Ray not found in existing venv, will reinstall...")

    print("\n📝 Creating Python virtual environment...")

    # Create virtual environment
    if not run_command([sys.executable, "-m", "venv", str(venv_path)], "Creating venv"):
        print("❌ Failed to create virtual environment")
        return 1

    python_bin = venv_path / "bin" / "python"
    pip_bin = venv_path / "bin" / "pip"

    print("\n📦 Upgrading pip, setuptools, and wheel...")
    if not run_command([str(pip_bin), "install", "--upgrade", "pip", "setuptools", "wheel"]):
        print("⚠️  Warning: Could not upgrade pip packages")

    print("\n🚀 Installing Ray and dependencies...")

    # Ray installation
    ray_packages = [
        "ray[default]>=2.20.0",  # Ray with default dependencies
        "ray[tune]",              # For hyperparameter tuning
    ]

    for package in ray_packages:
        if not run_command([str(pip_bin), "install", package], f"Installing {package}"):
            print(f"⚠️  Warning: Could not install {package}")

    print("\n✨ Installing additional dependencies...")
    additional_deps = [
        "numpy",
        "pandas",
        "scikit-learn",
        "matplotlib",
    ]

    for package in additional_deps:
        run_command([str(pip_bin), "install", package], f"Installing {package}")

    print("\n🔍 Verifying installation...")

    # Verify Ray installation
    try:
        result = subprocess.run(
            [str(python_bin), "-c", "import ray; print(f'Ray {ray.__version__}')"],
            capture_output=True,
            text=True,
            check=True
        )
        print(f"✅ {result.stdout.strip()}")
    except Exception as e:
        print(f"❌ Ray verification failed: {e}")
        return 1

    # Test Ray basic functionality
    print("\n🧪 Testing Ray functionality...")
    test_script = """
import ray

@ray.remote
def test_function():
    return "Ray is working!"

ray.init(address='auto', ignore_reinit_error=True)
result = ray.get(test_function.remote())
print(f"✅ {result}")
ray.shutdown()
"""

    try:
        result = subprocess.run(
            [str(python_bin), "-c", test_script],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print(f"⚠️  Ray test warning: {result.stderr[:200]}")
    except subprocess.TimeoutExpired:
        print("⚠️  Ray test timed out (expected if no Ray cluster running)")
    except Exception as e:
        print(f"⚠️  Ray test skipped: {e}")

    print("\n" + "=" * 70)
    print("✅ Environment setup complete!")
    print("=" * 70)
    print(f"\nVirtual environment: {venv_path}")
    print(f"Python binary: {python_bin}")
    print(f"Pip binary: {pip_bin}")
    print("\nTo activate the environment manually:")
    print(f"  source {venv_path}/bin/activate")
    print("\nNext step: Run 'launch_ray_cluster.py' job to start Ray cluster")

    return 0


if __name__ == "__main__":
    sys.exit(main())
