# Development

Dev workflow, commands, and a regression checklist for the two highest-risk files
in this repo. For "how a user deploys a model," see [user-guide.md](user-guide.md).

## Install

Requires **Python 3.9+** and **Ray[serve] ≥ 2.53.0**.

```bash
# Core library (orchestration, Management API, cluster CLI)
pip install -e .

# With an inference engine — pick ONE of vllm / sglang; they conflict on llguidance
pip install -e ".[vllm]"     # vLLM >= 0.13.0 (+ ninja for FlashInfer JIT on T4/SM7.5)
pip install -e ".[sglang]"   # SGLang >= 0.5.7
pip install -e ".[yolo]"     # Ultralytics YOLO + Pillow + OpenCV

# Tooling
pip install -e ".[dev]"      # pytest, ruff, black, mypy
pip install -e ".[docs]"     # mkdocs-material
```

There is intentionally no `[all]` install — vLLM and SGLang need conflicting
`llguidance` versions, so they must live in separate venvs. On a running cluster
this is handled by the Environments API
([component-api.md](component-api.md#environments--apiv1environments)).

GPU inference additionally needs CUDA 11.8+ and a compatible driver on the worker
nodes (CUDA ≥12.9 for Blackwell/sm_120 — see
[docs/BLACKWELL_CUDA_NVCC_NCCL.md](docs/BLACKWELL_CUDA_NVCC_NCCL.md)).

## Checks

```bash
ruff check ray_serve_cai            # lint
black ray_serve_cai                 # format
mypy ray_serve_cai                  # type-check
pytest                              # run tests (with coverage)
```

CAI end-to-end tests need a real CML instance:

```bash
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export CML_PROJECT_ID="your-project-id"
python tests/test_cluster_deployment.py --workers 2
```

## Regression checklist for the two Hot Paths

Lessons already paid for once this project's lifetime — don't re-pay them.

### `ray_serve_cai/engines/vllm_engine.py`

- If you change placement-group generation, verify it against **both** code
  paths: the auto-generated defaults (`placement_group_bundles is None`) *and*
  caller-supplied bundles passed through `scheduling.placement_group_bundles`.
  A fix applied only to one path will still crash the other — vLLM v1's Ray
  executor rejects any bundle with `GPU > 1`, and this has bitten the project
  twice from two different code paths.
- If you change `runtime_env` construction, verify the change reaches **Ray
  worker actors** (`RayWorkerProc`), not just the deployment actor. `py_executable`
  swaps the interpreter but not `PATH`; only `runtime_env['env_vars']` propagates
  to workers.
- After any change here, do a real GPU deploy (not just `py_compile`) — most
  bugs in this file are silent until `EngineCore` actually initializes.

### `ray_serve_cai/management/models/requests.py`

- New top-level request fields will 422 (`extra_forbidden`) unless added to the
  right model — check nesting before adding a field (e.g. placement fields belong
  under `scheduling`, not top-level on `DeployApplicationRequest`).
- Do not add `PATH`/`LD_*`/`PYTHON*` to what `SchedulingConfig.env_vars` accepts —
  the denylist exists for linker/interpreter-hijack safety. If code needs one of
  those injected, do it in the engine factory itself, not by relaxing this schema.

## Project layout

See [architecture.md](architecture.md#component-split) for the annotated tree.
