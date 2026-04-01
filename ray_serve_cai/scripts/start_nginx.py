#!/usr/bin/env python3
"""
Start Nginx reverse proxy for the Ray cluster head node.

Renders Jinja2 templates from ray_serve_cai/configs/nginx/ into a runtime
directory, then starts (or reloads) the nginx process.

Runtime layout under NGINX_RUNTIME_DIR (default /home/cdsw/nginx):
    nginx.conf
    conf.d/
        upstreams.conf
        server.conf
    snippets/
        proxy_params.conf
    logs/
        nginx_access.log
        nginx_error.log
    run/
        nginx.pid
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Source template directory (relative to this file: ../../configs/nginx/)
TEMPLATE_DIR = Path(__file__).parent.parent / "configs" / "nginx"

# Default runtime directory — all rendered configs and logs go here
DEFAULT_RUNTIME_DIR = Path("/home/cdsw/nginx")

# Static landing-page root
DEFAULT_STATIC_ROOT = Path("/home/cdsw/ray_serve_cai/static")

# Nginx binary search order
NGINX_CANDIDATES = [
    str(Path.home() / ".local" / "bin" / "nginx"),
    "/usr/sbin/nginx",
    "/usr/bin/nginx",
]


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def find_mime_types() -> str:
    """
    Return the path to nginx's mime.types file.

    System nginx installs it under /etc/nginx/; a from-source build under
    the compiled prefix.  We probe candidates in priority order.
    """
    candidates = [
        "/etc/nginx/mime.types",                          # system nginx
        "/home/cdsw/.local/nginx/conf/mime.types",        # compiled from source
        "/usr/local/nginx/conf/mime.types",               # alt compiled location
        "/usr/share/nginx/mime.types",                    # some distros
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    # Last resort: use the compiled location even if it doesn't exist yet
    # (nginx will report a clear error rather than a cryptic one).
    return "/home/cdsw/.local/nginx/conf/mime.types"


def build_context(runtime_dir: Path, static_root: Path) -> dict:
    """
    Build the Jinja2 template context from environment variables and defaults.

    Every value here is overridable via the corresponding env var so users can
    tune nginx without touching the templates.  See the table below.

    Env var                         Default   Description
    ──────────────────────────────────────────────────────────────────────────
    CDSW_APP_PORT                   8080      External-facing port (nginx listen)
    RAY_SERVE_PORT                  5000      Internal Management API port
    RAY_DASHBOARD_PORT              8265      Internal Ray Dashboard port
    NGINX_WORKER_PROCESSES          auto      nginx worker_processes directive
    NGINX_WORKER_CONNECTIONS        1024      worker_connections per process
    NGINX_KEEPALIVE_TIMEOUT         65        keepalive_timeout (seconds)
    NGINX_CLIENT_MAX_BODY_SIZE      100M      client_max_body_size
    NGINX_GZIP                      on        gzip on|off
    NGINX_TCP_NOPUSH                on        tcp_nopush on|off
    NGINX_TCP_NODELAY               on        tcp_nodelay on|off
    NGINX_API_TIMEOUT               300       proxy read/send timeout for /api/
    NGINX_DASHBOARD_TIMEOUT         86400     proxy read timeout for /dashboard/
    ──────────────────────────────────────────────────────────────────────────
    """
    return {
        # ── External port ──────────────────────────────────────────────────
        "app_port": int(os.environ.get("CDSW_APP_PORT", 8080)),
        # ── Internal Ray service ports ────────────────────────────────────
        "ray_dashboard_port": int(os.environ.get("RAY_DASHBOARD_PORT", 8265)),
        "ray_serve_port": int(os.environ.get("RAY_SERVE_PORT", 5000)),
        # ── Runtime directory paths ────────────────────────────────────────
        "conf_dir": str(runtime_dir),
        "log_dir": str(runtime_dir / "logs"),
        "run_dir": str(runtime_dir / "run"),
        "static_root": str(static_root),
        # ── mime.types: location differs between system and source builds ──
        "mime_types_path": find_mime_types(),
        # ── Worker / process tuning ────────────────────────────────────────
        "worker_processes": os.environ.get("NGINX_WORKER_PROCESSES", "auto"),
        "worker_connections": int(os.environ.get("NGINX_WORKER_CONNECTIONS", 1024)),
        "keepalive_timeout": int(os.environ.get("NGINX_KEEPALIVE_TIMEOUT", 65)),
        # ── TCP performance ────────────────────────────────────────────────
        "tcp_nopush": os.environ.get("NGINX_TCP_NOPUSH", "on"),
        "tcp_nodelay": os.environ.get("NGINX_TCP_NODELAY", "on"),
        # ── Compression ────────────────────────────────────────────────────
        "gzip": os.environ.get("NGINX_GZIP", "on"),
        # ── Body / timeout limits ──────────────────────────────────────────
        "client_max_body_size": os.environ.get("NGINX_CLIENT_MAX_BODY_SIZE", "100M"),
        "api_timeout": int(os.environ.get("NGINX_API_TIMEOUT", 300)),
        "dashboard_timeout": int(os.environ.get("NGINX_DASHBOARD_TIMEOUT", 86400)),
    }


def render_templates(runtime_dir: Path, context: dict) -> None:
    """
    Render every *.j2 template from TEMPLATE_DIR into runtime_dir,
    preserving the subdirectory structure (conf.d/, snippets/).
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
    )

    # Walk all .j2 files under the template directory
    for template_path in sorted(TEMPLATE_DIR.rglob("*.j2")):
        rel = template_path.relative_to(TEMPLATE_DIR)
        # Output file: strip the .j2 suffix
        output_path = runtime_dir / rel.with_suffix("")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        template_name = str(rel)  # e.g. "conf.d/server.conf.j2"
        rendered = env.get_template(template_name).render(**context)
        output_path.write_text(rendered)
        print(f"  rendered: {template_name} → {output_path}")


# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

def create_runtime_dirs(runtime_dir: Path, static_root: Path) -> None:
    """Create all runtime directories nginx needs."""
    for d in [
        runtime_dir / "conf.d",
        runtime_dir / "snippets",
        runtime_dir / "logs",
        runtime_dir / "run",
        static_root,
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # Ensure a minimal landing page exists
    index = static_root / "index.html"
    if not index.exists():
        index.write_text(
            "<!DOCTYPE html><html><head>"
            "<title>Ray Cluster Management</title></head><body>"
            "<h1>Ray Cluster Management</h1><ul>"
            '<li><a href="/docs">API Docs (Swagger)</a></li>'
            '<li><a href="/redoc">API Docs (ReDoc)</a></li>'
            '<li><a href="/api/v1/cluster/status">Cluster Status</a></li>'
            '<li><a href="/dashboard/">Ray Dashboard</a></li>'
            "</ul></body></html>\n"
        )
        print(f"  created default landing page: {index}")


# ---------------------------------------------------------------------------
# Nginx process management
# ---------------------------------------------------------------------------

def find_nginx() -> str:
    """Return the path to the nginx binary, or raise RuntimeError."""
    for candidate in NGINX_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    result = subprocess.run(["which", "nginx"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    raise RuntimeError(
        "nginx binary not found. Run setup_environment.py first.\n"
        f"Searched: {NGINX_CANDIDATES}"
    )


def stop_nginx(nginx_bin: str) -> None:
    """Gracefully stop a running nginx master process."""
    result = subprocess.run(
        ["pgrep", "-f", "nginx: master process"],
        capture_output=True,
    )
    if result.returncode == 0:
        print("  stopping existing nginx master...")
        subprocess.run([nginx_bin, "-s", "stop"], capture_output=True)
        time.sleep(2)


def start_nginx(nginx_bin: str, conf_path: Path) -> None:
    """Start nginx with the rendered configuration."""
    result = subprocess.run(
        [nginx_bin, "-c", str(conf_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"nginx failed to start (exit {result.returncode}):\n{result.stderr}"
        )


def verify_nginx(app_port: int, retries: int = 5, delay: float = 1.0) -> bool:
    """Return True once nginx is accepting connections on app_port."""
    import socket

    for attempt in range(1, retries + 1):
        time.sleep(delay)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            if sock.connect_ex(("127.0.0.1", app_port)) == 0:
                return True
        print(f"  waiting for nginx... ({attempt}/{retries})")
    return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--foreground", action="store_true",
        help="Exec into nginx with 'daemon off;' so this process blocks until nginx exits.",
    )
    args, _ = parser.parse_known_args()

    print("=" * 70)
    print("Starting Nginx Reverse Proxy")
    print("=" * 70)

    runtime_dir = Path(os.environ.get("NGINX_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR)))
    static_root = Path(os.environ.get("NGINX_STATIC_ROOT", str(DEFAULT_STATIC_ROOT)))

    # 1. Locate nginx
    try:
        nginx_bin = find_nginx()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"nginx binary : {nginx_bin}")

    # 2. Build Jinja2 context
    context = build_context(runtime_dir, static_root)
    print(f"app_port        : {context['app_port']}")
    print(f"ray_serve       : 127.0.0.1:{context['ray_serve_port']}")
    print(f"ray_dashboard   : 127.0.0.1:{context['ray_dashboard_port']}")
    print(f"worker_processes: {context['worker_processes']}")
    print(f"mime_types      : {context['mime_types_path']}")
    print(f"runtime_dir     : {runtime_dir}")

    # 3. Create directories
    create_runtime_dirs(runtime_dir, static_root)

    # 4. Render templates
    print("\nRendering templates...")
    render_templates(runtime_dir, context)

    # 5. Stop any existing nginx
    conf_path = runtime_dir / "nginx.conf"
    stop_nginx(nginx_bin)

    if args.foreground:
        # Replace this Python process with nginx running in the foreground.
        # The caller blocks until nginx exits — no while-loop needed.
        print(f"\nStarting nginx in foreground mode (process will block)...")
        print(f"  config : {conf_path}")
        print("=" * 70)
        os.execv(nginx_bin, [nginx_bin, "-c", str(conf_path), "-g", "daemon off;"])
        # os.execv() never returns

    # Daemon mode (default) — start nginx, verify, then return.
    print(f"\nStarting nginx with config: {conf_path}")
    try:
        start_nginx(nginx_bin, conf_path)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    if verify_nginx(context["app_port"]):
        print(f"\nnginx is listening on port {context['app_port']}")
    else:
        print(f"\nWARNING: nginx started but is not responding on port {context['app_port']}")

    print("\nRouting:")
    print(f"  /             → static landing page")
    print(f"  /api/*        → Ray Serve / Management API (:{context['ray_serve_port']})")
    print(f"  /docs         → Swagger UI")
    print(f"  /redoc        → ReDoc")
    print(f"  /dashboard/   → Ray Dashboard (:{context['ray_dashboard_port']})")
    print(f"  /health       → health check")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
