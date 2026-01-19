#!/usr/bin/env python3
"""
Setup Python environment for Ray cluster on CML.

This script:
1. Creates a Python virtual environment using uv
2. Installs Ray and dependencies using uv
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
        print(f"Error running command: {e}")
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


def install_nginx():
    """Install nginx binary without sudo (download precompiled binary)."""
    print("\n🌐 Setting up Nginx...")

    nginx_bin = "/home/cdsw/.local/bin/nginx"
    nginx_dir = "/home/cdsw/.local/bin"

    # Create bin directory if it doesn't exist
    os.makedirs(nginx_dir, exist_ok=True)

    # Check if nginx is already installed
    if os.path.exists(nginx_bin):
        print("✅ Nginx already installed")
        return True

    # First check if system nginx exists
    result = subprocess.run("which nginx", shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        system_nginx = result.stdout.strip()
        print(f"✅ System nginx found: {system_nginx}")
        try:
            os.symlink(system_nginx, nginx_bin)
        except:
            pass
        return True

    print("📦 Downloading Nginx static binary...")

    # Use static nginx binary from GitHub releases (nginx-static-binary project)
    # This provides fully static binaries that work without any dependencies
    try:
        import platform
        arch = platform.machine()

        # Map architecture names
        if arch == "x86_64":
            arch_suffix = "amd64"
        elif arch == "aarch64" or arch == "arm64":
            arch_suffix = "arm64"
        else:
            print(f"⚠️  Unsupported architecture: {arch}")
            return False

        download_url = f"https://github.com/just-containers/nginx-static/releases/download/v1.25.3/nginx-linux-{arch_suffix}.tar.gz"

        cmds = [
            f"cd /tmp",
            f"curl -L -o nginx.tar.gz {download_url}",
            f"tar xzf nginx.tar.gz",
            f"mv nginx {nginx_bin}",
            f"chmod +x {nginx_bin}",
            f"rm -f nginx.tar.gz",
        ]

        full_cmd = " && ".join(cmds)
        result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=120)

        if result.returncode == 0 and os.path.exists(nginx_bin):
            print("✅ Nginx installed successfully")
            print(f"   Binary location: {nginx_bin}")

            # Verify it works
            version_check = subprocess.run(f"{nginx_bin} -v", shell=True, capture_output=True, text=True)
            if version_check.returncode == 0:
                print(f"   {version_check.stderr.strip()}")

            return True
        else:
            print(f"⚠️  Nginx download/install failed")
            if result.stderr:
                print(f"   Error: {result.stderr[:200]}")
            return False

    except Exception as e:
        print(f"⚠️  Exception during nginx installation: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Main setup function."""
    print("=" * 70)
    print("🔧 Setting up Python environment for Ray cluster")
    print("=" * 70)

    # Change to project directory
    os.chdir("/home/cdsw")
    print(f"Working directory: {os.getcwd()}\n")

    # Install system dependencies
    install_nginx()

    venv_dir = "/home/cdsw/.venv"

    # Check if environment is already properly configured
    if is_venv_ready(venv_dir):
        print(f"✅ Virtual environment already exists at: {venv_dir}")
        print("   Verifying Ray installation...")

        # Check if Ray is installed
        check_ray = f'{venv_dir}/bin/python -c "import ray; print(ray.__version__)"'
        result = subprocess.run(check_ray, shell=True, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"✅ Ray {result.stdout.strip()} is already installed")
            print("\n" + "=" * 70)
            print("✅ Environment already ready - skipped setup!")
            print("=" * 70)
            return
        else:
            print("⚠️  Ray not found, will reinstall...")

    # Install uv first (bypasses pip config issues)
    print("\n⬇️  Installing uv package manager...")
    if not run_command("pip install uv"):
        print("❌ Failed to install uv")
        sys.exit(1)

    # Verify uv installation
    print("\n🔍 Verifying uv installation...")
    if not run_command("uv --version"):
        print("❌ Failed to verify uv installation")
        sys.exit(1)

    # Create virtual environment with uv
    print("\n📝 Creating Python virtual environment...")
    if os.path.exists(venv_dir):
        print(f"   Removing existing incomplete venv...")
        run_command(f"rm -rf {venv_dir}")

    if not run_command(f"uv venv {venv_dir}"):
        print("❌ Failed to create virtual environment")
        sys.exit(1)

    print("✅ Virtual environment created\n")

    # Install Ray and dependencies using uv
    print("🚀 Installing Ray and dependencies...")

    ray_packages = [
        "ray[default]>=2.20.0",
        "ray[tune]",
        "ray[serve]",
        "numpy",
        "pandas",
        "scikit-learn",
        "matplotlib",
        "fastapi",
        "uvicorn[standard]",
        "pydantic",
        "httpx",
    ]

    for package in ray_packages:
        print(f"\n📦 Installing {package}...")
        if not run_command(f"uv pip install {package}"):
            print(f"⚠️  Warning: Could not install {package}")

    # Verify Ray installation
    print("\n🔍 Verifying Ray installation...")
    check_ray = f'{venv_dir}/bin/python -c "import ray; print(ray.__version__)"'
    result = subprocess.run(check_ray, shell=True, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✅ Ray {result.stdout.strip()}")
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
    print("\nTo activate the environment manually:")
    print(f"  source {venv_dir}/bin/activate")
    print("\nNext step: Ray cluster will be launched by the next job")


if __name__ == "__main__":
    main()
