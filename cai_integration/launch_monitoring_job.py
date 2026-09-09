#!/usr/bin/env python3
"""
CAI Job Entry Point for Launching Monitoring Dashboards

Mirrors launch_ray_cluster_job.py: the CML job runs this stdlib-only entry
point, which shells into the project ``.venv`` so ``requests`` / ``pyyaml`` /
``ray`` are importable. Created + triggered by the GitHub Action via
jobs_config.yaml (child of launch_ray_cluster).

Steps:
  1. launch_monitoring.py   — create the Prometheus + Grafana CML applications.
  2. provision_monitoring.py — import Ray's built-in Grafana dashboards
                               (best-effort; failure does not fail the job).

Inside a CML job, CDSW_DOMAIN / CDSW_APIV2_KEY / CDSW_PROJECT_ID are injected
automatically, so no secrets need to be passed here.

Usage as CAI Job:
  script: "cai_integration/launch_monitoring_job.py"

Or manually:
  python cai_integration/launch_monitoring_job.py
"""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        project_root = Path(__file__).resolve().parent.parent
    except (NameError, AttributeError):
        project_root = Path.cwd()

    venv_python = project_root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print(f"❌ Error: venv not found at {venv_python}")
        print("Please run setup_environment.py (setup_base_env) first")
        return 1

    print("=" * 70)
    print("🚀 Launch Monitoring Dashboards (CAI Job Entry Point)")
    print("=" * 70)
    print(f"   Project root: {project_root}\n")

    # ── 1. Create Prometheus + Grafana CML applications ────────────────────────
    print("[1/2] cai_integration/launch_monitoring.py")
    rc = subprocess.run(
        [str(venv_python), "-u",
         str(project_root / "cai_integration" / "launch_monitoring.py")],
        cwd=str(project_root),
    ).returncode
    if rc != 0:
        print(f"❌ launch_monitoring.py failed (rc={rc})")
        return rc

    # ── 2. Provision Ray's built-in Grafana dashboards (best-effort) ───────────
    domain = os.environ.get("CDSW_DOMAIN", "").strip()
    grafana_sub = os.environ.get("GRAFANA_SUBDOMAIN", "grafana-server")
    grafana_host = os.environ.get("GRAFANA_HOST", "").strip() or (
        f"https://{grafana_sub}.{domain}" if domain else ""
    )

    print("\n[2/2] cai_integration/provision_monitoring.py")
    if not grafana_host:
        print("⚠️  CDSW_DOMAIN/GRAFANA_HOST unset — skipping Ray dashboard provisioning")
        return 0

    prov_rc = subprocess.run(
        [str(venv_python), "-u",
         str(project_root / "cai_integration" / "provision_monitoring.py")],
        cwd=str(project_root),
        env=dict(os.environ, GRAFANA_HOST=grafana_host),
    ).returncode
    if prov_rc != 0:
        # Dashboards apps are up; only the Ray-panel import failed. Don't fail
        # the whole job — the Grafana app is still usable and can be re-provisioned.
        print(f"⚠️  provision_monitoring.py failed (rc={prov_rc}) — "
              "Grafana/Prometheus are up, but Ray panels were not imported")

    return 0


if __name__ == "__main__":
    _rc = main()
    if _rc:
        sys.exit(_rc)
