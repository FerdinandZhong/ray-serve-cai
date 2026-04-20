#!/usr/bin/env python3
"""
Deploy standalone YOLO API as a CAI Application (no Ray Serve).

Creates/updates a CAI Application that runs yolo_api_server.py directly via
uvicorn — useful as a benchmark baseline against the Ray Serve dynamic-batching
YOLO engine.

Environment Variables:
    CDSW_APIV2_KEY      Cloudera AI API key (required)
    CDSW_DOMAIN         CAI domain URL (required)
    MODEL_PATH          Path to YOLO .pt weights (default: /home/cdsw/models/Yolo8n_finetuned/best.pt)
    DEVICE              '0' for GPU, 'cpu' for CPU (default: cpu)
    CONF_THRESHOLD      Detection confidence threshold (default: 0.25)
    IOU_THRESHOLD       NMS IoU threshold (default: 0.45)

Usage (local):
    python benchmark_scripts/launch_yolo_standalone.py

Usage (CAI job):
    Set script to benchmark_scripts/launch_yolo_standalone.py
"""

import argparse
import os
import sys
from typing import Dict, Any

import requests


APP_NAME = "yolo-standalone-benchmark"
APP_SUBDOMAIN = "yolo-standalone"
APP_SCRIPT = "benchmark_scripts/run_yolo_server.py"


def get_cai_client(api_key: str, domain: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    })
    session.verify = False
    return session


def get_project_id(client: requests.Session, domain: str) -> str:
    project_name = os.getenv("CDSW_PROJECT") or os.getenv("CAI_PROJECT_NAME")
    url = f"{domain}/api/v2/projects"
    resp = client.get(url)
    resp.raise_for_status()
    data = resp.json()
    projects = data.get("projects", data) if isinstance(data, dict) else data

    if not project_name:
        # Try to infer from CDSW_PROJECT_ID
        pid = os.getenv("CDSW_PROJECT_ID") or os.getenv("CML_PROJECT_ID")
        if pid:
            return str(pid)
        project_name = "ray-cluster"

    for p in projects:
        if p.get("name") == project_name:
            return str(p["id"])

    available = [p.get("name") for p in projects]
    raise ValueError(f"Project '{project_name}' not found. Available: {available}")


def create_or_update_app(
    client: requests.Session,
    domain: str,
    project_id: str,
    model_path: str,
    device: str,
    conf: str,
    iou: str,
    use_gpu: bool,
    runtime: str,
) -> Dict[str, Any]:
    list_url = f"{domain}/api/v2/projects/{project_id}/applications"

    app_config = {
        "name": APP_NAME,
        "subdomain": APP_SUBDOMAIN,
        "script": APP_SCRIPT,
        "cpu": 4,
        "memory": 16,
        "bypass_authentication": True,
        "runtime_identifier": runtime,
        "environment": {
            "MODEL_PATH": model_path,
            "BACKEND": "ultralytics",
            "CONF_THRESHOLD": conf,
            "IOU_THRESHOLD": iou,
            "DEVICE": device,
        },
    }
    if use_gpu:
        app_config["nvidia_gpu"] = 1

    # Check if app already exists
    resp = client.get(list_url)
    resp.raise_for_status()
    data = resp.json()
    apps = data.get("applications", data) if isinstance(data, dict) else data

    existing = next((a for a in apps if a.get("name") == APP_NAME), None)

    if existing:
        app_id = existing["id"]
        print(f"  Updating existing application: {APP_NAME} [{app_id}]")
        update_url = f"{list_url}/{app_id}"
        client.patch(update_url, json=app_config).raise_for_status()
        client.post(f"{update_url}/restart").raise_for_status()
        print(f"  Application updated and restarting.")
        return existing
    else:
        print(f"  Creating new application: {APP_NAME}")
        resp = client.post(list_url, json=app_config)
        resp.raise_for_status()
        created = resp.json()
        print(f"  Application created: {created.get('id', 'N/A')}")
        return created


def main():
    parser = argparse.ArgumentParser(
        description="Deploy standalone YOLO API as CAI Application"
    )
    parser.add_argument("--api-key", default=os.getenv("CDSW_APIV2_KEY"))
    parser.add_argument("--domain", default=os.getenv("CDSW_DOMAIN"))
    parser.add_argument("--model", default=os.getenv(
        "MODEL_PATH", "/home/cdsw/models/Yolo8n_finetuned/best.pt"))
    parser.add_argument("--device", default=os.getenv("DEVICE", "cpu"))
    parser.add_argument("--conf", default=os.getenv("CONF_THRESHOLD", "0.25"))
    parser.add_argument("--iou", default=os.getenv("IOU_THRESHOLD", "0.45"))
    parser.add_argument("--runtime", default=os.getenv(
        "RUNTIME_ID",
        "docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-standard:2026.01.1-b6",
    ))
    args, _ = parser.parse_known_args()

    use_gpu = args.device != "cpu"
    if use_gpu:
        # Switch to CUDA runtime
        args.runtime = args.runtime.replace("-standard:", "-cuda:")

    print("=" * 60)
    print("Deploy Standalone YOLO API (benchmark baseline)")
    print("=" * 60)
    print(f"  Model:    {args.model}")
    print(f"  Device:   {args.device}")
    print(f"  GPU:      {'Yes' if use_gpu else 'No (CPU)'}")
    print(f"  Runtime:  {args.runtime[:60]}...")
    print()

    if not args.api_key:
        print("ERROR: CDSW_APIV2_KEY not set.")
        return 1
    if not args.domain:
        print("ERROR: CDSW_DOMAIN not set.")
        return 1

    if not args.domain.startswith(("http://", "https://")):
        args.domain = f"https://{args.domain}"

    try:
        client = get_cai_client(args.api_key, args.domain)
        project_id = get_project_id(client, args.domain)
        print(f"  Project ID: {project_id}")
        print()

        app = create_or_update_app(
            client, args.domain, project_id,
            args.model, args.device, args.conf, args.iou,
            use_gpu, args.runtime,
        )

        print()
        print("=" * 60)
        app_url = f"https://{APP_SUBDOMAIN}.{args.domain.replace('https://', '')}"
        print(f"  App URL:    {app_url}")
        print(f"  Health:     {app_url}/health")
        print(f"  Swagger:    {app_url}/docs")
        print(f"  Detect:     {app_url}/v1/detect")
        print()
        print("  Wait 1-2 min for the app to start, then:")
        print(f"  curl -sk {app_url}/health")
        print("=" * 60)
        return 0

    except Exception as exc:
        print(f"ERROR: {exc}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    rc = main()
    if rc != 0:
        sys.exit(rc)
