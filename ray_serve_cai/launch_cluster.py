#!/usr/bin/env python3
"""
Ray Cluster Launcher for vLLM Playground

This script launches a Ray cluster using YAML configuration files.
Supports both local Ray clusters and CAI-based distributed clusters.

Usage:
    # Start local cluster with config
    python launch_cluster.py --config cluster_config.yaml start

    # Start CAI-based cluster
    python launch_cluster.py --config cai_cluster_config.yaml start-cai

    # Check cluster status
    python launch_cluster.py status

    # Stop cluster
    python launch_cluster.py stop

    # Get cluster address
    python launch_cluster.py get-address
"""

import argparse
import logging
import os
import subprocess
import sys
import time
import yaml
from pathlib import Path
from typing import Dict, Any, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RayClusterManager:
    """Manage Ray cluster lifecycle using ray CLI or CAI applications."""

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize cluster manager.

        Args:
            config_path: Path to YAML configuration file
        """
        self.config_path = config_path
        self.config = None
        self.cai_manager = None  # CAI cluster manager instance

        if config_path:
            self.config = self._load_config(config_path)

    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Load YAML configuration file."""
        try:
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f)
            logger.info(f"Loaded configuration from {config_path}")
            return config
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise

    def _run_command(self, cmd: list, check: bool = True) -> subprocess.CompletedProcess:
        """Run a shell command."""
        logger.debug(f"Running command: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=check
            )
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {e.stderr}")
            raise

    def start_local_cluster(self) -> Dict[str, Any]:
        """
        Start a local Ray cluster.

        Returns:
            Dictionary with cluster info (address, dashboard_url, etc.)
        """
        try:
            # Check if Ray is installed
            result = self._run_command(["ray", "--version"], check=False)
            if result.returncode != 0:
                raise RuntimeError("Ray is not installed. Install with: pip install ray[serve]")

            logger.info("Starting local Ray cluster...")

            # Build ray start command
            cmd = ["ray", "start", "--head"]

            if self.config:
                cluster_config = self.config.get('cluster', {})

                # Port configuration
                if 'port' in cluster_config:
                    cmd.extend(["--port", str(cluster_config['port'])])
                else:
                    cmd.extend(["--port", "6379"])  # Default port

                # Dashboard configuration
                if cluster_config.get('dashboard', {}).get('enabled', True):
                    dashboard_port = cluster_config.get('dashboard', {}).get('port', 8265)
                    cmd.extend(["--dashboard-host", "0.0.0.0"])
                    cmd.extend(["--dashboard-port", str(dashboard_port)])
                else:
                    cmd.append("--no-dashboard")

                # Resource configuration
                resources = cluster_config.get('resources', {})
                if 'num_cpus' in resources:
                    cmd.extend(["--num-cpus", str(resources['num_cpus'])])
                if 'num_gpus' in resources:
                    cmd.extend(["--num-gpus", str(resources['num_gpus'])])

                # Memory configuration
                if 'object_store_memory' in resources:
                    cmd.extend(["--object-store-memory", str(resources['object_store_memory'])])

                # Block mode
                if cluster_config.get('block', False):
                    cmd.append("--block")
            else:
                # Default configuration
                cmd.extend([
                    "--port", "6379",
                    "--dashboard-host", "0.0.0.0",
                    "--dashboard-port", "8265"
                ])

            # Start the cluster
            result = self._run_command(cmd)

            # Parse output for cluster info
            output = result.stdout
            logger.info(f"Ray cluster started successfully")
            logger.info(output)

            # Extract address from output
            address = "127.0.0.1:6379"  # Default
            dashboard_url = None

            for line in output.split('\n'):
                if 'ray start --address' in line:
                    # Extract address from connection string
                    parts = line.split("'")
                    if len(parts) >= 2:
                        address = parts[1]
                elif 'dashboard' in line.lower() and 'http' in line:
                    # Extract dashboard URL
                    parts = line.split()
                    for part in parts:
                        if part.startswith('http'):
                            dashboard_url = part

            cluster_info = {
                'status': 'running',
                'address': address,
                'dashboard_url': dashboard_url,
                'mode': 'local_head'
            }

            logger.info(f"✅ Cluster ready")
            logger.info(f"   Address: {address}")
            if dashboard_url:
                logger.info(f"   Dashboard: {dashboard_url}")

            return cluster_info

        except Exception as e:
            logger.error(f"Failed to start cluster: {e}")
            raise

    def stop_cluster(self) -> bool:
        """
        Stop the Ray cluster.

        Returns:
            True if stopped successfully
        """
        try:
            logger.info("Stopping Ray cluster...")

            result = self._run_command(["ray", "stop"], check=False)

            if result.returncode == 0:
                logger.info("✅ Cluster stopped successfully")
                return True
            else:
                logger.warning(f"Stop command output: {result.stdout}")
                return False

        except Exception as e:
            logger.error(f"Error stopping cluster: {e}")
            return False

    def get_status(self) -> Dict[str, Any]:
        """
        Get cluster status.

        Returns:
            Dictionary with cluster status information
        """
        try:
            result = self._run_command(["ray", "status"], check=False)

            if result.returncode == 0:
                output = result.stdout

                # Parse status output
                status_info = {
                    'running': True,
                    'output': output
                }

                # Extract useful information
                for line in output.split('\n'):
                    if 'address' in line.lower():
                        logger.info(line)
                    elif 'resources' in line.lower():
                        logger.info(line)

                return status_info
            else:
                return {
                    'running': False,
                    'message': result.stdout or result.stderr
                }

        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return {
                'running': False,
                'error': str(e)
            }

    def get_address(self) -> Optional[str]:
        """
        Get the cluster address.

        Returns:
            Cluster address string (e.g., "127.0.0.1:6379") or None
        """
        try:
            # Try to get address from ray status
            result = self._run_command(["ray", "status"], check=False)

            if result.returncode != 0:
                logger.info("No Ray cluster running")
                return None

            # Parse output for address
            output = result.stdout
            for line in output.split('\n'):
                if 'address' in line.lower() and ':' in line:
                    # Try to extract IP:port
                    parts = line.split()
                    for part in parts:
                        if ':' in part and any(c.isdigit() for c in part):
                            # Found something that looks like an address
                            address = part.strip('",\'')
                            logger.info(f"Cluster address: {address}")
                            return address

            # Default address if running
            logger.info("Cluster running, using default address: 127.0.0.1:6379")
            return "127.0.0.1:6379"

        except Exception as e:
            logger.error(f"Error getting address: {e}")
            return None

    def start_with_autoscaler(self, config_path: str) -> Dict[str, Any]:
        """
        Start Ray cluster with autoscaler using ray up.

        Args:
            config_path: Path to Ray cluster YAML config

        Returns:
            Dictionary with cluster info
        """
        try:
            logger.info(f"Starting cluster with autoscaler using config: {config_path}")
            logger.info("Note: This requires cloud provider credentials configured")

            cmd = ["ray", "up", config_path, "-y"]

            result = self._run_command(cmd)

            logger.info("✅ Cluster started with autoscaler")
            logger.info(result.stdout)

            return {
                'status': 'running',
                'mode': 'autoscaler',
                'config': config_path
            }

        except Exception as e:
            logger.error(f"Failed to start cluster with autoscaler: {e}")
            raise

    def start_cai_cluster(self) -> Dict[str, Any]:
        """
        Start Ray cluster using CAI (CML) applications.

        Head node is created WITHOUT GPUs (for cluster coordination).
        Worker nodes get GPU allocation (for actual computation).

        Requires config with 'cai' section:
        cai:
          host: https://ml-instance.cloudera.site
          api_key: your-api-key  # or set CML_API_KEY env var
          project_id: project-123
          num_workers: 2
          resources:  # Worker node resources
            cpu: 16
            memory: 64
            num_gpus: 1
          head_resources:  # Optional: Head node resources (defaults to worker resources but 0 GPU)
            cpu: 8
            memory: 32

        Returns:
            Dictionary with cluster info
        """
        if not self.config:
            raise ValueError("Config file required for CAI cluster")

        cai_config = self.config.get('cai', {})

        if not cai_config:
            raise ValueError("Config must contain 'cai' section for CAI cluster")

        # Get CAI credentials
        cml_host = cai_config.get('host')
        cml_api_key = cai_config.get('api_key') or os.environ.get('CML_API_KEY')
        project_id = cai_config.get('project_id')

        if not all([cml_host, cml_api_key, project_id]):
            raise ValueError(
                "CAI config must include: host, api_key (or CML_API_KEY env), project_id"
            )

        # Get cluster configuration
        num_workers = cai_config.get('num_workers', 1)

        # Worker node resources
        resources = cai_config.get('resources', {})
        cpu = resources.get('cpu', 32)
        memory = resources.get('memory', 32)
        num_gpus = resources.get('num_gpus', 4)

        # Head node resources (optional, defaults to worker resources)
        head_resources = cai_config.get('head_resources', {})
        head_cpu = head_resources.get('cpu')  # None means use worker cpu
        head_memory = head_resources.get('memory')  # None means use worker memory

        runtime_identifier = cai_config.get('runtime_identifier')

        try:
            # Import CAI cluster manager
            from .cai_cluster import CAIClusterManager

            # Initialize CAI manager
            logger.info("Initializing CAI cluster manager...")
            self.cai_manager = CAIClusterManager(
                cml_host=cml_host,
                cml_api_key=cml_api_key,
                project_id=project_id,
                verbose=logger.level <= logging.DEBUG
            )

            # Start cluster
            logger.info(f"Starting CAI-based Ray cluster...")
            logger.info(f"  Workers: {num_workers}")
            logger.info(f"  Worker resources: {cpu}CPU, {memory}GB, {num_gpus}GPU")
            if head_cpu or head_memory:
                logger.info(f"  Head resources: {head_cpu or cpu}CPU, {head_memory or memory}GB, 0GPU")
            else:
                logger.info(f"  Head resources: {cpu}CPU, {memory}GB, 0GPU (using worker resources)")

            cluster_info = self.cai_manager.start_cluster(
                num_workers=num_workers,
                cpu=cpu,
                memory=memory,
                num_gpus=num_gpus,
                head_cpu=head_cpu,
                head_memory=head_memory,
                runtime_identifier=runtime_identifier,
                wait_ready=True
            )

            logger.info("✅ CAI cluster started successfully")

            return cluster_info

        except Exception as e:
            logger.error(f"Failed to start CAI cluster: {e}")
            raise

    def stop_cai_cluster(self) -> bool:
        """
        Stop CAI cluster.

        Returns:
            True if stopped successfully
        """
        if not self.cai_manager:
            logger.warning("No CAI cluster manager found")
            return False

        try:
            logger.info("Stopping CAI cluster...")
            import asyncio

            # Run async stop
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(self.cai_manager.stop_cluster())

            if result.get('stopped'):
                logger.info("✅ CAI cluster stopped successfully")
                return True
            else:
                logger.warning("CAI cluster stop completed with warnings")
                return False

        except Exception as e:
            logger.error(f"Error stopping CAI cluster: {e}")
            return False

    def get_cai_status(self) -> Dict[str, Any]:
        """
        Get CAI cluster status.

        Returns:
            Dictionary with cluster status
        """
        if not self.cai_manager:
            return {
                'running': False,
                'status': 'no_cai_cluster'
            }

        try:
            return self.cai_manager.get_status()
        except Exception as e:
            logger.error(f"Error getting CAI cluster status: {e}")
            return {
                'running': False,
                'status': 'error',
                'error': str(e)
            }


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Ray Cluster Manager for vLLM Playground',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start local cluster with default settings
  python launch_cluster.py start

  # Start cluster with custom config
  python launch_cluster.py --config my_cluster.yaml start

  # Start CAI-based distributed cluster
  python launch_cluster.py --config cai_cluster.yaml start-cai

  # Check CAI cluster status
  python launch_cluster.py status-cai

  # Check local cluster status
  python launch_cluster.py status

  # Get cluster address for vllm-playground
  python launch_cluster.py get-address

  # Stop local cluster
  python launch_cluster.py stop

  # Stop CAI cluster
  python launch_cluster.py stop-cai

  # Start with autoscaler (for cloud deployments)
  python launch_cluster.py --config cloud_cluster.yaml start-autoscaler
        """
    )

    parser.add_argument(
        '--config',
        type=str,
        help='Path to YAML configuration file'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    parser.add_argument(
        'command',
        choices=['start', 'stop', 'status', 'get-address', 'start-autoscaler', 'start-cai', 'stop-cai', 'status-cai'],
        help='Command to execute'
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Initialize cluster manager
    manager = RayClusterManager(config_path=args.config)

    # Execute command
    try:
        if args.command == 'start':
            result = manager.start_local_cluster()
            print(f"\n✅ Cluster started successfully!")
            print(f"Address: {result['address']}")
            if result.get('dashboard_url'):
                print(f"Dashboard: {result['dashboard_url']}")
            print(f"\nTo use with vllm-playground, set:")
            print(f"  RAY_ADDRESS={result['address']}")

        elif args.command == 'stop':
            success = manager.stop_cluster()
            sys.exit(0 if success else 1)

        elif args.command == 'status':
            status = manager.get_status()
            if status['running']:
                print("\n✅ Ray cluster is running")
                print(status['output'])
            else:
                print("\n❌ No Ray cluster running")
                if 'message' in status:
                    print(status['message'])

        elif args.command == 'get-address':
            address = manager.get_address()
            if address:
                print(address)
                sys.exit(0)
            else:
                print("No cluster running", file=sys.stderr)
                sys.exit(1)

        elif args.command == 'start-autoscaler':
            if not args.config:
                print("Error: --config required for start-autoscaler", file=sys.stderr)
                sys.exit(1)
            result = manager.start_with_autoscaler(args.config)
            print(f"\n✅ Cluster started with autoscaler")

        elif args.command == 'start-cai':
            if not args.config:
                print("Error: --config required for start-cai", file=sys.stderr)
                sys.exit(1)
            result = manager.start_cai_cluster()
            print(f"\n✅ CAI cluster started successfully!")
            print(f"Head address: {result['head_address']}")
            print(f"Workers: {result['num_workers']}")
            print(f"\nTo use with Ray, connect to:")
            print(f"  ray://{result['head_address']}")

        elif args.command == 'stop-cai':
            success = manager.stop_cai_cluster()
            sys.exit(0 if success else 1)

        elif args.command == 'status-cai':
            status = manager.get_cai_status()
            if status['running']:
                print("\n✅ CAI Ray cluster is running")
                print(f"\nHead node:")
                print(f"  ID: {status['head']['id']}")
                print(f"  Status: {status['head']['status']}")
                print(f"  Address: {status['head']['address']}")
                print(f"\nWorker nodes: {len(status['workers'])}")
                for i, worker in enumerate(status['workers'], 1):
                    print(f"  Worker {i}: {worker['status']} (ID: {worker['id']})")
                print(f"\nTotal nodes: {status['total_nodes']}")
            else:
                print("\n❌ No CAI Ray cluster running")
                if 'error' in status:
                    print(f"Error: {status['error']}")

    except Exception as e:
        logger.error(f"Command failed: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
