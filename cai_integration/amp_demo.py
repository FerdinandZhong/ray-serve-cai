#!/usr/bin/env python3
"""
AMP demo task: deploy a model via the Management API and run one sample query.

Is created by the AMP import and deliberately run manually after the user has
added a GPU worker through the Management API. Uses only workbench-injected env vars:

    CDSW_DOMAIN        → head URL  https://ray-cluster-head.<CDSW_DOMAIN>
    CDSW_APIV2_KEY     → Authorization: Bearer for the Management API

Configurable (AMP environment_variables, see .project-metadata.yaml):
    VLLM_MODEL_ID           (default Qwen/Qwen3.8-27B-FP8)
    TENSOR_PARALLEL_SIZE    (default 2)
    RAY_WORKER_NODE_TYPE    (default gpu-worker)
    HUGGING_FACE_HUB_TOKEN  (default "" — gated models only)

Exit codes: 0 = the model reached RUNNING and returned a non-empty completion;
1 = readiness, deployment, or inference failed (including timeouts).
"""

import os
import sys
import time
from pathlib import Path

# Re-exec under the base venv (has `requests`), same pattern as
# test_list_applications.py — only if we aren't already running under it.
_VENV_PYTHON = Path("/home/cdsw/.venv/bin/python")


def _running_in_venv(venv_python: Path) -> bool:
    """Check the active environment, not interpreter symlink identity."""
    return Path(sys.prefix).resolve() == venv_python.parent.parent.resolve()


def _demo_script_path() -> Path:
    if globals().get("__file__"):
        return Path(__file__).resolve()
    for root in (Path(os.environ.get("CDSW_PROJECT_DIR") or Path.cwd()), Path.cwd(), Path.cwd().parent):
        script = root / "cai_integration" / "amp_demo.py"
        if script.is_file():
            return script.resolve()
    raise RuntimeError("Cannot locate cai_integration/amp_demo.py in the project checkout")


if __name__ == "__main__" and _VENV_PYTHON.exists() and not _running_in_venv(_VENV_PYTHON):
    # Never pass notebook kernel argv to the child Python process.
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), "-u", str(_demo_script_path())])

import requests  # noqa: E402 - the venv re-exec above must happen before import

# ── Config ───────────────────────────────────────────────────────────────────
HEAD_APP_NAME = "ray-cluster-head"   # must match configs/ray_cluster_config.yaml
MODEL_NAME = "qwen3-27b-demo"
ROUTE_PREFIX = "/qwen3-demo"

READY_POLL_S = 10
READY_TIMEOUT_S = 600       # Management API should be up within 10 min
DEPLOY_TIMEOUT_S = 1800     # 27B FP8 download + load
TERMINAL_FAILURE_STATES = {"DEPLOY_FAILED", "UNHEALTHY"}


def _application_status(response) -> str:
    """Return a normalized Ray status, tolerating malformed API responses."""
    if response.status_code != 200:
        return "?"
    payload = response.json()
    if not isinstance(payload, dict):
        return "?"
    status = str(payload.get("status", "?")).upper()
    return status.rsplit(".", 1)[-1]

def main() -> int:
    domain = os.environ.get("CDSW_DOMAIN", "").strip()
    api_key = os.environ.get("CDSW_APIV2_KEY", "").strip()
    if not domain or not api_key:
        print("❌ CDSW_DOMAIN / CDSW_APIV2_KEY missing — this script must run "
              "inside a workbench job/session.")
        return 1

    base = f"https://{HEAD_APP_NAME}.{domain}"
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    model = os.environ.get("VLLM_MODEL_ID", "Qwen/Qwen3.8-27B-FP8")
    tp = int(os.environ.get("TENSOR_PARALLEL_SIZE", "2"))
    node_type = os.environ.get("RAY_WORKER_NODE_TYPE", "gpu-worker")
    hf_token = os.environ.get("HUGGING_FACE_HUB_TOKEN", "").strip()

    print(f"Management API : {base}")
    print(f"Model          : {model} (tp={tp}, node_type={node_type})")

    # ── 1. Wait for the Management API ───────────────────────────────────────
    deadline = time.time() + READY_TIMEOUT_S
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/api/health", headers=headers, timeout=10)
            if r.status_code == 200:
                print(f"✅ Management API ready (HTTP {r.status_code})")
                break
        except requests.RequestException:
            pass
        print(f"  … waiting for {base} ({int(deadline - time.time())}s left)")
        time.sleep(READY_POLL_S)
    else:
        print(f"❌ Management API not reachable at {base} — check the "
              f"launch_ray_cluster job logs.")
        return 1

    # ── 2. Deploy the model (idempotent: reuse if already deployed) ──────────
    try:
        existing = requests.get(f"{base}/api/v1/applications/{MODEL_NAME}",
                                headers=headers, timeout=15)
    except requests.RequestException as exc:
        print(f"❌ could not check existing deployment: {exc}")
        return 1
    if existing.status_code == 200:
        print(f"ℹ️  {MODEL_NAME} already deployed — skipping deploy.")
    elif existing.status_code == 404:
        deploy = {
            "name": MODEL_NAME,
            "engine_type": "vllm",
            "model": model,
            "route_prefix": ROUTE_PREFIX,
            "tensor_parallel_size": tp,
            "engine_config": {
                "dtype": "auto",
                "max_model_len": 131072,
                "gpu_memory_utilization": 0.95,
                "enable_prefix_caching": True,
                "trust_remote_code": True,
            },
            "scheduling": {"resources": {f"node_type:{node_type}": 0.001}},
        }
        if hf_token:
            deploy["scheduling"]["env_vars"] = {"HUGGING_FACE_HUB_TOKEN": hf_token}
        try:
            r = requests.post(f"{base}/api/v1/applications",
                              headers=headers, json=deploy, timeout=30)
        except requests.RequestException as exc:
            print(f"❌ deploy request failed: {exc}")
            return 1
        if r.status_code not in (200, 201, 202):
            print(f"❌ deploy failed: HTTP {r.status_code}: {r.text[:500]}")
            return 1
        print(f"✅ deploy accepted ({r.status_code}) — polling for RUNNING…")
    else:
        print(f"❌ could not check existing deployment: HTTP "
              f"{existing.status_code}: {existing.text[:500]}")
        return 1

    # ── 3. Poll until RUNNING ────────────────────────────────────────────────
    deadline = time.time() + DEPLOY_TIMEOUT_S
    status = ""
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}{ROUTE_PREFIX}/v1/models",
                             headers=headers, timeout=15)
            if r.status_code == 200:
                status = "RUNNING"
                break
            r = requests.get(f"{base}/api/v1/applications/{MODEL_NAME}",
                             headers=headers, timeout=15)
            status = _application_status(r)
        except (requests.RequestException, ValueError) as exc:
            status = "?"
            print(f"  … transient status check failure: {exc}")
        if status in TERMINAL_FAILURE_STATES:
            print(f"❌ {MODEL_NAME} entered terminal failure state {status}")
            return 1
        print(f"  … {MODEL_NAME} status={status} ({int(deadline - time.time())}s left)")
        time.sleep(30)

    if status != "RUNNING":
        print(f"❌ {MODEL_NAME} did not become ready within {DEPLOY_TIMEOUT_S}s "
              f"(last status={status}). Check {base}/docs → "
              f"GET /api/v1/applications/{MODEL_NAME}")
        return 1

    # ── 4. Sample query ──────────────────────────────────────────────────────
    print("✅ model is serving — running sample query…")
    try:
        r = requests.post(
            f"{base}{ROUTE_PREFIX}/v1/completions",
            headers=headers,
            json={
                "model": model,
                "prompt": "In one sentence, what is tensor parallelism?",
                "max_tokens": 128,
            },
            timeout=120,
        )
    except requests.RequestException as exc:
        print(f"❌ query request failed: {exc}")
        return 1
    if r.status_code != 200:
        print(f"❌ query failed: HTTP {r.status_code}: {r.text[:500]}")
        return 1
    try:
        reply = r.json()["choices"][0]["text"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        print(f"❌ query returned an invalid completion payload: {exc}")
        return 1
    if not isinstance(reply, str) or not reply.strip():
        print("❌ query returned an empty completion")
        return 1
    print("\n" + "─" * 60)
    print("ASSISTANT:", reply.strip())
    print("─" * 60)
    print(f"\n🎉 Demo complete. Explore at {base}/ (UI) or {base}/docs (Swagger).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
