#!/usr/bin/env python3
"""
Launch Ray cluster using CAI (CML) applications.

This script:
1. Loads Ray cluster configuration
2. Creates head node application
3. Creates worker node applications
4. Monitors cluster startup
5. Outputs connection information

Run this as a CML job after setup_environment.py completes.
IMPORTANT: This script must run within the virtual environment created by setup_environment.py.
If running as a CML job, ensure the job is configured to use: /home/cdsw/.venv/bin/python
"""

import json
import os
import sys
import yaml
from pathlib import Path

# Add parent directory to path for imports
# Handle both regular Python and IPython/Jupyter environments
try:
    script_dir = Path(__file__).parent
    script_file = __file__
except NameError:
    # __file__ not available in IPython/Jupyter - use current working directory
    script_dir = Path.cwd() / "cai_integration"
    script_file = None

# CRITICAL: Ensure virtual environment is used (only if running as script)
if script_file:
    venv_python = Path("/home/cdsw/.venv/bin/python")
    current_python = Path(sys.executable)

    if venv_python.exists() and not str(current_python).startswith("/home/cdsw/.venv"):
        print("=" * 70)
        print("🔄 Re-executing with virtual environment...")
        print("=" * 70)
        import subprocess
        # Re-execute this script with the venv Python
        result = subprocess.run([str(venv_python), script_file] + sys.argv[1:])
        sys.exit(result.returncode)

sys.path.insert(0, str(script_dir.parent))

from ray_serve_cai.cai_cluster import CAIClusterManager


def create_ray_launcher_scripts(head_address: str = None) -> tuple:
    """
    Create Ray launcher scripts in project directory.

    These scripts will be used as entry points for CAI applications.

    Args:
        head_address: Address of head node for worker scripts

    Returns:
        Tuple of (head_script_path, worker_script_path)
    """
    project_dir = Path("/home/cdsw")

    # Create head launcher script
    head_script_path = project_dir / "ray_head_launcher.py"
    head_script_content = '''#!/usr/bin/env python3
"""Ray Head Node Launcher for CAI with Nginx Proxy"""
import subprocess
import sys
import os
import time
from pathlib import Path

# Activate venv
venv_python = Path("/home/cdsw/.venv/bin/python")
if not venv_python.exists():
    print("❌ Virtual environment not found at /home/cdsw/.venv")
    sys.exit(1)

print("=" * 70)
print("🚀 Starting Ray Head Node with Nginx Proxy")
print("=" * 70)

# Step 1: Start Ray head on internal dashboard port (8265)
ray_cmd = [
    str(venv_python), "-m", "ray.scripts.ray_start",
    "--head",
    "--port", "6379",
    "--dashboard-host", "127.0.0.1",  # Internal only
    "--dashboard-port", "8265",       # Internal dashboard port
    "--include-dashboard", "true"
]

print("\\n📊 Starting Ray Dashboard on internal port 8265...")
print(f"Command: {' '.join(ray_cmd)}")

try:
    result = subprocess.run(ray_cmd, check=True)
    if result.returncode != 0:
        print(f"❌ Ray head failed with exit code {result.returncode}")
        sys.exit(result.returncode)
except Exception as e:
    print(f"❌ Failed to start Ray head: {e}")
    sys.exit(1)

print("✅ Ray head started")

# Step 2: Start Nginx proxy (will be started by management API deployment)
# The Nginx setup is handled separately by the management API deployment script

print("\\n" + "=" * 70)
print("✅ Ray Head Node Started Successfully")
print("=" * 70)
print("\\nServices:")
print("  • Ray Dashboard: 127.0.0.1:8265 (internal)")
print("  • Ray GCS: 0.0.0.0:6379")
print("\\nNote: Nginx proxy will be started by management API deployment")
'''

    with open(head_script_path, 'w') as f:
        f.write(head_script_content)
    os.chmod(head_script_path, 0o755)

    # Create worker launcher script
    worker_script_path = project_dir / "ray_worker_launcher.py"

    if head_address:
        worker_script_content = f'''#!/usr/bin/env python3
"""Ray Worker Node Launcher for CAI"""
import subprocess
import sys
import os
from pathlib import Path

# Activate venv
venv_python = Path("/home/cdsw/.venv/bin/python")
if not venv_python.exists():
    print("❌ Virtual environment not found at /home/cdsw/.venv")
    sys.exit(1)

# Ray start command for worker node
cmd = [
    str(venv_python), "-m", "ray.scripts.ray_start",
    "--address", "{head_address}"
]

print("🚀 Starting Ray worker node...")
print(f"Connecting to: {head_address}")
print(f"Command: {{' '.join(cmd)}}")

try:
    # Start Ray worker
    result = subprocess.run(cmd, check=True)
    sys.exit(result.returncode)
except Exception as e:
    print(f"❌ Failed to start Ray worker: {{e}}")
    sys.exit(1)
'''
    else:
        worker_script_content = '''#!/usr/bin/env python3
"""Ray Worker Node Launcher for CAI"""
import subprocess
import sys
import os
import time
from pathlib import Path

# Activate venv
venv_python = Path("/home/cdsw/.venv/bin/python")
if not venv_python.exists():
    print("❌ Virtual environment not found at /home/cdsw/.venv")
    sys.exit(1)

# Get head address from environment
head_address = os.environ.get("RAY_HEAD_ADDRESS")
if not head_address:
    print("❌ RAY_HEAD_ADDRESS environment variable not set")
    sys.exit(1)

print(f"Waiting for head node at {head_address}...")
time.sleep(10)  # Give head node time to start

# Ray start command for worker node
cmd = [
    str(venv_python), "-m", "ray.scripts.ray_start",
    "--address", head_address
]

print("🚀 Starting Ray worker node...")
print(f"Connecting to: {head_address}")
print(f"Command: {' '.join(cmd)}")

try:
    # Start Ray worker
    result = subprocess.run(cmd, check=True)
    sys.exit(result.returncode)
except Exception as e:
    print(f"❌ Failed to start Ray worker: {e}")
    sys.exit(1)
'''

    with open(worker_script_path, 'w') as f:
        f.write(worker_script_content)
    os.chmod(worker_script_path, 0o755)

    print(f"✅ Created head launcher: {head_script_path}")
    print(f"✅ Created worker launcher: {worker_script_path}")

    return str(head_script_path), str(worker_script_path)


def load_config():
    """Load Ray cluster configuration from environment or config file."""
    config = {
        'num_workers': int(os.environ.get('RAY_NUM_WORKERS', 1)),
        'head_cpu': int(os.environ.get('RAY_HEAD_CPU', 8)),
        'head_memory': int(os.environ.get('RAY_HEAD_MEMORY', 32)),
        'worker_cpu': int(os.environ.get('RAY_WORKER_CPU', 32)),
        'worker_memory': int(os.environ.get('RAY_WORKER_MEMORY', 32)),
        'worker_gpus': int(os.environ.get('RAY_WORKER_GPUS', 4)),
        'ray_port': int(os.environ.get('RAY_PORT', 6379)),
        'dashboard_port': int(os.environ.get('RAY_DASHBOARD_PORT', 8265)),
    }

    # Try to load from config file
    config_path = Path(__file__).parent.parent / "ray_cluster_config.yaml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_config = yaml.safe_load(f) or {}
            config.update(file_config.get('ray_cluster', {}))
            print(f"✅ Loaded configuration from {config_path}")
        except Exception as e:
            print(f"⚠️  Could not load config file: {e}")

    return config


def deploy_management_app_to_ray(
    cluster_info: dict,
    ray_config: dict,
    project_id: str
) -> dict:
    """
    Deploy the management API as a Ray Serve application on the Ray cluster.

    Args:
        cluster_info: Cluster information dictionary
        ray_config: Ray cluster configuration
        project_id: CML project ID

    Returns:
        Management app deployment information
    """
    try:
        import ray
        from ray import serve
        import subprocess

        # Connect to Ray cluster
        ray_address = f"ray://{cluster_info['head_address']}"
        print(f"   Connecting to Ray cluster at {ray_address}")

        ray.init(address=ray_address, ignore_reinit_error=True)
        print(f"   ✅ Connected to Ray cluster")

        # Start Ray Serve on internal port 8000
        print(f"   Starting Ray Serve on internal port 8000...")
        serve.start(detached=True, http_options={
            "host": "127.0.0.1",  # Internal only
            "port": 8000           # Ray Serve default port
        })
        print(f"   ✅ Ray Serve started on port 8000 (internal)")

        # Import the FastAPI app
        sys.path.insert(0, "/home/cdsw")

        # Set environment variables for the deployment
        os.environ["RAY_ADDRESS"] = "auto"  # Running inside Ray cluster
        os.environ["CML_PROJECT_ID"] = project_id

        from ray_serve_cai.management.app import app

        # Create Ray Serve deployment with /api route prefix
        print(f"   Deploying management API to Ray Serve with route_prefix=/api...")

        @serve.deployment(
            name="management-api",
            route_prefix="/api",  # Management API at /api/*
            num_replicas=1,
            ray_actor_options={
                "num_cpus": 4,
                "memory": 8 * 1024 * 1024 * 1024  # 8GB
            }
        )
        @serve.ingress(app)
        class ManagementAPI:
            pass

        # Deploy the application
        deployment = ManagementAPI.bind()
        serve.run(deployment, name="management-api", route_prefix="/api")

        print(f"   ✅ Management API deployed to Ray Serve at /api/*")

        # Start Nginx proxy on public port (CDSW_APP_PORT)
        print(f"\n   Starting Nginx reverse proxy...")
        nginx_script = Path("/home/cdsw/ray_serve_cai/scripts/start_nginx.py")

        if nginx_script.exists():
            result = subprocess.run(
                ["/home/cdsw/.venv/bin/python", str(nginx_script)],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode == 0:
                print(f"   ✅ Nginx proxy started successfully")
                print(result.stdout)
            else:
                print(f"   ⚠️  Warning: Nginx startup reported issues")
                if result.stderr:
                    print(f"   Error: {result.stderr}")
        else:
            print(f"   ⚠️  Warning: Nginx startup script not found at {nginx_script}")

        # Construct management API URL (through Nginx on public port)
        head_host = cluster_info['head_address'].split(':')[0]
        public_port = int(os.environ.get('CDSW_APP_PORT', 8080))
        management_url = f"http://{head_host}:{public_port}"

        return {
            'url': management_url,
            'deployed_via': 'ray_serve_with_nginx',
            'port': public_port,
            'internal_ray_serve_port': 8000,
            'internal_dashboard_port': 8265
        }

    except Exception as e:
        print(f"   ⚠️  Failed to deploy management API to Ray: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Main launch function."""
    print("=" * 70)
    print("🚀 Launching Ray Cluster on CML")
    print("=" * 70)

    # Get CML configuration
    cml_host = os.environ.get("CML_HOST")
    cml_api_key = os.environ.get("CML_API_KEY")
    project_id = os.environ.get("CDSW_PROJECT_ID") or os.environ.get("CML_PROJECT_ID")
    runtime_identifier = os.environ.get(
        "RUNTIME_IDENTIFIER",
        "docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-standard:2025.09.1-b5"
    )

    print("\n📋 Configuration:")
    print(f"   CML Host: {cml_host}")
    print(f"   Project ID: {project_id}")
    print(f"   Runtime: {runtime_identifier[:80]}...")

    if not all([cml_host, cml_api_key, project_id]):
        print("\n❌ Missing required environment variables:")
        print("   Required: CML_HOST, CML_API_KEY, CML_PROJECT_ID (or CDSW_PROJECT_ID)")
        return 1

    # Load Ray cluster configuration
    ray_config = load_config()

    print("\n🎯 Ray Cluster Configuration:")
    print(f"   Head Node: {ray_config['head_cpu']}CPU, {ray_config['head_memory']}GB RAM, 0GPU")
    print(f"   Workers: {ray_config['num_workers']} nodes")
    print(f"   Worker Resources: {ray_config['worker_cpu']}CPU, {ray_config['worker_memory']}GB RAM, {ray_config['worker_gpus']}GPU each")
    print(f"   Ray Port: {ray_config['ray_port']}")
    print(f"   Dashboard Port: {ray_config['dashboard_port']}")

    try:
        # Create launcher scripts
        print("\n📝 Creating Ray launcher scripts...")
        head_script_path, worker_script_path = create_ray_launcher_scripts()

        # Initialize CAI cluster manager
        print("\n🔧 Initializing CAI cluster manager...")
        manager = CAIClusterManager(
            cml_host=cml_host,
            cml_api_key=cml_api_key,
            project_id=project_id,
            verbose=True
        )
        print("✅ Manager initialized")

        # Start Ray cluster with script paths
        print("\n🚀 Starting Ray cluster...")
        print("   (This may take 2-5 minutes)")
        print(f"   Head script: {head_script_path}")
        print(f"   Worker script: {worker_script_path}")

        cluster_info = manager.start_cluster(
            num_workers=ray_config['num_workers'],
            cpu=ray_config['worker_cpu'],
            memory=ray_config['worker_memory'],
            num_gpus=ray_config['worker_gpus'],
            head_cpu=ray_config['head_cpu'],
            head_memory=ray_config['head_memory'],
            ray_port=ray_config['ray_port'],
            dashboard_port=ray_config['dashboard_port'],
            runtime_identifier=runtime_identifier,
            head_script_path=head_script_path,
            worker_script_path=worker_script_path,
            wait_ready=True,
            timeout=600
        )

        # Deploy management API as Ray Serve application
        print("\n🎮 Deploying Management API to Ray Serve...")
        management_app_info = deploy_management_app_to_ray(
            cluster_info=cluster_info,
            ray_config=ray_config,
            project_id=project_id
        )

        # Add management API info to cluster info
        if management_app_info:
            cluster_info['management_api_url'] = management_app_info.get('url')
            cluster_info['management_api_port'] = management_app_info.get('port')
            print(f"✅ Management API: {management_app_info.get('url')}")

        # Save cluster info to file for reference
        info_file = Path("/home/cdsw/ray_cluster_info.json")
        with open(info_file, 'w') as f:
            json.dump(cluster_info, f, indent=2)
        print(f"\n💾 Cluster info saved to {info_file}")

        # Print cluster information
        print("\n" + "=" * 70)
        print("✅ Ray Cluster Started Successfully!")
        print("=" * 70)
        print(f"\n📊 Cluster Information:")
        print(f"   Head Node ID: {cluster_info['head_app_id']}")
        print(f"   Head Address: {cluster_info['head_address']}")
        print(f"   Dashboard: http://{cluster_info['head_address'].split(':')[0]}:{ray_config['dashboard_port']}")
        print(f"   Workers: {cluster_info['num_workers']} nodes")
        if cluster_info['worker_app_ids']:
            print(f"   Worker IDs:")
            for i, worker_id in enumerate(cluster_info['worker_app_ids'], 1):
                print(f"      {i}. {worker_id}")

        print(f"\n🔗 Connection Details:")
        print(f"   Ray Address: ray://{cluster_info['head_address']}")
        print(f"   Python API: ray.init(address='ray://{cluster_info['head_address']}')")
        if cluster_info.get('management_api_url'):
            print(f"   Management API: {cluster_info['management_api_url']}")
            print(f"   API Docs: {cluster_info['management_api_url']}/docs")

        print(f"\n📝 Usage:")
        print(f"   To connect from another application:")
        print(f"   ```python")
        print(f"   import ray")
        print(f"   ray.init(address='ray://{cluster_info['head_address']}')")
        print(f"   ```")

        if cluster_info.get('management_api_url'):
            print(f"\n🎮 Management API Endpoints:")
            print(f"   • Interactive Docs: {cluster_info['management_api_url']}/docs")
            print(f"   • Cluster Status: GET {cluster_info['management_api_url']}/api/v1/cluster/status")
            print(f"   • List Nodes: GET {cluster_info['management_api_url']}/api/v1/resources/nodes")
            print(f"   • Add Worker: POST {cluster_info['management_api_url']}/api/v1/resources/nodes/add")
            print(f"   • List Apps: GET {cluster_info['management_api_url']}/api/v1/applications")

        print("\n" + "=" * 70)
        print("Cluster info saved to /home/cdsw/ray_cluster_info.json")
        print("=" * 70)

        return 0

    except RuntimeError as e:
        print(f"\n❌ Configuration Error: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Cluster launch failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
