# Isolated Inference Environments — Architecture

Each inference engine runs in its own NFS-mounted venv to prevent dependency conflicts
(e.g. vLLM and SGLang require mutually exclusive `llguidance` versions). The head node
runs only the root env and never imports engine packages.

## Architecture Diagram

```mermaid
flowchart TD
    subgraph CLIENT["Client"]
        C[HTTP Request]
    end

    subgraph HEAD["Head Node — /home/cdsw/.venv (root env)"]
        direction TB
        NGINX[nginx reverse proxy]
        MGMT["Management API\n/api/v1/..."]
        REG["EngineRegistry\nvllm · sglang · yolo · mcp · litellm"]
        RAY_HEAD["Ray GCS / Dashboard"]

        NGINX --> MGMT
        MGMT --> REG
        MGMT --> RAY_HEAD
    end

    subgraph REGISTRATION["Engine Registration (two paths)"]
        STATIC["Static (startup)\nengines/__init__.py\nstub-fallback if lib missing"]
        DYNAMIC["Dynamic API\nPOST /api/v1/engines/register\nallowlist · 403 · 409 · audit log"]
        STATIC --> REG
        DYNAMIC --> REG
    end

    subgraph WORKERS["Worker Nodes — Ray actors via runtime_env py_executable"]
        direction LR

        subgraph W_VLLM["/home/cdsw/.venv-vllm"]
            VLLM_ACT["VLLMEngine actor\n(in-process vllm import)\nray_actor_options:\n  py_executable: .venv-vllm/bin/python"]
        end

        subgraph W_SGLANG["/home/cdsw/.venv-sglang"]
            SGLANG_ACT["SGLangEngine actor\n(FastAPI ingress)\nray_actor_options:\n  py_executable: .venv-sglang/bin/python"]
            SGLANG_PROC["SGLang subprocess\n.venv-sglang/bin/python\n-m sglang.launch_server"]
            SGLANG_ACT -->|"Popen explicit venv python"| SGLANG_PROC
        end

        subgraph W_LITELLM["/home/cdsw/.venv-litellm"]
            LT_ACT["LiteLLMEngine actor\n(FastAPI ingress)\nruntime_env: py_executable\n.venv-litellm/bin/python"]
            LT_PROC["LiteLLM proxy subprocess\n.venv-litellm/bin/python\n.venv-litellm/bin/litellm"]
            LT_ACT -->|"Popen explicit venv python"| LT_PROC
        end

        subgraph W_YOLO["/home/cdsw/.venv-yolo"]
            YOLO_ACT["YOLOEngine actor\n(in-process ultralytics)\npy_executable: .venv-yolo/bin/python"]
        end

        subgraph W_MCP["/home/cdsw/.venv-mcp"]
            MCP_ACT["MCPEngine actor\n(in-process mcp)\npy_executable: .venv-mcp/bin/python"]
        end
    end

    subgraph SETUP["Build-time Provisioning (setup_environment.py)"]
        UV["uv venv + uv pip install\nper engine, NFS-safe fcntl.flock"]
        UV --> W_VLLM
        UV --> W_SGLANG
        UV --> W_LITELLM
        UV --> W_YOLO
        UV --> W_MCP
    end

    C --> NGINX
    RAY_HEAD -->|"deploy / route"| VLLM_ACT
    RAY_HEAD -->|"deploy / route"| SGLANG_ACT
    RAY_HEAD -->|"deploy / route"| LT_ACT
    RAY_HEAD -->|"deploy / route"| YOLO_ACT
    RAY_HEAD -->|"deploy / route"| MCP_ACT

    style HEAD fill:#e8f4e8,stroke:#4a9e4a
    style W_VLLM fill:#ddeeff,stroke:#3377cc
    style W_SGLANG fill:#ddeeff,stroke:#3377cc
    style W_LITELLM fill:#ddeeff,stroke:#3377cc
    style W_YOLO fill:#ddeeff,stroke:#3377cc
    style W_MCP fill:#ddeeff,stroke:#3377cc
    style SETUP fill:#fff8e1,stroke:#f0a500
    style REGISTRATION fill:#f3e8ff,stroke:#8844cc
```

## Key Design Rules

| Rule | Detail |
|---|---|
| **Root venv is engine-free** | `/home/cdsw/.venv` contains only `ray[serve]`, FastAPI, nginx, management deps. `import vllm/sglang/litellm` on the head raises `ImportError`. |
| **`py_executable` for isolation** | All factories set `ray_actor_options["runtime_env"] = {"py_executable": f"{venv}/bin/python"}`. Ray starts the actor worker process under the specified interpreter. |
| **Subprocess engines use explicit Python** | SGLang and LiteLLM launch a child process; both use the venv Python path directly (`{venv}/bin/python -m sglang.launch_server`, `{venv}/bin/python {venv}/bin/litellm`), not `sys.executable`. |
| **NFS-safe provisioning** | `setup_environment.py` uses `fcntl.flock` on `.venv-<engine>.lock` to prevent concurrent venv creation races on shared NFS. |
| **Fail loud, no pip fallback** | If a per-engine venv doesn't exist the factory skips wiring `py_executable` (venv path check) and Ray falls back to the root env — making the missing venv visible immediately. |
| **Head-safe registration** | `engines/__init__.py` imports only config/factory modules (no heavy libs) and falls back to a stub class if the engine module can't import. All 5 engines always register. |

## Engine Authoring Rule — deferred in-process imports

An engine module (`<engine>_engine.py`) is imported **on the head** for registration,
where the heavy lib is absent. This creates a subtle trap for engines that import their
lib **in-process** (vLLM, YOLO):

> A heavy symbol resolved at **module top-level** falls back to `None` on the head.
> Ray then serializes the deployment carrying those head-side globals, so the replica's
> `__init__` sees `None` even though its own venv has the real object — surfacing as a
> cryptic `'NoneType' object is not callable` at deploy time.

Rules for any new (or edited) engine:

1. **Never resolve a heavy symbol at module top-level for use at actor runtime.** Keep the
   module top-level import-safe (wrap in `try/except ImportError` with `None` fallbacks so
   registration survives head-side).
2. **Bind heavy classes inside `__init__` (or request handlers)** via
   `engine_utils.load_engine_symbols(label, [(module, attr), ...])`, which imports at
   call-time from the actor's own venv and raises a clear `RuntimeError` (not a downstream
   `NoneType`) if the lib is missing. Example (vLLM):
   ```python
   from .engine_utils import load_engine_symbols
   AsyncLLMEngine, AsyncEngineArgs = load_engine_symbols(
       "vLLM (.venv-vllm)",
       [("vllm", "AsyncLLMEngine"), ("vllm.engine.arg_utils", "AsyncEngineArgs")],
   )
   ```
3. **Prefer subprocess isolation where practical** (SGLang, LiteLLM launch their lib as a
   child process via `{venv}/bin/python`) — the actor never imports the heavy lib, so this
   trap does not apply.

Regression guard: `tests/test_engine_import_safety.py` blocks the per-engine libs to
simulate the head and asserts the engines package still imports and registers.

## Venv Contents

| Venv | Path | Contents |
|---|---|---|
| root | `/home/cdsw/.venv` | `ray[serve]`, `fastapi`, `uvicorn`, `httpx`, `pyyaml`, `jinja2`, nginx |
| vllm | `/home/cdsw/.venv-vllm` | `ray[serve]`, `vllm>=0.13.0` |
| sglang | `/home/cdsw/.venv-sglang` | `ray[serve]`, `sglang>=0.5.7` |
| yolo | `/home/cdsw/.venv-yolo` | `ray[serve]`, `ultralytics`, `Pillow` |
| mcp | `/home/cdsw/.venv-mcp` | `ray[serve]`, `mcp`, `httpx` |
| litellm | `/home/cdsw/.venv-litellm` | `litellm[proxy]`, `pyyaml` |
