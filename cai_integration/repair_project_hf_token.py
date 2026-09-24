#!/usr/bin/env python3
"""Repair a malformed AMP Hugging Face token without exposing its value."""

import argparse
import getpass
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

TOKEN_KEY = "HUGGING_FACE_HUB_TOKEN"


def _request(url: str, credential: str, *, method: str = "GET", body=None):
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {credential}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else {}
    except HTTPError as exc:
        raise RuntimeError(f"Project API {method} failed: HTTP {exc.code}; response omitted") from None
    except URLError as exc:
        raise RuntimeError(f"Project API {method} failed: {exc.reason}") from None


def _environment(project):
    raw = project.get("environment")
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        raise ValueError("Project environment is invalid JSON; no settings changed") from None
    if not isinstance(values, dict):
        raise ValueError("Project environment is not an object; no settings changed")
    return values


def repair_project_hf_token(host: str, project_id: str, credential: str, hf_token: str):
    """Replace only the malformed token and verify the project environment."""
    if not hf_token.strip():
        raise ValueError("Hugging Face token is empty")
    parsed = urlparse(host)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("Workbench host must be an HTTPS URL without credentials")
    url = f"{host.rstrip('/')}/api/v2/projects/{project_id}"
    values = _environment(_request(url, credential))
    invalid = [key for key, value in values.items() if key != TOKEN_KEY and not isinstance(value, str)]
    if invalid:
        raise ValueError("Other non-string project variables remain: " + ", ".join(sorted(invalid)))
    updated = dict(values)
    updated[TOKEN_KEY] = hf_token.strip()
    _request(url, credential, method="PATCH", body={"environment": json.dumps(updated)})
    verified = _environment(_request(url, credential))
    if verified != updated:
        raise RuntimeError("Project environment did not match the requested update; values omitted")
    print("Project Hugging Face token repaired and verified; value omitted")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("CML_HOST") or "https://" + os.environ.get("CDSW_DOMAIN", ""))
    parser.add_argument("--project-id", default=os.environ.get("CDSW_PROJECT_ID") or os.environ.get("CML_PROJECT_ID"))
    parser.add_argument("--cml-token-file", type=Path)
    parser.add_argument("--hf-token-file", type=Path)
    args = parser.parse_args(argv)
    if not args.project_id:
        parser.error("Provide --project-id or run inside the CAI project")
    credential = (
        args.cml_token_file.expanduser().read_text().strip()
        if args.cml_token_file else os.environ.get("CDSW_APIV2_KEY") or os.environ.get("CML_API_KEY")
    )
    if not credential:
        parser.error("Provide --cml-token-file or run inside the CAI project")
    if credential.lower().startswith("bearer "):
        credential = credential[7:].strip()
    hf_token = (
        args.hf_token_file.expanduser().read_text().strip()
        if args.hf_token_file else getpass.getpass("Hugging Face token: ").strip()
    )
    repair_project_hf_token(args.host, args.project_id, credential, hf_token)


if __name__ == "__main__":
    main()
