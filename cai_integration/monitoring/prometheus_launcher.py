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

Co-located with this app is a dynamic service-discovery (SD) registry
(``sd_registry.py``): clients register their own ingress-reachable ``/metrics``
endpoints and Prometheus discovers them via ``http_sd_configs``. It is served
under the ``/sd`` path prefix behind the same CML app ingress (Swagger UI at
``/sd/docs``), so the whole monitoring plane stays independent of the Ray
cluster's lifecycle.

Environment variables:
  RAY_CLUSTER_HEAD_URL     — head ingress base URL (e.g. https://ray-cluster-head.example.com)
  RAY_METRICS_BEARER_TOKEN — optional Bearer token if the head app requires auth
  CDSW_APP_PORT            — CML application port (proxied to Prometheus 9090)
  PROMETHEUS_VERSION       — Prometheus release to download (default: 3.4.1)
  PROMETHEUS_RETENTION     — Data retention period (default: 15d)
  SD_REGISTRY_ENABLED      — set 'false' to disable the co-located SD registry
  SD_REGISTRY_PORT         — localhost port for the SD registry (default: 9099)
  SD_REGISTRY_TOKEN        — optional shared secret guarding SD write endpoints
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

# Allow importing the sibling sd_registry module whether this file is run as a
# normal script or inside a kernel (CML apps exec the script via IPython, where
# __file__ is undefined). Try the file's own directory first, then fall back to
# the known location under the project root (cwd, typically /home/cdsw).
def _this_dir() -> Path:
    try:
        return Path(__file__).resolve().parent  # type: ignore[name-defined]
    except NameError:
        cand = Path.cwd() / "cai_integration" / "monitoring"
        return cand if (cand / "sd_registry.py").exists() else Path.cwd()


sys.path.insert(0, str(_this_dir()))
try:
    import sd_registry  # type: ignore
except Exception as _sd_exc:  # pragma: no cover - defensive
    sd_registry = None
    print(f"WARNING: SD registry unavailable ({_sd_exc}); "
          f"dynamic discovery disabled")

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

# Co-located dynamic service-discovery registry (see sd_registry.py).
SD_ENABLED = (
    sd_registry is not None
    and os.environ.get("SD_REGISTRY_ENABLED", "true").strip().lower() != "false"
)
SD_PORT = int(os.environ.get("SD_REGISTRY_PORT", "9099"))
SD_PREFIX = getattr(sd_registry, "SD_PREFIX", "/sd")

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

    if SD_ENABLED:
        # Dynamic targets registered via the co-located SD registry. Prometheus
        # reads the target list over localhost; each target carries its own
        # __scheme__/__metrics_path__ meta-labels, so a single job covers all of
        # them. IMPORTANT: no job-level `authorization` here — a shared bearer
        # would be sent to every directly-advertised registered host (credential
        # theft). Targets that need auth register a per-target token and are
        # scraped through the registry's /sd/scrape proxy instead.
        jobs += (
            "- job_name: 'ray-dynamic-sd'\n"
            "  http_sd_configs:\n"
            f"    - url: 'http://127.0.0.1:{SD_PORT}{SD_PREFIX}/targets'\n"
            "      refresh_interval: 30s\n"
            "  tls_config:\n"
            "    insecure_skip_verify: true\n"
        )

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

    def _sd_ingress_allowed(self) -> bool:
        """Only a safe subset of /sd is reachable through the PUBLIC app ingress.

        Prometheus reads the registry over localhost (127.0.0.1:SD_PORT), so the
        target list, the auth-injecting scrape proxy, and the full-topology
        listing must NOT be ingress-exposed:
          * /sd/scrape       — confused deputy; would return a tokenized target's
                               metrics to any caller and enable SSRF.
          * /sd/targets      — internal target list.
          * /sd/registrations— full internal topology.
        Allowed publicly: docs/openapi (read-only) and the token-guarded writes
        (register/heartbeat/delete) plus health.
        """
        path = urlparse(self.path).path.rstrip("/")
        method = self.command
        if method == "GET" and path in (
            f"{SD_PREFIX}", f"{SD_PREFIX}/docs",
            f"{SD_PREFIX}/openapi.json", f"{SD_PREFIX}/health",
        ):
            return True
        if method == "POST" and (
            path == f"{SD_PREFIX}/register"
            or path.startswith(f"{SD_PREFIX}/heartbeat/")
        ):
            return True
        if method == "DELETE" and path.startswith(f"{SD_PREFIX}/targets/"):
            return True
        return False

    def _proxy(self):
        import urllib.request
        try:
            # Route SD-registry paths to the co-located registry; everything
            # else goes to Prometheus. Both listen on localhost. Match the
            # prefix exactly (avoid '/sdxyz' slipping through).
            path = urlparse(self.path).path
            is_sd = SD_ENABLED and (path == SD_PREFIX or path.startswith(SD_PREFIX + "/"))
            if is_sd:
                if not self._sd_ingress_allowed():
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"not found\n")
                    return
                target = f"http://127.0.0.1:{SD_PORT}{self.path}"
            else:
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

    # Start the co-located SD registry (localhost only; reached via the proxy
    # under /sd and scraped by Prometheus via http_sd on 127.0.0.1:SD_PORT).
    if SD_ENABLED:
        threading.Thread(
            target=lambda: sd_registry.run_registry_server(SD_PORT),
            daemon=True,
        ).start()
        print(f"SD registry listening on 127.0.0.1:{SD_PORT} "
              f"(Swagger UI via app ingress at {SD_PREFIX}/docs)")

    class _ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    threading.Thread(
        target=lambda: _ReusableServer(("127.0.0.1", APP_PORT), _ProxyHandler).serve_forever(),
        daemon=True,
    ).start()
    print(f"Proxy listening on 127.0.0.1:{APP_PORT} -> :{PROM_PORT} (+ {SD_PREFIX} -> :{SD_PORT})")

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
