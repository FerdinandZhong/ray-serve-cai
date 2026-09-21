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
import re
import shlex
import shutil
import subprocess
import sys
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


_UV_CMD = None


def _probe(cmd: str) -> bool:
    """True if ``<cmd> --version`` actually runs (short, quiet, never raises)."""
    try:
        r = subprocess.run(
            f"{cmd} --version", shell=True,
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except Exception:
        return False


def _resolve_uv() -> str | None:
    """Find a *working* uv command without installing anything.

    Returns a command string (possibly ``'<python> -m uv'``) verified to run,
    or None if no usable uv exists on the box.
    """
    # a) plain `uv` executable on PATH.
    exe = shutil.which("uv")
    if exe:
        return exe
    # b) The `uv` pip package normally bundles its binary; ask it where it is.
    try:
        import uv as _uv_mod  # noqa: PLC0415
        try:
            cand = _uv_mod.find_uv_bin()
            if cand and os.access(cand, os.X_OK):
                return cand
        except Exception:
            pass
        # `python -m uv` re-runs find_uv_bin() internally, so it ONLY works if
        # the binary truly exists. A stub-only wheel (common on CML — the pip
        # package installs without its binary) makes both fail, so probe before
        # trusting `-m uv` instead of returning a command guaranteed to die.
        mod_cmd = f"{sys.executable} -m uv"
        if _probe(mod_cmd):
            return mod_cmd
    except Exception:
        pass
    # c) Known bin locations (installed-script layouts).
    import site  # noqa: PLC0415
    for cand in (
        os.path.join(os.path.dirname(sys.executable), "uv"),
        os.path.join(_BASE_VENV, "bin", "uv"),
        os.path.join(site.getuserbase(), "bin", "uv"),
        os.path.expanduser("~/.local/bin/uv"),
        os.path.expanduser("~/.cargo/bin/uv"),
    ):
        if os.access(cand, os.X_OK):
            return cand
    return None


def _ensure_uv() -> str:
    """Resolve a usable ``uv`` executable, installing it if necessary.

    ``setup_ray_environment()`` bootstraps uv, but standalone engine jobs
    (e.g. ``setup_vllm_env.py``) call ``setup_engine_venv()`` directly and never
    run that bootstrap. Resolve robustly and cache. Raises if uv genuinely
    cannot be made to work — but the self-heal path uses _pip_install_into_venv()
    which does NOT require uv, so a broken uv only blocks fresh venv creation.
    """
    global _UV_CMD
    if _UV_CMD:
        return _UV_CMD

    cmd = _resolve_uv()
    if not cmd:
        # A stub-only `uv` wheel installs the package without its binary; a
        # forced reinstall re-fetches the platform wheel that carries the binary
        # (landing in ~/.local/bin for a --user install), then re-resolve.
        print("⬇️  uv not usable — (re)installing it ...")
        run_command(f"{sys.executable} -m pip install --user --force-reinstall uv")
        import importlib  # noqa: PLC0415
        importlib.invalidate_caches()
        cmd = _resolve_uv()

    if not cmd:
        raise RuntimeError(
            "uv is not available and could not be installed (tried PATH, "
            "`import uv`/find_uv_bin, probed `-m uv`, base venv, user base, "
            "~/.local/bin, ~/.cargo/bin, and a --user --force-reinstall)"
        )

    _UV_CMD = cmd
    return _UV_CMD


def _pip_install_into_venv(venv_dir: str, spec: str) -> bool:
    """Install one requirement into an existing venv — uv-optional.

    The self-heal / reconcile path must work even when uv is broken or absent
    (a stub-only `uv` wheel on CML makes `uv`/`-m uv` unusable). Prefer a
    *verified* uv; otherwise fall back to the venv's own pip, bootstrapping it
    with stdlib ``ensurepip`` first because ``uv venv`` creates venvs without pip.
    """
    cmd = _resolve_uv()  # no-install probe; avoids a pip-install storm per repin
    if cmd:
        return run_command(f"{cmd} pip install --python {venv_dir}/bin/python '{spec}'")
    py = f"{venv_dir}/bin/python"
    print("   uv unavailable — installing via the venv's own pip (ensurepip)")
    # CML base images export PIP_USER=1 so image-level pip installs land in
    # ~/.local. Inherited into a venv, pip then attempts an illegal `--user`
    # install ("User site-packages are not visible in this virtualenv").
    # Force PIP_USER=0 so the install targets the venv's own site-packages.
    env = "PIP_USER=0"
    run_command(f"{env} {py} -m ensurepip --upgrade")  # idempotent if pip present
    return run_command(f"{env} {py} -m pip install '{spec}'")


def _pip_install_bundle_into_venv(venv_dir: str, specs: list[str]) -> bool:
    """Install requirements in one resolver transaction.

    Installing one requirement per command can silently upgrade/downgrade a
    dependency chosen for an earlier requirement.  A single invocation makes
    the resolver consider the complete engine contract together.
    """
    if not specs:
        return True
    requirements = " ".join(shlex.quote(spec) for spec in specs)
    cmd = _resolve_uv()
    if cmd:
        return run_command(
            f"{cmd} pip install --python {venv_dir}/bin/python {requirements}"
        )
    py = f"{venv_dir}/bin/python"
    print("   uv unavailable — installing via the venv's own pip (ensurepip)")
    env = "PIP_USER=0"
    run_command(f"{env} {py} -m ensurepip --upgrade")
    return run_command(f"{env} {py} -m pip install {requirements}")


def _venv_dependencies_consistent(venv_dir: str) -> bool:
    """Check the target venv for broken or conflicting transitive dependencies."""
    cmd = _resolve_uv()
    if cmd:
        return run_command(f"{cmd} pip check --python {venv_dir}/bin/python")
    py = f"{venv_dir}/bin/python"
    print("   uv unavailable — validating dependencies via existing target-venv pip")
    if run_command(f"PIP_USER=0 {py} -m pip check"):
        return True
    print(
        f"❌ Could not validate dependencies in {venv_dir}; target pip is missing "
        "or reported an inconsistent environment"
    )
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


# Base venv created by main() — the cluster head and Management API run from
# here.  Every engine venv MUST run the same Ray version as this one; see
# _pin_ray_to_base().
_BASE_VENV = "/home/cdsw/.venv"


def venv_ray_version(venv_dir: str) -> str | None:
    """Return the Ray version installed in *venv_dir*, or None if absent."""
    check = f'{venv_dir}/bin/python -c "import ray; print(ray.__version__)"'
    result = subprocess.run(check, shell=True, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _venv_pkg_version(venv_dir: str, pkg_name: str) -> str | None:
    """Return the version of *pkg_name* installed in *venv_dir*, or None."""
    check = (
        f'{venv_dir}/bin/python -c '
        f'"import importlib.metadata; print(importlib.metadata.version(\'{pkg_name}\'))"'
    )
    result = subprocess.run(check, shell=True, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _pin_ray_to_base(packages: list, base_version: str | None) -> list:
    """Rewrite any floating ``ray``/``ray[serve]`` requirement to an exact pin.

    Ray Serve requires the worker venv and the cluster head to run the SAME
    Ray version — otherwise DeploymentConfig proto (de)serialization mismatches
    and the actor dies with an opaque error (e.g. a missing FieldDescriptor
    attribute).  A floating ``>=`` lets the engine venv drift to a newer PyPI
    release than the base env when they're installed at different times.
    Pinning every engine venv to the base env's exact version prevents this.
    """
    if not base_version:
        print("⚠️  Could not read base env Ray version — leaving ray requirement unpinned")
        return packages
    pinned = []
    for pkg in packages:
        m = re.match(r"^ray(\[[^\]]*\])?(?:[<>=!~].*)?$", pkg.strip())
        if m:
            extras = m.group(1) or ""
            pinned.append(f"ray{extras}=={base_version}")
        else:
            pinned.append(pkg)
    return pinned


def _base_pkg_version(pkg_name: str) -> str | None:
    """Return the version of *pkg_name* installed in the base venv, or None."""
    check = (
        f'{_BASE_VENV}/bin/python -c '
        f'"import importlib.metadata; print(importlib.metadata.version(\'{pkg_name}\'))"'
    )
    result = subprocess.run(check, shell=True, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def _pin_pkgs_to_base(packages: list, pkg_names: list[str]) -> list:
    """Rewrite floating requirements for *pkg_names* to exact base-env versions.

    Ray cloudpickles certain objects (e.g. the FastAPI app) on the head node
    and unpickles them inside each engine venv actor.  If an internal class was
    renamed or removed between versions, unpickling fails with AttributeError.
    Pinning these packages to the base env's exact version prevents the drift.
    """
    versions = {}
    for name in pkg_names:
        v = _base_pkg_version(name)
        if v:
            versions[name] = v
        else:
            print(f"⚠️  Could not read base env {name} version — leaving unpinned")

    if not versions:
        return packages

    pinned = []
    for pkg in packages:
        matched = False
        for name, version in versions.items():
            # Match bare name or name[extras], ignoring any existing specifier.
            m = re.match(rf"^{re.escape(name)}(\[[^\]]*\])?(?:[<>=!~].*)?$", pkg.strip(), re.IGNORECASE)
            if m:
                extras = m.group(1) or ""
                pinned.append(f"{name}{extras}=={version}")
                matched = True
                break
        if not matched:
            pinned.append(pkg)
    return pinned


def _requirement_satisfied(venv_dir: str, spec: str) -> bool:
    """Return whether the installed distribution satisfies *spec*."""
    try:
        from packaging.requirements import Requirement
    except ImportError:
        # packaging ships with pip even on lean bootstrap images.
        from pip._vendor.packaging.requirements import Requirement

    requirement = Requirement(spec)
    installed = _venv_pkg_version(venv_dir, requirement.name)
    return installed is not None and (
        not requirement.specifier or requirement.specifier.contains(installed)
    )


def _resolved_engine_packages(packages: list[str]) -> list[str]:
    """Apply head/worker compatibility pins before resolving an engine bundle."""
    packages = _pin_ray_to_base(packages, venv_ray_version(_BASE_VENV))
    return _pin_pkgs_to_base(packages, ["fastapi"])


def _reconcile_engine_venv(
    engine: str,
    venv_dir: str,
    lock_path: str,
    packages: list = (),
    *,
    lock_held: bool = False,
) -> bool:
    """Reconcile an *existing* venv with the base env, in place.

    Every declared top-level requirement is checked, including engine-specific
    version ranges and build/toolkit packages. If any are missing or out of
    range, the complete bundle is resolved together and installed in place.
    Finally, a target-venv dependency check catches invalid transitive state
    that top-level version checks alone cannot detect.

    The *checks* run lock-free (cheap, read-only). Only when a repair is needed
    do we take the NFS flock and reinstall — re-checking under the lock so
    concurrent pods don't reinstall redundantly.
    """
    import fcntl

    required = _resolved_engine_packages(list(packages))
    unsatisfied = [spec for spec in required if not _requirement_satisfied(venv_dir, spec)]
    if not unsatisfied:
        return _venv_dependencies_consistent(venv_dir)

    lock_fd = None if lock_held else open(lock_path, "w")
    try:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        # Another pod may have repaired the venv while this process waited.
        unsatisfied = [
            spec for spec in required if not _requirement_satisfied(venv_dir, spec)
        ]
        if not unsatisfied:
            return _venv_dependencies_consistent(venv_dir)
        print(f"⚠️  {engine} venv does not satisfy: {', '.join(unsatisfied)}")
        if not _pip_install_bundle_into_venv(venv_dir, required):
            print(f"❌ Failed to reconcile the required {engine} package bundle")
            return False
        remaining = [
            spec for spec in required if not _requirement_satisfied(venv_dir, spec)
        ]
        if remaining:
            print(
                f"❌ {engine} venv still violates required packages after install: "
                f"{', '.join(remaining)}"
            )
            return False
        if not _venv_dependencies_consistent(venv_dir):
            print(f"❌ {engine} venv has inconsistent transitive dependencies")
            return False
        print(f"✅ {engine} venv reconciled with required package bundle")
        return True
    finally:
        if lock_fd is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


def setup_engine_venv(
    engine: str,
    packages: list,
    venv_base: str = "/home/cdsw",
    python: str = None,
) -> bool:
    """Create /home/cdsw/.venv-<engine> with fcntl.flock for NFS-safe concurrent creation.

    Args:
        engine:    Engine name (used as the venv suffix, e.g. 'litellm').
        packages:  List of pip requirement strings to install.
        venv_base: Parent directory for the venv (default /home/cdsw).
        python:    Python interpreter for uv venv, e.g. 'python3.11'.
                   When None, defaults to the base venv's interpreter so the
                   engine actor and the cluster head run the SAME Python (see
                   below). Only pass an explicit value if you know that
                   interpreter exists in the runtime image.
    """
    import fcntl

    # Derive the interpreter from the base venv (== the runtime python) unless
    # the caller pins one explicitly. Hardcoding e.g. 'python3.11' silently
    # breaks on a python3.10 runtime: uv can't find it, downloads a standalone
    # CPython (fatal when air-gapped), and the resulting head(3.10)↔actor(3.11)
    # minor-version split corrupts Ray's cloudpickle/proto payloads.
    if not python:
        base_python = f"{_BASE_VENV}/bin/python"
        python = base_python if os.path.exists(base_python) else None

    venv_dir = f"{venv_base}/.venv-{engine}"
    lock_path = f"{venv_base}/.venv-{engine}.lock"

    if is_venv_ready(venv_dir):
        print(f"✅ {engine} venv already ready at {venv_dir}")
        # Self-heal: an existing venv can drift from the base env (e.g. created
        # before the fastapi pin / after a base-env bump) or miss a build tool
        # added later (e.g. ninja), then fail at deploy. Reconcile in place.
        return _reconcile_engine_venv(engine, venv_dir, lock_path, packages)

    python_flag = f"--python {python}" if python else ""
    print(f"\n🔧 Creating {engine} venv at {venv_dir} (python={python or 'default'}) ...")
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if is_venv_ready(venv_dir):
            print(f"✅ {engine} venv created by another process")
            return _reconcile_engine_venv(
                engine, venv_dir, lock_path, packages, lock_held=True
            )

        if not run_command(f"{_ensure_uv()} venv {python_flag} {venv_dir}".strip()):
            print(f"❌ Failed to create {engine} venv")
            return False

        # Pin Ray to the base env's exact version so the worker actor and the
        # cluster head never run mismatched Ray (see _pin_ray_to_base).
        base_version = venv_ray_version(_BASE_VENV)
        packages = _resolved_engine_packages(list(packages))
        if not _pip_install_bundle_into_venv(venv_dir, packages):
            print(f"❌ Failed to install the required {engine} package bundle")
            return False

        ready = is_venv_ready(venv_dir)
        if not ready:
            print(f"❌ {engine} venv not ready after install")
            return False

        unsatisfied = [
            spec for spec in packages if not _requirement_satisfied(venv_dir, spec)
        ]
        if unsatisfied:
            print(
                f"❌ {engine} venv violates required packages after install: "
                f"{', '.join(unsatisfied)}"
            )
            return False
        if not _venv_dependencies_consistent(venv_dir):
            print(f"❌ {engine} venv has inconsistent transitive dependencies")
            return False

        # Hard gate: the engine venv's Ray MUST match the base env's Ray.
        engine_version = venv_ray_version(venv_dir)
        if base_version and engine_version != base_version:
            print(
                f"❌ {engine} venv Ray {engine_version} != base env Ray "
                f"{base_version}. Ray Serve requires identical versions on the "
                f"worker and head — recreate this venv."
            )
            return False

        print(f"✅ {engine} venv ready (Ray {engine_version})")
        return True
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


# protobuf<7 is REQUIRED until the cluster Ray includes ray-project/ray#64362:
# protobuf 7 removed the deprecated FieldDescriptor.label that Ray Serve's
# _proto_to_dict relies on, so an actor in a venv with protobuf 7.x dies on
# DeploymentConfig deserialization ("'FieldDescriptor' object has no attribute
# 'label'").  Every engine venv deserializes DeploymentConfig, so all need it.
# Exact pins for packages that must match the head env to avoid actor startup
# failures:
#   ray[serve]:  worker and head MUST run the same Ray version
#   protobuf:    <7 required until Ray ships ray-project/ray#64362 (protobuf 7
#                removed FieldDescriptor.label used by Ray Serve's _proto_to_dict)
#   fastapi:     Ray cloudpickles the FastAPI app on the head; mismatched versions
#                cause AttributeError on renamed internal classes (_IncludedRouter)
# NOTE: fastapi here is only a *fallback* pin for fresh engine-venv creation —
# _pin_pkgs_to_base() overrides it with the base env's live fastapi at create
# time, and _reconcile_engine_venv() repins existing venvs to the base env on
# every deploy. The hard ceiling is vLLM 0.26's own requirement
# (fastapi[standard]>=0.133.0,<0.137.0): the engine venv can never exceed 0.136.x
# while vLLM is installed, so the head/base env MUST stay in the same window or
# cloudpickle across the head->replica boundary fails. pyproject pins the base
# env into that window too; 0.136.3 is the current resolved top of it.
_RAY_BASE = [
    "ray[serve]==2.56.1",
    "protobuf>=5.29.6,<7.0",
    "fastapi==0.136.3",
]

# Single source of truth for every engine venv's package set. The setup_*_env.py
# CML jobs import from here rather than redefining their own lists, so a change to
# _RAY_BASE (e.g. a Ray bump) propagates to every engine venv automatically.
_ENGINE_PACKAGES = {
    # vLLM and FlashInfer are an atomic, publisher-declared release pair. Use
    # vLLM 0.29.0 with its matching FlashInfer 0.6.18 instead of combining
    # vLLM 0.28.0's Python-3.11-incompatible 0.6.16.post3 with post4. The
    # deployment factory still defaults to vLLM's native sampler: installing
    # this pair does not claim FlashInfer JIT is validated on Blackwell.
    #
    # nvidia-cuda-{nvcc,runtime,cccl}==13.0.*: Blackwell/sm_120 needs a CUDA >=12.9
    # toolkit for FlashInfer/torch.compile JIT (see docs/BLACKWELL_CUDA_NVCC_NCCL.md).
    # These wheels version independently, so an unpinned install let nvcc and the
    # runtime headers land on different CUDA minors — tripping CCCL's compiler↔header
    # equality guard. The `==13.0.*` prefix pin locks all three to the same major.minor
    # (what the guard checks), aligns with torch's cu130 build, and survives patch yanks.
    # CUDA_HOME still must point at the wheel toolkit root at deploy time (payload env).
    "vllm":    _RAY_BASE + [
        "vllm==0.29.0",
        "flashinfer-python==0.6.18",
        "nvidia-cuda-nvcc==13.0.*",
        "nvidia-cuda-runtime==13.0.*",
        "nvidia-cuda-cccl==13.0.*",
        "ninja",
    ],
    "sglang":  _RAY_BASE + ["sglang>=0.5.7"],
    "yolo":    _RAY_BASE + ["ultralytics>=8.0.0", "Pillow>=9.0.0", "opencv-python-headless>=4.8.0"],
    "mcp":     _RAY_BASE + ["mcp>=1.0.0", "httpx>=0.27.0"],
    # [proxy] pulls in websockets + apscheduler + other proxy-subprocess deps;
    # pyyaml is used by litellm_engine.py to write the proxy config YAML.
    "litellm": _RAY_BASE + ["litellm[proxy]>=1.83.0", "pyyaml>=6.0.3"],
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

    # Ensure uv is available (resolve on PATH / known bin dirs, else install).
    print("\n⬇️  Ensuring uv package manager is available...")
    try:
        uv = _ensure_uv()
    except Exception as e:
        print(f"❌ Failed to make uv available: {e}")
        sys.exit(1)
    run_command(f"{uv} --version")

    # Create virtual environment with uv
    print("\n📝 Creating Python virtual environment...")
    if os.path.exists(venv_dir):
        print("   Removing existing incomplete venv...")
        run_command(f"rm -rf {venv_dir}")

    if not run_command(f"{uv} venv {venv_dir}"):
        print("❌ Failed to create virtual environment")
        sys.exit(1)

    print("✅ Virtual environment created\n")

    # Install the package itself first (includes all dependencies from pyproject.toml)
    print("🚀 Installing ray-serve-cai package and dependencies...")

    # Always target the venv explicitly so packages land in the right place
    # regardless of whether the caller has activated the venv.
    uv_install = f"{uv} pip install --python {venv_dir}/bin/python"

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
            "protobuf>=5.29.6,<7.0",
            "pyyaml>=6.0.3",
            "aiohttp>=3.13.3",
            "fastapi>=0.133.0,<0.137.0",
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
    if __package__:
        from .project_environment import preflight_project_environment
    else:
        from project_environment import preflight_project_environment

    preflight_project_environment()
    main()
