#!/usr/bin/env python3
"""
CML Application: Prometheus for Ray cluster metrics.

Downloads the Prometheus binary (if missing) and runs it configured to
scrape Ray nodes discovered via the Management API's /api/v1/metrics/discovery
endpoint (http_sd_configs).

Environment variables:
  RAY_CLUSTER_HEAD_URL   — Management API base URL  (e.g. https://ray-cluster-head.example.com)
  CDSW_APP_PORT          — CML application port (proxied to Prometheus 9090)
  PROMETHEUS_VERSION     — Prometheus release to download (default: 3.4.1)
  PROMETHEUS_RETENTION   — Data retention period (default: 15d)
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
RAY_HEAD_URL = os.environ.get("RAY_CLUSTER_HEAD_URL", "")
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


def write_config():
    # Always self-scrape so the config is valid and Prometheus starts even
    # before a Ray head URL is configured.
    jobs = textwrap.dedent(f"""\
          - job_name: 'prometheus'
            static_configs:
              - targets: ['127.0.0.1:{PROM_PORT}']
    """)

    if RAY_HEAD_URL:
        parsed = urlparse(RAY_HEAD_URL)
        scheme = parsed.scheme or "https"
        host_port = (
            f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
        )
        jobs += textwrap.dedent(f"""\
          - job_name: 'ray-nodes'
            http_sd_configs:
              - url: '{RAY_HEAD_URL}/api/v1/metrics/discovery'
                refresh_interval: 15s
            metrics_path: /metrics
            scrape_interval: 15s
            scrape_timeout: 10s
            scheme: http

          - job_name: 'ray-aggregated'
            static_configs:
              - targets: ['{host_port}']
            metrics_path: /metrics
            scrape_interval: 30s
            scrape_timeout: 15s
            scheme: {scheme}
            tls_config:
              insecure_skip_verify: true
    """)
    else:
        print("WARNING: RAY_CLUSTER_HEAD_URL not set — only self-scrape configured; "
              "set it so Prometheus can discover Ray nodes")

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
