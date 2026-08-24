#!/usr/bin/env python3
"""
CML Application: Grafana for Ray Dashboard metrics tab.

Downloads Grafana (if missing), auto-provisions the Prometheus datasource,
extracts Ray's built-in dashboard JSONs from the installed ray package,
and runs Grafana with anonymous read-only access and embedding enabled.

Environment variables:
  PROMETHEUS_URL      — Internal URL of the Prometheus CML app
  CDSW_APP_PORT       — CML application port (proxied to Grafana 3000)
  GRAFANA_VERSION     — Grafana release to download (default: 11.6.0)
  GF_SECURITY_ADMIN_PASSWORD — Admin password (default: admin)
"""

import os
import shutil
import signal
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen, urlretrieve

GF_VERSION = os.environ.get("GRAFANA_VERSION", "11.6.0")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
APP_PORT = int(os.environ.get("CDSW_APP_PORT", "8091"))
GF_PORT = 3000

INSTALL_DIR = Path("/home/cdsw/.local/grafana")
DATA_DIR = Path("/home/cdsw/grafana_data")
PROVISION_DIR = Path("/home/cdsw/grafana_provisioning")
DASHBOARD_DIR = PROVISION_DIR / "dashboards"

# Grafana 10+ uses bin/grafana; older versions used bin/grafana-server
_GF_CANDIDATES = [INSTALL_DIR / "bin" / "grafana", INSTALL_DIR / "bin" / "grafana-server"]


def download_grafana():
    if any(c.exists() for c in _GF_CANDIDATES):
        print("Grafana binary exists")
        return
    arch = "linux-amd64"
    tarball = f"grafana-enterprise-{GF_VERSION}.{arch}.tar.gz"
    url = f"https://dl.grafana.com/enterprise/release/{tarball}"
    dest = Path(f"/tmp/{tarball}")
    print(f"Downloading Grafana {GF_VERSION} ...")
    urlretrieve(url, str(dest))
    print("Extracting ...")
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(dest), "r:gz") as tar:
        # Strip the top-level grafana-vX.Y.Z/ directory prefix
        prefix = next(
            (m.name.rstrip("/") + "/" for m in tar.getmembers() if m.isdir() and m.name.count("/") == 0),
            f"grafana-v{GF_VERSION}/",
        )
        for member in tar.getmembers():
            if member.name.startswith(prefix) and member.name != prefix:
                member.name = member.name[len(prefix):]
                tar.extract(member, str(INSTALL_DIR))
    for c in _GF_CANDIDATES:
        if c.exists():
            c.chmod(0o755)
    dest.unlink()
    print(f"Installed Grafana to {INSTALL_DIR}")


def provision_datasource():
    ds_dir = PROVISION_DIR / "datasources"
    ds_dir.mkdir(parents=True, exist_ok=True)
    (ds_dir / "prometheus.yml").write_text(f"""\
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: {PROMETHEUS_URL}
    isDefault: true
    editable: true
    jsonData:
      httpMethod: GET
      timeInterval: 15s
""")
    print(f"Datasource → {PROMETHEUS_URL}")


def provision_dashboards():
    """Extract Ray's built-in Grafana dashboard JSONs from the ray package."""
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)

    # Dashboard provider so Grafana loads JSON files from DASHBOARD_DIR
    (PROVISION_DIR / "dashboards").mkdir(parents=True, exist_ok=True)
    (PROVISION_DIR / "dashboards" / "provider.yml").write_text(f"""\
apiVersion: 1
providers:
  - name: Ray Dashboards
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: {DASHBOARD_DIR}
""")

    # Try to copy dashboard JSONs from installed ray package
    try:
        import ray as _ray
        ray_pkg = Path(_ray.__file__).parent
        templates_dir = ray_pkg / "dashboard" / "modules" / "metrics" / "grafana_dashboard_templates"
        if templates_dir.exists():
            count = 0
            for src in templates_dir.glob("*.json"):
                shutil.copy(src, DASHBOARD_DIR / src.name)
                count += 1
            print(f"Copied {count} Ray dashboard JSONs from {templates_dir}")
        else:
            print(f"WARNING: Ray dashboard templates not found at {templates_dir}")
    except Exception as exc:
        print(f"WARNING: could not extract Ray dashboards: {exc}")


class _ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):    self._proxy()
    def do_POST(self):   self._proxy()
    def do_PUT(self):    self._proxy()
    def do_DELETE(self): self._proxy()
    def do_PATCH(self):  self._proxy()

    def _proxy(self):
        import urllib.request
        try:
            target = f"http://127.0.0.1:{GF_PORT}{self.path}"
            body = None
            length = self.headers.get("Content-Length")
            if length:
                body = self.rfile.read(int(length))
            req = urllib.request.Request(target, data=body, method=self.command)
            for k, v in self.headers.items():
                if k.lower() not in ("host", "transfer-encoding"):
                    req.add_header(k, v)
            with urlopen(req, timeout=30) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() != "transfer-encoding":
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp.read())
        except Exception as exc:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(f"Proxy error: {exc}\n".encode())

    def log_message(self, fmt, *args):
        pass  # silent


def main():
    download_grafana()
    provision_datasource()
    provision_dashboards()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    class _ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    threading.Thread(
        target=lambda: _ReusableServer(("127.0.0.1", APP_PORT), _ProxyHandler).serve_forever(),
        daemon=True,
    ).start()
    print(f"Proxy listening on 127.0.0.1:{APP_PORT} -> :{GF_PORT}")

    gf_bin = next((str(c) for c in _GF_CANDIDATES if c.exists()), None)
    if not gf_bin:
        print("ERROR: Grafana binary not found after install", file=sys.stderr)
        sys.exit(1)

    env = {
        **os.environ,
        "GF_PATHS_DATA": str(DATA_DIR),
        "GF_PATHS_PROVISIONING": str(PROVISION_DIR),
        "GF_SERVER_HTTP_PORT": str(GF_PORT),
        "GF_SERVER_ROOT_URL": "%(protocol)s://%(domain)s/",
        "GF_SECURITY_ADMIN_PASSWORD": os.environ.get("GF_SECURITY_ADMIN_PASSWORD", "admin"),
        # Anonymous read-only access (required for Ray Dashboard iframe embedding)
        "GF_AUTH_ANONYMOUS_ENABLED": "true",
        "GF_AUTH_ANONYMOUS_ORG_ROLE": "Viewer",
        "GF_AUTH_DISABLE_LOGIN_FORM": "true",
        # Allow embedding in iframes (Ray Dashboard metrics tab)
        "GF_SECURITY_ALLOW_EMBEDDING": "true",
    }

    cmd = [gf_bin, "--homepath", str(INSTALL_DIR)]
    print(f"Starting Grafana: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, env=env)

    def _shutdown(sig, frame):
        proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()
