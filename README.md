# ray-serve-cai

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Ray](https://img.shields.io/badge/ray-2.53.0+-green.svg)](https://docs.ray.io/)

**Ray Serve orchestration for model inference on Cloudera AI.**

`ray-serve-cai` turns a set of Cloudera AI (CAI) / Cloudera Machine Learning (CML)
Applications into a live Ray cluster and gives you a single REST API to deploy,
scale, place, and monitor inference workloads on it — vLLM and SGLang LLMs, a
LiteLLM gateway, YOLO vision models, MCP tool servers, or any custom Ray Serve app.

Each engine runs in its own isolated Python virtual environment so mutually
incompatible dependency stacks (e.g. vLLM vs SGLang) coexist on the same cluster,
and every deployment can be pinned to specific nodes and GPU topologies through a
declarative scheduling block.

---

## Table of Contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [Installation](#installation)
- [Concepts](#concepts)
- [Quick start](#quick-start)
  - [1. Launch a cluster](#1-launch-a-cluster)
  - [2. Run the Management API](#2-run-the-management-api)
  - [3. Deploy a model](#3-deploy-a-model)
  - [4. Query it](#4-query-it)
- [Supported engines](#supported-engines)
- [The Management REST API](#the-management-rest-api)
  - [Applications](#applications--apiv1applications)
  - [Scheduling & placement groups](#scheduling--placement-groups)
  - [Environments (venv isolation)](#environments--apiv1environments)
  - [Resources & nodes](#resources--nodes--apiv1resources)
  - [Engines](#engines--apiv1engines)
  - [Cluster & metrics](#cluster--metrics)
- [Node targeting](#node-targeting)
- [Adding a custom engine](#adding-a-custom-engine)
- [Configuration reference](#configuration-reference)
- [Project layout](#project-layout)
- [Development](#development)
- [Documentation](#documentation)
- [License](#license)

---

## Why this exists

Serving models on CAI/CML has three recurring pain points that this project solves:

1. **No native multi-node Ray on CML.** CML exposes *Applications* (long-running
   containers) but no first-class Ray cluster. `cai_integration` launches one CML
   Application as the Ray head and N more as workers, wiring them into a single
   cluster over the pod network.
2. **Engine dependency conflicts.** vLLM and SGLang require incompatible
   `llguidance` versions and cannot share one environment. Each engine is
   installed into its own venv (`/home/cdsw/.venv-<engine>`) on shared NFS, and
   the actor for that engine is launched under that interpreter via Ray's
   `py_executable` runtime env.
3. **Hard-to-express placement.** Getting tensor-parallel shards onto the right
   GPUs, or a fractional-GPU + KV-cache topology onto one node, normally means
   hand-writing Ray placement groups. This project derives sensible placement
   groups automatically and lets you override any part of them declaratively.

## Architecture

The repository is a **library + deployment template** with a clean split:

```
┌───────────────────────────────────────────────┐
│  ray_serve_cai/                                 │  Generic, platform-neutral
│    engines/       engine registry + factories   │  Ray Serve orchestration.
│    management/    FastAPI Management REST API    │  Works on any Ray cluster.
│    ray_backend.py programmatic Python API        │
│    launch_cluster.py  cluster CLI                │
└───────────────────────────────────────────────┘
                    ▲
                    │ imports
                    │
┌───────────────────────────────────────────────┐
│  cai_integration/                               │  CML-specific: launches Ray
│    launch_ray_cluster.py  head + worker apps     │  head/workers as CML Apps,
│    templates/             worker launcher (j2)   │  sets up nginx, venvs, NFS.
│    setup_environment.py   per-engine venv builds │
└───────────────────────────────────────────────┘
```

At runtime the pieces fit together like this:

```
                    ┌─────────────────────────────────────────┐
   HTTP client ───► │  Management API (FastAPI)  /api/v1/...    │
                    │  deploy · scale · place · monitor         │
                    └───────────────┬───────────────────────────┘
                                    │  ray.serve.run / ray.nodes / CML API
                    ┌───────────────▼───────────────────────────┐
                    │            Ray cluster (on CAI)            │
                    │  head (no GPU)  ·  worker₁ … workerₙ (GPU) │
                    │  each Serve deployment → actor in its own  │
                    │  venv, pinned by a placement group          │
                    └────────────────────────────────────────────┘
```

## Installation

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

> **Note on `[all]`**: there is intentionally no combined install. vLLM and SGLang
> require conflicting `llguidance` versions, so they must live in separate venvs.
> On a running cluster this is handled for you by the [Environments API](#environments--apiv1environments).

GPU inference additionally needs CUDA 11.8+ and a compatible driver on the worker nodes.

## Concepts

| Term | Meaning |
|------|---------|
| **Head node** | The Ray head. Runs the Management API and coordination. **No GPUs.** |
| **Worker node** | A CML Application that joins the cluster and carries GPUs/CPU for inference. |
| **Engine** | A registered inference backend: `vllm`, `sglang`, `litellm`, `yolo`, `mcp`, or custom. |
| **Application** | A Ray Serve deployment. Created via `POST /api/v1/applications`. |
| **Environment** | An isolated venv at `/home/cdsw/.venv-<name>` that an engine's actor runs under. |
| **`node_type`** | A logical worker-group label (e.g. `l40-gpu-worker`) registered as a Ray resource. |
| **`node_label`** | A Kubernetes node-selector label used to place a worker *pod* onto a specific K8s node. |
| **SchedulingConfig** | Declarative actor resources, placement-group bundles, strategy, and env vars for a deployment. |

## Quick start

### 1. Launch a cluster

**Local (single machine, for development):**

```bash
python -m ray_serve_cai.launch_cluster start
python -m ray_serve_cai.launch_cluster status
```

**Distributed on Cloudera AI (1 head + N workers as CML Applications):**

```yaml
# cai_cluster.yaml
cai:
  host: https://ml.example.cloudera.site
  api_key: your-api-key
  project_id: your-project-id
  num_workers: 2
  resources:            # worker resources (GPUs live here)
    cpu: 16
    memory: 64
    num_gpus: 1
  head_resources:       # head is CPU-only
    cpu: 8
    memory: 32
```

```bash
python -m ray_serve_cai.launch_cluster --config cai_cluster.yaml start-cai
python -m ray_serve_cai.launch_cluster status-cai
python -m ray_serve_cai.launch_cluster stop-cai
```

The CLI (`ray-serve-cai` console script, or `python -m ray_serve_cai.launch_cluster`)
supports: `start`, `stop`, `status`, `get-address`, `start-autoscaler`,
`start-cai`, `stop-cai`, `status-cai`. See the
[CAI Cluster Guide](docs/cai_cluster_guide.md).

### 2. Run the Management API

On CML the head-node Application serves the Management API automatically. To run it
directly (e.g. locally against `RAY_ADDRESS=auto`):

```bash
python -m ray_serve_cai.management.app
# → http://<host>:<CDSW_APP_PORT|8080>
#   Swagger UI at /docs, ReDoc at /redoc, health at /api/health
```

### 3. Deploy a model

Everything deploys through one endpoint — `POST /api/v1/applications`:

```bash
curl -X POST http://<head>/api/v1/applications \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "llama3-8b",
        "engine_type": "vllm",
        "model": "meta-llama/Llama-3.1-8B-Instruct",
        "route_prefix": "/llama3",
        "tensor_parallel_size": 1,
        "engine_config": {"dtype": "bfloat16", "gpu_memory_utilization": 0.9},
        "scheduling": {"resources": {"node_type:l40-gpu-worker": 0.001}}
      }'
```

Deployment is asynchronous for large models: the call returns `deploying` and Ray
Serve continues to bring the replica up in the background. Poll
`GET /api/v1/applications/llama3-8b` for status.

### 4. Query it

Each LLM engine serves OpenAI-compatible routes under its `route_prefix`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://<head>/llama3/v1", api_key="not-required")
resp = client.chat.completions.create(
    model="meta-llama/Llama-3.1-8B-Instruct",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

Available per deployment: `POST {prefix}/v1/completions`,
`POST {prefix}/v1/chat/completions`, `GET {prefix}/v1/models`,
`GET {prefix}/health`, and (vLLM/SGLang) `GET {prefix}/metrics`.

## Supported engines

| Engine | `engine_type` | Status | Notes |
|--------|---------------|--------|-------|
| **vLLM** | `vllm` | ✅ Stable | High-throughput LLM serving; tensor parallelism, fractional GPU, multi-node. |
| **SGLang** | `sglang` | ✅ Stable | Runs SGLang's server as a subprocess; native Prometheus metrics. |
| **LiteLLM** | `litellm` | ✅ Stable | Proxy/gateway to external providers (OpenAI, Anthropic, …); no local model. |
| **YOLO** | `yolo` | ✅ Stable | Ultralytics object detection; batched inference. |
| **MCP** | `mcp` | ✅ Stable | Model Context Protocol tool servers. |
| **Custom** | *(your name)* | 🔌 Extensible | Register your own via the engine registry. |

## The Management REST API

All endpoints are under `/api/v1`. Full interactive schema is at `/docs`.

### Applications — `/api/v1/applications`

A single, unified deployment endpoint. Provide **exactly one** discriminator:
`engine_type` (engine-registry path) **or** `import_path` (raw Ray Serve app).
Supplying both or neither is a `422`.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/applications` | Deploy an engine model **or** a raw Ray Serve app. |
| `GET` | `/applications` | List all Serve applications (with live status, route, replicas). |
| `GET` | `/applications/{name}` | Get one application. |
| `DELETE` | `/applications/{name}` | Undeploy an application. |
| `POST` | `/applications/model` | **Deprecated** — 308-redirects to `/applications`. |

Raw Ray Serve app example:

```json
{
  "name": "my-service",
  "import_path": "my_module:app",
  "route_prefix": "/svc",
  "ray_actor_options": {"num_cpus": 2},
  "scheduling": {"resources": {"node_type:cpu-worker": 0.001}}
}
```

> `import_path` is validated to `module:attribute` format and checked against the
> `ALLOWED_ENGINE_MODULES` allowlist (default `custom_engines,ray_serve_cai`) before
> import, to prevent arbitrary module execution.

### Scheduling & placement groups

Every deployment accepts a `scheduling` block that gives you full, declarative
control over where and how its actors are placed:

```jsonc
"scheduling": {
  // Node affinity for this deployment's GPU work. Use 0.001 for soft affinity
  // (a hint that consumes no capacity). Merged into GPU placement-group bundles
  // when a placement group is used, else set on the actor directly.
  "resources": {"instance-group-id:ig-n4bsnv8r": 0.001},

  // Full per-bundle override. Each dict is one bundle; keys are Ray resource
  // names (custom node labels allowed), values are quantities.
  "placement_group_bundles": [
    {"CPU": 2.0, "GPU": 0.01},
    {"GPU": 0.99},
    {"GPU": 0.99}
  ],

  // PACK | STRICT_PACK | SPREAD | STRICT_SPREAD
  "placement_group_strategy": "STRICT_PACK",

  // Actor env vars, merged with the venv runtime env (dangerous keys such as
  // LD_PRELOAD / PYTHONPATH are rejected).
  "env_vars": {"VLLM_RAY_PER_WORKER_GPUS": "0.99", "VLLM_RAY_BUNDLE_INDICES": "1,2"}
}
```

When you omit `placement_group_bundles`, sensible defaults are generated per
scenario:

| Scenario | Auto placement group |
|----------|----------------------|
| `tensor_parallel_size > 1`, single-node | one `{GPU: tp, CPU: tp}` bundle, `STRICT_PACK` |
| `tensor_parallel_size > 1`, `multi_node: true` | `{CPU:4}` scheduler + `tp × {GPU:1}` executor bundles, `PACK` |
| `gpu_fraction < 1` | one `{GPU: fraction, CPU: 2}` bundle, `PACK` |
| plain single GPU / CPU | no placement group |

`scheduling.resources` labels are automatically merged into the **GPU-bearing
bundles** so every shard lands on the target nodes — not just the coordinating
actor. This is the difference between pinning the scheduler and pinning the
actual GPU work.

### Environments — `/api/v1/environments`

Manage the isolated venvs (`/home/cdsw/.venv-<name>`) that engine actors run under.
Creation runs `uv venv` + `uv pip install` in a background thread (heavy engines
like vLLM can take minutes), so `POST` returns `202` immediately — poll to see
readiness.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/environments` | List all venvs (on-disk + in-flight creations). |
| `POST` | `/environments` | Create a venv `{name, packages, python?}` → `202`. |
| `GET` | `/environments/{name}` | Status of one venv. |

```bash
curl -X POST http://<head>/api/v1/environments \
  -H 'Content-Type: application/json' \
  -d '{"name": "vllm-013", "packages": ["vllm==0.27.1", "ninja"]}'
```

Then deploy against it with `"venv_name": "vllm-013"` on the application request.

### Resources & nodes — `/api/v1/resources`

Add and inspect worker nodes. Each worker is a CML Application that joins the cluster.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/resources/nodes` | Add a worker node (creates a CML App). Returns `201` with `app_id`. |
| `DELETE` | `/resources/nodes/{app_id}` | Remove a worker node (stops the CML App). |
| `GET` | `/resources/nodes` | List Ray nodes enriched with CML `app_id`, `app_name`, `cml_status`. |
| `GET` | `/resources/workers` | **Deprecated** — use `/resources/nodes`. |
| `GET` | `/resources/allocation` | API-tracked resource allocations. |
| `GET` | `/resources/capacity` | Live Ray cluster capacity & utilization. |

### Engines — `/api/v1/engines`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/engines` | List registered engine types and the default engine. |
| `POST` | `/engines/register` | Dynamically register a custom engine (allowlist-gated). |

### Cluster & metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/cluster/status` | Node counts, app counts, resource utilization. |
| `GET` | `/cluster/info` | Head address, dashboard URL, Ray version. |
| `GET` | `/cluster/gcs-address` | Internal Ray GCS address for workers to join. |
| `GET` | `/metrics` | Head node Prometheus metrics. |
| `GET` | `/metrics/all` | Aggregated metrics from all alive nodes (10 s cache). |
| `GET` | `/metrics/apps` | Per-application metrics (e.g. vLLM). |
| `GET` | `/metrics/discovery` | Prometheus HTTP service-discovery targets. |

There is also `/api/v1/cml-apps` for launching/stopping generic (non-worker) CML
Applications on the cluster.

## Node targeting

Placement works at two layers, both driven from a single label you supply when
adding a worker:

1. **Pod placement (Kubernetes).** `node_label` on `POST /resources/nodes` becomes
   the pod's `NODE_SELECTOR_KEY/VALUE`, steering the worker container onto a
   specific K8s node. The right key is provider-specific:
   - Cloudera/Liftie: `liftie.cloudera.com/instance-group-id`
   - EKS: `node.kubernetes.io/instance-type`
   - NVIDIA GFD: `nvidia.com/gpu.product`
2. **Actor scheduling (Ray).** The same `node_label` is auto-derived into a
   short-key Ray resource (e.g. `instance-group-id:ig-n4bsnv8r=1`) so that
   deployments can target that exact node via `scheduling.resources`.

```bash
# Add a worker pinned to a specific K8s node group
curl -X POST http://<head>/api/v1/resources/nodes -d '{
  "node_type": "l40-gpu-worker",
  "gpus": 1,
  "node_label": {"liftie.cloudera.com/instance-group-id": "ig-n4bsnv8r"}
}'

# Deploy a model onto that exact node
curl -X POST http://<head>/api/v1/applications -d '{
  "name": "qwen", "engine_type": "vllm", "model": "Qwen/Qwen3-8B",
  "scheduling": {"resources": {"instance-group-id:ig-n4bsnv8r": 0.001}}
}'
```

## Adding a custom engine

An engine is three objects: a config builder, a deployment factory, and (optionally)
an engine class. Implement the protocols and register them.

```python
from ray_serve_cai import (
    register_engine,
    ConfigBuilderProtocol,
    DeploymentFactoryProtocol,
)

class MyConfigBuilder(ConfigBuilderProtocol):
    def build_config(self, user_config: dict) -> dict:
        # validate + translate the request into engine kwargs
        return {"model": user_config["model"]}

    def validate_config(self, user_config: dict):
        return (True, None)

    def get_default_config(self) -> dict:
        return {}

class MyDeploymentFactory(DeploymentFactoryProtocol):
    def create_deployment(self, engine_config: dict, num_replicas: int = 1, **kwargs):
        from ray import serve
        # build and .bind() a serve deployment; honor scheduling_resources /
        # scheduling_env_vars from engine_config for placement support
        ...

register_engine(
    engine_type="my_engine",
    engine_class=object,               # or your LLMEngineProtocol impl
    config_builder=MyConfigBuilder(),
    deployment_factory=MyDeploymentFactory(),
)
```

Custom engines can also be registered at runtime over HTTP via
`POST /api/v1/engines/register` (module must be on the `ALLOWED_ENGINE_MODULES`
allowlist). A complete, working template lives at
[`examples/custom_engine_template/my_engine.py`](examples/custom_engine_template/my_engine.py).

## Configuration reference

### Environment variables

| Variable | Purpose |
|----------|---------|
| `CML_HOST` / `CDSW_DOMAIN` | CML instance URL for the CAI API. |
| `CML_API_KEY` / `CDSW_APIV2_KEY` | CML API key for launching Applications. |
| `CML_PROJECT_ID` / `CDSW_PROJECT_ID` | Target CML project. |
| `RAY_ADDRESS` | Ray cluster address (default `auto`). |
| `ALLOWED_ENGINE_MODULES` | Comma-separated import allowlist (default `custom_engines,ray_serve_cai`). |
| `CDSW_APP_PORT` / `CDSW_APP_HOST` | Bind address for the Management API. |
| `RAY_METRICS_PORT` / `RAY_SERVE_PORT` | Ports used by the metrics endpoints. |

Copy [`.env.example`](.env.example) to `.env` and fill in your values.

### `DeployApplicationRequest` key fields

| Field | Type | Notes |
|-------|------|-------|
| `name` | str | Unique Serve application name. Re-deploying = rolling update. |
| `engine_type` | str? | One of the registered engines. Mutually exclusive with `import_path`. |
| `import_path` | str? | `module:attribute` for a raw Serve app. |
| `model` | str? | HF id / path. Required for vLLM & SGLang. |
| `route_prefix` | str | HTTP mount prefix. |
| `num_replicas` | int | Replica count (mutually exclusive with `autoscaling_config`). |
| `tensor_parallel_size` | int | GPUs per replica for TP. |
| `gpu_fraction` | float? | Fractional GPU per replica. |
| `multi_node` | bool | Allow TP shards to span nodes. |
| `venv_name` | str? | Isolated env to run the actor in (defaults to `engine_type`). |
| `engine_config` | dict? | Engine-specific parameters. |
| `scheduling` | object? | [Scheduling block](#scheduling--placement-groups). |
| `autoscaling_config` | dict? | Ray Serve autoscaling. |

## Project layout

```
ray_serve_cai/                 # the library
├── engines/                   # engine registry + per-engine config/factory
│   ├── registry.py            #   register_engine / get_registry
│   ├── vllm_*.py  sglang_*.py litellm_*.py yolo_*.py mcp_*.py
│   └── venv_utils.py          #   venv resolution & validation
├── management/                # the FastAPI Management API
│   ├── app.py                 #   FastAPI app + lifespan
│   ├── api/                   #   routers: applications, resources, cluster,
│   │                          #   engines, environments, metrics, cml_apps
│   ├── services/              #   RayService, CAIService, Coordinator
│   └── models/                #   Pydantic request/response models
├── ray_backend.py             # programmatic Python API (RayBackend)
├── launch_cluster.py          # cluster lifecycle CLI
├── cai_cluster.py             # CAI cluster manager + WorkerGroupConfig
└── worker_app.py              # worker-side info server

cai_integration/               # CML deployment layer (uses the library)
├── launch_ray_cluster.py      # launches head + worker CML Applications
├── setup_environment.py       # builds per-engine venvs (NFS-safe)
└── templates/                 # worker launcher + nginx templates

examples/custom_engine_template/  # a complete custom-engine example
docs/                             # architecture, guides, design docs
tests/                            # unit + CAI end-to-end tests
```

## Development

```bash
pip install -e ".[dev]"

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

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — components and data flow
- [Installation](docs/INSTALLATION.md)
- [Quickstart](docs/QUICKSTART.md)
- [Cluster Setup](docs/CLUSTER_SETUP.md) · [CAI Cluster Guide](docs/cai_cluster_guide.md)
- [Isolated Environments Design](docs/ISOLATED_ENV_DESIGN.md)
- [CML Deployment Guide](cai_integration/README.md)
- [Roadmap](docs/ROADMAP.md) · [Docs Index](docs/INDEX.md)

## License

Apache License 2.0.

---

*Built for the Cloudera AI community.*
