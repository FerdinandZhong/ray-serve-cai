#!/usr/bin/env python3
"""
Test Ray cluster deployment via internal CAI network.

This script verifies a deployed Ray cluster is accessible and healthy
by connecting to it from within a CAI job using ray.init().

Usage (from within a CAI job):
    # Default: connects to ray-cluster-head:6379
    python tests/test_cluster_deployment.py

    # Custom head address
    export RAY_HEAD_ADDRESS="ray-cluster-head-custom:6379"
    python tests/test_cluster_deployment.py

    # With custom timeout
    python tests/test_cluster_deployment.py --timeout 120

    # For CAI job configuration:
    script: "tests/test_cluster_deployment.py"
    engine: "python3"
"""

import os
import sys
import argparse
import time

try:
    import ray
except ImportError:
    print("❌ Error: Ray is not installed")
    print("   Install with: pip install ray")
    sys.exit(1)


class RayClusterTester:
    """Test Ray cluster deployment by connecting via Ray client."""

    def __init__(self, head_address: str, timeout: int = 300):
        """Initialize tester with cluster head address.

        Args:
            head_address: Ray cluster head address (e.g., "ray-cluster-head:6379")
            timeout: Maximum wait time for cluster to be ready
        """
        self.head_address = head_address
        self.timeout = timeout
        self.ray_context = None

        print("=" * 70)
        print("🧪 Ray Cluster Deployment Test")
        print("=" * 70)
        print(f"\n📋 Configuration:")
        print(f"   Head Address: {self.head_address}")
        print(f"   Timeout: {timeout}s")
        print()

    def connect_to_cluster(self) -> bool:
        """Connect to Ray cluster.

        Returns:
            True if connected successfully, False otherwise
        """
        print("=" * 70)
        print("1️⃣  Connecting to Ray Cluster")
        print("=" * 70)
        print()

        start_time = time.time()

        while time.time() - start_time < self.timeout:
            elapsed = int(time.time() - start_time)
            try:
                if elapsed == 0:
                    print(f"   Connecting to {self.head_address}...")

                ray.init(
                    address=f"ray://{self.head_address}",
                    namespace="",
                    ignore_reinit_error=True,
                    _temp_dir="/tmp/ray"
                )
                print(f"\n✅ Connected to Ray cluster! ({elapsed}s elapsed)\n")
                self.ray_context = ray
                return True

            except ConnectionError:
                # Still connecting
                if elapsed % 30 == 0 and elapsed > 0:
                    print(f"   [{elapsed}s] Still waiting for cluster...")
                time.sleep(5)
            except Exception as e:
                print(f"   Error: {e}")
                time.sleep(5)

        print(f"\n❌ Timeout connecting to cluster ({elapsed}s / {self.timeout}s)")
        print()
        return False

    def test_cluster_status(self) -> bool:
        """Test cluster status and resources.

        Returns:
            True if cluster info is available, False otherwise
        """
        print("=" * 70)
        print("2️⃣  Checking Cluster Status")
        print("=" * 70)
        print()

        try:
            info = ray.cluster_resources()
            print(f"✅ Cluster Resources:")
            print(f"   CPU: {info.get('CPU', 0)}")
            print(f"   GPU: {info.get('GPU', 0)}")
            print(f"   Memory: {info.get('memory', 0) / (1024**3):.1f} GB")
            print()

            # Get runtime info
            runtime_info = ray.runtime_context()
            print(f"✅ Runtime Info:")
            print(f"   Namespace: {runtime_info.namespace}")
            print(f"   Node ID: {runtime_info.node_id}")
            print()

            return True

        except Exception as e:
            print(f"❌ Failed to get cluster status: {e}\n")
            return False

    def test_simple_task(self) -> bool:
        """Test submitting a simple task to the cluster.

        Returns:
            True if task completed successfully, False otherwise
        """
        print("=" * 70)
        print("3️⃣  Testing Task Submission")
        print("=" * 70)
        print()

        try:
            @ray.remote
            def test_task():
                return "Hello from Ray cluster!"

            print("   Submitting test task...")
            result_ref = test_task.remote()
            result = ray.get(result_ref, timeout=30)
            print(f"   Result: {result}")
            print(f"✅ Task submission and execution successful!\n")
            return True

        except Exception as e:
            print(f"❌ Task submission failed: {e}\n")
            return False

    def run_all_tests(self) -> bool:
        """Run all tests against deployed cluster.

        Returns:
            True if all tests pass, False otherwise
        """
        print("\n🚀 Starting Ray cluster deployment tests...\n")

        # Test 1: Connect to cluster
        if not self.connect_to_cluster():
            print("\n❌ Failed to connect to Ray cluster!")
            return False

        # Test 2: Check cluster status
        if not self.test_cluster_status():
            print("❌ Failed to get cluster status!")
            return False

        # Test 3: Submit a task
        if not self.test_simple_task():
            print("❌ Failed to submit task to cluster!")
            return False

        print("=" * 70)
        print("✅ Ray cluster deployment test passed!")
        print("=" * 70)
        print()

        return True

    def cleanup(self):
        """Clean up Ray context."""
        try:
            if ray.is_initialized():
                ray.shutdown()
        except Exception as e:
            print(f"Warning: Error during cleanup: {e}")


def main():
    """Main execution function."""
    parser = argparse.ArgumentParser(
        description='Test deployed Ray cluster from CAI job',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default (connects to ray-cluster-head:6379)
  python tests/test_cluster_deployment.py

  # Custom head address
  python tests/test_cluster_deployment.py --address ray-cluster-head-custom:6379

  # Using environment variable
  export RAY_HEAD_ADDRESS="ray-cluster-head:6379"
  python tests/test_cluster_deployment.py

  # With custom timeout
  python tests/test_cluster_deployment.py --timeout 120
        """
    )

    parser.add_argument(
        '--address',
        dest='head_address',
        default=os.environ.get('RAY_HEAD_ADDRESS', 'ray-cluster-head:6379'),
        help='Ray cluster head address (default: ray-cluster-head:6379 or RAY_HEAD_ADDRESS env var)'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=300,
        help='Maximum wait time for cluster readiness in seconds (default: 300)'
    )

    args = parser.parse_args()

    tester = None
    try:
        tester = RayClusterTester(
            head_address=args.head_address,
            timeout=args.timeout
        )
        success = tester.run_all_tests()
        sys.exit(0 if success else 1)
    except SystemExit:
        raise
    except Exception as e:
        print(f"\n❌ Unexpected error: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if tester:
            tester.cleanup()


if __name__ == "__main__":
    main()
