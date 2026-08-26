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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen, urlretrieve

GF_VERSION = os.environ.get("GRAFANA_VERSION", "11.6.0")
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
APP_PORT = int(os.environ.get("CDSW_APP_PORT", "8091"))
GF_PORT = 3000

INSTALL_DIR = Path("/home/cdsw/.local/grafana")
# Grafana's SQLite DB (and plugin dir) MUST live on local disk, not NFS.
# /home/cdsw is NFS-backed in CML and SQLite locking is unreliable over NFS,
# which surfaces as fatal "database is locked" provisioning errors. State here
# is disposable: datasources/dashboards are re-provisioned from files each boot.
DATA_DIR = Path(os.environ.get("GRAFANA_DATA_DIR", "/tmp/grafana_data"))
PROVISION_DIR = Path("/home/cdsw/grafana_provisioning")
DASHBOARD_DIR = PROVISION_DIR / "dashboards"

# Grafana 10+ uses bin/grafana; older versions used bin/grafana-server
_GF_CANDIDATES = [INSTALL_DIR / "bin" / "grafana", INSTALL_DIR / "bin" / "grafana-server"]


def _install_is_complete() -> bool:
    """A usable Grafana install needs the binary AND the bundled `public`
    assets. Grafana refuses to start if ``public/emails/*.html`` is missing
    (it loads notification email templates at boot), so we treat that dir as
    the completeness sentinel rather than just checking for the binary."""
    if not any(c.exists() for c in _GF_CANDIDATES):
        return False
    emails_dir = INSTALL_DIR / "public" / "emails"
    return emails_dir.is_dir() and any(emails_dir.glob("*.html"))


def download_grafana():
    if _install_is_complete():
        print("Grafana binary + public assets present")
        return
    if INSTALL_DIR.exists():
        # A partial/corrupt extraction (e.g. binary present but public/emails
        # missing) would otherwise persist forever; wipe and re-extract clean.
        print("Grafana install incomplete; re-extracting from scratch")
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
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
    if not _install_is_complete():
        raise RuntimeError(
            f"Grafana extraction incomplete: expected assets under "
            f"{INSTALL_DIR / 'public' / 'emails'} not found"
        )
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


_GF_PROC_PATTERNS = (
    # Anchor on the install path so we never match this launcher's own cmdline
    # or unrelated processes; covers both `grafana server` and legacy binary.
    str(INSTALL_DIR / "bin" / "grafana"),
    str(INSTALL_DIR / "bin" / "grafana-server"),
)


def _grafana_running() -> bool:
    for pat in _GF_PROC_PATTERNS:
        try:
            if subprocess.run(["pgrep", "-f", pat], check=False).returncode == 0:
                return True
        except FileNotFoundError:
            return False  # pgrep unavailable; assume none
    return False


def _kill_stale_grafana(timeout: float = 15.0):
    """Reap any Grafana left over from a previous run in this kernel and WAIT
    for it to exit before returning.

    Re-running the launcher inside the same workbench session otherwise leaves
    an old grafana holding both the SQLite DB and port 3000, so the new process
    fails with "database is locked" or "address already in use". We SIGTERM,
    poll until the process is gone, then escalate to SIGKILL. CML app
    containers are single-tenant, so reaping lingering grafana here is safe."""
    try:
        for pat in _GF_PROC_PATTERNS:
            subprocess.run(["pkill", "-f", pat], check=False)
    except FileNotFoundError:
        return  # pkill unavailable; nothing to do

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _grafana_running():
            return
        time.sleep(0.5)

    # Graceful shutdown didn't finish in time — force kill and wait briefly.
    for pat in _GF_PROC_PATTERNS:
        subprocess.run(["pkill", "-9", "-f", pat], check=False)
    hard_deadline = time.monotonic() + 5.0
    while time.monotonic() < hard_deadline:
        if not _grafana_running():
            return
        time.sleep(0.25)
    print("WARNING: stale Grafana process may still be running", file=sys.stderr)


def main():
    _kill_stale_grafana()
    download_grafana()
    provision_datasource()
    provision_dashboards()
    # Start from a clean local DB so a stale/locked NFS-era DB never blocks boot.
    # Guard against wiping a dangerous/shared path if GRAFANA_DATA_DIR is
    # overridden to something like /, /tmp, or the home directory.
    _protected = {Path("/"), Path("/tmp"), Path.home(), Path("/home/cdsw")}
    if DATA_DIR.resolve() in {p.resolve() for p in _protected}:
        raise RuntimeError(
            f"Refusing to wipe GRAFANA_DATA_DIR={DATA_DIR}: pick a dedicated "
            f"disposable directory (e.g. /tmp/grafana_data)"
        )
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    for sub in (DATA_DIR, DATA_DIR / "log", DATA_DIR / "plugins"):
        sub.mkdir(parents=True, exist_ok=True)

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
        "GF_PATHS_HOME": str(INSTALL_DIR),
        "GF_PATHS_DATA": str(DATA_DIR),
        # Logs and plugins default to <homepath>/data/* (on NFS); pin them under
        # the local DATA_DIR so all mutable state stays off NFS and consistent.
        "GF_PATHS_LOGS": str(DATA_DIR / "log"),
        "GF_PATHS_PLUGINS": str(DATA_DIR / "plugins"),
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
        # Don't background-install the default apps (pyroscope, lokiexplore):
        # they need network to grafana.com, fail signature validation, and add
        # SQLite write contention. We only need the Prometheus datasource.
        "GF_PLUGINS_PREINSTALL_DISABLED": "true",
    }

    # Grafana 10+ ships a single `grafana` binary that requires the `server`
    # subcommand before flags; the legacy `grafana-server` binary takes
    # --homepath directly.
    if Path(gf_bin).name == "grafana":
        cmd = [gf_bin, "server", "--homepath", str(INSTALL_DIR)]
    else:
        cmd = [gf_bin, "--homepath", str(INSTALL_DIR)]
    print(f"Starting Grafana: {' '.join(cmd)}")
    # Run from the home dir so Grafana resolves `public/` (dashboards, email
    # templates) relative to the correct location regardless of caller cwd.
    proc = subprocess.Popen(cmd, env=env, cwd=str(INSTALL_DIR))

    def _shutdown(sig, frame):
        proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()
