"""Service for CML/CAI operations."""

import fcntl
import json
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional
import logging

from ray_serve_cai.cai_cluster import CAIClusterManager, WorkerGroupConfig

logger = logging.getLogger(__name__)

# Path where launch_ray_cluster.py saves cluster state.
_CLUSTER_INFO_PATH = Path("/home/cdsw/ray_cluster_info.json")


class CAIService:
    """Handles CML/CAI platform operations."""

    def __init__(self, project_id: str, cml_host: str = None, api_key: str = None):
        """
        Initialize CAI service.

        Args:
            project_id: CML project ID
            cml_host: CML host URL (defaults to CML_HOST env var)
            api_key: CML API key (defaults to CML_API_KEY or CDSW_APIV2_KEY env var)
        """
        self.project_id = project_id
        self.cml_host = cml_host or os.environ.get("CML_HOST")
        self.api_key = (
            api_key
            or os.environ.get("CML_API_KEY")
            or os.environ.get("CDSW_APIV2_KEY")
        )

        if not self.cml_host or not self.api_key:
            raise ValueError(
                "CML_HOST and CML_API_KEY (or CDSW_APIV2_KEY) must be "
                "provided or set in the environment"
            )

        self.manager = CAIClusterManager(
            cml_host=self.cml_host,
            cml_api_key=self.api_key,   # was incorrectly `api_key=` before
            project_id=self.project_id,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_cluster_info(self) -> Dict[str, Any]:
        """Load persisted cluster info from disk."""
        if not _CLUSTER_INFO_PATH.exists():
            raise RuntimeError(
                f"Cluster info not found at {_CLUSTER_INFO_PATH}. "
                "Ensure the Ray cluster has been started."
            )
        with open(_CLUSTER_INFO_PATH) as f:
            return json.load(f)

    def _save_cluster_info(self, info: Dict[str, Any]) -> None:
        """Atomically persist cluster info (tmp + os.replace — safe on NFS)."""
        tmp = _CLUSTER_INFO_PATH.with_suffix(_CLUSTER_INFO_PATH.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(info, f, indent=2)
        os.replace(tmp, _CLUSTER_INFO_PATH)  # atomic on POSIX/NFS

    def _group_from_cluster_info(self, node_type: str) -> WorkerGroupConfig:
        """
        Reconstruct a WorkerGroupConfig for the given node_type from saved
        cluster info.  Raises RuntimeError if the node_type is not found.
        """
        cluster_info = self._load_cluster_info()
        for g in cluster_info.get("worker_groups", []):
            if g["node_type"] == node_type:
                return WorkerGroupConfig(
                    name=g["name"],
                    node_type=g["node_type"],
                    count=g["count"],
                    cpu=g["cpu"],
                    memory=g["memory"],
                    gpus=g.get("gpus", 0),
                    accelerator_type=g.get("accelerator_type"),
                    node_label=g.get("node_label"),
                    script_path=g.get("script_path"),
                    runtime_identifier=g.get("runtime_identifier"),
                )
        available = [g["node_type"] for g in cluster_info.get("worker_groups", [])]
        raise RuntimeError(
            f"No worker group with node_type='{node_type}' found in cluster info. "
            f"Available: {available}"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    @staticmethod
    def _group_to_dict(g: WorkerGroupConfig) -> Dict[str, Any]:
        """Serialise a group to the cluster_info worker_groups entry shape."""
        return {
            "name":               g.name,
            "node_type":          g.node_type,
            "count":              g.count,
            "cpu":                g.cpu,
            "memory":             g.memory,
            "gpus":               g.gpus,
            "accelerator_type":   g.accelerator_type,
            "node_label":         g.node_label,
            "script_path":        g.script_path,
            "runtime_identifier": g.runtime_identifier,
        }

    def list_worker_groups(self) -> list:
        """Return the worker groups (node_types) known to the running cluster."""
        return self._load_cluster_info().get("worker_groups", [])

    def define_node_type(
        self,
        node_type: str,
        cpu: int,
        memory: int,
        gpus: int = 0,
        accelerator_type: str = None,
        node_label: dict = None,
        runtime_identifier: str = None,
        count: int = 0,
        name: str = None,
    ) -> Dict[str, Any]:
        """Register a new worker group at runtime — no cluster relaunch.

        Renders the group's launcher (baking in its node_type) and appends it to
        cluster_info.worker_groups so the existing add-node path can scale it.
        Registration only; launching workers stays with POST /resources/nodes.

        Raises ValueError if node_type already exists.
        """
        # Lazy import: launch_ray_cluster runs a venv re-exec guard at import
        # time, so only pull it in when actually defining a type (mirrors
        # environments.py's lazy setup import).
        from cai_integration.launch_ray_cluster import render_worker_launcher

        lock_path = _CLUSTER_INFO_PATH.with_suffix(_CLUSTER_INFO_PATH.suffix + ".lock")
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)  # ponytail: coarse lock; writes are rare/admin
            info = self._load_cluster_info()
            groups = info.setdefault("worker_groups", [])
            if any(g["node_type"] == node_type for g in groups):
                raise ValueError(f"node_type '{node_type}' already exists")

            group = WorkerGroupConfig(
                name=name or node_type,
                node_type=node_type,
                count=count,
                cpu=cpu,
                memory=memory,
                gpus=gpus,
                accelerator_type=accelerator_type,
                node_label=node_label,
                runtime_identifier=runtime_identifier,
            )
            cfg = info.get("configuration", {})
            render_worker_launcher(
                group,
                head_address=info.get("head_address"),
                ray_port=cfg.get("ray_port", 6379),
                metrics_port=cfg.get("metrics_port", 9090),
            )
            groups.append(self._group_to_dict(group))
            self._save_cluster_info(info)
            logger.info(f"Defined node_type '{node_type}' (script: {group.script_path})")
            return groups[-1]
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def remove_worker_group(self, node_type: str) -> Dict[str, Any]:
        """Remove a worker group definition from cluster_info.

        Leaves the rendered launcher script on disk (cheap, harmless).
        Raises ValueError if the node_type is not found.
        """
        lock_path = _CLUSTER_INFO_PATH.with_suffix(_CLUSTER_INFO_PATH.suffix + ".lock")
        lock_fd = open(lock_path, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            info = self._load_cluster_info()
            groups = info.get("worker_groups", [])
            kept = [g for g in groups if g["node_type"] != node_type]
            if len(kept) == len(groups):
                raise ValueError(f"node_type '{node_type}' not found")
            info["worker_groups"] = kept
            self._save_cluster_info(info)
            logger.info(f"Removed node_type '{node_type}'")
            return {"status": "removed", "node_type": node_type}
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def create_worker_node(
        self,
        node_type: str,
        cpu: int = None,
        memory: int = None,
        gpus: int = None,
        runtime_identifier: str = None,
        node_label: dict = None,
        ray_labels: dict = None,
    ) -> Dict[str, Any]:
        """
        Launch a new worker node as a CML application.

        The worker group configuration (script, runtime, resources) is loaded
        from the persisted cluster info so callers only need to specify the
        node_type.  cpu / memory / gpus override the group defaults when
        provided.

        Args:
            node_type: Logical node type label (e.g. "cpu-worker",
                       "t4_gpu_node_single").  Must match a group that was
                       used when the cluster was started.
            cpu: Override CPU cores (uses group default when None).
            memory: Override memory in GB (uses group default when None).
            gpus: Override GPU count (uses group default when None).

        Returns:
            Dict with app_id, app_name, node_type, cpu, memory, gpus.
        """
        group = self._group_from_cluster_info(node_type)

        # Apply any overrides
        if cpu is not None:
            group.cpu = cpu
        if memory is not None:
            group.memory = memory
        if gpus is not None:
            group.gpus = gpus
        if runtime_identifier is not None:
            group.runtime_identifier = runtime_identifier

        worker_name = f"ray-{group.name}-{int(time.time())}"

        # The worker launcher script reads these env vars at runtime:
        #   RAY_HEAD_ADDRESS — GCS address when not baked into the script
        #   WORKER_CPUS / WORKER_MEMORY_GB / WORKER_GPUS — passed to the
        #     worker info server (worker_app.py) so GET /info reports the
        #     correct resources, especially when cpu/memory are overridden.
        cluster_info = self._load_cluster_info()
        env: dict = {
            "WORKER_CPUS":      str(group.cpu),
            "WORKER_MEMORY_GB": str(group.memory),
            "WORKER_GPUS":      str(group.gpus),
        }
        head_address = cluster_info.get("head_address")
        if head_address:
            env["RAY_HEAD_ADDRESS"] = head_address

        # K8s node placement: if the caller supplies a node_label override it takes
        # precedence over whatever was baked into the worker group at cluster-start
        # time.  launch_worker() in cai_cluster.py derives NODE_SELECTOR_KEY/VALUE
        # from group.node_label; we override that group field here so the same
        # derivation path is used regardless of whether the label came from YAML or
        # from the API request.
        if node_label:
            group.node_label = node_label

        # Build Ray resource labels:
        #   1. built-in:  node_type:<type> + accelerator_type:<GPU>
        #   2. derived:   short-key version of each node_label entry so actors can
        #                 target the same node the pod landed on without knowing the
        #                 full K8s label key
        #      e.g.  "liftie.cloudera.com/instance-group-id": "ig-n4bsnv8r"
        #            → "instance-group-id:ig-n4bsnv8r": 1
        #   3. explicit:  caller-supplied ray_labels override/extend the above
        _ray: dict = {f"node_type:{group.node_type}": 1}
        if group.accelerator_type:
            _ray[f"accelerator_type:{group.accelerator_type}"] = 1
        _nl = node_label or group.node_label
        if _nl:
            for _k, _v in _nl.items():
                _short = _k.split("/")[-1]   # strip domain prefix
                _ray[f"{_short}:{_v}"] = 1
        if ray_labels:
            _ray.update(ray_labels)
        env["RAY_EXTRA_RESOURCES"] = json.dumps(_ray)

        app_info = self.manager.launch_worker(
            group=group,
            name=worker_name,
            environment=env or None,
        )

        logger.info(f"Created worker node: {worker_name}  [node_type:{node_type}]")
        return {
            "status":    "success",
            "app_id":    app_info["id"],
            "app_name":  worker_name,
            "node_type": node_type,
            "cpu":       group.cpu,
            "memory":    group.memory,
            "gpus":      group.gpus,
        }

    def delete_application(self, app_id: str) -> Dict[str, Any]:
        """
        Stop a CML application by ID (worker node or generic workload).

        Args:
            app_id: CML application ID

        Returns:
            Dict with status and app_id.

        Raises:
            RuntimeError: If the CML API returns a non-success response.
        """
        success = self.manager.stop_application(app_id)
        if not success:
            raise RuntimeError(
                f"CML API returned failure when deleting application {app_id}. "
                "The application may not exist or the API key may lack permission."
            )
        logger.info(f"Deleted application: {app_id}")
        return {"status": "success", "app_id": app_id}

    def launch_cai_application(
        self,
        name: str,
        script: str,
        cpu: int,
        memory: int,
        gpus: int = 0,
        runtime_identifier: str = None,
        environment: dict = None,
        bypass_authentication: bool = True,
    ) -> Dict[str, Any]:
        """
        Launch a generic CML application.

        Args:
            name: CML application name.
            script: Script path to run (relative to /home/cdsw).
            cpu: CPU cores.
            memory: Memory in GB.
            gpus: Number of GPUs (0 for CPU-only).
            runtime_identifier: Docker runtime. Falls back to the cluster default
                from ray_cluster_info.json when None.
            environment: Optional env vars injected at start.
            bypass_authentication: Allow unauthenticated access.

        Returns:
            Dict with app_id, app_name, status.
        """
        if runtime_identifier is None:
            try:
                cluster_info = self._load_cluster_info()
                runtime_identifier = (
                    cluster_info.get("worker_runtime_identifier")
                    or cluster_info.get("head_runtime_identifier")
                )
            except Exception:
                pass

        if not runtime_identifier:
            raise RuntimeError(
                "runtime_identifier is required but was not provided and could not "
                "be resolved from ray_cluster_info.json."
            )

        subdomain = name.replace("_", "-").lower()
        app_info = self.manager.cml_client.create_application(
            project_id=self.project_id,
            name=name,
            script=script,
            cpu=cpu,
            memory=memory,
            runtime_identifier=runtime_identifier,
            subdomain=subdomain,
            bypass_authentication=bypass_authentication,
            num_gpus=gpus,
            environment=environment,
        )
        logger.info(f"Launched CML application: {name}  [{app_info.id}]")
        return {
            "status":   "success",
            "app_id":   app_info.id,
            "app_name": name,
            "app_status": app_info.status,
        }

    def list_applications(self) -> list:
        """
        List all CML applications in the project.

        Returns:
            List of application info dicts.
        """
        return self.manager.list_applications()

    def get_application(self, app_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific CML application.

        Args:
            app_id: Application ID

        Returns:
            Application info dict, or None on error.
        """
        try:
            return self.manager.get_application(app_id)
        except Exception as e:
            logger.error(f"Failed to get application {app_id}: {e}")
            return None
