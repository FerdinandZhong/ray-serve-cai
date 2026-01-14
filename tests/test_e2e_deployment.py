#!/usr/bin/env python3
"""
End-to-end test for CML deployment: Project creation → Ray cluster deployment.

This comprehensive test verifies the complete workflow:
1. Project creation with git cloning
2. Ray cluster deployment on CAI applications
3. Cluster status verification
4. Cleanup

Usage:
    # Set environment variables
    export CML_HOST="https://ml.example.cloudera.site"
    export CML_API_KEY="your-api-key"
    export GITHUB_REPOSITORY="owner/ray-serve-cai"  # Optional

    # Run the end-to-end test
    python tests/test_e2e_deployment.py

    # Quick test (no wait time)
    python tests/test_e2e_deployment.py --no-wait

    # With 2 workers and GPUs
    python tests/test_e2e_deployment.py --workers 2 --gpu 1

    # Leave cluster running for inspection
    python tests/test_e2e_deployment.py --no-cleanup
"""

import json
import os
import sys
import argparse
import time
import requests
from pathlib import Path
from typing import Dict, Any, Optional

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ray_serve_cai.cai_cluster import CAIClusterManager


class E2EDeploymentTester:
    """End-to-end deployment test: project creation → cluster deployment."""

    def __init__(self, args):
        """Initialize tester with environment variables."""
        self.cml_host = os.environ.get("CML_HOST")
        self.cml_api_key = os.environ.get("CML_API_KEY")
        self.github_repo = os.environ.get("GITHUB_REPOSITORY")
        self.gh_pat = os.environ.get("GH_PAT") or os.environ.get("GITHUB_TOKEN")
        self.project_id = None
        self.project_name = "ray-serve-cluster"
        self.args = args
        self.verbose = args.verbose if hasattr(args, 'verbose') else False

        print("=" * 70)
        print("🚀 End-to-End CML Deployment Test")
        print("=" * 70)
        print(f"\n📋 Configuration:")
        print(f"   CML Host: {self.cml_host}")
        print(f"   API Key: {'***' + self.cml_api_key[-4:] if self.cml_api_key else 'NOT SET'}")
        if self.github_repo:
            print(f"   GitHub Repo: {self.github_repo}")

        if not all([self.cml_host, self.cml_api_key]):
            print("\n❌ Error: Missing required environment variables")
            print("   Required: CML_HOST, CML_API_KEY")
            print("   Optional: GITHUB_REPOSITORY, GH_PAT")
            raise SystemExit(1)

        self.api_url = f"{self.cml_host.rstrip('/')}/api/v2"
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self.cml_api_key.strip()}",
        }

        print("\n✅ Configuration validated\n")

    # =========================================================================
    # PHASE 1: PROJECT CREATION & SETUP
    # =========================================================================

    def make_request(
        self, method: str, endpoint: str, data: Dict = None, params: Dict = None
    ) -> Optional[Dict]:
        """Make API request to CML."""
        url = f"{self.api_url}/{endpoint.lstrip('/')}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                json=data,
                params=params,
                timeout=30,
            )

            if 200 <= response.status_code < 300:
                if response.text:
                    return response.json()
                return {}
            else:
                print(f"❌ API Error ({response.status_code}): {response.text[:200]}")
                return None

        except Exception as e:
            print(f"❌ Request error: {e}")
            return None

    def test_cml_connection(self) -> bool:
        """Test basic CML connectivity."""
        print("=" * 70)
        print("PHASE 1: PROJECT SETUP")
        print("=" * 70)
        print("\n1️⃣  Testing CML Connection")
        print("-" * 70)

        result = self.make_request("GET", "projects?page_size=1")

        if result is not None:
            print("✅ CML connection successful\n")
            return True
        else:
            print("❌ CML connection failed\n")
            return False

    def search_or_create_project(self) -> Optional[str]:
        """Search for existing project or create new one."""
        print("2️⃣  Project Setup")
        print("-" * 70)

        # Search for existing project
        search_filter = f'{{"name":"{self.project_name}"}}'
        result = self.make_request(
            "GET", "projects", params={"search_filter": search_filter, "page_size": 50}
        )

        if result:
            projects = result.get("projects", [])
            if projects:
                project_id = projects[0].get("id")
                print(f"✅ Found existing project: {project_id}")
                print(f"   Name: {projects[0].get('name')}\n")
                self.project_id = project_id
                return project_id

        # Create new project if repository is provided
        if not self.github_repo:
            print("⚠️  No existing project found")
            print("   Tip: Set GITHUB_REPOSITORY to create new project")
            print("   export GITHUB_REPOSITORY='owner/ray-serve-cai'\n")
            return None

        print(f"Creating new project with git repository...")
        print(f"   Repository: {self.github_repo}")
        git_url = f"https://github.com/{self.github_repo}"

        if self.gh_pat and "github.com" in git_url:
            print("   Auth: GitHub token available (GH_PAT set)")
        else:
            print("   Auth: Using public git URL (no token)")
            if "private" in self.github_repo.lower():
                print("   ⚠️  WARNING: Private repo without token may fail!")

        project_data = {
            "name": self.project_name,
            "description": "Ray cluster deployment project",
            "template": "git",
            "project_visibility": "private",
        }

        # Add git configuration (without token in URL for security)
        if git_url:
            project_data["git_url"] = git_url

        print("\n📤 Sending project creation request...")
        if self.verbose:
            print(f"   Payload: {json.dumps(project_data, indent=2)}")

        result = self.make_request("POST", "projects", data=project_data)

        if result:
            print(f"✅ API Response received:")
            if self.verbose:
                print(f"   Full Response: {json.dumps(result, indent=2)}")

            project_id = result.get("id")
            if project_id:
                print(f"✅ Project created: {project_id}")
                print(f"   Project will clone: {self.github_repo}")
                print()
                self.project_id = project_id
                return project_id
            else:
                print(f"⚠️  Response received but no project ID")
                print(f"   Response: {result}")
                print()
                return None

        print("❌ Failed to create project")
        print("   API returned None or error")
        print("   Possible causes:")
        print("   - Invalid repository URL")
        print("   - Repository does not exist")
        print("   - Authentication failed (if private repo)")
        print("   - CML API error or network issue")
        print("   - API key may not have project creation permissions")
        print()
        return None

    def wait_for_git_clone(self, timeout: int = 900) -> bool:
        """Wait for git clone to complete."""
        if not self.project_id:
            return False

        print("3️⃣  Waiting for Git Clone")
        print("-" * 70)

        start_time = time.time()
        last_status = None
        poll_count = 0

        while time.time() - start_time < timeout:
            result = self.make_request("GET", f"projects/{self.project_id}")
            poll_count += 1

            if result:
                status = result.get("status", "unknown").lower()

                # Only print on status change
                if status != last_status:
                    elapsed = int(time.time() - start_time)
                    print(f"   [{elapsed}s] Status: {status}")
                    last_status = status

                if status in ["success", "ready"]:
                    print(f"✅ Git clone complete")
                    print(f"   Waiting 30 seconds for files to be written...\n")
                    time.sleep(30)
                    return True
                elif status in ["error", "failed"]:
                    print(f"\n❌ Git clone failed: {status}")
                    # Print detailed error information
                    error_msg = result.get("error_message", "No error message provided")
                    print(f"   Error Message: {error_msg}")
                    # Print full response for debugging
                    if self.verbose:
                        print(f"   Full Response: {result}")
                    # Check for specific common issues
                    if "not found" in error_msg.lower():
                        print(f"   → Repository may not exist or be inaccessible")
                        print(f"   → Check: {self.github_repo}")
                    elif "authentication" in error_msg.lower() or "token" in error_msg.lower():
                        print(f"   → Authentication failed - check GH_PAT/GITHUB_TOKEN")
                    elif "permission" in error_msg.lower() or "access denied" in error_msg.lower():
                        print(f"   → Access denied - check token permissions")
                    print()
                    return False

            time.sleep(10)

        elapsed = int(time.time() - start_time)
        print(f"❌ Timeout waiting for git clone ({elapsed}s / {timeout}s)")
        print(f"   Polled {poll_count} times\n")
        return False

    # =========================================================================
    # PHASE 2: RAY CLUSTER DEPLOYMENT
    # =========================================================================

    def _create_launcher_scripts(self):
        """Create launcher scripts for Ray head and worker nodes."""
        project_dir = Path(__file__).parent

        # Create launcher scripts
        head_script_path = project_dir / "ray_head_launcher.py"
        worker_script_path = project_dir / "ray_worker_launcher.py"

        # Head launcher script content
        head_script_content = '''#!/usr/bin/env python3
"""Ray Head Node Launcher for CAI (E2E Test)"""
import subprocess
import sys
import os
from pathlib import Path

# Check for venv
venv_python = Path("/home/cdsw/.venv/bin/python")
if not venv_python.exists():
    print("❌ Virtual environment not found at /home/cdsw/.venv")
    print("   Please run setup_environment.py first")
    sys.exit(1)

print("🚀 Starting Ray head node...")
print(f"   Using Python: {venv_python}")

# Ray start command for head node
cmd = [
    str(venv_python), "-m", "ray.scripts.ray_start",
    "--head",
    "--port", "6379",
    "--dashboard-host", "0.0.0.0",
    "--dashboard-port", "8265",
    "--include-dashboard", "true"
]

try:
    result = subprocess.run(cmd, check=True)
    sys.exit(result.returncode)
except Exception as e:
    print(f"❌ Error starting Ray head: {e}")
    sys.exit(1)
'''

        # Worker launcher script content
        worker_script_content = '''#!/usr/bin/env python3
"""Ray Worker Node Launcher for CAI (E2E Test)"""
import subprocess
import sys
import time
import os
from pathlib import Path

# Check for venv
venv_python = Path("/home/cdsw/.venv/bin/python")
if not venv_python.exists():
    print("❌ Virtual environment not found at /home/cdsw/.venv")
    print("   Please run setup_environment.py first")
    sys.exit(1)

# Get head address from environment or config
head_address = os.environ.get("RAY_HEAD_ADDRESS", "localhost:6379")

print("🚀 Starting Ray worker node...")
print(f"   Using Python: {venv_python}")
print(f"   Head Address: {head_address}")

# Wait for head node to stabilize
time.sleep(10)

# Ray start command for worker node
cmd = [
    str(venv_python), "-m", "ray.scripts.ray_start",
    "--address", head_address
]

try:
    result = subprocess.run(cmd, check=True)
    sys.exit(result.returncode)
except Exception as e:
    print(f"❌ Error starting Ray worker: {e}")
    sys.exit(1)
'''

        # Write scripts
        head_script_path.write_text(head_script_content)
        worker_script_path.write_text(worker_script_content)

        # Make scripts executable
        os.chmod(head_script_path, 0o755)
        os.chmod(worker_script_path, 0o755)

        print(f"📄 Created launcher scripts:")
        print(f"   Head: {head_script_path}")
        print(f"   Worker: {worker_script_path}\n")

        return str(head_script_path), str(worker_script_path)

    def test_cluster_creation(self) -> bool:
        """Test creating a Ray cluster on CAI."""
        print("\n" + "=" * 70)
        print("PHASE 2: RAY CLUSTER DEPLOYMENT")
        print("=" * 70)
        print("\n1️⃣  Initializing Cluster Manager")
        print("-" * 70)

        if not self.project_id:
            print("❌ No project available for cluster deployment")
            return False

        try:
            # Initialize CAI cluster manager
            print("🔧 Initializing CAI cluster manager...")
            manager = CAIClusterManager(
                cml_host=self.cml_host,
                cml_api_key=self.cml_api_key,
                project_id=self.project_id,
                verbose=True
            )
            print("✅ Manager initialized\n")

            # Create launcher scripts
            print("2️⃣  Creating Launcher Scripts")
            print("-" * 70)
            head_script_path, worker_script_path = self._create_launcher_scripts()

            # Start cluster
            print("3️⃣  Starting Ray Cluster")
            print("-" * 70)
            print(f"\n📝 Cluster Configuration:")
            print(f"   Workers: {self.args.workers}")
            print(f"   Head Resources: {self.args.head_cpu}CPU, {self.args.head_memory}GB, 0GPU")
            print(f"   Worker Resources: {self.args.cpu}CPU, {self.args.memory}GB, {self.args.gpu}GPU")
            print(f"\n🚀 Starting cluster (this may take 2-5 minutes)...\n")

            cluster_info = manager.start_cluster(
                num_workers=self.args.workers,
                cpu=self.args.cpu,
                memory=self.args.memory,
                num_gpus=self.args.gpu,
                head_cpu=self.args.head_cpu,
                head_memory=self.args.head_memory,
                runtime_identifier="docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-standard:2025.09.1-b5",
                head_script_path=head_script_path,
                worker_script_path=worker_script_path,
                wait_ready=True,
                timeout=300
            )

            print("\n✅ Cluster created successfully!")
            print(f"\n📊 Cluster Information:")
            print(f"   Head App ID: {cluster_info['head_app_id']}")
            print(f"   Head Address: {cluster_info['head_address']}")
            print(f"   Worker Count: {cluster_info['num_workers']}")
            if cluster_info['worker_app_ids']:
                print(f"   Worker App IDs:")
                for i, worker_id in enumerate(cluster_info['worker_app_ids'], 1):
                    print(f"     {i}. {worker_id}")

            print()
            self.manager = manager
            self.cluster_info = cluster_info
            return True

        except Exception as e:
            print(f"\n❌ Cluster creation failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_cluster_status(self) -> bool:
        """Test getting cluster status."""
        print("4️⃣  Verifying Cluster Status")
        print("-" * 70)

        try:
            status = self.manager.get_status()
            print(f"\n📊 Cluster Status:")
            print(f"   Running: {status['running']}")
            if status['running']:
                print(f"   Head Node:")
                print(f"     ID: {status['head']['id']}")
                print(f"     Status: {status['head']['status']}")
                print(f"     Address: {status['head']['address']}")
                print(f"   Worker Nodes: {len(status['workers'])}")
                for i, worker in enumerate(status['workers'], 1):
                    print(f"     {i}. {worker['status']} (ID: {worker['id']})")
                print(f"   Total Nodes: {status['total_nodes']}\n")
            return True
        except Exception as e:
            print(f"\n❌ Status check failed: {e}\n")
            return False

    def test_cluster_cleanup(self) -> bool:
        """Test cluster cleanup/deletion."""
        print("5️⃣  Cluster Cleanup")
        print("-" * 70)

        try:
            print("\n🧹 Stopping and cleaning up cluster...")
            result = self.manager.stop_cluster()

            if result['stopped']:
                print("✅ Cluster cleaned up successfully")
                if result.get('errors'):
                    print(f"   ⚠️  Some warnings:")
                    for error in result['errors']:
                        print(f"      - {error}")
                print()
                return True
            else:
                print("❌ Cleanup completed with errors\n")
                return False

        except Exception as e:
            print(f"❌ Cleanup failed: {e}\n")
            return False

    # =========================================================================
    # MAIN TEST FLOW
    # =========================================================================

    def run_all_tests(self) -> bool:
        """Run complete end-to-end test flow."""
        print("\n🚀 Starting end-to-end deployment test...\n")

        # Phase 1: Project Setup
        if not self.test_cml_connection():
            print("❌ Cannot proceed without CML connection!")
            return False

        project_id = self.search_or_create_project()

        if not project_id:
            print("⚠️  Skipping cluster deployment (no project)")
            return False

        if self.github_repo and not self.wait_for_git_clone():
            print("❌ Cannot proceed without git clone!")
            return False

        # Phase 2: Ray Cluster Deployment
        if not self.test_cluster_creation():
            print("❌ Cannot proceed without cluster!")
            return False

        # Verify cluster
        self.test_cluster_status()

        # Wait if specified
        if not self.args.no_wait:
            print(f"⏳ Cluster is running. Waiting {self.args.wait_time}s before cleanup...")
            print("   (You can check the CML UI to see the applications)\n")
            time.sleep(self.args.wait_time)

        # Cleanup (if not skipped)
        if not self.args.no_cleanup:
            success = self.test_cluster_cleanup()
            if not success:
                print("⚠️  Cleanup had issues. Check CML UI to verify.")
        else:
            print("⚠️  Skipping cleanup (--no-cleanup flag set)")
            print("   Remember to manually delete the applications from CML UI:")
            print(f"     - Head: {self.cluster_info['head_app_id']}")
            for worker_id in self.cluster_info['worker_app_ids']:
                print(f"     - Worker: {worker_id}")
            print()

        print("=" * 70)
        print("✅ End-to-end test completed successfully!")
        print("=" * 70)

        return True


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description='End-to-end CML deployment test (project creation → cluster deployment)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Complete E2E test
  python tests/test_e2e_deployment.py

  # With 2 workers and GPUs
  python tests/test_e2e_deployment.py --workers 2 --gpu 1

  # Quick test (no wait time)
  python tests/test_e2e_deployment.py --no-wait

  # Leave cluster running for inspection
  python tests/test_e2e_deployment.py --no-cleanup

  # Production-like test
  python tests/test_e2e_deployment.py --workers 2 --cpu 16 --memory 64 --gpu 1
        """
    )

    parser.add_argument(
        '--workers',
        type=int,
        default=1,
        help='Number of worker nodes (default: 1)'
    )
    parser.add_argument(
        '--cpu',
        type=int,
        default=4,
        help='CPU cores per worker node (default: 4)'
    )
    parser.add_argument(
        '--memory',
        type=int,
        default=16,
        help='Memory in GB per worker node (default: 16)'
    )
    parser.add_argument(
        '--gpu',
        type=int,
        default=0,
        help='GPUs per worker node (default: 0, head always 0)'
    )
    parser.add_argument(
        '--head-cpu',
        type=int,
        default=2,
        help='CPU cores for head node (default: 2)'
    )
    parser.add_argument(
        '--head-memory',
        type=int,
        default=8,
        help='Memory in GB for head node (default: 8)'
    )
    parser.add_argument(
        '--no-cleanup',
        action='store_true',
        help='Skip cleanup (leave cluster running)'
    )
    parser.add_argument(
        '--no-wait',
        action='store_true',
        help='Skip wait time before cleanup'
    )
    parser.add_argument(
        '--wait-time',
        type=int,
        default=30,
        help='Seconds to wait before cleanup (default: 30)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging (show API responses)'
    )

    args = parser.parse_args()

    try:
        tester = E2EDeploymentTester(args)
        success = tester.run_all_tests()
        sys.exit(0 if success else 1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
