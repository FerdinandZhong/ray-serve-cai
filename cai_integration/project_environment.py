"""Read-only AMP project-environment preflight; never log variable values."""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def validate_project_environment(raw):
    """Validate the JSON string returned by API v2 GetProject."""
    if raw in (None, ""):
        return
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        raise ValueError("Project environment is invalid JSON (values omitted)") from None
    if not isinstance(values, dict):
        raise ValueError("Project environment must be an object of string values")
    invalid = [key for key, value in values.items() if not isinstance(value, str)]
    if invalid:
        raise ValueError(
            "Project environment contains non-string values for: "
            + ", ".join(sorted(invalid))
            + ". Values must be strings, not browser events or AMP definitions. "
            "Lost input values cannot be reconstructed from defaults. "
            "Use the input-safe cai_integration.create_amp entry point for new "
            "imports. No settings were changed."
        )


def preflight_project_environment():
    """Fail before costly setup/application creation using the job's credentials."""
    host = os.environ.get("CML_HOST") or "https://" + os.environ.get("CDSW_DOMAIN", "")
    token = os.environ.get("CML_API_KEY") or os.environ.get("CDSW_APIV2_KEY")
    project = os.environ.get("CDSW_PROJECT_ID") or os.environ.get("CML_PROJECT_ID")
    if host == "https://" or not token or not project:
        raise RuntimeError("AMP project preflight requires host, project ID and API credentials")
    request = Request(
        f"{host.rstrip('/')}/api/v2/projects/{project}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            data = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"AMP project preflight failed: HTTP {exc.code}; no settings changed") from None
    if "environment" not in data:
        raise RuntimeError("GetProject omitted environment; cannot validate AMP setup")
    validate_project_environment(data["environment"])
    print("Project environment preflight passed (string values; values not logged)")


if __name__ == "__main__":
    preflight_project_environment()
