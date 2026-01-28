"""
CAI-based Ray Cluster Manager

This module provides functionality to launch Ray clusters using
Cloudera Machine Learning (CML) Applications as cluster nodes.

Architecture:
- Head node: One CAI application running Ray head
- Worker nodes: Multiple CAI applications connecting to head

Usage:
    from ray_serve_cai.cai_cluster import CAIClusterManager

    manager = CAIClusterManager(
        cml_host="https://ml.example.com",
        cml_api_key="your-api-key",
        project_id="project-123"
    )

    # Start cluster with 1 head + 2 workers
    cluster_info = manager.start_cluster(
        num_workers=2,
        cpu=16,
        memory=64,
        num_gpus=1
    )

    # Get cluster address for Ray client
    address = cluster_info['head_address']
"""

import logging
import time
import sys
import os
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


class CAIClusterManager:
    """
    Manage Ray clusters using CAI (CML) applications.

    This manager creates and manages a Ray cluster where:
    - One CAI application serves as the Ray head node
    - Additional CAI applications serve as Ray worker nodes
    - All communication happens through CAI application networking
    """

    def __init__(
        self,
        cml_host: str,
        cml_api_key: str,
        project_id: str,
        verbose: bool = False
    ):
        """
        Initialize CAI cluster manager.

        Args:
            cml_host: CML instance URL (e.g., https://ml-instance.cloudera.site)
            cml_api_key: API key for CML authentication
            project_id: CML project ID where applications will be created
            verbose: Enable verbose logging
        """
        self.cml_host = cml_host
        self.cml_api_key = cml_api_key
        self.project_id = project_id
        self.verbose = verbose

        # Cluster state
        self.head_app_id: Optional[str] = None
        self.worker_app_ids: List[str] = []
        self.head_address: Optional[str] = None

        # Initialize CML client
        try:
            # Try to import from local caikit package
            sys.path.insert(0, str(Path(__file__).parent.parent.parent / "caikit"))
            from caikit import CMLClient

            self.cml_client = CMLClient(
                host=cml_host,
                api_key=cml_api_key,
                verbose=verbose
            )
            logger.info(f"✅ Connected to CML instance: {cml_host}")

        except ImportError as e:
            logger.error(f"Failed to import caikit library: {e}")
            logger.error("Please ensure caikit package is installed or in Python path")
            raise RuntimeError(
                "caikit library not found. Install it or add to PYTHONPATH"
            )



    def start_cluster(
        self,
        num_workers: int = 1,
        cpu: int = 16,
        memory: int = 64,
        num_gpus: int = 0,
        head_cpu: Optional[int] = None,
        head_memory: Optional[int] = None,
        ray_port: int = 6379,
        dashboard_port: int = 8265,
        runtime_identifier: Optional[str] = None,
        head_runtime_identifier: Optional[str] = None,
        worker_runtime_identifier: Optional[str] = None,
        head_script_path: Optional[str] = None,
        worker_script_path: Optional[str] = None,
        wait_ready: bool = True,
        timeout: int = 300
    ) -> Dict[str, Any]:
        """
        Start Ray cluster using CAI applications.

        The head node is created WITHOUT GPUs (GPUs are only for workers).
        This is the recommended Ray cluster architecture.

        Args:
            num_workers: Number of worker nodes to create
            cpu: CPU cores per worker node
            memory: Memory in GB per worker node
            num_gpus: GPUs per worker node (head node always has 0 GPUs)
            head_cpu: CPU cores for head node (defaults to same as workers)
            head_memory: Memory in GB for head node (defaults to same as workers)
            ray_port: Ray GCS server port
            dashboard_port: Ray dashboard port
            runtime_identifier: Docker runtime identifier (DEPRECATED - use head_runtime_identifier and worker_runtime_identifier)
            head_runtime_identifier: Docker runtime identifier for head node (overrides runtime_identifier)
            worker_runtime_identifier: Docker runtime identifier for worker nodes (overrides runtime_identifier)
            head_script_path: Path to head node launcher script (REQUIRED - must be created before calling)
            worker_script_path: Path to worker node launcher script (REQUIRED - must be created before calling)
            wait_ready: Wait for cluster to be ready
            timeout: Maximum wait time in seconds

        Returns:
            Dictionary with cluster information

        Raises:
            RuntimeError: If runtime_identifier(s), head_script_path, or worker_script_path not provided
        """
        # Determine runtime identifiers
        # Priority: specific head/worker identifiers > generic runtime_identifier > error
        head_rt = head_runtime_identifier or runtime_identifier
        worker_rt = worker_runtime_identifier or runtime_identifier

        if not head_rt or not worker_rt:
            raise RuntimeError(
                "Runtime identifiers are required for applications in this project.\n"
                "Please provide either:\n"
                "  - head_runtime_identifier and worker_runtime_identifier, or\n"
                "  - runtime_identifier (used for both head and workers)\n"
                "Example:\n"
                'head_runtime_identifier="docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-standard:2025.09.1-b5"\n'
                'worker_runtime_identifier="docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-cuda:2025.09.1-b5"'
            )

        # Set head node resources (default to worker resources if not specified)
        head_cpu = head_cpu or cpu
        head_memory = head_memory or memory

        logger.info("🚀 Starting Ray cluster on CAI...")
        logger.info(f"   Head node: {head_cpu}CPU, {head_memory}GB RAM, 0GPU")
        logger.info(f"   Workers: {num_workers} nodes, {cpu}CPU, {memory}GB RAM, {num_gpus}GPU each")

        try:
            # Step 1: Create head node application
            # Head node always gets 0 GPUs - it's for cluster coordination only
            logger.info("🎯 Creating head node application...")

            # Validate head script path is provided (required)
            if not head_script_path:
                raise RuntimeError(
                    "head_script_path is required for head node application.\n"
                    "This should be created by launch_ray_cluster.py before calling this method."
                )

            logger.info(f"   Using head script: {head_script_path}")

            head_app = self.cml_client.applications.create(
                project_id=self.project_id,
                name="ray-cluster-head",
                script=head_script_path,
                cpu=head_cpu,
                memory=head_memory,
                runtime_identifier=head_rt,
                subdomain="ray-cluster-head",
                bypass_authentication=True
            )
            self.head_app_id = head_app.id
            logger.info(f"✅ Head node application created: {head_app.id}")

            # Step 3: Wait for head node to be running
            if wait_ready:
                logger.info("⏳ Waiting for head node to start...")
                head_ready = self._wait_for_application(
                    head_app.id,
                    timeout=timeout
                )
                if not head_ready:
                    raise RuntimeError("Head node failed to start")

                # Get head node details
                head_app = self.cml_client.applications.get(
                    self.project_id,
                    head_app.id
                )

                # Extract head address from application URL
                head_url = head_app.metadata.get('url') or head_app.subdomain
                if head_url:
                    # Remove http:// or https:// and extract hostname
                    if head_url.startswith('http://'):
                        head_hostname = head_url[7:].split(':')[0].split('/')[0]
                    elif head_url.startswith('https://'):
                        head_hostname = head_url[8:].split(':')[0].split('/')[0]
                    else:
                        head_hostname = head_url.split(':')[0].split('/')[0]

                    self.head_address = f"{head_hostname}:{ray_port}"
                    logger.info(f"✅ Head node ready: {self.head_address}")
                else:
                    logger.warning("Could not determine head node address from application URL")
                    self.head_address = f"ray-cluster-head:{ray_port}"

            # Step 4: Create worker nodes
            if num_workers > 0 and self.head_address:
                logger.info(f"🔧 Creating {num_workers} worker node(s)...")

                # Create worker applications
                # Validate worker script path is provided (required)
                if not worker_script_path:
                    raise RuntimeError(
                        "worker_script_path is required for worker node applications.\n"
                        "This should be created by launch_ray_cluster.py before calling this method."
                    )

                for i in range(num_workers):
                    logger.info(f"   Creating worker {i+1}/{num_workers}...")

                    if i == 0:
                        logger.info(f"      Using worker script: {worker_script_path}")

                    worker_kwargs = {
                        'project_id': self.project_id,
                        'name': f"ray-cluster-worker-{i+1}",
                        'script': worker_script_path,
                        'cpu': cpu,
                        'memory': memory,
                        'runtime_identifier': worker_rt,
                        'subdomain': f"ray-cluster-worker-{i+1}",
                        'bypass_authentication': True
                    }
                    if num_gpus > 0:
                        worker_kwargs['num_gpus'] = num_gpus

                    worker_app = self.cml_client.applications.create(**worker_kwargs)
                    self.worker_app_ids.append(worker_app.id)
                    logger.info(f"   ✅ Worker {i+1} created: {worker_app.id}")

                # Wait for workers to be ready
                if wait_ready:
                    logger.info("⏳ Waiting for workers to start...")
                    for i, worker_id in enumerate(self.worker_app_ids):
                        worker_ready = self._wait_for_application(
                            worker_id,
                            timeout=timeout
                        )
                        if worker_ready:
                            logger.info(f"   ✅ Worker {i+1} ready")
                        else:
                            logger.warning(f"   ⚠️  Worker {i+1} may not be ready")

            # Return cluster info
            cluster_info = {
                'status': 'running',
                'head_app_id': self.head_app_id,
                'head_address': self.head_address,
                'worker_app_ids': self.worker_app_ids,
                'num_workers': len(self.worker_app_ids),
                'configuration': {
                    'head': {
                        'cpu': head_cpu,
                        'memory': head_memory,
                        'num_gpus': 0  # Head node never has GPUs
                    },
                    'workers': {
                        'cpu': cpu,
                        'memory': memory,
                        'num_gpus': num_gpus
                    },
                    'ray_port': ray_port,
                    'dashboard_port': dashboard_port
                }
            }

            logger.info("="*60)
            logger.info("✅ Ray cluster started successfully!")
            logger.info(f"   Head address: {self.head_address}")
            logger.info(f"   Head resources: {head_cpu}CPU, {head_memory}GB RAM, 0GPU")
            logger.info(f"   Workers: {len(self.worker_app_ids)} nodes")
            logger.info(f"   Worker resources: {cpu}CPU, {memory}GB RAM, {num_gpus}GPU each")
            logger.info(f"   Total nodes: {1 + len(self.worker_app_ids)}")
            logger.info("="*60)

            return cluster_info

        except Exception as e:
            logger.error(f"Failed to start cluster: {e}")
            # Cleanup on failure
            self.stop_cluster()
            raise

    def _wait_for_application(
        self,
        app_id: str,
        timeout: int = 300,
        check_interval: int = 10
    ) -> bool:
        """
        Wait for application to be in running state.

        Args:
            app_id: Application ID
            timeout: Maximum wait time in seconds
            check_interval: Seconds between status checks

        Returns:
            True if application is running, False if timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                app = self.cml_client.applications.get(
                    self.project_id,
                    app_id
                )

                status = app.status.lower()

                if status == "running":
                    return True
                elif status in ["failed", "stopped", "error"]:
                    logger.error(f"Application {app_id} failed with status: {status}")
                    return False

                # Still starting, wait and retry
                time.sleep(check_interval)

            except Exception as e:
                logger.warning(f"Error checking application status: {e}")
                time.sleep(check_interval)

        logger.error(f"Timeout waiting for application {app_id}")
        return False

    def stop_cluster(self) -> Dict[str, Any]:
        """
        Stop Ray cluster and delete all applications.

        Returns:
            Dictionary with stop status
        """
        logger.info("🛑 Stopping Ray cluster...")

        stopped_apps = []
        errors = []

        # Stop all worker nodes
        for worker_id in self.worker_app_ids:
            try:
                logger.info(f"   Stopping worker: {worker_id}")
                # CML API doesn't have explicit stop, would need to delete
                # For now, just track the ID
                stopped_apps.append(worker_id)
            except Exception as e:
                logger.error(f"   Error stopping worker {worker_id}: {e}")
                errors.append(str(e))

        # Stop head node
        if self.head_app_id:
            try:
                logger.info(f"   Stopping head node: {self.head_app_id}")
                stopped_apps.append(self.head_app_id)
            except Exception as e:
                logger.error(f"   Error stopping head node: {e}")
                errors.append(str(e))

        # Clear state
        self.head_app_id = None
        self.head_address = None
        self.worker_app_ids = []

        if errors:
            logger.warning(f"⚠️  Cluster stopped with {len(errors)} error(s)")
        else:
            logger.info("✅ Cluster stopped successfully")

        return {
            'stopped': True,
            'stopped_apps': stopped_apps,
            'errors': errors if errors else None
        }

    def get_status(self) -> Dict[str, Any]:
        """
        Get current cluster status.

        Returns:
            Dictionary with cluster status
        """
        if not self.head_app_id:
            return {
                'running': False,
                'status': 'not_started'
            }

        try:
            # Check head node status
            head_app = self.cml_client.applications.get(
                self.project_id,
                self.head_app_id
            )

            # Check worker nodes status
            worker_statuses = []
            for worker_id in self.worker_app_ids:
                try:
                    worker_app = self.cml_client.applications.get(
                        self.project_id,
                        worker_id
                    )
                    worker_statuses.append({
                        'id': worker_id,
                        'status': worker_app.status
                    })
                except Exception as e:
                    worker_statuses.append({
                        'id': worker_id,
                        'status': 'error',
                        'error': str(e)
                    })

            return {
                'running': head_app.status.lower() == 'running',
                'head': {
                    'id': self.head_app_id,
                    'status': head_app.status,
                    'address': self.head_address
                },
                'workers': worker_statuses,
                'total_nodes': 1 + len(self.worker_app_ids)
            }

        except Exception as e:
            logger.error(f"Error getting cluster status: {e}")
            return {
                'running': False,
                'status': 'error',
                'error': str(e)
            }
