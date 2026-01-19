#!/usr/bin/env python3
"""
Start Nginx reverse proxy for Ray cluster.

This script:
1. Generates Nginx configuration from template
2. Creates necessary directories
3. Starts Nginx process
"""

import os
import sys
import subprocess
from pathlib import Path


def substitute_env_vars(template_content):
    """Substitute environment variables in template."""
    import re

    def replace_var(match):
        var_name = match.group(1)
        return os.environ.get(var_name, f"${{{var_name}}}")

    return re.sub(r'\$\{(\w+)\}', replace_var, template_content)


def generate_nginx_config():
    """Generate Nginx configuration from template."""
    template_path = Path("/home/cdsw/ray_serve_cai/configs/nginx.conf.template")
    output_path = Path("/home/cdsw/nginx.conf")

    print(f"📄 Generating Nginx configuration...")
    print(f"   Template: {template_path}")
    print(f"   Output: {output_path}")

    if not template_path.exists():
        print(f"❌ Template not found: {template_path}")
        return False

    # Read template
    with open(template_path, 'r') as f:
        template_content = f.read()

    # Substitute environment variables
    config_content = substitute_env_vars(template_content)

    # Write config file
    with open(output_path, 'w') as f:
        f.write(config_content)

    print(f"✅ Nginx configuration generated")
    return True


def create_directories():
    """Create necessary directories for Nginx."""
    dirs = [
        "/home/cdsw/logs",
        "/home/cdsw/ray_serve_cai/static",
    ]

    for dir_path in dirs:
        Path(dir_path).mkdir(parents=True, exist_ok=True)
        print(f"✅ Created directory: {dir_path}")

    # Ensure static HTML file exists
    static_html = Path("/home/cdsw/ray_serve_cai/static/index.html")
    if not static_html.exists():
        print(f"⚠️  Warning: Landing page not found at {static_html}")
        print(f"   Creating a basic landing page...")

        # Create a basic fallback landing page
        basic_html = """<!DOCTYPE html>
<html>
<head>
    <title>Ray Cluster Management</title>
</head>
<body>
    <h1>Ray Cluster Management</h1>
    <ul>
        <li><a href="/docs">API Documentation (Swagger UI)</a></li>
        <li><a href="/redoc">API Documentation (ReDoc)</a></li>
        <li><a href="/api/v1/cluster/status">Cluster Status</a></li>
        <li><a href="/dashboard/">Ray Dashboard</a></li>
    </ul>
</body>
</html>"""
        with open(static_html, 'w') as f:
            f.write(basic_html)
        print(f"✅ Created basic landing page")


def start_nginx():
    """Start Nginx server."""
    config_path = "/home/cdsw/nginx.conf"

    # Find nginx binary
    nginx_paths = [
        "/home/cdsw/.local/bin/nginx",
        "/usr/sbin/nginx",
        "/usr/bin/nginx",
    ]

    nginx_bin = None
    for path in nginx_paths:
        if os.path.exists(path):
            nginx_bin = path
            break

    if not nginx_bin:
        # Try to find via which
        result = subprocess.run("which nginx", shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            nginx_bin = result.stdout.strip()

    if not nginx_bin:
        print("❌ Nginx binary not found. Please run setup_environment.py first.")
        return False

    print(f"\n🚀 Starting Nginx...")
    print(f"   Binary: {nginx_bin}")
    print(f"   Config: {config_path}")
    print(f"   Port: {os.environ.get('CDSW_APP_PORT', '8080')}")

    # Check if Nginx is already running
    result = subprocess.run(
        "pgrep -f 'nginx: master process'",
        shell=True,
        capture_output=True
    )

    if result.returncode == 0:
        print("⚠️  Nginx is already running, stopping it first...")
        subprocess.run(f"{nginx_bin} -s stop", shell=True, capture_output=True)
        import time
        time.sleep(2)

    # Start Nginx
    cmd = f"{nginx_bin} -c {config_path}"
    result = subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        print("✅ Nginx started successfully")
        return True
    else:
        print(f"❌ Failed to start Nginx")
        if result.stderr:
            print(f"Error: {result.stderr}")
        return False


def test_nginx():
    """Test if Nginx is responding."""
    import time
    import socket

    port = int(os.environ.get('CDSW_APP_PORT', 8080))

    print(f"\n🧪 Testing Nginx on port {port}...")
    time.sleep(2)  # Give Nginx time to start

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()

    if result == 0:
        print(f"✅ Nginx is listening on port {port}")
        return True
    else:
        print(f"❌ Nginx is not responding on port {port}")
        return False


def main():
    """Main function."""
    print("=" * 70)
    print("🌐 Starting Nginx Reverse Proxy")
    print("=" * 70)

    # Create directories
    create_directories()

    # Generate configuration
    if not generate_nginx_config():
        sys.exit(1)

    # Start Nginx
    if not start_nginx():
        sys.exit(1)

    # Test Nginx
    if not test_nginx():
        print("⚠️  Warning: Nginx started but not responding")

    print("\n" + "=" * 70)
    print("✅ Nginx proxy started successfully")
    print("=" * 70)
    print(f"\nProxy listening on port: {os.environ.get('CDSW_APP_PORT', '8080')}")
    print("\nRouting:")
    print("  / → Landing page")
    print("  /dashboard/ → Ray Dashboard (port 8265)")
    print("  /api/v1/* → Management API (port 8000)")
    print("  /docs → Management API Swagger UI")
    print("  /redoc → Management API ReDoc")


if __name__ == "__main__":
    main()
