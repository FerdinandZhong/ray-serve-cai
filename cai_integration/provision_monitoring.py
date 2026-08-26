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


def _dashboards_from_session() -> list[tuple[str, str]]:
    """Ray writes generated dashboard JSONs into the session metrics dir when a
    cluster is running. Prefer these — they already reflect this cluster."""
    session = Path(os.environ.get(
        "RAY_SESSION_DIR", "/tmp/ray/session_latest"
    )) / "metrics" / "grafana" / "dashboards"
    if not session.exists():
        return []
    return [(p.stem, p.read_text()) for p in sorted(session.glob("*.json"))]


def _dashboards_from_factory() -> list[tuple[str, str]]:
    """Generate dashboard JSONs from the installed ray package (no running
    cluster required). Ray does not ship ready-made JSONs; it renders them from
    base templates via grafana_dashboard_factory.generate_*_grafana_dashboard().
    """
    try:
        from ray.dashboard.modules.metrics import grafana_dashboard_factory as gdf
    except Exception as exc:
        print(f"WARNING: cannot import ray grafana factory: {exc}")
        return []

    import inspect

    out: list[tuple[str, str]] = []
    for fname, fn in inspect.getmembers(gdf, inspect.isfunction):
        if not (fname.startswith("generate_") and fname.endswith("_grafana_dashboard")):
            continue
        # Only call zero-required-arg generators (default/serve/data/train/...).
        sig = inspect.signature(fn)
        if any(p.default is inspect.Parameter.empty
               and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
               for p in sig.parameters.values()):
            continue
        try:
            result = fn()
        except Exception as exc:
            print(f"  skip {fname}: {exc}")
            continue
        content = result[0] if isinstance(result, tuple) else result
        name = fname[len("generate_"):-len("_grafana_dashboard")] or "default"
        out.append((name, content))
    return out


def find_ray_dashboards() -> list[tuple[str, str]]:
    """Return a list of (name, dashboard_json_str)."""
    return _dashboards_from_session() or _dashboards_from_factory()


def main():
    if not GRAFANA_HOST:
        print("ERROR: set GRAFANA_HOST", file=sys.stderr)
        return 1

    dashboards = find_ray_dashboards()
    if not dashboards:
        print("No Ray dashboards found. Run with the venv python that has ray "
              "installed (e.g. .venv/bin/python), or run on a node where "
              "/tmp/ray/session_latest exists.")
        return 1

    print(f"Importing {len(dashboards)} Ray dashboards into {GRAFANA_HOST} ...")
    ok = 0
    for name, content in dashboards:
        try:
            model = json.loads(content)
        except Exception as exc:
            print(f"  FAIL {name}: invalid dashboard JSON: {exc}")
            continue
        model.pop("id", None)  # Grafana assigns a new id on import
        payload = {
            "dashboard": model,
            "overwrite": True,
            "folderId": 0,
            "inputs": [],
        }
        result = _post("/api/dashboards/import", payload)
        if "error" in result or result.get("status") == "error":
            print(f"  FAIL {name}: {result.get('error') or result.get('message')}")
        else:
            print(f"  OK   {name} → uid={result.get('uid')}")
            ok += 1

    print(f"\n{ok}/{len(dashboards)} dashboards imported.")
    return 0 if ok == len(dashboards) else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
