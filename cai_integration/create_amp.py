"""Input-safe AMP import through API v2; dry-run unless --apply is supplied.

This bypasses the Workbench import form, not Workbench authentication. It creates
a NEW AMP only; it never edits an existing project's environment.
"""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]


def resolve_inputs(definitions, overrides):
    """Resolve defaults once, then preserve explicit string inputs verbatim."""
    if not isinstance(overrides, dict):
        raise ValueError("AMP overrides must be a JSON object")
    unknown = set(overrides) - set(definitions)
    if unknown:
        raise ValueError("Unknown AMP inputs: " + ", ".join(sorted(unknown)))
    result = {}
    for name, spec in definitions.items():
        value = overrides[name] if name in overrides else spec.get("default", "")
        if not isinstance(value, str):
            raise ValueError(f"{name}: expected a string, not an event/object; values omitted")
        if spec.get("required") and not value.strip():
            raise ValueError(f"{name}: required input is empty")
        result[name] = value
    for name in (
        "RAY_HEAD_CPU", "RAY_HEAD_MEMORY", "RAY_WORKER_CPU", "RAY_WORKER_MEMORY",
        "RAY_WORKER_GPUS", "TENSOR_PARALLEL_SIZE",
    ):
        value = result.get(name, "")
        if not value.strip():
            continue  # Existing launcher treats blank optional fields as unset.
        try:
            number = int(value)
        except ValueError:
            raise ValueError(f"{name}: expected an integer string") from None
        minimum = 0 if name == "RAY_WORKER_GPUS" else 1
        if number < minimum:
            raise ValueError(f"{name}: resource value is out of range")
    initial = result.get("RAY_LAUNCH_INITIAL_WORKERS", "false").strip().lower()
    if initial not in ("", "true", "false"):
        raise ValueError("RAY_LAUNCH_INITIAL_WORKERS: expected true or false")
    return result


def build_payload(manifest, overrides, *, name, runtime, git_url, git_ref):
    return {
        "configure_prototype_request": {
            "execute_amp_steps": True,
            "run_import_tasks": True,
            "runtime_identifier": runtime,
        },
        "create_project_request": {
            "name": name,
            "description": manifest["description"],
            "template": "git",
            "visibility": "private",
            "git_url": git_url,
            "git_ref": git_ref,
            "environment": resolve_inputs(manifest["environment_variables"], overrides),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--git-url", default="https://github.com/FerdinandZhong/ray-serve-cai.git")
    parser.add_argument("--git-ref", required=True, help="Ref containing the AMP code to deploy")
    parser.add_argument("--env-file", type=Path, help="JSON object with string values; values are not logged")
    parser.add_argument("--host", default=os.environ.get("CML_HOST"))
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--apply", action="store_true", help="Create a NEW project and run its AMP jobs")
    args = parser.parse_args(argv)
    try:
        overrides = json.loads(args.env_file.read_text()) if args.env_file else {}
        if not isinstance(overrides, dict):
            raise ValueError("AMP overrides must be a JSON object")
        manifest = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
        payload = build_payload(
            manifest, overrides, name=args.name, runtime=args.runtime,
            git_url=args.git_url, git_ref=args.git_ref,
        )
    except (ValueError, OSError) as exc:
        # JSON decoder messages can include input fragments; don't print them.
        if isinstance(exc, json.JSONDecodeError):
            parser.error("Invalid environment JSON; values omitted")
        parser.error(str(exc))
    env = payload["create_project_request"]["environment"]
    print(f"Validated {len(env)} string-valued AMP inputs; values omitted")
    if not args.apply:
        print("Dry run: no API requests, project changes or jobs. Use --apply to create a NEW AMP.")
        return 0
    parsed = urlparse(args.host or "")
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        parser.error("--host must be an HTTPS Workbench URL without credentials")
    token = args.token_file.expanduser().read_text().strip() if args.token_file else os.environ.get("CML_API_KEY", "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        parser.error("--apply requires --token-file or CML_API_KEY")
    response = requests.post(
        args.host.rstrip("/") + "/api/v2/amps", json=payload,
        headers={"Authorization": f"Bearer {token}"}, timeout=60,
        allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"AMP creation HTTP {response.status_code}; response body omitted")
    print("AMP creation accepted. Monitor AMP job status; acceptance is not deployment readiness.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
