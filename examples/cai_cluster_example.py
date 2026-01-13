#!/usr/bin/env python3
"""
CAI Cluster Example

This example demonstrates how to:
1. Start a Ray cluster using CAI applications
2. Connect to the cluster
3. Run distributed tasks
4. Stop the cluster

Prerequisites:
- CML instance with API access
- API key with application creation permissions
- Sufficient resource quotas

Usage:
    # Set your API key
    export CML_API_KEY=your-api-key-here

    # Run the example
    python examples/cai_cluster_example.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from ray_serve_cai.cai_cluster import CAIClusterManager


async def main():
    """Main example flow."""

    # Configuration
    CML_HOST = os.environ.get('CML_HOST', 'https://ml.example.cloudera.site')
    CML_API_KEY = os.environ.get('CML_API_KEY')
    PROJECT_ID = os.environ.get('CML_PROJECT_ID', 'your-project-id')

    if not CML_API_KEY:
        print("❌ Error: CML_API_KEY environment variable not set")
        print("   Please set it with: export CML_API_KEY=your-api-key")
        sys.exit(1)

    if PROJECT_ID == 'your-project-id':
        print("⚠️  Warning: Using default PROJECT_ID")
        print("   Set your project ID with: export CML_PROJECT_ID=your-project-id")
        print()

    print("="*60)
    print("CAI Ray Cluster Example")
    print("="*60)
    print(f"CML Host: {CML_HOST}")
    print(f"Project ID: {PROJECT_ID}")
    print()

    # Initialize cluster manager
    print("🚀 Initializing CAI cluster manager...")
    manager = CAIClusterManager(
        cml_host=CML_HOST,
        cml_api_key=CML_API_KEY,
        project_id=PROJECT_ID,
        verbose=True
    )
    print()

    try:
        # Start cluster
        print("🎯 Starting Ray cluster on CAI...")
        print("   This will create 1 head + 2 workers")
        print("   Each node: 8 CPU, 32GB RAM, 0 GPU")
        print()

        cluster_info = manager.start_cluster(
            num_workers=2,
            cpu=8,
            memory=32,
            num_gpus=0,
            wait_ready=True,
            timeout=300
        )

        print()
        print("="*60)
        print("✅ Cluster started successfully!")
        print("="*60)
        print(f"Head Address: {cluster_info['head_address']}")
        print(f"Head App ID: {cluster_info['head_app_id']}")
        print(f"Workers: {cluster_info['num_workers']}")
        print(f"Worker IDs: {', '.join(cluster_info['worker_app_ids'])}")
        print("="*60)
        print()

        # Check cluster status
        print("📊 Checking cluster status...")
        status = manager.get_status()
        print(f"   Running: {status['running']}")
        print(f"   Total nodes: {status['total_nodes']}")
        print(f"   Head status: {status['head']['status']}")
        for i, worker in enumerate(status['workers'], 1):
            print(f"   Worker {i} status: {worker['status']}")
        print()

        # Example: Connect to cluster with Ray
        print("🔌 To connect to this cluster with Ray:")
        print()
        print("   import ray")
        print(f"   ray.init(address='ray://{cluster_info['head_address']}')")
        print()
        print("   # Your Ray code here")
        print("   @ray.remote")
        print("   def my_task():")
        print("       return 'Hello from CAI Ray cluster!'")
        print()
        print("   result = ray.get(my_task.remote())")
        print("   print(result)")
        print()

        # Optional: Try connecting if Ray is available
        try:
            import ray

            print("🧪 Testing Ray connection...")
            ray.init(address=f"ray://{cluster_info['head_address']}")

            @ray.remote
            def test_task():
                import socket
                return f"Task executed on: {socket.gethostname()}"

            # Run a test task
            result = ray.get(test_task.remote())
            print(f"   ✅ Test successful: {result}")

            # Get cluster resources
            resources = ray.cluster_resources()
            print(f"   Cluster resources: {resources}")

            ray.shutdown()
            print()

        except ImportError:
            print("ℹ️  Ray not installed, skipping connection test")
            print("   Install with: pip install ray")
            print()
        except Exception as e:
            print(f"⚠️  Ray connection test failed: {e}")
            print()

        # Keep cluster running for demo (optional)
        print("ℹ️  Cluster is running and ready to use!")
        print()

        # Prompt user
        user_input = input("Press Enter to stop the cluster (or Ctrl+C to keep it running): ")

        # Stop cluster
        print()
        print("🛑 Stopping cluster...")
        stop_result = await manager.stop_cluster()

        if stop_result['stopped']:
            print("✅ Cluster stopped successfully")
        else:
            print("⚠️  Cluster stopped with warnings")
            if stop_result.get('errors'):
                for error in stop_result['errors']:
                    print(f"   Error: {error}")

    except KeyboardInterrupt:
        print()
        print("⚠️  Interrupted by user")
        print("   Cluster is still running on CAI")
        print()
        print("   To stop it later, use:")
        print("   python -m ray_serve_cai.launch_cluster stop-cai")
        sys.exit(0)

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

        # Try to cleanup
        print()
        print("🧹 Attempting cleanup...")
        try:
            await manager.stop_cluster()
            print("✅ Cleanup complete")
        except Exception as cleanup_error:
            print(f"⚠️  Cleanup failed: {cleanup_error}")

        sys.exit(1)

    print()
    print("="*60)
    print("Example completed successfully!")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
