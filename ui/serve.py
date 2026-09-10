#!/usr/bin/env python3
"""Zero-dependency dev server + CORS-bypass proxy for the model chat UI.

The deployed vLLM app does NOT send CORS headers, so a browser that loads
chat.html from file:// (or any other origin) is blocked from calling the
endpoint directly. This tiny server sidesteps that entirely:

  * GET  /                -> serves chat.html
  * GET  /chat.html       -> serves chat.html
  * ANY  /<anything else> -> transparently proxied to TARGET/<same path>,
                             streaming the response (works for SSE).

Because the browser only ever talks to http://localhost:<port> (same origin),
there is no cross-origin request and therefore no CORS problem.

Usage:
    python3 serve.py                       # target = default cluster URL
    python3 serve.py --target https://host # point at a different base URL
    python3 serve.py --port 9000
    TARGET=https://host python3 serve.py

Then open http://localhost:8080/  and leave "Base URL" pointing at this
proxy (the page auto-fills it). Keep the route prefix (e.g. qwen3-35b).

Stdlib only — no pip install required.
"""
from __future__ import annotations

import argparse
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest

DEFAULT_TARGET = "https://ray-cluster-head.ml-e8a34ebc-1ec.qzhong-1.a465-9q4k.cloudera.site"
HERE = Path(__file__).resolve().parent
HTML_PATH = HERE / "chat.html"

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    target = DEFAULT_TARGET  # overridden in main()

    # --- static page -----------------------------------------------------
    def _serve_html(self) -> None:
        try:
            body = HTML_PATH.read_bytes()
        except OSError as exc:
            self.send_error(500, f"cannot read chat.html: {exc}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Never let the browser cache the page — otherwise edits to chat.html
        # don't take effect without a manual hard-reload.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _is_page(self) -> bool:
        path = self.path.split("?", 1)[0]
        return path in ("/", "/index.html", "/chat.html")

    # --- proxy ------------------------------------------------------------
    def _proxy(self) -> None:
        url = self.target.rstrip("/") + self.path
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else None

        fwd_headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        req = urlrequest.Request(url, data=body, method=self.command, headers=fwd_headers)

        try:
            # No read timeout: streaming (SSE) responses stay open.
            upstream = urlrequest.urlopen(req, timeout=None)
        except urlerror.HTTPError as exc:
            # Forward the upstream error response verbatim.
            self.send_response(exc.code)
            payload = exc.read()
            for k, v in exc.headers.items():
                if k.lower() not in _HOP_BY_HOP and k.lower() != "content-length":
                    self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        except (urlerror.URLError, OSError) as exc:
            self.send_error(502, f"upstream error: {exc}")
            return

        with upstream:
            self.send_response(upstream.status)
            # Force chunked streaming: drop upstream Content-Length so we can
            # flush SSE bytes as they arrive.
            for k, v in upstream.headers.items():
                if k.lower() in _HOP_BY_HOP or k.lower() == "content-length":
                    continue
                self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                while True:
                    chunk = upstream.read(1024)
                    if not chunk:
                        break
                    size = f"{len(chunk):X}\r\n".encode("ascii")
                    self.wfile.write(size + chunk + b"\r\n")
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client navigated away / hit Stop

    # --- verbs ------------------------------------------------------------
    def do_GET(self) -> None:
        if self._is_page():
            self._serve_html()
        else:
            self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def do_OPTIONS(self) -> None:
        self._proxy()

    def log_message(self, fmt: str, *args) -> None:  # quieter logs
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=os.environ.get("TARGET", DEFAULT_TARGET),
                    help="Upstream base URL to proxy to (no trailing path).")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    args = ap.parse_args()

    if not HTML_PATH.exists():
        print(f"error: {HTML_PATH} not found (run from the ui/ directory)", file=sys.stderr)
        return 1

    Handler.target = args.target.rstrip("/")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"Serving chat UI  ->  {url}")
    print(f"Proxying /v1/... ->  {Handler.target}")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
