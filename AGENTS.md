# AGENTS.md

What an AI agent needs to know before touching this repo. For the human-facing
picture, start at [project-overview.md](project-overview.md) instead.

## What this project is

`ray-serve-cai` turns Cloudera AI (CAI/CML) Applications into a live Ray cluster and
exposes one REST API (`ray_serve_cai/management/`) to deploy, scale, place, and
monitor inference workloads on it. Two packages, one-way dependency:

- `ray_serve_cai/` — generic Ray Serve orchestration library. No CML knowledge.
- `cai_integration/` — CML-specific deployment layer. Imports from `ray_serve_cai`,
  never the reverse.

Full depth: [architecture.md](architecture.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Hot paths — read before editing

These three files carry the highest blast radius per change. Touch them carefully:

- `ray_serve_cai/engines/vllm_engine.py` — vLLM deployment factory (placement
  groups, runtime_env, ray_actor_options). Most bugs here are silent until a real
  GPU deploy — validate against both the auto-generated AND caller-supplied
  `placement_group_bundles` code paths, not just one.
- `cai_integration/launch_ray_cluster.py` — head/worker CML Application launch
  sequence. Breaking this breaks cluster bring-up, not just one deployment.
- `ray_serve_cai/management/models/requests.py` — Pydantic request models
  (`extra="forbid"`; unknown top-level fields 422). `SchedulingConfig` denylists
  `PATH`/`LD_*`/`PYTHON*` in `env_vars` for linker/interpreter-hijack safety — if a
  fix needs one of those injected, it has to happen in code (e.g.
  `vllm_engine.py`'s `runtime_env` construction), never by relaxing the denylist.

## Known sharp edges (learn once, don't re-derive)

- **vLLM v1's Ray executor allows ≤1 GPU per placement-group bundle.** A single
  `{GPU: tensor_parallel_size}` bundle raises `ValueError: Placement group bundle
  cannot have more than 1 GPU` deep inside a Ray worker actor. Always emit one
  `{GPU:1}` bundle per TP shard plus a GPU-less scheduler bundle 0.
- **Ray's `py_executable` swaps the interpreter but not PATH.** Console-scripts
  installed in an isolated venv (notably `ninja`, which FlashInfer/torch.compile
  shell out to at startup) won't resolve inside `RayWorkerProc` workers unless the
  venv's `bin/` is explicitly prepended to `PATH` via `runtime_env['env_vars']`.
  Fixing only the deployment actor's own PATH preflight is not enough.
- **Istio/Envoy sidecars intercept cross-pod raw TCP**, including the
  `torch.distributed` TCPStore rendezvous for cross-node tensor parallelism. This
  looks like a NCCL/socket-interface bug but isn't — loopback is the only
  sidecar-exempt path. Default to single-pod multi-GPU TP; see
  `docs/superpowers/specs/2026-09-08-cross-pod-collectives-plan.md` for the
  cross-pod plan.
- **CML job venvs live on shared NFS** (`/home/cdsw/.venv-*`) — install/repair
  once, every pod sees it immediately. Don't assume `uv` is on PATH in a raw CML
  pod shell; fall back to the venv's own `pip`/`ensurepip` with `PIP_USER=0`
  (CML images export `PIP_USER=1`, which breaks an in-venv install otherwise).

## Working conventions

- Git: new commits, never `--amend`; no force-push; no `--no-verify`. Stage
  specific files, not `-A`/`.`.
- Only commit/push when explicitly asked.
- Lint/format/type-check/test commands: see [development.md](development.md).
- Full REST surface: [component-api.md](component-api.md).
