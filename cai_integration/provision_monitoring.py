#!/usr/bin/env python3
"""
Provision Ray's built-in Grafana dashboards into a running Grafana instance.

Extracts dashboard JSONs from the installed ray package and imports them
via the Grafana HTTP API. Run this once after Grafana is healthy.

Usage:
    GRAFANA_HOST=https://grafana-server.example.com python cai_integration/provision_monitoring.py

Environment variables:
    GRAFANA_HOST       — Grafana base URL (required)
    GRAFANA_API_KEY    — Grafana service-account token (optional; uses admin:admin if absent)
    GRAFANA_ADMIN_USER — Admin username (default: admin)
    GRAFANA_ADMIN_PASS — Admin password (default: admin)
    GRAFANA_ORG_ID     — Org ID to import into (default: 1)
"""

import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

GRAFANA_HOST = os.environ.get("GRAFANA_HOST", "").rstrip("/")
GRAFANA_API_KEY = os.environ.get("GRAFANA_API_KEY", "")
GRAFANA_USER = os.environ.get("GRAFANA_ADMIN_USER", "admin")
GRAFANA_PASS = os.environ.get("GRAFANA_ADMIN_PASS", "admin")
GRAFANA_ORG_ID = int(os.environ.get("GRAFANA_ORG_ID", "1"))


def _auth_header() -> str:
    if GRAFANA_API_KEY:
        return f"Bearer {GRAFANA_API_KEY}"
    import base64
    creds = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASS}".encode()).decode()
    return f"Basic {creds}"


def _post(path: str, payload: dict) -> dict:
    url = f"{GRAFANA_HOST}{path}"
    body = json.dumps(payload).encode()
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", _auth_header())
    try:
        with urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except HTTPError as exc:
        return {"error": exc.read().decode()}


def find_ray_dashboards() -> list[Path]:
    try:
        import ray as _ray
        templates = Path(_ray.__file__).parent / "dashboard" / "modules" / "metrics" / "grafana_dashboard_templates"
        if templates.exists():
            return sorted(templates.glob("*.json"))
    except ImportError:
        pass
    return []


def main():
    if not GRAFANA_HOST:
        print("ERROR: set GRAFANA_HOST", file=sys.stderr)
        return 1

    dashboards = find_ray_dashboards()
    if not dashboards:
        print("No Ray dashboard JSONs found in installed ray package")
        return 1

    print(f"Importing {len(dashboards)} Ray dashboards into {GRAFANA_HOST} ...")
    ok = 0
    for path in dashboards:
        model = json.loads(path.read_text())
        model.pop("id", None)  # Grafana assigns a new id on import
        payload = {
            "dashboard": model,
            "overwrite": True,
            "folderId": 0,
            "inputs": [],
        }
        result = _post("/api/dashboards/import", payload)
        if "error" in result or result.get("status") == "error":
            print(f"  FAIL {path.name}: {result.get('error') or result.get('message')}")
        else:
            print(f"  OK   {path.name} → uid={result.get('uid')}")
            ok += 1

    print(f"\n{ok}/{len(dashboards)} dashboards imported.")
    return 0 if ok == len(dashboards) else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
