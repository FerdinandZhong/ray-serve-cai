"""
CAI-based Ray Cluster Manager

This module provides functionality to launch Ray clusters using
Cloudera Machine Learning (CML) Applications as cluster nodes.

Architecture:
- Head node: One CAI application running Ray head
- Worker nodes: Multiple CAI applications connecting to head, organised into
  one or more WorkerGroupConfig groups (e.g. CPU workers, T4 GPU workers,
  L40 GPU workers).  Each group registers a "node_type:<label>" Ray resource
  so that coordinator._detect_node_type() and scheduling strategies can
  identify and target specific node types.

Usage:
    from ray_serve_cai.cai_cluster import CAIClusterManager, WorkerGroupConfig

    manager = CAIClusterManager(
        cml_host="https://ml.example.com",
        cml_api_key="your-api-key",
        project_id="project-123"
    )

    worker_groups = [
        WorkerGroupConfig(name="t4-workers",  node_type="t4_gpu_node_single",   count=2, cpu=16, memory=64,  gpus=1),
        WorkerGroupConfig(name="l40-workers", node_type="l40_gpu_node_2_gpus",  count=1, cpu=32, memory=128, gpus=2),
    ]

    cluster_info = manager.start_cluster(
        worker_groups=worker_groups,
        head_script_path="/home/cdsw/ray_head_launcher.py",
        head_runtime_identifier="...",
        worker_runtime_identifier="...",
    )
"""

import logging
import time
import requests
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ApplicationInfo:
    """Simple data class to hold application info from CML API."""
    id: str
    name: str
    status: str
    subdomain: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class WorkerGroupConfig:
    """Configuration for a homogeneous group of Ray worker nodes.

    Each group self-registers a custom Ray resource label
    ("node_type:<node_type>") via --resources at ray start time.
    coordinator._detect_node_type() reads this label to classify nodes
    without needing a static registry — any label suffix is valid.

    Examples:
        WorkerGroupConfig(name="cpu-workers",  node_type="cpu-worker",          count=4, cpu=8,  memory=32,  gpus=0)
        WorkerGroupConfig(name="t4-workers",   node_type="t4_gpu_node_single",  count=2, cpu=16, memory=64,  gpus=1)
        WorkerGroupConfig(name="l40-workers",  node_type="l40_gpu_node_2_gpus", count=1, cpu=32, memory=128, gpus=2)
    """
    name: str                              # used as part of CAI application names
    node_type: str                         # label registered as "node_type:<node_type>"
    count: int                             # number of workers in this group
    cpu: int                               # CPU cores per worker
    memory: int                            # memory in GB per worker
    gpus: int = 0                          # GPUs per worker  (0 = CPU-only)
    accelerator_type: Optional[str] = None # GPU type label (e.g. "L40S", "T4", "A10")
    node_label: Optional[Dict[str, str]] = None  # K8s node selector labels for pod placement
    runtime_identifier: Optional[str] = None   # Docker runtime; None = cluster default
    script_path: Optional[str] = None     # set by create_ray_launcher_scripts()


class CMLAPIClient:
    """
    Simple CML API v2 client using direct HTTP requests.
    Replaces external caikit dependency with internal implementation.
    """

    def __init__(self, host: str, api_key: str, verbose: bool = False):
        """
        Initialize CML API client.

        Args:
            host: CML instance URL (e.g., https://ml-instance.cloudera.site)
            api_key: API key for authentication (CDSW_APIV2_KEY)
            verbose: Enable verbose logging
        """
        self.host = host.rstrip('/')
        self.api_key = api_key
        self.verbose = verbose
        self.base_url = f"{self.host}/api/v2"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        })

    def create_application(
        self,
        project_id: str,
        name: str,
        script: str,
        cpu: int,
        memory: int,
        runtime_identifier: str,
        subdomain: str,
        bypass_authentication: bool = True,
        num_gpus: int = 0,
        environment: Optional[Dict[str, str]] = None,
    ) -> ApplicationInfo:
        """
        Create a CML application.

        Args:
            project_id: Project ID
            name: Application name
            script: Script path to run
            cpu: Number of CPU cores
            memory: Memory in GB
            runtime_identifier: Docker runtime identifier
            subdomain: Application subdomain
            bypass_authentication: Allow unauthenticated access
            num_gpus: Number of GPUs (0 for no GPU)
            environment: Optional env vars injected into the application at start

        Returns:
            ApplicationInfo with created application details
        """
        url = f"{self.base_url}/projects/{project_id}/applications"

        payload = {
            'name': name,
            'script': script,
            'cpu': cpu,
            'memory': memory,
            'runtime_identifier': runtime_identifier,
            'subdomain': subdomain,
            'bypass_authentication': bypass_authentication,
        }

        if num_gpus > 0:
            payload['nvidia_gpu'] = num_gpus
        if environment:
            payload['environment'] = environment
        if self.verbose:
            logger.debug(f"Creating application: POST {url}")
            logger.debug(f"Payload: {payload}")

        response = self.session.post(url, json=payload)
        if response.status_code >= 400:
            logger.error(
                "Failed to create application (HTTP %d): %s",
                response.status_code, response.text,
            )
            response.raise_for_status()

        data = response.json()
        return ApplicationInfo(
            id=data.get('id'),
            name=data.get('name'),
            status=data.get('status', 'unknown'),
            subdomain=data.get('subdomain'),
            metadata=data
        )

    def list_applications(self, project_id: str) -> List[ApplicationInfo]:
        """
        List all applications in a project, following pagination tokens.

        Args:
            project_id: Project ID

        Returns:
            List of ApplicationInfo
        """
        base_url = f"{self.base_url}/projects/{project_id}/applications"
        all_apps: List[ApplicationInfo] = []
        page_token: str = ""

        while True:
            params = {"page_size": 100}
            if page_token:
                params["page_token"] = page_token

            logger.info(f"GET {base_url}  params={params}")

            response = self.session.get(base_url, params=params)

            logger.info(f"  -> HTTP {response.status_code}")
            if response.status_code != 200:
                logger.warning(f"  -> body: {response.text[:500]}")
            response.raise_for_status()

            data = response.json()
            if isinstance(data, dict):
                logger.info(f"  -> response keys: {list(data.keys())}")
            items = data.get("applications", []) if isinstance(data, dict) else data
            logger.info(f"  -> {len(items)} application(s) on this page")
            all_apps.extend([
                ApplicationInfo(
                    id=a.get("id"),
                    name=a.get("name"),
                    status=a.get("status", "unknown"),
                    subdomain=a.get("subdomain"),
                    metadata=a,
                )
                for a in items
            ])

            page_token = data.get("next_page_token", "") if isinstance(data, dict) else ""
            if not page_token:
                break

        return all_apps

    def get_application(self, project_id: str, app_id: str) -> ApplicationInfo:
        """
        Get application details.

        Args:
            project_id: Project ID
            app_id: Application ID

        Returns:
            ApplicationInfo with application details
        """
        url = f"{self.base_url}/projects/{project_id}/applications/{app_id}"

        if self.verbose:
            logger.debug(f"Getting application: GET {url}")

        response = self.session.get(url)
        response.raise_for_status()

        data = response.json()
        return ApplicationInfo(
            id=data.get('id'),
            name=data.get('name'),
            status=data.get('status', 'unknown'),
            subdomain=data.get('subdomain'),
            metadata=data
        )

    def delete_application(self, project_id: str, app_id: str) -> bool:
        """
        Delete an application.

        Args:
            project_id: Project ID
            app_id: Application ID

        Returns:
            True if deleted successfully
        """
        url = f"{self.base_url}/projects/{project_id}/applications/{app_id}"

        if self.verbose:
            logger.debug(f"Deleting application: DELETE {url}")

        response = self.session.delete(url)
        return response.status_code in [200, 204]

    def create_job(
        self,
        project_id: str,
        name: str,
        script: str,
        runtime_identifier: str,
        cpu: int = 2,
        memory: int = 4,
        kernel: str = "python3",
    ) -> Optional[str]:
        """Create an on-demand CML Job (manual "Run" entrypoint).

        Used to register the head-recovery job so an operator can trigger it
        from the CML UI when the head — and thus the management API — is down.
        Returns the job id, or None on failure (callers treat this as
        best-effort so job registration never blocks cluster startup).
        """
        url = f"{self.base_url}/projects/{project_id}/jobs"
        payload = {
            "name": name,
            "script": script,
            "cpu": cpu,
            "memory": memory,
            "runtime_identifier": runtime_identifier,
            "kernel": kernel,
        }
        try:
            response = self.session.post(url, json=payload)
            if response.status_code >= 400:
                logger.warning("create_job HTTP %d: %s", response.status_code, response.text[:300])
                return None
            return response.json().get("id")
        except requests.RequestException as e:
            logger.warning("create_job request failed: %s", e)
            return None

    def restart_application(self, project_id: str, app_id: str) -> bool:
        """
        Restart an existing CML application via the CML restart endpoint.

        Use this to revive a stopped/failed application without recreating it.
        The application must already exist (not deleted).

        Args:
            project_id: Project ID
            app_id: Application ID

        Returns:
            True if the API accepted the restart request (200/202).
        """
        url = f"{self.base_url}/projects/{project_id}/applications/{app_id}/restart"

        if self.verbose:
            logger.debug(f"Restarting application: POST {url}")

        response = self.session.post(url)
        return response.status_code in [200, 202]


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
        self.worker_groups: List[WorkerGroupConfig] = []
        self.head_address: Optional[str] = None
        self.head_url: Optional[str] = None  # public CAI application URL

        # Initialize CML API client (internal implementation)
        self.cml_client = CMLAPIClient(
            host=cml_host,
            api_key=cml_api_key,
            verbose=verbose
        )
        logger.info(f"✅ Connected to CML instance: {cml_host}")



    def start_cluster(
        self,
        worker_groups: List[WorkerGroupConfig],
        head_app_name: str = "ray-cluster-head",
        head_cpu: int = 8,
        head_memory: int = 32,
        ray_port: int = 6379,
        dashboard_port: int = 8265,
        head_runtime_identifier: Optional[str] = None,
        worker_runtime_identifier: Optional[str] = None,
        head_script_path: Optional[str] = None,
        wait_ready: bool = True,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """
        Start Ray cluster using CAI applications.

        The head node is CPU-only.  Workers are organised into one or more
        WorkerGroupConfig groups — each group registers a unique
        "node_type:<node_type>" Ray resource label so coordinator._detect_node_type()
        and NodeAffinitySchedulingStrategy can target specific node types.

        Args:
            worker_groups: One or more WorkerGroupConfig objects describing
                worker node pools.  Each group's script_path must already be
                set by create_ray_launcher_scripts().
            head_cpu: CPU cores for the head node.
            head_memory: Memory in GB for the head node.
            ray_port: Ray GCS server port.
            dashboard_port: Ray dashboard port.
            head_runtime_identifier: Docker runtime for the head node. Required.
            worker_runtime_identifier: Default Docker runtime for worker groups
                that do not specify their own runtime_identifier.
            head_script_path: Path to head node launcher script. Required.
            wait_ready: Wait for all applications to reach running state.
            timeout: Maximum seconds to wait per application.

        Returns:
            Dictionary with cluster information including worker_groups metadata.

        Raises:
            RuntimeError: If required parameters are missing or a node fails to start.
        """
        if not head_runtime_identifier:
            raise RuntimeError("head_runtime_identifier is required")
        if not head_script_path:
            raise RuntimeError(
                "head_script_path is required. "
                "Run create_ray_launcher_scripts() before calling start_cluster()."
            )
        if worker_groups is None:
            worker_groups = []

        for group in worker_groups:
            if not group.script_path:
                raise RuntimeError(
                    f"Worker group '{group.name}' has no script_path. "
                    "Run create_ray_launcher_scripts() before calling start_cluster()."
                )
            if not (group.runtime_identifier or worker_runtime_identifier):
                raise RuntimeError(
                    f"Worker group '{group.name}' has no runtime_identifier and "
                    "no default worker_runtime_identifier was provided."
                )

        # Ensure every group has a concrete runtime_identifier stored on it so
        # that the value is preserved when cluster_info is serialised to JSON
        # and later used by CAIService._group_from_cluster_info() → launch_worker().
        for g in worker_groups:
            if not g.runtime_identifier and worker_runtime_identifier:
                g.runtime_identifier = worker_runtime_identifier

        self.worker_groups = worker_groups

        logger.info("🚀 Starting Ray cluster on CAI...")
        logger.info(f"   Head node : {head_cpu}CPU, {head_memory}GB RAM, 0GPU")
        for g in worker_groups:
            logger.info(
                f"   Group '{g.name}' [{g.node_type}]: "
                f"{g.count} × {g.cpu}CPU, {g.memory}GB RAM, {g.gpus}GPU"
            )

        try:
            # ── Head node ────────────────────────────────────────────────────
            logger.info("🎯 Creating head node application...")
            head_app = self.cml_client.create_application(
                project_id=self.project_id,
                name=head_app_name,
                script=head_script_path,
                cpu=head_cpu,
                memory=head_memory,
                runtime_identifier=head_runtime_identifier,
                subdomain=head_app_name,
                bypass_authentication=True,
            )
            self.head_app_id = head_app.id
            logger.info(f"✅ Head node application created: {head_app.id}")

            if wait_ready:
                logger.info("⏳ Waiting for head node to start...")
                if not self._wait_for_application(head_app.id, timeout=timeout):
                    raise RuntimeError("Head node failed to start")

                head_app = self.cml_client.get_application(self.project_id, head_app.id)
                head_url = head_app.metadata.get('url') or head_app.subdomain
                if head_url:
                    if not head_url.startswith('http'):
                        head_url = f"https://{head_url}"
                    self.head_url = head_url.rstrip('/')
                    logger.info(f"✅ Head node CML app running. Public URL: {self.head_url}")
                logger.info("   GCS address will be resolved once the Management API is ready.")

            # Workers are launched by the caller via Management API
            # (POST /api/v1/resources/nodes/add) once the head is healthy.

            cluster_info = {
                'status': 'running',
                'head_app_id': self.head_app_id,
                'head_address': self.head_address,
                'head_url': self.head_url,
                'worker_app_ids': self.worker_app_ids,
                'num_workers': len(self.worker_app_ids),
                'worker_groups': [
                    {
                        'name':               g.name,
                        'node_type':          g.node_type,
                        'count':              g.count,
                        'cpu':                g.cpu,
                        'memory':             g.memory,
                        'gpus':               g.gpus,
                        'accelerator_type':   g.accelerator_type,
                        'node_label':         g.node_label,
                        'script_path':        g.script_path,
                        'runtime_identifier': g.runtime_identifier,
                    }
                    for g in worker_groups
                ],
                'configuration': {
                    'head': {'cpu': head_cpu, 'memory': head_memory, 'num_gpus': 0},
                    'ray_port': ray_port,
                    'dashboard_port': dashboard_port,
                },
            }

            logger.info("=" * 60)
            logger.info("✅ Ray cluster started successfully!")
            logger.info(f"   Head: {self.head_address}")
            logger.info(f"   Total workers: {len(self.worker_app_ids)}")
            logger.info(f"   Total nodes  : {1 + len(self.worker_app_ids)}")
            logger.info("=" * 60)

            return cluster_info

        except Exception as e:
            logger.error(f"Failed to start cluster: {e}")
            self.stop_cluster()
            raise

    def _get_gcs_address(self, ray_port: int, timeout: int = 120) -> str:
        """
        Query the head node's Management API for its internal GCS address.

        The Management API returns the pod's CDSW_IP_ADDRESS which is
        reachable directly on non-HTTPS ports from within the CML cluster.
        Falls back to the public hostname if the endpoint is unavailable.

        Args:
            ray_port: Ray GCS port.
            timeout:  Maximum seconds to wait for the endpoint.

        Returns:
            "IP:port" string suitable for `ray start --address`.
        """
        import time
        import urllib.request
        import json as _json

        if not self.head_url:
            logger.warning("head_url not set — cannot query GCS address endpoint")
            return f"ray-cluster-head:{ray_port}"

        url = f"{self.head_url}/api/v1/cluster/gcs-address"
        start = time.time()
        while time.time() - start < timeout:
            try:
                with urllib.request.urlopen(url, timeout=10) as resp:
                    data = _json.loads(resp.read())
                    addr = data.get("gcs_address", "")
                    if addr:
                        return addr
            except Exception:
                pass
            time.sleep(10)

        # Fallback: extract hostname from public URL (may not work for GCS port)
        hostname = self.head_url.split('://', 1)[-1].split('/')[0]
        fallback = f"{hostname}:{ray_port}"
        logger.warning(
            f"Could not retrieve GCS address from Management API — "
            f"falling back to {fallback} (workers may fail to connect)"
        )
        return fallback

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
                app = self.cml_client.get_application(
                    self.project_id,
                    app_id
                )

                status = app.status.lower()

                if "running" in status:
                    return True
                elif any(s in status for s in ["failed", "stopped", "error"]):
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

        # Delete all worker nodes
        for worker_id in self.worker_app_ids:
            try:
                logger.info(f"   Deleting worker: {worker_id}")
                self.cml_client.delete_application(self.project_id, worker_id)
                stopped_apps.append(worker_id)
            except Exception as e:
                logger.error(f"   Error deleting worker {worker_id}: {e}")
                errors.append(str(e))

        # Delete head node
        if self.head_app_id:
            try:
                logger.info(f"   Deleting head node: {self.head_app_id}")
                self.cml_client.delete_application(self.project_id, self.head_app_id)
                stopped_apps.append(self.head_app_id)
            except Exception as e:
                logger.error(f"   Error deleting head node: {e}")
                errors.append(str(e))

        # Clear state
        self.head_app_id = None
        self.head_address = None
        self.head_url = None
        self.worker_app_ids = []
        self.worker_groups = []

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
            head_app = self.cml_client.get_application(
                self.project_id,
                self.head_app_id
            )

            # Check worker nodes status
            worker_statuses = []
            for worker_id in self.worker_app_ids:
                try:
                    worker_app = self.cml_client.get_application(
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
                'running': 'running' in head_app.status.lower(),
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

    # ── Per-application helpers ───────────────────────────────────────────────

    def launch_worker(
        self,
        group: WorkerGroupConfig,
        name: str = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Launch a single worker application from a WorkerGroupConfig.

        The group's script_path and runtime_identifier must already be set
        (populated by create_ray_launcher_scripts() or loaded from
        ray_cluster_info.json).  Pass environment to inject RAY_HEAD_ADDRESS
        when the worker script was rendered without a baked-in head address.

        Args:
            group: Worker group configuration (node_type, cpu, memory, gpus, …).
            name: CAI application name; defaults to "ray-<group.name>-<timestamp>".
            environment: Optional env vars for the application (e.g. RAY_HEAD_ADDRESS).

        Returns:
            Dict with keys: id, name, status.
        """
        if not group.script_path:
            raise RuntimeError(
                f"Worker group '{group.name}' has no script_path. "
                "Ensure create_ray_launcher_scripts() was called and "
                "the result saved to ray_cluster_info.json."
            )
        rt = group.runtime_identifier
        if not rt:
            raise RuntimeError(
                f"Worker group '{group.name}' has no runtime_identifier."
            )

        app_name = name or f"ray-{group.name}-{int(time.time())}"
        subdomain = app_name.replace("_", "-").lower()

        env = dict(environment) if environment else {}
        if group.node_label:
            # Take the first key/value pair as the K8s node-selector.
            # CML reads NODE_SELECTOR_KEY/VALUE to steer the pod onto the
            # matching K8s node before Ray even starts.  The label key is
            # infra-provider-specific (Liftie, EKS, GKE, vanilla K8s) and is
            # supplied by the user in ray_cluster_config.yaml or at add-node time.
            _nl_key, _nl_val = next(iter(group.node_label.items()))
            env["NODE_SELECTOR_KEY"]   = _nl_key
            env["NODE_SELECTOR_VALUE"] = _nl_val

        app = self.cml_client.create_application(
            project_id=self.project_id,
            name=app_name,
            script=group.script_path,
            cpu=group.cpu,
            memory=group.memory,
            runtime_identifier=rt,
            subdomain=subdomain,
            bypass_authentication=True,
            num_gpus=group.gpus,
            environment=env or None,
        )
        self.worker_app_ids.append(app.id)
        logger.info(f"✅ Launched worker '{app_name}': {app.id}  [node_type:{group.node_type}]")
        return {"id": app.id, "name": app_name, "status": app.status}

    def stop_application(self, app_id: str) -> bool:
        """
        Delete a single CAI application and remove it from tracked workers.

        Note: CML has no explicit "pause" — stop means delete.  To bring the
        application back without recreating from scratch, use restart_application()
        while it is still in a stopped/failed (not yet deleted) state.

        Args:
            app_id: CML application ID to delete.

        Returns:
            True if the API returned 200/204.
        """
        success = self.cml_client.delete_application(self.project_id, app_id)
        if app_id in self.worker_app_ids:
            self.worker_app_ids.remove(app_id)
        if success:
            logger.info(f"✅ Application deleted: {app_id}")
        else:
            logger.warning(f"⚠️  delete_application returned False for {app_id}")
        return success

    def restart_application(self, app_id: str) -> bool:
        """
        Restart an existing (stopped/failed) CAI application without recreating it.

        This is the counterpart to stop_application() when the application has
        not been deleted — e.g. after a crash or a manual stop via the CML UI.
        To add a brand-new worker use launch_worker() instead.

        Args:
            app_id: CML application ID to restart.

        Returns:
            True if the CML restart API accepted the request.
        """
        success = self.cml_client.restart_application(self.project_id, app_id)
        if success:
            logger.info(f"✅ Application restart requested: {app_id}")
        else:
            logger.warning(f"⚠️  restart_application returned False for {app_id}")
        return success

    def list_applications(self) -> List[Dict[str, Any]]:
        """
        List all CAI applications in the project.

        Returns:
            List of dicts with keys: id, name, status, subdomain.
        """
        apps = self.cml_client.list_applications(self.project_id)
        return [
            {
                "id":        a.id,
                "name":      a.name,
                "status":    a.status,
                "subdomain": a.subdomain,
            }
            for a in apps
        ]

    def get_application(self, app_id: str) -> Dict[str, Any]:
        """
        Get a single CAI application by ID (uses self.project_id).

        Args:
            app_id: CML application ID.

        Returns:
            Dict with keys: id, name, status, subdomain, metadata.
        """
        app = self.cml_client.get_application(self.project_id, app_id)
        return {
            "id":        app.id,
            "name":      app.name,
            "status":    app.status,
            "subdomain": app.subdomain,
            "metadata":  app.metadata,
        }
