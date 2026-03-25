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
    """
    Install nginx without requiring apt/sudo.

    Strategy (tried in order):
      1. Already installed at the expected path — use as-is.
      2. System nginx is on PATH — symlink it.
      3. Compile from source with a minimal feature set that only requires
         standard build tools (gcc, make) and zlib, both of which are present
         in every Cloudera ML runtime image.

    Modules intentionally excluded:
      --without-http_ssl_module    : no libssl-dev needed; TLS is terminated
                                     by the CAI/CML platform layer, not nginx.
      --without-http_v2_module     : no libssl-dev needed; HTTP/2 is not
                                     required for internal proxying.
      --without-http_rewrite_module: no libpcre-dev needed; we use only
                                     prefix/exact location matches, not regex.

    Modules intentionally kept (defaults):
      http_gzip_module  : zlib is always available; enables `gzip on` in conf.
      http_proxy_module : core requirement for reverse-proxying Ray services.
    """
    print("\n Setting up Nginx (no-root install)...")

    nginx_bin = "/home/cdsw/.local/bin/nginx"
    nginx_prefix = "/home/cdsw/.local/nginx"

    os.makedirs("/home/cdsw/.local/bin", exist_ok=True)

    # ------------------------------------------------------------------ #
    # Step 1: already installed?                                           #
    # ------------------------------------------------------------------ #
    if os.path.exists(nginx_bin):
        result = subprocess.run(
            [nginx_bin, "-v"], capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"   Nginx already installed: {result.stderr.strip()}")
            return True
        print("   Existing nginx binary is broken — reinstalling...")
        os.remove(nginx_bin)

    # ------------------------------------------------------------------ #
    # Step 2: system nginx on PATH?                                        #
    # ------------------------------------------------------------------ #
    result = subprocess.run(
        ["which", "nginx"], capture_output=True, text=True
    )
    if result.returncode == 0:
        system_nginx = result.stdout.strip()
        print(f"   System nginx found: {system_nginx}")
        try:
            os.symlink(system_nginx, nginx_bin)
            print(f"   Symlinked to: {nginx_bin}")
            return True
        except OSError as e:
            print(f"   Could not create symlink: {e} — will compile from source")

    # ------------------------------------------------------------------ #
    # Step 3: compile from source (no SSL, no PCRE, no apt needed)        #
    # ------------------------------------------------------------------ #
    print("   No system nginx found — compiling from source...")
    print("   (requires gcc, make, zlib — all present in the ML runtime)")

    import tarfile
    import tempfile

    nginx_version = "1.28.1"
    nginx_url = f"https://nginx.org/download/nginx-{nginx_version}.tar.gz"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Download
            tar_path = os.path.join(tmpdir, "nginx.tar.gz")
            print(f"   Downloading nginx-{nginx_version}...")
            if not run_command(f"curl -fsSL -o {tar_path} {nginx_url}", cwd=tmpdir):
                print("   Failed to download nginx source")
                return False

            # Extract
            print("   Extracting...")
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(path=tmpdir)

            src_dir = os.path.join(tmpdir, f"nginx-{nginx_version}")
            if not os.path.isdir(src_dir):
                print(f"   Source directory not found: {src_dir}")
                return False

            # Configure — minimal: no SSL, no HTTP/2, no PCRE/rewrite
            print("   Configuring (minimal build: proxy + gzip only)...")
            configure_cmd = " ".join([
                "./configure",
                f"--prefix={nginx_prefix}",
                f"--sbin-path={nginx_bin}",
                # These paths are compile-time defaults only;
                # our Jinja2 templates override them at runtime via -c flag.
                f"--conf-path={nginx_prefix}/conf/nginx.conf",
                f"--pid-path={nginx_prefix}/run/nginx.pid",
                f"--error-log-path={nginx_prefix}/logs/error.log",
                f"--http-log-path={nginx_prefix}/logs/access.log",
                # Excluded to avoid dev-header dependencies:
                "--without-http_rewrite_module",   # no libpcre-dev needed
                "--without-http_ssl_module",       # no libssl-dev needed (TLS
                                                   # is terminated by CAI/CML)
                "--without-http_v2_module",        # no libssl-dev needed
                # Excluded modules not needed for local proxying:
                "--without-mail_smtp_module",
                "--without-mail_imap_module",
                "--without-mail_pop3_module",
                # NOTE: do NOT add --without-stream_ssl_module here.
                # The stream module is not enabled (no --with-stream), so that
                # flag is unrecognised and causes ./configure to abort.
            ])
            if not run_command(configure_cmd, cwd=src_dir):
                print("   Configure failed")
                return False

            # Compile
            num_cores = os.cpu_count() or 2
            print(f"   Compiling with {num_cores} cores (this takes 1-3 minutes)...")
            if not run_command(f"make -j{num_cores}", cwd=src_dir):
                print("   Compile failed")
                return False

            # Install
            print("   Installing...")
            if not run_command("make install", cwd=src_dir):
                print("   Install failed")
                return False

        # Verify
        if os.path.isfile(nginx_bin):
            result = subprocess.run(
                [nginx_bin, "-v"], capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"   Nginx compiled and installed: {result.stderr.strip()}")
                print(f"   Binary: {nginx_bin}")
                return True

        print("   Nginx binary not found after compilation")
        return False

    except Exception as exc:
        import traceback
        print(f"   Exception during nginx compilation: {exc}")
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
            "starlette>=0.36.0",
            "jinja2>=3.1.0",      # nginx config template rendering
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
