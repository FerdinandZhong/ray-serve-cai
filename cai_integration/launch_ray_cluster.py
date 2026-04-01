#!/usr/bin/env python3
"""
Launch Ray cluster using CAI (CML) applications.

This script:
1. Loads Ray cluster configuration
2. Creates head node application
3. Creates worker node applications
4. Monitors cluster startup
5. Outputs connection information

Run this via the bash wrapper: cai_integration/launch_ray_cluster.sh
The wrapper script handles virtual environment activation.

Usage:
    bash cai_integration/launch_ray_cluster.sh
    # or directly with Python (if venv is already activated)
    python cai_integration/launch_ray_cluster.py
    # or via Python job wrapper (simulates CAI)
    python cai_integration/launch_ray_cluster_job.py
"""

import json
import logging
import os
import sys
import time
import yaml
from pathlib import Path

# Configure logging before anything else so that logger calls from cai_cluster
# and other modules are visible.  Write to stdout (same stream as print) so
# log lines and print lines appear in the correct order in the job log.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s -- %(message)s",
    stream=sys.stdout,
)

# ---------------------------------------------------------------------------
# Re-exec with venv Python if we're not already inside it.
# This lets the script be invoked directly (e.g. as a CML job entry point)
# without requiring the caller to activate the venv first.
# ---------------------------------------------------------------------------
_VENV_PYTHON = Path("/home/cdsw/.venv/bin/python")

if _VENV_PYTHON.exists() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)

from jinja2 import Environment, FileSystemLoader

# Add parent directory to path for imports
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir.parent))

from ray_serve_cai.cai_cluster import CAIClusterManager, WorkerGroupConfig

# Jinja2 templates for generated launcher scripts
TEMPLATE_DIR = script_dir / "templates"


def create_ray_launcher_scripts(
    worker_groups: list,
    head_address: str = None,
    ray_port: int = 6379,
    dashboard_port: int = 8265,
    metrics_port: int = 9090,
    mgmt_cpu: int = 2,
    mgmt_memory_gb: int = 8,
    proxy_health_check_period_s: float = None,
    proxy_health_check_timeout_s: float = None,
    proxy_ready_check_timeout_s: float = None,
    proxy_min_draining_period_s: float = None,
) -> tuple:
    """
    Render and write the head launcher and one worker launcher per group.

    Templates live in cai_integration/templates/:
        ray_head_launcher.py.j2   -- full startup: Ray + Management API + nginx
        ray_worker_launcher.py.j2 -- connects to head GCS, registers node_type label

    Each WorkerGroupConfig in worker_groups gets its own launcher script so
    that each group's node_type label is baked in at render time.  The
    group's script_path field is updated in-place.

    Args:
        worker_groups:  List of WorkerGroupConfig; script_path is set here.
        head_address:   Ray GCS address of head (baked into worker scripts).
                        If None, workers read RAY_HEAD_ADDRESS at runtime.
        ray_port:       Ray GCS port.
        dashboard_port: Ray Dashboard port (internal).
        mgmt_cpu:       CPUs for the Management API Ray Serve deployment.
        mgmt_memory_gb: Memory (GB) for the Management API deployment.

    Returns:
        Tuple of (head_script_path: str, worker_groups: list) where each
        group's script_path has been populated.
    """
    project_dir = Path("/home/cdsw")
    venv_python = project_dir / ".venv" / "bin" / "python"

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
    )

    # -- Head launcher -------------------------------------------------------
    head_context = {
        "venv_python":    str(venv_python),
        "project_dir":    str(project_dir),
        "ray_port":       ray_port,
        "dashboard_port": dashboard_port,
        "metrics_port":   metrics_port,
        "mgmt_cpu":       mgmt_cpu,
        "mgmt_memory_gb": mgmt_memory_gb,
        "proxy_health_check_period_s":  proxy_health_check_period_s,
        "proxy_health_check_timeout_s": proxy_health_check_timeout_s,
        "proxy_ready_check_timeout_s":  proxy_ready_check_timeout_s,
        "proxy_min_draining_period_s":  proxy_min_draining_period_s,
    }
    head_script_path = project_dir / "ray_head_launcher.py"
    head_script_path.write_text(
        env.get_template("ray_head_launcher.py.j2").render(**head_context)
    )
    head_script_path.chmod(0o755)
    print(f"Created head launcher   : {head_script_path}")
    print(f"  mgmt_cpu={mgmt_cpu}, mgmt_memory_gb={mgmt_memory_gb}")

    # -- Worker launchers (one per group) ------------------------------------
    worker_template = env.get_template("ray_worker_launcher.py.j2")
    for group in worker_groups:
        worker_context = {
            "venv_python":      str(venv_python),
            "project_dir":      str(project_dir),
            "head_address":     head_address,   # None → reads RAY_HEAD_ADDRESS at runtime
            "ray_port":         ray_port,
            "metrics_port":     metrics_port,
            "node_type":        group.node_type,
            "worker_memory_gb": group.memory,
        }
        # Sanitise group name for use as a filename component.
        safe_name = group.name.replace("-", "_").replace(" ", "_")
        script_path = project_dir / f"ray_worker_{safe_name}_launcher.py"
        script_path.write_text(worker_template.render(**worker_context))
        script_path.chmod(0o755)
        group.script_path = str(script_path)   # write back into the dataclass
        print(f"Created worker launcher : {script_path}  [node_type:{group.node_type}]")

    return str(head_script_path), worker_groups



def load_config():
    """
    Load Ray cluster configuration.

    Priority order (highest wins):
      1. Environment variables
      2. ray_cluster_config.yaml  (ray_cluster section)
      3. Built-in defaults

    management_api_cpu / management_api_memory are optional; when absent
    they default to half of the head node resources (computed in main()).
    Head node has no GPUs — only workers carry GPU resources.
    """
    # ── Step 1: built-in defaults ────────────────────────────────────────────
    _STD  = "docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-standard:2026.01.1-b6"
    _CUDA = "docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-cuda:2026.01.1-b6"
    config = {
        'num_workers':              1,
        'head_cpu':                 8,
        'head_memory':              32,
        'worker_cpu':               16,
        'worker_memory':            32,
        'worker_gpus':              0,
        'worker_node_type':         None,
        'ray_port':                 6379,
        'dashboard_port':           8265,
        'metrics_port':             9090,
        'management_api_cpu':       None,
        'management_api_memory':    None,
        'worker_groups':            None,
        'head_app_name':            'ray-cluster-head',
        'head_runtime_identifier':  _STD,
        'worker_runtime_identifier': _CUDA,
        # Ray Serve proxy tuning — None means "use Ray's built-in default"
        'proxy_health_check_period_s':  None,
        'proxy_health_check_timeout_s': None,
        'proxy_ready_check_timeout_s':  None,
        'proxy_min_draining_period_s':  None,
    }

    # ── Step 2: YAML overrides defaults ─────────────────────────────────────
    config_path = Path(__file__).parent.parent / "ray_cluster_config.yaml"
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_config = yaml.safe_load(f) or {}
            config.update(file_config.get('ray_cluster', {}))
            ray_serve_section = file_config.get('ray_serve', {}) or {}
            for key in ('proxy_health_check_period_s', 'proxy_health_check_timeout_s',
                        'proxy_ready_check_timeout_s', 'proxy_min_draining_period_s'):
                if key in ray_serve_section:
                    config[key] = float(ray_serve_section[key])
            cai_section = file_config.get('cai', {}) or {}
            if 'head_app_name' in cai_section:
                config['head_app_name'] = cai_section['head_app_name']
            if 'head_runtime_identifier' in cai_section:
                config['head_runtime_identifier'] = cai_section['head_runtime_identifier']
            if 'worker_runtime_identifier' in cai_section:
                config['worker_runtime_identifier'] = cai_section['worker_runtime_identifier']
            print(f"Loaded configuration from {config_path}")
        except Exception as e:
            print(f"Warning: could not load config file: {e}")

    # ── Step 3: env vars override everything (highest priority) ─────────────
    # Only apply when the variable is actually set so that an absent env var
    # does not silently zero-out a value supplied by the YAML.
    _env_int = [
        ('RAY_NUM_WORKERS',    'num_workers'),
        ('RAY_HEAD_CPU',       'head_cpu'),
        ('RAY_HEAD_MEMORY',    'head_memory'),
        ('RAY_WORKER_CPU',     'worker_cpu'),
        ('RAY_WORKER_MEMORY',  'worker_memory'),
        ('RAY_WORKER_GPUS',    'worker_gpus'),
        ('RAY_PORT',           'ray_port'),
        ('RAY_DASHBOARD_PORT', 'dashboard_port'),
        ('RAY_METRICS_PORT',   'metrics_port'),
    ]
    for env_var, key in _env_int:
        val = os.environ.get(env_var)
        if val is not None:
            config[key] = int(val)

    val = os.environ.get('RAY_WORKER_NODE_TYPE')
    if val is not None:
        config['worker_node_type'] = val

    _env_float = [
        ('RAY_SERVE_PROXY_HEALTH_CHECK_PERIOD_S',  'proxy_health_check_period_s'),
        ('RAY_SERVE_PROXY_HEALTH_CHECK_TIMEOUT_S', 'proxy_health_check_timeout_s'),
        ('RAY_SERVE_PROXY_READY_CHECK_TIMEOUT_S',  'proxy_ready_check_timeout_s'),
        ('RAY_SERVE_PROXY_MIN_DRAINING_PERIOD_S',  'proxy_min_draining_period_s'),
    ]
    for env_var, key in _env_float:
        val = os.environ.get(env_var)
        if val is not None:
            config[key] = float(val)

    return config


def _wait_for_management_api(cluster_info: dict, timeout: int = 300) -> str:
    """
    Poll the head node's /health endpoint until it responds 200.

    The Management API is started automatically by the head node launcher
    (ray_head_launcher.py) — this function just waits for it to be ready
    and returns the public base URL.

    Args:
        cluster_info: Must contain 'head_url' (the CAI application public URL).
        timeout: Maximum seconds to wait.

    Returns:
        Public base URL of the Management API, or None on timeout.
    """
    import urllib.request

    head_url = cluster_info.get("head_url", "").rstrip("/")
    if not head_url:
        print("   head_url not available — skipping health check")
        return None

    health_url = f"{head_url}/api/health"
    print(f"   Polling {health_url} ...")

    start = time.time()
    attempt = 0
    while time.time() - start < timeout:
        attempt += 1
        try:
            with urllib.request.urlopen(health_url, timeout=5) as resp:
                if resp.status == 200:
                    elapsed = int(time.time() - start)
                    print(f"   Management API healthy (attempt {attempt}, {elapsed}s)")
                    return head_url
        except Exception:
            pass
        time.sleep(10)

    print(f"   Timeout: management API not ready after {timeout}s")
    return None


def main():
    """Main launch function."""
    print("=" * 70)
    print("🚀 Launching Ray Cluster on CML")
    print("=" * 70)

    # Get CML configuration from environment
    # Support both CML_* and CDSW_* environment variables
    cml_host = os.environ.get("CML_HOST")
    if not cml_host:
        # Construct from CDSW_DOMAIN if CML_HOST not set
        cdsw_domain = os.environ.get("CDSW_DOMAIN")
        if cdsw_domain:
            cml_host = f"https://{cdsw_domain}"

    cml_api_key = os.environ.get("CML_API_KEY") or os.environ.get("CDSW_APIV2_KEY")
    project_id = os.environ.get("CDSW_PROJECT_ID") or os.environ.get("CML_PROJECT_ID")

    # Load cluster configuration first so runtime defaults come from the YAML.
    ray_config = load_config()

    # Runtime identifiers: env var > ray_cluster_config.yaml > built-in defaults
    head_runtime   = os.environ.get("HEAD_RUNTIME_IDENTIFIER",   ray_config['head_runtime_identifier'])
    worker_runtime = os.environ.get("WORKER_RUNTIME_IDENTIFIER", ray_config['worker_runtime_identifier'])

    print("\n📋 Configuration:")
    print(f"   CML Host: {cml_host}")
    print(f"   Project ID: {project_id}")
    print(f"   Head Runtime: {head_runtime[:80]}...")
    print(f"   Worker Runtime: {worker_runtime[:80]}...")

    if not all([cml_host, cml_api_key, project_id]):
        print("\n❌ Missing required environment variables:")
        print("   Required: CML_HOST, CML_API_KEY, CML_PROJECT_ID (or CDSW_PROJECT_ID)")
        return 1

    head_app_name = ray_config['head_app_name']
    # Derive head URL from app name + CDSW_DOMAIN — deterministic, no CML API call needed.
    cdsw_domain = os.environ.get("CDSW_DOMAIN", "").strip()
    head_url_from_domain = (
        f"https://{head_app_name}.{cdsw_domain}" if cdsw_domain else None
    )
    print(f"   Head App Name : {head_app_name}")
    print(f"   Head URL      : {head_url_from_domain or '(CDSW_DOMAIN not set)'}")

    # Compute management API resources — default to half of head node resources.
    # Head node is CPU-only; management API inherits that constraint.
    head_cpu    = ray_config['head_cpu']
    head_memory = ray_config['head_memory']
    mgmt_cpu    = ray_config['management_api_cpu']    or max(1, head_cpu // 2)
    mgmt_memory = ray_config['management_api_memory'] or max(4, head_memory // 2)

    # ── Build worker groups ───────────────────────────────────────────────────
    # Advanced: explicit worker_groups list from YAML takes priority.
    # Simple:   build one group from the flat num_workers / worker_* params.
    if ray_config.get('worker_groups'):
        worker_groups = [
            WorkerGroupConfig(
                name=g['name'],
                node_type=g['node_type'],
                count=g['count'],
                cpu=g['cpu'],
                memory=g['memory'],
                gpus=g.get('gpus', 0),
                runtime_identifier=g.get('runtime_identifier'),
            )
            for g in ray_config['worker_groups']
        ]
    else:
        node_type = (
            ray_config['worker_node_type']
            or ("gpu-worker" if ray_config['worker_gpus'] > 0 else "cpu-worker")
        )
        worker_groups = [WorkerGroupConfig(
            name="workers",
            node_type=node_type,
            count=ray_config['num_workers'],
            cpu=ray_config['worker_cpu'],
            memory=ray_config['worker_memory'],
            gpus=ray_config['worker_gpus'],
        )]

    print("\n🎯 Ray Cluster Configuration:")
    print(f"   Head Node     : {head_cpu} CPU, {head_memory} GB RAM  (no GPU)")
    print(f"   Management API: {mgmt_cpu} CPU, {mgmt_memory} GB RAM  (subset of head)")
    for g in worker_groups:
        print(f"   Worker group '{g.name}' [{g.node_type}]: "
              f"{g.count} × {g.cpu} CPU, {g.memory} GB RAM, {g.gpus} GPU")
    print(f"   Ray Port      : {ray_config['ray_port']}")
    print(f"   Dashboard Port: {ray_config['dashboard_port']}")

    try:
        # Render launcher scripts from Jinja2 templates (one per worker group)
        print("\n📝 Rendering Ray launcher scripts from templates...")
        head_script_path, worker_groups = create_ray_launcher_scripts(
            worker_groups=worker_groups,
            ray_port=ray_config['ray_port'],
            dashboard_port=ray_config['dashboard_port'],
            metrics_port=ray_config['metrics_port'],
            mgmt_cpu=mgmt_cpu,
            mgmt_memory_gb=mgmt_memory,
            proxy_health_check_period_s=ray_config['proxy_health_check_period_s'],
            proxy_health_check_timeout_s=ray_config['proxy_health_check_timeout_s'],
            proxy_ready_check_timeout_s=ray_config['proxy_ready_check_timeout_s'],
            proxy_min_draining_period_s=ray_config['proxy_min_draining_period_s'],
        )

        # Initialize CAI cluster manager
        print("\n🔧 Initializing CAI cluster manager...")
        manager = CAIClusterManager(
            cml_host=cml_host,
            cml_api_key=cml_api_key,
            project_id=project_id,
            verbose=True
        )
        print("✅ Manager initialized")

        # ── Step 1: start head node ────────────────────────────────────────────
        info_file = Path("/home/cdsw/ray_cluster_info.json")
        print("\n🚀 Starting Ray head node...")
        print(f"   Head script: {head_script_path}")
        cluster_info = manager.start_cluster(
            worker_groups=worker_groups,
            head_app_name=head_app_name,
            head_cpu=ray_config['head_cpu'],
            head_memory=ray_config['head_memory'],
            ray_port=ray_config['ray_port'],
            dashboard_port=ray_config['dashboard_port'],
            head_runtime_identifier=head_runtime,
            worker_runtime_identifier=worker_runtime,
            head_script_path=head_script_path,
            wait_ready=True,
            timeout=600,
        )
        # CML v2 API does not return a full URL in its response — override
        # with the deterministic URL derived from app name + CDSW_DOMAIN.
        if head_url_from_domain:
            cluster_info['head_url'] = head_url_from_domain
        print(f"   head_url: {cluster_info.get('head_url', '(unknown)')}")

        # ── Step 2: wait for Management API ───────────────────────────────────
        print("\n⏳ Waiting for Management API to become healthy on head node...")
        management_url = _wait_for_management_api(cluster_info, timeout=300)

        if management_url:
            cluster_info['management_api_url'] = management_url
            print(f"✅ Management API: {management_url}")
        else:
            cluster_info['management_api_url'] = cluster_info.get('head_url', '')
            print("⚠️  Management API health check timed out (may still be starting)")

        # ── Resolve GCS address if not already in cluster_info ────────────────
        # Workers need this to connect to Ray GCS. We fetch it from the
        # Management API (which reads /home/cdsw/ray_gcs_address written by
        # the head launcher at startup).
        if management_url and not cluster_info.get("head_address"):
            import urllib.request as _urlreq2
            try:
                with _urlreq2.urlopen(
                    f"{management_url}/api/v1/cluster/gcs-address", timeout=10
                ) as _r:
                    _gcs = json.loads(_r.read()).get("gcs_address", "")
                    if _gcs:
                        cluster_info["head_address"] = _gcs
                        with open(info_file, "w") as f:
                            json.dump(cluster_info, f, indent=2)
                        print(f"   GCS address: {_gcs}")
            except Exception as _exc:
                print(f"⚠️  Could not fetch GCS address from Management API: {_exc}")

        # ── Step 3: add worker nodes via Management API ────────────────────────
        total_workers = sum(g.count for g in worker_groups)
        if total_workers > 0 and management_url:
            import urllib.request as _urlreq
            import json as _json
            print(f"\n🔧 Adding {total_workers} worker(s) via Management API...")
            add_url = f"{management_url}/api/v1/resources/nodes/add"
            for g in worker_groups:
                for i in range(g.count):
                    payload = _json.dumps({
                        "node_type": g.node_type,
                        "cpu":       g.cpu,
                        "memory":    g.memory,
                        "gpus":      g.gpus,
                    }).encode()
                    req = _urlreq.Request(
                        add_url,
                        data=payload,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    try:
                        with _urlreq.urlopen(req, timeout=30) as resp:
                            result = _json.loads(resp.read())
                            print(f"   ✅ [{g.node_type}] worker {i+1}/{g.count} — "
                                  f"app: {result.get('app_name', '?')}")
                    except Exception as exc:
                        print(f"   ⚠️  [{g.node_type}] worker {i+1}/{g.count} failed: {exc}")
        elif total_workers > 0:
            print("⚠️  Skipping worker launch — Management API not reachable")

        # Save cluster info to file for reference
        info_file = Path("/home/cdsw/ray_cluster_info.json")
        with open(info_file, 'w') as f:
            json.dump(cluster_info, f, indent=2)
        print(f"\n💾 Cluster info saved to {info_file}")

        # Print cluster information
        print("\n" + "=" * 70)
        print("✅ Ray Cluster Started Successfully!")
        print("=" * 70)
        head_address = cluster_info.get('head_address')
        print(f"\n📊 Cluster Information:")
        print(f"   Head Node ID: {cluster_info['head_app_id']}")
        print(f"   Head Address: {head_address or '(not yet resolved)'}")
        if head_address:
            print(f"   Dashboard: http://{head_address.split(':')[0]}:{ray_config['dashboard_port']}")
        print(f"   Workers: {cluster_info['num_workers']} nodes")
        for g in cluster_info.get('worker_groups', []):
            print(f"   Group '{g['name']}' [{g['node_type']}]: "
                  f"{g['count']} × {g['cpu']}CPU, {g['memory']}GB, {g['gpus']}GPU")

        print(f"\n🔗 Connection Details:")
        if head_address:
            print(f"   Ray Address: ray://{head_address}")
            print(f"   Python API: ray.init(address='ray://{head_address}')")
        else:
            print(f"   Ray Address: (GCS address not resolved — check Management API)")
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
    _rc = main()
    if _rc:
        sys.exit(_rc)  # non-zero → real error; zero → fall through cleanly
