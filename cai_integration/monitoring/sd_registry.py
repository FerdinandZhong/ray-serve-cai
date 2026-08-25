#!/usr/bin/env python3
"""
Dynamic Prometheus service-discovery (SD) registry — stdlib only.

This is part of the *monitoring plane* and is co-located inside the Prometheus
CML application (see ``prometheus_launcher.py``). Keeping it here — rather than
in the Ray Management API — means Prometheus can discover scrape targets without
any dependency on the Ray cluster being up: the monitoring plane stays fully
self-contained.

Clients (Ray Serve apps, exporters, anything with an ingress-reachable
``/metrics``) register themselves; Prometheus pulls the resulting target list
via ``http_sd_configs``.

Design notes / constraints:
  * Prometheus runs as a separate CML app and can only reach other components
    over their 443 HTTPS ingress. Registered ``targets`` must therefore be
    ingress-reachable ``host[:port]`` values, not internal pod IPs.
  * Per-target ``scheme`` and ``metrics_path`` are supported via Prometheus's
    ``__scheme__`` / ``__metrics_path__`` meta-labels embedded in the SD JSON.
  * Per-target bearer auth is NOT expressible through ``http_sd`` (auth is
    scrape-job-level). To support it we use the *multi-target exporter pattern*:
    a target registered WITH a ``token`` is advertised via an in-process
    auth-injecting scrape proxy (``/sd/scrape``). Prometheus scrapes the proxy
    over localhost; the proxy looks up the target's stored token and injects the
    ``Authorization`` header when fetching the real endpoint. Tokens never leave
    this process — they are stored 0600 on disk and redacted from all read
    endpoints. Targets registered WITHOUT a token are advertised directly (no
    proxy hop), as before.

Endpoints (served under the ``/sd`` prefix so they coexist with Prometheus
behind the same CML app ingress):

  GET    /sd/docs               — Swagger UI
  GET    /sd/openapi.json       — OpenAPI 3.0 spec
  GET    /sd/health             — liveness probe
  GET    /sd/targets            — Prometheus http_sd JSON (open; Prometheus pulls this)
  GET    /sd/scrape?id=&target= — auth-injecting scrape proxy for tokenized targets
  GET    /sd/registrations      — full entries + metadata, token redacted (debug/inspection)
  POST   /sd/register           — register/upsert a target        (token-guarded)
  POST   /sd/heartbeat/{id}     — refresh a target's TTL           (token-guarded)
  DELETE /sd/targets/{id}       — deregister a target              (token-guarded)

Environment variables:
  SD_REGISTRY_FILE   — NFS JSON persistence path (default: /home/cdsw/ray_sd_targets.json)
  SD_REGISTRY_TOKEN  — optional shared secret for write endpoints (open if unset)
  SD_DEFAULT_TTL     — default target TTL in seconds (default: 90)
"""

import fcntl
import hashlib
import hmac
import json
import logging
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

SD_PREFIX = "/sd"
REGISTRY_FILE = Path(os.environ.get("SD_REGISTRY_FILE", "/home/cdsw/ray_sd_targets.json"))
REGISTRY_TOKEN = os.environ.get("SD_REGISTRY_TOKEN", "").strip()
DEFAULT_TTL = int(os.environ.get("SD_DEFAULT_TTL", "90"))
SCRAPE_TIMEOUT = float(os.environ.get("SD_SCRAPE_TIMEOUT", "10"))
# Cap proxied response bodies so a hostile/broken target cannot OOM the app.
MAX_SCRAPE_BYTES = int(os.environ.get("SD_MAX_SCRAPE_BYTES", str(16 * 1024 * 1024)))

# SSRF guard: registered target hosts must end with one of these suffixes.
# Defaults to the CML domain so only in-domain CML app ingresses can be
# registered (not arbitrary internal IPs like 169.254.169.254). Set
# SD_ALLOWED_TARGET_SUFFIXES (comma-separated) to override; set it to "*" to
# disable the check (NOT recommended outside dev).
def _default_allowed_suffixes() -> List[str]:
    raw = os.environ.get("SD_ALLOWED_TARGET_SUFFIXES", "").strip()
    if raw:
        return [s.strip().lower() for s in raw.split(",") if s.strip()]
    domain = os.environ.get("CDSW_DOMAIN", "").strip().lower()
    return [domain] if domain else []


ALLOWED_TARGET_SUFFIXES = _default_allowed_suffixes()


def _parse_bool(value: Any, default: bool = False) -> bool:
    """Robustly coerce a JSON value to bool (handles the string 'false')."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "on"):
            return True
        if v in ("false", "0", "no", "off", ""):
            return False
    return default


def _validate_target_host(target: str) -> None:
    """SSRF guard: ensure a registered target host is within the allowlist.

    ``target`` is a ``host[:port]`` string. When ALLOWED_TARGET_SUFFIXES is
    empty (no CDSW_DOMAIN and no override) the check is skipped (dev mode);
    when it contains only ``*`` it is explicitly disabled.
    """
    if not ALLOWED_TARGET_SUFFIXES or ALLOWED_TARGET_SUFFIXES == ["*"]:
        return
    host = target.rsplit(":", 1)[0].strip().lower()
    # Strip IPv6 brackets if present.
    host = host.strip("[]")
    if not any(host == suf or host.endswith("." + suf) or host.endswith(suf)
               for suf in ALLOWED_TARGET_SUFFIXES):
        raise ValueError(
            f"target host '{host}' is not within the allowed suffixes "
            f"{ALLOWED_TARGET_SUFFIXES}; set SD_ALLOWED_TARGET_SUFFIXES to allow it"
        )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
class TargetRegistry:
    """Thread-safe, NFS-persisted registry of Prometheus scrape targets.

    In-memory state is guarded by a threading.Lock (ThreadingHTTPServer serves
    requests concurrently); the on-disk JSON is written atomically via a
    temp-file + ``os.replace`` and an ``fcntl`` exclusive lock, matching the
    pattern used by ``deployment_store.py`` / ``recovery_state.py``.
    """

    def __init__(self, path: Path = REGISTRY_FILE, default_ttl: int = DEFAULT_TTL):
        self._path = Path(path)
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._load()

    # -- persistence -------------------------------------------------------- #
    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                for entry in data.get("targets", []):
                    tid = entry.get("id")
                    if tid:
                        self._entries[tid] = entry
                logger.info("Loaded %d SD target(s) from %s",
                            len(self._entries), self._path)
        except Exception as exc:
            logger.warning("Could not load SD registry %s: %s", self._path, exc)
            self._entries = {}

    def _persist_locked(self) -> None:
        """Atomically write the current entries. Caller must hold self._lock."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(f"{self._path.name}.tmp.{os.getpid()}")
            payload = {
                "targets": list(self._entries.values()),
                "updated_at": time.time(),
            }
            # The registry now stores per-target bearer tokens, so keep the file
            # readable only by the owner (0600) at rest.
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                fh.write(json.dumps(payload, indent=2))
                fh.flush()
                os.fsync(fh.fileno())
                fcntl.flock(fh, fcntl.LOCK_UN)
            os.replace(tmp, self._path)
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass
        except Exception as exc:
            logger.error("Failed to persist SD registry: %s", exc)

    # -- helpers ------------------------------------------------------------ #
    @staticmethod
    def _make_id(job: str, targets: List[str], metrics_path: str) -> str:
        h = hashlib.sha1()
        h.update(job.encode())
        h.update(b"|")
        h.update("|".join(sorted(targets)).encode())
        h.update(b"|")
        h.update(metrics_path.encode())
        return h.hexdigest()[:16]

    def _prune_locked(self) -> List[str]:
        now = time.time()
        expired = [
            tid for tid, e in self._entries.items()
            if now - e.get("last_seen", 0) > e.get("ttl_seconds", self._default_ttl)
        ]
        for tid in expired:
            del self._entries[tid]
        if expired:
            logger.info("Pruned %d expired SD target(s): %s", len(expired), expired)
            self._persist_locked()
        return expired

    # -- public API --------------------------------------------------------- #
    def register(self, body: Dict[str, Any]) -> Dict[str, Any]:
        job = str(body.get("job") or "").strip()
        if not job:
            raise ValueError("'job' is required")

        targets = body.get("targets")
        if not targets:
            addr = str(body.get("address") or "").strip()
            if not addr:
                raise ValueError("provide either 'targets' (list) or 'address' (string)")
            targets = [addr]
        if not isinstance(targets, list) or not all(isinstance(t, str) and t.strip() for t in targets):
            raise ValueError("'targets' must be a non-empty list of 'host[:port]' strings")
        targets = [t.strip() for t in targets]
        for t in targets:
            _validate_target_host(t)

        scheme = str(body.get("scheme") or "https").strip().lower()
        if scheme not in ("http", "https"):
            raise ValueError("'scheme' must be 'http' or 'https'")

        metrics_path = str(body.get("metrics_path") or "/metrics").strip()
        if not metrics_path.startswith("/"):
            metrics_path = "/" + metrics_path

        labels = body.get("labels") or {}
        if not isinstance(labels, dict):
            raise ValueError("'labels' must be an object of string->string")
        # Reserved/meta labels are set by the registry; a registrant must not be
        # able to override where/how Prometheus scrapes (e.g. __address__) or the
        # identity labels. Drop any such keys from custom labels.
        _reserved = {"job", "instance"}
        labels = {
            str(k): str(v) for k, v in labels.items()
            if not str(k).startswith("__") and str(k) not in _reserved
        }

        try:
            ttl = int(body.get("ttl_seconds") or self._default_ttl)
        except (TypeError, ValueError):
            raise ValueError("'ttl_seconds' must be an integer")
        if ttl <= 0:
            raise ValueError("'ttl_seconds' must be positive")

        token = str(body.get("token") or "").strip()
        # TLS verification for the proxied scrape. Defaults to skip-verify to
        # match the existing ingress scrape jobs (CML ingress terminates TLS);
        # set false to enforce certificate verification. Parse robustly so the
        # JSON string "false" does not become True.
        tls_skip = _parse_bool(body.get("tls_insecure_skip_verify", True), default=True)

        tid = str(body.get("id") or "").strip() or self._make_id(job, targets, metrics_path)
        now = time.time()
        with self._lock:
            existing = self._entries.get(tid, {})
            entry = {
                "id": tid,
                "job": job,
                "targets": targets,
                "scheme": scheme,
                "metrics_path": metrics_path,
                "labels": {str(k): str(v) for k, v in labels.items()},
                "ttl_seconds": ttl,
                # Empty token → advertised directly; non-empty → via /sd/scrape.
                "token": token,
                "tls_insecure_skip_verify": tls_skip,
                "registered_at": existing.get("registered_at", now),
                "last_seen": now,
            }
            self._entries[tid] = entry
            self._persist_locked()
        logger.info("Registered SD target id=%s job=%s targets=%s tokened=%s",
                    tid, job, targets, bool(token))
        return self._redact(entry)

    @staticmethod
    def _redact(entry: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of an entry safe to expose (token stripped)."""
        safe = {k: v for k, v in entry.items() if k != "token"}
        safe["has_token"] = bool(entry.get("token"))
        return safe

    def get(self, tid: str) -> Optional[Dict[str, Any]]:
        """Return the raw entry (INCLUDING token) — internal use by the proxy."""
        with self._lock:
            entry = self._entries.get(tid)
            return dict(entry) if entry is not None else None

    def heartbeat(self, tid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._entries.get(tid)
            if entry is None:
                return None
            entry["last_seen"] = time.time()
            self._persist_locked()
            return self._redact(entry)

    def delete(self, tid: str) -> bool:
        with self._lock:
            removed = self._entries.pop(tid, None)
            if removed is not None:
                self._persist_locked()
            return removed is not None

    def registrations(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._prune_locked()
            now = time.time()
            out = []
            for e in self._entries.values():
                ttl = e.get("ttl_seconds", self._default_ttl)
                age = now - e.get("last_seen", 0)
                out.append({**self._redact(e), "expires_in": max(0.0, round(ttl - age, 1))})
            return out

    def http_sd(self, job_filter: Optional[str] = None,
                proxy_addr: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the Prometheus ``http_sd`` document (list of target groups).

        Targets without a token are advertised directly. Targets *with* a token
        are advertised through the local scrape proxy (``proxy_addr`` + the
        ``/sd/scrape`` path): one group per real host, carrying ``__param_id`` /
        ``__param_target`` so the proxy knows which endpoint + token to use, and
        ``instance`` set to the real host so series stay per-target.
        """
        with self._lock:
            self._prune_locked()
            groups = []
            for e in self._entries.values():
                # Defensive: never let one malformed persisted entry 500 the
                # whole discovery endpoint.
                try:
                    job = e["job"]
                    targets = e["targets"]
                    if not job or not targets:
                        continue
                    if job_filter and job != job_filter:
                        continue
                    # Custom labels first; reserved/meta labels applied last so
                    # they always win (custom was already sanitized at register).
                    custom = dict(e.get("labels", {}))
                    if e.get("token") and proxy_addr:
                        for host in targets:
                            labels = dict(custom)
                            labels.update({
                                "job": job,
                                "__scheme__": "http",
                                "__metrics_path__": f"{SD_PREFIX}/scrape",
                                "__param_id": e["id"],
                                "__param_target": host,
                                "instance": host,
                            })
                            groups.append({"targets": [proxy_addr], "labels": labels})
                    else:
                        labels = dict(custom)
                        labels.update({
                            "job": job,
                            "__scheme__": e.get("scheme", "https"),
                            "__metrics_path__": e.get("metrics_path", "/metrics"),
                        })
                        groups.append({"targets": targets, "labels": labels})
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning("Skipping malformed SD entry %s: %s",
                                   e.get("id"), exc)
            return groups

    def proxy_fetch(self, tid: str, target: Optional[str]) -> Tuple[int, bytes, str]:
        """Fetch a tokenized target's metrics, injecting its Authorization
        header. Returns (status, body, content_type). Used by /sd/scrape."""
        entry = self.get(tid)
        if entry is None:
            return 404, b"# unknown target id\n", "text/plain"
        hosts = entry.get("targets", [])
        host = target or (hosts[0] if hosts else None)
        if not host:
            return 502, b"# no target host\n", "text/plain"
        if host not in hosts:
            return 400, b"# target not registered under this id\n", "text/plain"

        url = f"{entry['scheme']}://{host}{entry['metrics_path']}"
        req = urllib.request.Request(url, method="GET")
        token = entry.get("token")
        if token:
            req.add_header("Authorization", f"Bearer {token}")

        ctx = None
        if entry["scheme"] == "https" and entry.get("tls_insecure_skip_verify", True):
            ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=SCRAPE_TIMEOUT, context=ctx) as resp:
                # Cap the body so a hostile/broken target cannot OOM the app.
                body = resp.read(MAX_SCRAPE_BYTES + 1)
                if len(body) > MAX_SCRAPE_BYTES:
                    logger.warning("proxy_fetch %s exceeded %d bytes; truncated",
                                   host, MAX_SCRAPE_BYTES)
                    body = body[:MAX_SCRAPE_BYTES]
                ctype = resp.headers.get("Content-Type", "text/plain")
                return resp.status, body, ctype
        except urllib.error.HTTPError as exc:
            return exc.code, f"# upstream status {exc.code}\n".encode(), "text/plain"
        except Exception as exc:
            # Log details server-side; return a generic message to the caller.
            logger.warning("proxy_fetch %s failed: %s", host, exc)
            return 502, b"# scrape proxy error\n", "text/plain"


# --------------------------------------------------------------------------- #
# OpenAPI + Swagger UI
# --------------------------------------------------------------------------- #
def build_openapi() -> Dict[str, Any]:
    entry_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "job": {"type": "string"},
            "targets": {"type": "array", "items": {"type": "string"}},
            "scheme": {"type": "string", "enum": ["http", "https"]},
            "metrics_path": {"type": "string"},
            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
            "ttl_seconds": {"type": "integer"},
            "registered_at": {"type": "number"},
            "last_seen": {"type": "number"},
        },
    }
    register_req = {
        "type": "object",
        "required": ["job"],
        "properties": {
            "job": {"type": "string", "description": "Prometheus job label", "example": "vllm"},
            "targets": {
                "type": "array", "items": {"type": "string"},
                "description": "Ingress-reachable host[:port] values",
                "example": ["my-model.ml-xxxx.cloudera.site:443"],
            },
            "address": {
                "type": "string",
                "description": "Convenience single-target alternative to 'targets'",
            },
            "scheme": {"type": "string", "enum": ["http", "https"], "default": "https"},
            "metrics_path": {"type": "string", "default": "/metrics"},
            "labels": {
                "type": "object", "additionalProperties": {"type": "string"},
                "example": {"team": "ml-platform"},
            },
            "ttl_seconds": {
                "type": "integer", "default": DEFAULT_TTL,
                "description": "Target expires if no heartbeat/re-register within this window",
            },
            "token": {
                "type": "string",
                "description": (
                    "Optional per-target bearer token. If set, the target is "
                    "scraped through the in-process auth-injecting proxy "
                    "(/sd/scrape) which adds 'Authorization: Bearer <token>'. "
                    "Stored 0600 at rest and never returned by read endpoints."
                ),
            },
            "tls_insecure_skip_verify": {
                "type": "boolean", "default": True,
                "description": "Skip TLS cert verification for the proxied HTTPS scrape",
            },
            "id": {
                "type": "string",
                "description": "Optional stable id; derived from job+targets+path if omitted",
            },
        },
    }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Prometheus Dynamic SD Registry",
            "version": "1.0.0",
            "description": (
                "Register/deregister Prometheus scrape targets dynamically. "
                "Prometheus pulls GET /sd/targets via http_sd_configs."
            ),
        },
        "paths": {
            "/sd/register": {
                "post": {
                    "summary": "Register or update a scrape target",
                    "description": "Upsert a target. Re-calling refreshes its TTL. "
                                   "Requires the SD_REGISTRY_TOKEN bearer/header if configured.",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": register_req}},
                    },
                    "responses": {
                        "200": {"description": "Registered",
                                "content": {"application/json": {"schema": entry_schema}}},
                        "400": {"description": "Validation error"},
                        "401": {"description": "Missing/invalid token"},
                    },
                }
            },
            "/sd/heartbeat/{id}": {
                "post": {
                    "summary": "Refresh a target's TTL",
                    "parameters": [{"name": "id", "in": "path", "required": True,
                                    "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Refreshed",
                                "content": {"application/json": {"schema": entry_schema}}},
                        "401": {"description": "Missing/invalid token"},
                        "404": {"description": "Unknown target id"},
                    },
                }
            },
            "/sd/targets/{id}": {
                "delete": {
                    "summary": "Deregister a target",
                    "parameters": [{"name": "id", "in": "path", "required": True,
                                    "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"description": "Deleted"},
                        "401": {"description": "Missing/invalid token"},
                        "404": {"description": "Unknown target id"},
                    },
                }
            },
            "/sd/targets": {
                "get": {
                    "summary": "Prometheus http_sd document",
                    "description": "The target list Prometheus scrapes. Open (no auth).",
                    "parameters": [{"name": "job", "in": "query", "required": False,
                                    "schema": {"type": "string"},
                                    "description": "Optional job filter"}],
                    "responses": {"200": {"description": "http_sd target groups"}},
                }
            },
            "/sd/scrape": {
                "get": {
                    "summary": "Auth-injecting scrape proxy (for tokenized targets)",
                    "description": "Prometheus scrapes this instead of the real "
                                   "target; the proxy injects the stored bearer "
                                   "token. Not intended for manual use.",
                    "parameters": [
                        {"name": "id", "in": "query", "required": True,
                         "schema": {"type": "string"}},
                        {"name": "target", "in": "query", "required": False,
                         "schema": {"type": "string"},
                         "description": "Specific host[:port] of the target group"},
                    ],
                    "responses": {
                        "200": {"description": "Prometheus exposition text"},
                        "404": {"description": "Unknown target id"},
                        "502": {"description": "Upstream scrape failed"},
                    },
                }
            },
            "/sd/registrations": {
                "get": {
                    "summary": "Full registration entries + metadata (token redacted)",
                    "responses": {"200": {"description": "List of entries"}},
                }
            },
            "/sd/health": {
                "get": {"summary": "Liveness probe",
                        "responses": {"200": {"description": "ok"}}},
            },
        },
        "components": {
            "securitySchemes": {
                "RegistryToken": {"type": "http", "scheme": "bearer"},
            }
        },
    }


_SWAGGER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Prometheus SD Registry — API docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: "%s/openapi.json",
      dom_id: "#swagger-ui",
    });
  </script>
</body>
</html>
""" % SD_PREFIX


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
def make_handler(registry: TargetRegistry):
    """Build a BaseHTTPRequestHandler subclass bound to the given registry."""

    class _SDHandler(BaseHTTPRequestHandler):
        server_version = "SDRegistry/1.0"

        # -- response helpers ---------------------------------------------- #
        def _send_json(self, obj: Any, status: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, html: str, status: int = 200) -> None:
            body = html.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json_body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            if not raw:
                return {}
            return json.loads(raw)

        def _authorized(self) -> bool:
            if not REGISTRY_TOKEN:
                return True  # open in dev when no token configured
            supplied = ""
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
            if not supplied:
                supplied = (self.headers.get("X-Registry-Token") or "").strip()
            return bool(supplied) and hmac.compare_digest(supplied, REGISTRY_TOKEN)

        # -- routing ------------------------------------------------------- #
        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or SD_PREFIX
            if path in (SD_PREFIX, f"{SD_PREFIX}/docs"):
                return self._send_html(_SWAGGER_HTML)
            if path == f"{SD_PREFIX}/openapi.json":
                return self._send_json(build_openapi())
            if path == f"{SD_PREFIX}/health":
                return self._send_json({"status": "ok"})
            if path == f"{SD_PREFIX}/targets":
                qs = parse_qs(parsed.query)
                job = (qs.get("job") or [None])[0]
                # Advertise tokenized targets through this same server's
                # /sd/scrape proxy (localhost address Prometheus can reach).
                host, port = self.server.server_address[:2]
                proxy_addr = f"{host}:{port}"
                return self._send_json(
                    registry.http_sd(job_filter=job, proxy_addr=proxy_addr))
            if path == f"{SD_PREFIX}/scrape":
                qs = parse_qs(parsed.query)
                tid = (qs.get("id") or [""])[0]
                target = (qs.get("target") or [None])[0]
                status, body, ctype = registry.proxy_fetch(tid, target)
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == f"{SD_PREFIX}/registrations":
                return self._send_json(registry.registrations())
            return self._send_json({"error": "not found", "path": self.path}, 404)

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            if not self._authorized():
                return self._send_json({"error": "unauthorized"}, 401)
            if path == f"{SD_PREFIX}/register":
                try:
                    body = self._read_json_body()
                except Exception as exc:
                    return self._send_json({"error": f"invalid JSON: {exc}"}, 400)
                try:
                    entry = registry.register(body)
                except ValueError as exc:
                    return self._send_json({"error": str(exc)}, 400)
                return self._send_json(entry, 200)
            if path.startswith(f"{SD_PREFIX}/heartbeat/"):
                tid = path[len(f"{SD_PREFIX}/heartbeat/"):]
                entry = registry.heartbeat(tid)
                if entry is None:
                    return self._send_json({"error": "unknown id", "id": tid}, 404)
                return self._send_json(entry, 200)
            return self._send_json({"error": "not found", "path": self.path}, 404)

        def do_DELETE(self):
            path = urlparse(self.path).path.rstrip("/")
            if not self._authorized():
                return self._send_json({"error": "unauthorized"}, 401)
            if path.startswith(f"{SD_PREFIX}/targets/"):
                tid = path[len(f"{SD_PREFIX}/targets/"):]
                if registry.delete(tid):
                    return self._send_json({"status": "deleted", "id": tid}, 200)
                return self._send_json({"error": "unknown id", "id": tid}, 404)
            return self._send_json({"error": "not found", "path": self.path}, 404)

        def log_message(self, fmt, *args):
            pass  # silent; the launcher owns stdout

    return _SDHandler


def run_registry_server(port: int, host: str = "127.0.0.1",
                        registry: Optional[TargetRegistry] = None) -> None:
    """Create and start (serve_forever) an SD registry server. Blocks."""
    registry = registry or TargetRegistry()
    handler = make_handler(registry)

    class _ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    httpd = _ReusableServer((host, port), handler)
    httpd.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s -- %(message)s")
    _port = int(os.environ.get("SD_REGISTRY_PORT", "9099"))
    print(f"SD registry listening on 127.0.0.1:{_port} (Swagger UI at {SD_PREFIX}/docs)")
    run_registry_server(_port)
