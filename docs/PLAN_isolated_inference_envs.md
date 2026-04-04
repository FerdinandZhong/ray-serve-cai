# Plan: Isolated Inference Environments

> **Superseded by** `PLAN_v2_platform_features.md` Section 2, which adopts this plan's
> build-time NFS venv strategy (with `uv`, dash naming, template-driven interpreter
> selection) and adds NFS file locking + Ray `runtime_env` wiring.

## Problem

`vllm` and `sglang` cannot share a virtual environment because they require
mutually exclusive versions of `llguidance`:

- `vllm>=0.13.0` → `llguidance>=1.3.0,<1.4.0`
- `sglang>=0.5.7` → `llguidance>=0.7.11,<0.8.0`

The current workaround (install whichever succeeds) is fragile and limits each
node to a single inference engine regardless of workload.

---

## Target Architecture

Three separate virtual environments on each node:

| Environment | Path | Purpose |
|---|---|---|
| **main** | `/home/cdsw/.venv` | Cluster launch, Management API, nginx, `deploy_ray_app.py` |
| **vllm** | `/home/cdsw/.venv-vllm` | vLLM inference workers only |
| **sglang** | `/home/cdsw/.venv-sglang` | SGLang inference workers only |

The inference envs each contain `ray[serve]` (so workers can join the cluster)
plus their respective inference library.  The management app always runs in the
main env and never imports vllm or sglang directly.

---

## Environment Contents

### main (`/home/cdsw/.venv`)

```
ray[serve]>=2.53.0
fastapi, uvicorn, pydantic, httpx, starlette
jinja2, pyyaml, aiohttp
```

### vllm (`/home/cdsw/.venv-vllm`)

```
ray[serve]>=2.53.0   # worker must connect to the same cluster
vllm>=0.13.0
```

### sglang (`/home/cdsw/.venv-sglang`)

```
ray[serve]>=2.53.0
sglang>=0.5.7
```

---

## Changes Required

### 1. `pyproject.toml`

No changes needed — `[vllm]` and `[sglang]` extras already exist and are
kept separate.  The `[all]` extra remains empty (with explanatory comment).

### 2. `cai_integration/setup_environment.py`

Add `setup_vllm_venv()` and `setup_sglang_venv()` functions alongside the
existing `setup_main_venv()` logic.  Each creates its own venv via `uv venv`
and installs only its required packages.

Call order in `main()`:

```
install_nginx()
setup_main_venv()      # existing logic, stripped of inference engines
setup_vllm_venv()      # new: creates /home/cdsw/.venv-vllm
setup_sglang_venv()    # new: creates /home/cdsw/.venv-sglang
```

### 3. `pyproject.toml` / `setup_environment.py` install commands

| Env | Install command |
|---|---|
| main | `uv venv /home/cdsw/.venv && uv pip install -e '/home/cdsw'` |
| vllm | `uv venv /home/cdsw/.venv-vllm && uv pip install ray[serve] 'vllm>=0.13.0'` |
| sglang | `uv venv /home/cdsw/.venv-sglang && uv pip install ray[serve] 'sglang>=0.5.7'` |

### 4. `cai_integration/launch_ray_cluster.py` — `create_ray_launcher_scripts()`

Pass the chosen inference engine (e.g. `engine: str = "vllm"`) into the
template context so the worker launcher knows which Python interpreter to use.

New template context keys:

```python
"vllm_python":   "/home/cdsw/.venv-vllm/bin/python",
"sglang_python": "/home/cdsw/.venv-sglang/bin/python",
"engine":        engine,   # "vllm" | "sglang" | "none"
```

### 5. `cai_integration/templates/ray_worker_launcher.py.j2`

Select the interpreter based on `engine`:

```python
{% if engine == "vllm" %}
INFERENCE_PYTHON = Path("/home/cdsw/.venv-vllm/bin/python")
{% elif engine == "sglang" %}
INFERENCE_PYTHON = Path("/home/cdsw/.venv-sglang/bin/python")
{% else %}
INFERENCE_PYTHON = VENV_PYTHON   # no inference engine, use main venv
{% endif %}
```

Worker scripts that launch inference servers (e.g. a vllm `AsyncLLMEngine`
or an sglang `Runtime`) are invoked via `INFERENCE_PYTHON`, not `VENV_PYTHON`.

### 6. `ray_serve_cai/scripts/deploy_ray_app.py`

Add an `--engine` argument (`vllm` | `sglang` | `none`).  When deploying an
inference deployment, the script resolves the correct venv python and spawns
the inference process via subprocess (or sets `runtime_env.py_executable` in
the Ray Serve deployment config).

### 7. `cai_integration/templates/ray_head_launcher.py.j2`

The head node always uses the main venv for the Management API.  No change
needed beyond passing the engine through to the worker scripts.

---

## Cluster Config Integration

`cai_integration/ray_cluster_config.yaml` should gain a top-level `engine`
field (default `vllm`) that flows through `launch_ray_cluster.py` into the
template context:

```yaml
cluster:
  engine: vllm   # vllm | sglang | none
  worker_groups:
    ...
```

This is the single knob an operator turns to switch inference stacks.

---

## Rollout Order

1. Update `setup_environment.py` to create all three venvs.
2. Add `engine` to cluster config schema and plumb it through
   `launch_ray_cluster.py` → template context.
3. Update `ray_worker_launcher.py.j2` to select the interpreter.
4. Update `deploy_ray_app.py` to accept `--engine` and resolve the python path.
5. Update `ray_head_launcher.py.j2` step `[0/4]` to verify all three venvs
   exist (not just nginx).
6. Test: launch cluster with `engine: vllm`, confirm vllm workers start;
   repeat with `engine: sglang`.
