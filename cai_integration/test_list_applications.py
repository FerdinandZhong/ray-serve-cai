#!/usr/bin/env python3
"""
Quick smoke-test for CMLAPIClient.list_applications().

Run inside a CML job/session so the CDSW_* env vars are populated:
    python cai_integration/test_list_applications.py
"""

import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s -- %(message)s", stream=sys.stdout)

# Allow running without venv activation
_VENV_PYTHON = Path("/home/cdsw/.venv/bin/python")
if _VENV_PYTHON.exists() and Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)

# Load cai_cluster.py directly to avoid triggering ray_serve_cai/__init__.py,
# which eagerly imports Ray and Starlette (not available in the launcher venv).
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "cai_cluster",
    Path(__file__).parent.parent / "ray_serve_cai" / "cai_cluster.py",
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
CMLAPIClient = _mod.CMLAPIClient

# ── Read env vars ─────────────────────────────────────────────────────────────
cml_host = os.environ.get("CML_HOST")
if not cml_host:
    domain = os.environ.get("CDSW_DOMAIN", "")
    cml_host = f"https://{domain}" if domain else None

api_key    = os.environ.get("CML_API_KEY") or os.environ.get("CDSW_APIV2_KEY")
project_id = os.environ.get("CDSW_PROJECT_ID") or os.environ.get("CML_PROJECT_ID")

print(f"cml_host   : {cml_host}")
print(f"api_key    : {'SET (len=' + str(len(api_key)) + ')' if api_key else 'NOT SET'}")
print(f"project_id : {project_id}")
print()

if not all([cml_host, api_key, project_id]):
    print("ERROR: one or more required env vars are missing — aborting")
    sys.exit(1)

# ── Call the API ──────────────────────────────────────────────────────────────
client = CMLAPIClient(host=cml_host, api_key=api_key, verbose=True)
print(f"base_url   : {client.base_url}")
print()

apps = client.list_applications(project_id)

print()
print(f"Total applications returned: {len(apps)}")
for a in apps:
    print(f"  {a.name:40s}  id={a.id}  status={a.status}")
