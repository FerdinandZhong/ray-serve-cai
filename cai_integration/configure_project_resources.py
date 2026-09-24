#!/usr/bin/env python3
"""Configure project resources required by Ray before creating applications."""

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_SHARED_MEMORY_MB = 40000


def _connection():
    host = os.environ.get("CML_HOST")
    if not host:
        domain = os.environ.get("CDSW_DOMAIN", "").strip()
        host = f"https://{domain}" if domain else ""
    token = os.environ.get("CML_API_KEY") or os.environ.get("CDSW_APIV2_KEY")
    project_id = os.environ.get("CDSW_PROJECT_ID") or os.environ.get("CML_PROJECT_ID")
    if not host or not token or not project_id:
        raise RuntimeError("Project resource setup requires host, project ID, and API credentials")
    return host.rstrip("/"), token.strip(), project_id


def _request(url, token, *, method="GET", body=None):
    payload = json.dumps(body).encode() if body is not None else None
    request = Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RuntimeError(f"Project resource setup failed: HTTP {exc.code}: {detail}") from None
    except URLError as exc:
        raise RuntimeError(f"Project resource setup failed: {exc.reason}") from None


def configure_project_resources():
    """Set and verify the project-wide /dev/shm limit used by new CAI apps."""
    host, token, project_id = _connection()
    shared_memory_mb = DEFAULT_SHARED_MEMORY_MB
    url = f"{host}/api/v2/projects/{project_id}"

    _request(url, token, method="PATCH", body={"shared_memory_limit": shared_memory_mb})
    project = _request(url, token)
    actual = project.get("shared_memory_limit")
    try:
        actual = int(actual)
    except (TypeError, ValueError):
        raise RuntimeError("Project resource verification omitted shared_memory_limit") from None
    if actual != shared_memory_mb:
        raise RuntimeError(
            f"Project resource verification failed: requested {shared_memory_mb} MB, got {actual} MB"
        )
    print(f"Project shared memory configured: {actual} MB (applies to newly created applications)")


def main():
    try:
        configure_project_resources()
    except (RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    _rc = main()
    if _rc:
        sys.exit(_rc)
