#!/usr/bin/env python3
"""
Local test script for CAI cluster deployment.

This script tests the ability to deploy Ray cluster nodes (head + workers)
as CAI applications on a remote CML instance.

Usage:
    # Set environment variables
    export CML_HOST="https://ml.example.cloudera.site"
    export CML_API_KEY="your-api-key"
    export CML_PROJECT_ID="your-project-id"

    # Run the test via bash wrapper (recommended)
    bash scripts/test_cluster.sh

    # Or directly with Python (if venv is already activated)
    python tests/test_cluster_deployment.py

    # With custom configuration
    python tests/test_cluster_deployment.py --workers 1 --cpu 4 --memory 16
"""

import os
import sys
import argparse
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from ray_serve_cai.cai_cluster import CAIClusterManager
except ImportError as e:
    print("=" * 70)
    print("❌ ImportError: Could not import ray_serve_cai")
    print("=" * 70)
    print(f"\nError: {e}")
    print("\nThis test requires dependencies to be installed.")
    print("\nIn GitHub Actions or CI environments:")
    print("  1. Ensure the package is installed: pip install -e .")
    print("  2. Or install dependencies: pip install ray[serve] fastapi starlette")
    print("\nIn CAI environments:")
    print("  1. Run setup_environment.py as a CML Job first")
    print("  2. Then run this test via bash wrapper: bash build/shell_scripts/test_cluster.sh")
    print("  3. Or directly: python tests/test_cluster_deployment.py (if venv is activated)")
    print("\n" + "=" * 70)
    sys.exit(1)


class CAIClusterTester:
    """Test CAI cluster deployment locally."""

    def __init__(self):
        """Initialize tester with environment variables."""
        self.cml_host = os.environ.get("CML_HOST")
        self.cml_api_key = os.environ.get("CML_API_KEY")
        self.project_id = os.environ.get("CML_PROJECT_ID")

        print("=" * 70)
        print("🧪 CAI Cluster Deployment Test")
        print("=" * 70)
        print(f"\n📋 Configuration:")
        print(f"   CML Host: {self.cml_host}")
        print(f"   API Key: {'***' + self.cml_api_key[-4:] if self.cml_api_key else 'NOT SET'}")
        print(f"   Project ID: {self.project_id}")

        if not all([self.cml_host, self.cml_api_key, self.project_id]):
            print("\n❌ Error: Missing required environment variables")
            print("   Required: CML_HOST, CML_API_KEY, CML_PROJECT_ID")
            print("\n   Set them with:")
            print('   export CML_HOST="https://ml.example.cloudera.site"')
            print('   export CML_API_KEY="your-api-key"')
            print('   export CML_PROJECT_ID="your-project-id"')
            raise SystemExit(1)

        print("\n✅ Configuration validated\n")

    def test_cai_connection(self):
        """Test basic CAI/CML connection."""
        print("=" * 70)
        print("1️⃣  Testing CAI Connection")
        print("=" * 70)

        try:
            # Import caikit library
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "caikit"))
            from caikit import CMLClient

            client = CMLClient(
                host=self.cml_host,
                api_key=self.cml_api_key,
                verbose=False
            )

            # Test by getting project info
            try:
                project = client.projects.get(self.project_id)
                print(f"\n✅ Connected to CML successfully")
                print(f"   Project: {project.name}")
                print(f"   Created: {project.created_at}")
                return True
            except Exception as e:
                print(f"\n❌ Failed to get project: {e}")
                return False

        except ImportError as e:
            print(f"\n❌ Failed to import caikit library: {e}")
            print("   Make sure caikit package is installed or in PYTHONPATH")
            return False
        except Exception as e:
            print(f"\n❌ Connection test failed: {e}")
            return False

    def _create_test_launcher_scripts(self):
        """Create test launcher scripts for Ray head and worker nodes."""
        project_dir = Path(__file__).parent.parent

        # Create test launcher scripts
        head_script_path = project_dir / "ray_head_launcher_test.py"
        worker_script_path = project_dir / "ray_worker_launcher_test.py"

        # Head launcher script content
        head_script_content = '''#!/usr/bin/env python3
"""Ray Head Node Launcher for CAI (Test)"""
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
"""Ray Worker Node Launcher for CAI (Test)"""
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

        print(f"📄 Created test launcher scripts:")
        print(f"   Head: {head_script_path}")
        print(f"   Worker: {worker_script_path}")

        return str(head_script_path), str(worker_script_path)

    def test_cluster_creation(
        self,
        num_workers: int = 1,
        cpu: int = 32,
        memory: int = 32,
        num_gpus: int = 4,
        head_cpu: int = 8,
        head_memory: int = 32
    ):
        """Test creating a minimal CAI cluster."""
        print("\n" + "=" * 70)
        print("2️⃣  Testing Cluster Creation")
        print("=" * 70)
        print(f"\n📝 Test Configuration:")
        print(f"   Workers: {num_workers}")
        print(f"   Head Resources: {head_cpu}CPU, {head_memory}GB, 0GPU")
        print(f"   Worker Resources: {cpu}CPU, {memory}GB, {num_gpus}GPU")
        print()

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

            # Create test launcher scripts
            print("📝 Creating launcher scripts...")
            head_script_path, worker_script_path = self._create_test_launcher_scripts()
            print("✅ Launcher scripts created\n")

            # Start cluster
            print("🚀 Starting test cluster...")
            print("   (This may take 2-5 minutes)\n")

            cluster_info = manager.start_cluster(
                num_workers=num_workers,
                cpu=cpu,
                memory=memory,
                num_gpus=num_gpus,
                head_cpu=head_cpu,
                head_memory=head_memory,
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

            return manager, cluster_info

        except Exception as e:
            print(f"\n❌ Cluster creation failed: {e}")
            import traceback
            traceback.print_exc()
            return None, None

    def test_cluster_status(self, manager: CAIClusterManager):
        """Test getting cluster status."""
        print("\n" + "=" * 70)
        print("3️⃣  Testing Cluster Status")
        print("=" * 70)

        try:
            status = manager.get_status()
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
                print(f"   Total Nodes: {status['total_nodes']}")
            return True
        except Exception as e:
            print(f"\n❌ Status check failed: {e}")
            return False

    def test_cluster_cleanup(self, manager: CAIClusterManager):
        """Test cluster cleanup/deletion."""
        print("\n" + "=" * 70)
        print("4️⃣  Testing Cluster Cleanup")
        print("=" * 70)

        try:
            print("\n🧹 Stopping and cleaning up cluster...")
            result = manager.stop_cluster()

            if result['stopped']:
                print("\n✅ Cluster cleaned up successfully")
                if result.get('errors'):
                    print(f"   ⚠️  Some warnings:")
                    for error in result['errors']:
                        print(f"      - {error}")
                return True
            else:
                print("\n❌ Cleanup completed with errors")
                return False

        except Exception as e:
            print(f"\n❌ Cleanup failed: {e}")
            return False

    def run_all_tests(self, args):
        """Run all tests."""
        print("\n🚀 Starting CAI cluster deployment tests...\n")

        # Test 1: CAI Connection
        if not self.test_cai_connection():
            print("\n❌ Cannot proceed without CAI connection!")
            return False

        # Test 2: Cluster Creation
        manager, cluster_info = self.test_cluster_creation(
            num_workers=args.workers,
            cpu=args.cpu,
            memory=args.memory,
            num_gpus=args.gpu,
            head_cpu=args.head_cpu,
            head_memory=args.head_memory
        )

        if not manager or not cluster_info:
            print("\n❌ Cannot proceed without cluster!")
            return False

        # Test 3: Cluster Status
        self.test_cluster_status(manager)

        # Wait a bit to see cluster running
        if not args.no_wait:
            print(f"\n⏳ Cluster is running. Waiting {args.wait_time}s before cleanup...")
            print("   (You can check the CML UI to see the applications)")
            time.sleep(args.wait_time)

        # Test 4: Cleanup (if not skipped)
        if not args.no_cleanup:
            success = self.test_cluster_cleanup(manager)
            if not success:
                print("\n⚠️  Cleanup had issues. Check CML UI to verify.")
        else:
            print("\n⚠️  Skipping cleanup (--no-cleanup flag set)")
            print("   Remember to manually delete the applications from CML UI:")
            print(f"     - Head: {cluster_info['head_app_id']}")
            for worker_id in cluster_info['worker_app_ids']:
                print(f"     - Worker: {worker_id}")

        print("\n" + "=" * 70)
        print("✅ All tests completed!")
        print("=" * 70)

        return True


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description='Test CAI cluster deployment',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Minimal test (1 worker, small resources)
  python tests/test_cai_deployment.py

  # Test with 2 workers and more resources
  python tests/test_cai_deployment.py --workers 2 --cpu 8 --memory 32

  # Test with GPUs
  python tests/test_cai_deployment.py --gpu 1 --cpu 16 --memory 64

  # Test without cleanup (leave cluster running)
  python tests/test_cai_deployment.py --no-cleanup

  # Quick test (no wait time)
  python tests/test_cai_deployment.py --no-wait
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
        default=32,
        help='CPU cores per worker node (default: 32)'
    )
    parser.add_argument(
        '--memory',
        type=int,
        default=32,
        help='Memory in GB per worker node (default: 32)'
    )
    parser.add_argument(
        '--gpu',
        type=int,
        default=4,
        help='GPUs per worker node (default: 4)'
    )
    parser.add_argument(
        '--head-cpu',
        type=int,
        default=8,
        help='CPU cores for head node (default: 8)'
    )
    parser.add_argument(
        '--head-memory',
        type=int,
        default=32,
        help='Memory in GB for head node (default: 32)'
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

    args = parser.parse_args()

    try:
        tester = CAIClusterTester()
        success = tester.run_all_tests(args)
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
