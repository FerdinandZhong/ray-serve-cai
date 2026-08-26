#!/usr/bin/env python3
"""
Create Prometheus and Grafana CML Applications for Ray Dashboard metrics.

Run this once after the Ray cluster is up. It creates two CML applications
in the current project and prints the URLs to add to ray_cluster_config.yaml.

Usage:
    python cai_integration/launch_monitoring.py

Required environment variables (same as launch_ray_cluster.py):
    CML_HOST or CDSW_DOMAIN
    CML_API_KEY or CDSW_APIV2_KEY
    CDSW_PROJECT_ID or CML_PROJECT_ID

Optional:
    MONITORING_RUNTIME_IDENTIFIER  — runtime image (defaults to head node image from config)
    PROMETHEUS_SUBDOMAIN           — CML app subdomain (default: prometheus-server)
    GRAFANA_SUBDOMAIN              — CML app subdomain (default: grafana-server)
    RAY_CLUSTER_HEAD_URL           — injected into Prometheus for scraping
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ray_serve_cai.cai_cluster import CAIClusterManager

try:
    from cai_integration.launch_ray_cluster import load_config as _load_config
except ImportError:
    def _load_config():
        return {}


def _wait_healthy(url: str, timeout: int = 300) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    dots = 0
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status < 400:
                    return True
        except Exception:
            pass
        time.sleep(5)
        dots += 1
        if dots % 12 == 0:
            elapsed = int(timeout - (deadline - time.time()))
            print(f"  ... {elapsed}s elapsed")
    return False


def main():
    print("=" * 70)
    print("Launching Ray Monitoring Stack (Prometheus + Grafana)")
    print("=" * 70)

    cml_host = os.environ.get("CML_HOST")
    if not cml_host:
        domain = os.environ.get("CDSW_DOMAIN", "")
        if domain:
            cml_host = f"https://{domain}"

    cml_api_key = os.environ.get("CML_API_KEY") or os.environ.get("CDSW_APIV2_KEY")
    project_id = os.environ.get("CDSW_PROJECT_ID") or os.environ.get("CML_PROJECT_ID")
    cdsw_domain = os.environ.get("CDSW_DOMAIN", "").strip()

    if not all([cml_host, cml_api_key, project_id]):
        print("ERROR: set CML_HOST (or CDSW_DOMAIN), CML_API_KEY, CDSW_PROJECT_ID", file=sys.stderr)
        return 1

    ray_config = _load_config()
    runtime = (
        os.environ.get("MONITORING_RUNTIME_IDENTIFIER")
        or os.environ.get("HEAD_RUNTIME_IDENTIFIER")
        or ray_config.get("head_runtime_identifier", "")
        or "docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-standard:2026.04.1-b7"
    )

    prom_sub    = os.environ.get("PROMETHEUS_SUBDOMAIN", "prometheus-server")
    grafana_sub = os.environ.get("GRAFANA_SUBDOMAIN",    "grafana-server")

    # Head URL is deterministic from the head app subdomain + CDSW_DOMAIN, so we
    # can point Prometheus at it without the cluster being up yet — this breaks
    # the monitoring<->cluster launch-order cycle. Prometheus tolerates a target
    # that is down and marks it UP once the head comes online.
    ray_head_url = os.environ.get("RAY_CLUSTER_HEAD_URL", "").strip()
    if not ray_head_url:
        head_sub = (
            os.environ.get("RAY_HEAD_SUBDOMAIN")
            or ray_config.get("head_app_name")
            or "ray-cluster-head"
        )
        ray_head_url = f"https://{head_sub}.{cdsw_domain}" if cdsw_domain else ""

    prom_url    = f"https://{prom_sub}.{cdsw_domain}"    if cdsw_domain else ""
    grafana_url = f"https://{grafana_sub}.{cdsw_domain}" if cdsw_domain else ""

    print(f"\n  CML Host   : {cml_host}")
    print(f"  Project ID : {project_id}")
    print(f"  Prometheus : {prom_url}")
    print(f"  Grafana    : {grafana_url}")
    if ray_head_url:
        print(f"  Ray Head   : {ray_head_url}")

    manager = CAIClusterManager(
        cml_host=cml_host,
        cml_api_key=cml_api_key,
        project_id=project_id,
        verbose=False,
    )

    # ── 1. Prometheus ─────────────────────────────────────────────────────────
    print("\n[1/2] Creating Prometheus application...")
    prom_env = {}
    if ray_head_url:
        prom_env["RAY_CLUSTER_HEAD_URL"] = ray_head_url
    # Forward a Bearer token so Prometheus can scrape the head's ingress when
    # the head app requires authentication. Default to the CML API key
    # ($CDSW_APIV2_KEY / $CML_API_KEY), which the Management API accepts.
    _metrics_token = (
        os.environ.get("RAY_METRICS_BEARER_TOKEN", "").strip()
        or (cml_api_key or "").strip()
    )
    if _metrics_token:
        prom_env["RAY_METRICS_BEARER_TOKEN"] = _metrics_token

    # Forward the SD-registry write token so the co-located dynamic
    # service-discovery registry guards register/heartbeat/delete. If unset,
    # the registry's write endpoints stay open (fine for trusted networks).
    _sd_token = os.environ.get("SD_REGISTRY_TOKEN", "").strip()
    if _sd_token:
        prom_env["SD_REGISTRY_TOKEN"] = _sd_token

    manager.cml_client.create_application(
        project_id=project_id,
        name="prometheus-server",
        script="cai_integration/monitoring/prometheus_launcher.py",
        cpu=2,
        memory=4,
        runtime_identifier=runtime,
        subdomain=prom_sub,
        bypass_authentication=True,
        environment=prom_env or None,
    )
    print(f"  Polling {prom_url} ...")
    if prom_url and _wait_healthy(prom_url, timeout=300):
        print(f"  Prometheus healthy")
    else:
        print(f"  WARNING: Prometheus did not respond within 5 min — check app logs")

    # ── 2. Grafana ────────────────────────────────────────────────────────────
    print("\n[2/2] Creating Grafana application...")
    grafana_env = {}
    if prom_url:
        grafana_env["PROMETHEUS_URL"] = prom_url

    manager.cml_client.create_application(
        project_id=project_id,
        name="grafana-server",
        script="cai_integration/monitoring/grafana_launcher.py",
        cpu=2,
        memory=4,
        runtime_identifier=runtime,
        subdomain=grafana_sub,
        bypass_authentication=True,
        environment=grafana_env or None,
    )
    print(f"  Polling {grafana_url} ...")
    if grafana_url and _wait_healthy(grafana_url, timeout=300):
        print(f"  Grafana healthy")
    else:
        print(f"  WARNING: Grafana did not respond within 5 min — check app logs")

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("Monitoring stack ready.")
    print(f"  Prometheus : {prom_url}")
    print(f"  Grafana    : {grafana_url}")
    print()
    print("Add to configs/ray_cluster_config.yaml → monitoring:")
    print(f"  prometheus_host:     {prom_url}")
    print(f"  grafana_host:        {grafana_url}")
    print(f"  grafana_iframe_host: {grafana_url}")
    print("=" * 70)
    if ray_head_url:
        print(f"\nTo provision Ray dashboards:")
        print(f"  GRAFANA_HOST={grafana_url} python cai_integration/provision_monitoring.py")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
