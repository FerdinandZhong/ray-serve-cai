#!/usr/bin/env python3
"""
CML Application: Prometheus for Ray cluster metrics.

Downloads the Prometheus binary (if missing) and runs it configured to scrape
the Ray head's public HTTPS ingress endpoints. Prometheus runs as a *separate*
CML application, so it cannot reach the cluster's internal nodeIP:9090 exporters
that http_sd discovery would return — only the head's 443 ingress is routable.
We therefore scrape the head's aggregation routes instead:

  /metrics               → all alive nodes, aggregated (nginx → /api/v1/metrics/all)
  /api/v1/metrics/apps   → Ray Serve application metrics (vLLM, etc.)

Environment variables:
  RAY_CLUSTER_HEAD_URL     — head ingress base URL (e.g. https://ray-cluster-head.example.com)
  RAY_METRICS_BEARER_TOKEN — optional Bearer token if the head app requires auth
  CDSW_APP_PORT            — CML application port (proxied to Prometheus 9090)
  PROMETHEUS_VERSION       — Prometheus release to download (default: 3.4.1)
  PROMETHEUS_RETENTION     — Data retention period (default: 15d)
"""

import os
import signal
import subprocess
import sys
import tarfile
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen, urlretrieve

PROM_VERSION = os.environ.get("PROMETHEUS_VERSION", "3.4.1")
PROM_RETENTION = os.environ.get("PROMETHEUS_RETENTION", "15d")
RAY_HEAD_URL = os.environ.get("RAY_CLUSTER_HEAD_URL", "").strip()
# Fall back to the deterministic head ingress URL so this app is self-sufficient
# and launch order does not matter (the target is simply DOWN until the head is
# up). CDSW_DOMAIN is present in every CML workload/app in the project.
if not RAY_HEAD_URL:
    _cdsw_domain = os.environ.get("CDSW_DOMAIN", "").strip()
    _head_sub = os.environ.get("RAY_HEAD_SUBDOMAIN", "ray-cluster-head")
    if _cdsw_domain:
        RAY_HEAD_URL = f"https://{_head_sub}.{_cdsw_domain}"
METRICS_BEARER = os.environ.get("RAY_METRICS_BEARER_TOKEN", "").strip()
APP_PORT = int(os.environ.get("CDSW_APP_PORT", "8090"))
PROM_PORT = 9090

INSTALL_DIR = Path("/home/cdsw/.local/prometheus")
PROM_BIN = INSTALL_DIR / "prometheus"
DATA_DIR = Path("/home/cdsw/prometheus_data")
CONFIG_FILE = Path("/home/cdsw/prometheus.yml")


def download_prometheus():
    if PROM_BIN.exists():
        print(f"Prometheus binary exists: {PROM_BIN}")
        return
    arch = "linux-amd64"
    tarball = f"prometheus-{PROM_VERSION}.{arch}.tar.gz"
    url = f"https://github.com/prometheus/prometheus/releases/download/v{PROM_VERSION}/{tarball}"
    dest = Path(f"/tmp/{tarball}")
    print(f"Downloading Prometheus {PROM_VERSION} ...")
    urlretrieve(url, str(dest))
    print("Extracting ...")
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(str(dest), "r:gz") as tar:
        prefix = f"prometheus-{PROM_VERSION}.{arch}/"
        for member in tar.getmembers():
            if member.name.startswith(prefix) and member.name != prefix:
                member.name = member.name[len(prefix):]
                tar.extract(member, str(INSTALL_DIR))
    PROM_BIN.chmod(0o755)
    dest.unlink()
    print(f"Installed Prometheus to {INSTALL_DIR}")


def _auth_lines() -> str:
    """Optional Bearer auth block (2-space indented under a job), or empty."""
    if not METRICS_BEARER:
        return ""
    return (
        "  authorization:\n"
        "    type: Bearer\n"
        f"    credentials: '{METRICS_BEARER}'\n"
    )


def _ingress_job(name: str, host: str, scheme: str, metrics_path: str,
                 interval: str) -> str:
    """Build one static scrape job against the head's HTTPS ingress."""
    return (
        f"- job_name: '{name}'\n"
        f"  scheme: {scheme}\n"
        f"  metrics_path: {metrics_path}\n"
        f"  scrape_interval: {interval}\n"
        f"  static_configs:\n"
        f"    - targets: ['{host}']\n"
        f"  tls_config:\n"
        f"    insecure_skip_verify: true\n"
        f"{_auth_lines()}"
    )


def write_config():
    # Always self-scrape so the config is valid and Prometheus starts even
    # before a Ray head URL is configured.
    jobs = (
        "- job_name: 'prometheus'\n"
        "  static_configs:\n"
        f"    - targets: ['127.0.0.1:{PROM_PORT}']\n"
    )

    if RAY_HEAD_URL:
        parsed = urlparse(RAY_HEAD_URL)
        scheme = parsed.scheme or "https"
        host = f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
        # Scrape the head's public ingress routes (reachable across CML apps).
        # /metrics is an nginx alias for /api/v1/metrics/all (all nodes).
        jobs += _ingress_job("ray-cluster", host, scheme, "/metrics", "15s")
        jobs += _ingress_job("ray-serve-apps", host, scheme,
                             "/api/v1/metrics/apps", "30s")
        if not METRICS_BEARER:
            print("NOTE: RAY_METRICS_BEARER_TOKEN not set — if the head app "
                  "requires auth, scrapes will 401. Set it or make the head "
                  "app bypass authentication.")
    else:
        print("WARNING: RAY_CLUSTER_HEAD_URL not set — only self-scrape "
              "configured; set it so Prometheus can scrape the Ray head.")

    config = textwrap.dedent("""\
        global:
          scrape_interval: 15s
          scrape_timeout: 10s
          evaluation_interval: 15s

        scrape_configs:
        """) + jobs
    CONFIG_FILE.write_text(config)
    print(f"Wrote Prometheus config: {CONFIG_FILE}")


class _ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):    self._proxy()
    def do_POST(self):   self._proxy()
    def do_PUT(self):    self._proxy()
    def do_DELETE(self): self._proxy()

    def _proxy(self):
        import urllib.request
        try:
            target = f"http://127.0.0.1:{PROM_PORT}{self.path}"
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
    download_prometheus()
    write_config()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    class _ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    threading.Thread(
        target=lambda: _ReusableServer(("127.0.0.1", APP_PORT), _ProxyHandler).serve_forever(),
        daemon=True,
    ).start()
    print(f"Proxy listening on 127.0.0.1:{APP_PORT} -> :{PROM_PORT}")

    cmd = [
        str(PROM_BIN),
        f"--config.file={CONFIG_FILE}",
        f"--storage.tsdb.path={DATA_DIR}",
        f"--storage.tsdb.retention.time={PROM_RETENTION}",
        f"--web.listen-address=0.0.0.0:{PROM_PORT}",
        "--web.enable-lifecycle",
    ]
    print(f"Starting: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd)

    def _shutdown(sig, frame):
        proc.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    sys.exit(proc.wait())


if __name__ == "__main__":
    main()
