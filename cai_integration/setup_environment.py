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
    """Install nginx by compiling from source without sudo."""
    print("\n🌐 Setting up Nginx...")

    nginx_bin = "/home/cdsw/.local/bin/nginx"
    nginx_prefix = "/home/cdsw/.local/nginx"

    # Create bin directory if it doesn't exist
    os.makedirs("/home/cdsw/.local/bin", exist_ok=True)

    # Check if nginx is already installed
    if os.path.exists(nginx_bin):
        print("✅ Nginx already installed")
        # Verify it works
        result = subprocess.run(f"{nginx_bin} -v", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"   Version: {result.stderr.strip()}")  # nginx prints version to stderr
            return True
        else:
            print("⚠️  Existing nginx binary seems broken, reinstalling...")
            os.remove(nginx_bin)

    # First check if system nginx exists
    result = subprocess.run("which nginx", shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        system_nginx = result.stdout.strip()
        print(f"✅ System nginx found: {system_nginx}")
        try:
            os.symlink(system_nginx, nginx_bin)
            print(f"✅ Created symlink to system nginx")
            return True
        except Exception as e:
            print(f"⚠️  Could not create symlink: {e}")

    print("📦 Compiling Nginx from source...")
    print("   This may take a few minutes...")

    try:
        import tempfile
        import tarfile

        # Nginx version to download
        nginx_version = "1.28.1"
        nginx_url = f"https://nginx.org/download/nginx-{nginx_version}.tar.gz"

        with tempfile.TemporaryDirectory() as tmpdir:
            print(f"   Working directory: {tmpdir}")

            # Download nginx source
            print(f"   📥 Downloading nginx-{nginx_version}...")
            tar_path = os.path.join(tmpdir, "nginx.tar.gz")
            download_cmd = f"curl -L -o {tar_path} {nginx_url}"

            if not run_command(download_cmd, cwd=tmpdir):
                print("❌ Failed to download nginx source")
                return False

            # Extract
            print("   📦 Extracting source...")
            with tarfile.open(tar_path, 'r:gz') as tar:
                tar.extractall(path=tmpdir)

            nginx_src_dir = os.path.join(tmpdir, f"nginx-{nginx_version}")

            if not os.path.exists(nginx_src_dir):
                print(f"❌ Source directory not found: {nginx_src_dir}")
                return False

            # Configure
            print("   🔧 Configuring build...")
            configure_cmd = (
                f"./configure "
                f"--prefix={nginx_prefix} "
                f"--sbin-path={nginx_bin} "
                f"--conf-path={nginx_prefix}/conf/nginx.conf "
                f"--pid-path={nginx_prefix}/run/nginx.pid "
                f"--error-log-path={nginx_prefix}/logs/error.log "
                f"--http-log-path={nginx_prefix}/logs/access.log "
                f"--without-http_rewrite_module "
                f"--without-http_gzip_module "
                f"--with-http_ssl_module "
                f"--with-http_v2_module"
            )

            if not run_command(configure_cmd, cwd=nginx_src_dir):
                print("❌ Failed to configure nginx")
                return False

            # Compile
            print("   🔨 Compiling (this takes 2-3 minutes)...")
            num_cores = os.cpu_count() or 2
            make_cmd = f"make -j{num_cores}"

            if not run_command(make_cmd, cwd=nginx_src_dir):
                print("❌ Failed to compile nginx")
                return False

            # Install
            print("   📦 Installing...")
            install_cmd = "make install"

            if not run_command(install_cmd, cwd=nginx_src_dir):
                print("❌ Failed to install nginx")
                return False

        # Verify installation
        if os.path.exists(nginx_bin):
            result = subprocess.run(f"{nginx_bin} -v", shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                print(f"✅ Nginx installed successfully")
                print(f"   Binary: {nginx_bin}")
                print(f"   Version: {result.stderr.strip()}")
                return True

        print("❌ Nginx binary not found after installation")
        return False

    except Exception as e:
        print(f"❌ Exception during nginx installation: {e}")
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

    # Install the package itself first (includes all dependencies from pyproject.toml)
    print("🚀 Installing ray-serve-cai package and dependencies...")

    # Install package in editable mode with all extras (includes vLLM and sglang)
    print("\n📦 Installing ray-serve-cai package with all extras (vLLM, sglang)...")
    if run_command("uv pip install -e '/home/cdsw[all]'"):
        print("✅ ray-serve-cai package installed with all dependencies and extras")
    else:
        print("⚠️  Failed to install via package, installing dependencies manually...")

        # Fallback: Install dependencies manually
        # These match pyproject.toml dependencies
        ray_packages = [
            # Core dependencies from pyproject.toml
            "ray[serve]>=2.53.0",
            "pyyaml>=6.0.3",
            "aiohttp>=3.13.3",
            # Management API dependencies
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "pydantic>=2.0.0",
            "httpx>=0.27.0",
            "starlette>=0.36.0",  # FastAPI dependency
            # Common ML libraries (optional but useful)
            "numpy>=1.24.0",
            "pandas>=2.0.0",
            # LLM inference engines
            "vllm>=0.13.0",
            "sglang>=0.5.7",
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
