#!/usr/bin/env python3
"""CML Job entrypoint: recover a dead Ray head node.

Because the management API runs *on* the head, its recovery endpoint is
unreachable exactly when the head is down. This script is meant to be
registered as a CML **Job** (a separate on-demand pod) so an operator can
click "Run" in the CML UI to restart the head, refresh the persisted head
address, rebuild workers, and redeploy models from the deploy-intent store.

Env (already present in any CML pod of the project):
    CML_HOST / CDSW_DOMAIN, CML_API_KEY / CDSW_APIV2_KEY, CML_PROJECT_ID

Usage:
    python -m ray_serve_cai.scripts.recover_head [--dry-run] [--timeout 600]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests

from ray_serve_cai.management.services.cai_service import CAIService
from ray_serve_cai.management.services.deployment_store import DeploymentStore
from ray_serve_cai.recovery.recover import RecoveryOrchestrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("recover_head")

_CLUSTER_INFO = Path("/home/cdsw/ray_cluster_info.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover a dead Ray head node (CML Job).")
    parser.add_argument("--dry-run", action="store_true", help="log intended actions without mutating")
    parser.add_argument("--timeout", type=float, default=600.0, help="seconds to wait for the new head")
    parser.add_argument("--poll", type=float, default=10.0, help="head poll interval seconds")
    args = parser.parse_args(argv)

    project_id = os.environ.get("CML_PROJECT_ID") or os.environ.get("CDSW_PROJECT_ID")
    domain = os.environ.get("CDSW_DOMAIN", "").strip()
    cml_host = os.environ.get("CML_HOST") or (f"https://{domain}" if domain else None)
    service_token = os.environ.get("CML_API_KEY") or os.environ.get("CDSW_APIV2_KEY")

    if not (project_id and cml_host and service_token):
        logger.error("Missing CML env (CML_PROJECT_ID / CML_HOST|CDSW_DOMAIN / CML_API_KEY).")
        return 2

    cai = CAIService(project_id=project_id, cml_host=cml_host, api_key=service_token)

    orch = RecoveryOrchestrator(
        cml=cai.manager,
        cai_service=cai,
        deployment_store=DeploymentStore(),
        cluster_info_path=_CLUSTER_INFO,
        http=requests,
        service_token=service_token,
        poll_interval_s=args.poll,
        head_timeout_s=args.timeout,
        dry_run=args.dry_run,
    )

    result = orch.run(owner=f"recover-head-job:{os.environ.get('CDSW_IP_ADDRESS', 'unknown')}")
    logger.info("Recovery result: %s", result)
    return 0 if result.get("status") in ("recovered", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
