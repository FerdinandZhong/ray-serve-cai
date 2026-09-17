# Architecture

Quick map. Full depth (module organization, testing architecture, design-decision
rationale) lives in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — this file is a
condensed pointer, not a replacement.

## Component split

```
ray_serve_cai/                 the library — generic Ray Serve orchestration
├── engines/                   engine registry + per-engine config/factory
│   ├── registry.py            register_engine / get_registry
│   ├── vllm_*.py  sglang_*.py litellm_*.py yolo_*.py mcp_*.py
│   └── venv_utils.py          venv resolution & validation
├── management/                the FastAPI Management API
│   ├── app.py                 FastAPI app + lifespan
│   ├── api/                   routers: applications, resources, cluster,
│   │                          engines, environments, metrics, cml_apps
│   ├── services/               RayService, CAIService, Coordinator
│   └── models/                Pydantic request/response models
├── ray_backend.py             programmatic Python API (RayBackend)
├── launch_cluster.py          cluster lifecycle CLI
├── cai_cluster.py             CAI cluster manager + WorkerGroupConfig
└── worker_app.py              worker-side info server

cai_integration/               CML deployment layer (uses the library)
├── launch_ray_cluster.py      launches head + worker CML Applications
├── setup_environment.py       builds per-engine venvs (NFS-safe)
└── templates/                 worker launcher + nginx templates
```

`cai_integration` imports from `ray_serve_cai`; never the reverse. See
[DESIGN.md](DESIGN.md) for why.

## Runtime shape

```
HTTP client ──► Management API (FastAPI)  /api/v1/...
                deploy · scale · place · monitor
                        │  ray.serve.run / ray.nodes / CML API
                        ▼
                Ray cluster (on CAI)
                head (no GPU)  ·  worker₁ … workerₙ (GPU)
                each Serve deployment → actor in its own venv,
                pinned by a placement group
```

## Data flow — CML deployment

```
Job 1: Git Sync           → clone repo into CML project
Job 2: Setup Environment  → build /home/cdsw/.venv-* per engine
Job 3: Launch Ray Cluster → CAIClusterManager creates head (0 GPU) +
                             worker CML Applications (with GPU),
                             wires them into one Ray cluster
Job 4: Launch Monitoring  → provision Prometheus + Grafana CML apps
                             (cai_integration/launch_monitoring_job.py)
                        ↓
        Ray Head (GCS, Dashboard, Serve Controller, Management API, nginx)
        Ray Workers (GPU actors, one venv per engine)
                        ↓
        OpenAI-compatible API — curl https://<head>/<route_prefix>/v1/chat/completions
```

## Key runtime mechanisms

- **Engine isolation** — each engine (`vllm`, `sglang`, ...) gets its own venv at
  `/home/cdsw/.venv-<engine>` on shared project NFS; the actor is launched under
  that interpreter via Ray's `runtime_env['py_executable']`.
- **Declarative placement** — `scheduling.placement_group_bundles` /
  `placement_group_strategy` map to Ray placement groups; sensible defaults are
  auto-derived per scenario (single-node TP, multi-node TP, fractional GPU). See
  [component-api.md](component-api.md#scheduling--placement-groups).
- **Node targeting** — worker groups register a custom `node_type:<label>` Ray
  resource at `ray start --resources`, so deployments can be pinned to a specific
  GPU pool via `scheduling.resources`.

## Where to go deeper

- Full component responsibilities, module layout, "when to use which package,"
  testing architecture → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- System/API design rationale → [DESIGN.md](DESIGN.md)
- REST surface → [component-api.md](component-api.md)
