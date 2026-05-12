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
from pathlib import Path


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
        if e.stdout:
            print(f"Output: {e.stdout}")
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
      3. Download a pre-built static binary from nginx.org — no compiler,
         no dev headers, no build tools required.
    """
    print("\n Setting up Nginx (no-root install)...")

    home = Path.home()
    nginx_bin = str(home / ".local" / "bin" / "nginx")

    os.makedirs(str(home / ".local" / "bin"), exist_ok=True)

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
            print(f"   Could not create symlink: {e} — will download static binary")

    # ------------------------------------------------------------------ #
    # Step 3: compile from source (no SSL, no PCRE, no zlib needed)      #
    # ------------------------------------------------------------------ #
    import tarfile
    import tempfile

    nginx_version = os.environ.get("NGINX_VERSION", "1.29.7")
    nginx_url = os.environ.get(
        "NGINX_SOURCE_URL",
        f"https://nginx.org/download/nginx-{nginx_version}.tar.gz",
    )
    nginx_prefix = str(home / ".local" / "nginx")

    print(f"   No system nginx found — compiling from source (nginx {nginx_version})...")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = os.path.join(tmpdir, "nginx.tar.gz")
            print(f"   Downloading {nginx_url} ...")
            if not run_command(f"curl -fsSL -o {tar_path} {nginx_url}", cwd=tmpdir):
                print("   Failed to download nginx source")
                return False

            print("   Extracting...")
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(path=tmpdir)

            src_dir = os.path.join(tmpdir, f"nginx-{nginx_version}")
            if not os.path.isdir(src_dir):
                print(f"   Source directory not found: {src_dir}")
                return False

            # Minimal build: proxy only — no SSL, no PCRE, no zlib needed.
            # TLS is terminated by the CAI/CML platform layer, not nginx.
            configure_cmd = " ".join([
                "./configure",
                f"--prefix={nginx_prefix}",
                f"--sbin-path={nginx_bin}",
                f"--conf-path={nginx_prefix}/conf/nginx.conf",
                f"--pid-path={nginx_prefix}/run/nginx.pid",
                f"--error-log-path={nginx_prefix}/logs/error.log",
                f"--http-log-path={nginx_prefix}/logs/access.log",
                "--without-http_rewrite_module",  # no libpcre-dev
                # "--without-http_ssl_module",      # no libssl-dev
                # "--without-http_v2_module",       # no libssl-dev
                "--without-http_gzip_module",     # no zlib-dev
                "--without-mail_smtp_module",
                "--without-mail_imap_module",
                "--without-mail_pop3_module",
            ])
            print("   Configuring...")
            if not run_command(configure_cmd, cwd=src_dir):
                print("   Configure failed")
                return False

            num_cores = os.cpu_count() or 2
            print(f"   Compiling with {num_cores} cores...")
            if not run_command(f"make -j{num_cores}", cwd=src_dir):
                print("   Compile failed")
                return False

            if not run_command("make install", cwd=src_dir):
                print("   Install failed")
                return False

        result = subprocess.run([nginx_bin, "-v"], capture_output=True, text=True)
        if result.returncode == 0:
            print(f"   Nginx installed: {result.stderr.strip()}")
            return True

        print("   Nginx binary not found after compilation")
        return False

    except Exception as exc:
        import traceback
        print(f"   Exception during nginx compilation: {exc}")
        traceback.print_exc()
        return False


def setup_engine_venv(engine: str, packages: list, venv_base: str = "/home/cdsw") -> bool:
    """Create /home/cdsw/.venv-<engine> with fcntl.flock for NFS-safe concurrent creation."""
    import fcntl

    venv_dir = f"{venv_base}/.venv-{engine}"
    lock_path = f"{venv_base}/.venv-{engine}.lock"

    if is_venv_ready(venv_dir):
        print(f"✅ {engine} venv already ready at {venv_dir}")
        return True

    print(f"\n🔧 Creating {engine} venv at {venv_dir} ...")
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if is_venv_ready(venv_dir):
            print(f"✅ {engine} venv created by another process")
            return True

        if not run_command(f"uv venv {venv_dir}"):
            print(f"❌ Failed to create {engine} venv")
            return False

        uv_install = f"uv pip install --python {venv_dir}/bin/python"
        for pkg in packages:
            if not run_command(f"{uv_install} '{pkg}'"):
                print(f"⚠️  {pkg} failed for {engine} venv — continuing")

        ready = is_venv_ready(venv_dir)
        if ready:
            print(f"✅ {engine} venv ready")
        else:
            print(f"❌ {engine} venv not ready after install")
        return ready
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


_ENGINE_PACKAGES = {
    "vllm":    ["vllm>=0.13.0", "ninja"],
    "sglang":  ["sglang>=0.5.7"],
    "yolo":    ["ultralytics>=8.0.0", "Pillow>=9.0.0", "opencv-python-headless>=4.8.0"],
    "mcp":     ["mcp>=1.0.0", "httpx>=0.27.0"],
    "litellm": ["litellm>=1.83.0"],
}


def main():
    """Main setup function."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force", action="store_true",
        help="Delete and recreate the venv even if it already exists "
             "(also honoured via SETUP_FORCE_RECREATE=1)"
    )
    args, _ = parser.parse_known_args()

    force = args.force or os.environ.get("SETUP_FORCE_RECREATE", "").strip() in ("1", "true", "yes")

    print("=" * 70)
    print("🔧 Setting up Python environment for Ray cluster")
    print("=" * 70)

    # Change to project directory
    os.chdir("/home/cdsw")
    print(f"Working directory: {os.getcwd()}\n")

    # Install system dependencies
    install_nginx()

    venv_dir = "/home/cdsw/.venv"

    if force and os.path.exists(venv_dir):
        print(f"⚠️  --force: removing existing venv at {venv_dir}")
        run_command(f"rm -rf {venv_dir}")

    # Check if environment is already properly configured
    if not force and is_venv_ready(venv_dir):
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

    # Always target the venv explicitly so packages land in the right place
    # regardless of whether the caller has activated the venv.
    uv_install = f"uv pip install --python {venv_dir}/bin/python"

    # Install core package (no inference-engine extras — vllm and sglang
    # require conflicting llguidance versions and cannot be co-installed).
    print("\n📦 Installing ray-serve-cai core package...")
    if run_command(f"{uv_install} -e '/home/cdsw'"):
        print("✅ ray-serve-cai core package installed")
    else:
        print("⚠️  Failed to install via package, installing dependencies manually...")

        # Fallback: Install core dependencies manually (matches pyproject.toml)
        ray_packages = [
            "ray[serve]>=2.53.0",
            "pyyaml>=6.0.3",
            "aiohttp>=3.13.3",
            "fastapi>=0.110.0",
            "uvicorn[standard]>=0.27.0",
            "pydantic>=2.0.0",
            "httpx>=0.27.0",
            "starlette>=0.36.0",
            "jinja2>=3.1.0",
        ]

        for package in ray_packages:
            print(f"\n📦 Installing {package}...")
            if not run_command(f"{uv_install} {package}"):
                print(f"⚠️  Warning: Could not install {package}")

    # Install YOLO dependencies (ultralytics + Pillow).
    # These are lightweight and do not conflict with vllm/sglang.
    # opencv-python-headless is needed by ultralytics for image I/O on a
    # headless server (no display); the -headless variant avoids pulling in
    # libGL which is absent in most CML containers.
    print("\n📦 Installing YOLO dependencies (ultralytics, Pillow, opencv-headless)...")
    yolo_packages = [
        "ultralytics>=8.0.0",
        "Pillow>=9.0.0",
        "opencv-python-headless>=4.8.0",
    ]
    for pkg in yolo_packages:
        if run_command(f"{uv_install} '{pkg}'"):
            print(f"✅ {pkg.split('>=')[0]} installed")
        else:
            print(f"⚠️  {pkg} failed — YOLO engine may not work")

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
