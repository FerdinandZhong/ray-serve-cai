# Component / API Reference

The Management REST API surface and key request-model fields. All endpoints are
under `/api/v1`. Full interactive schema is at `/docs` (Swagger) / `/redoc`.

## Applications — `/api/v1/applications`

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
> `ALLOWED_ENGINE_MODULES` allowlist (default `custom_engines,ray_serve_cai`)
> before import, to prevent arbitrary module execution.

## Scheduling & placement groups

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
| `tensor_parallel_size > 1`, single-node | scheduler `{CPU:4}` + `tp × {GPU:1}` bundles, `STRICT_PACK` |
| `tensor_parallel_size > 1`, `multi_node: true` | `{CPU:4}` scheduler + `tp × {GPU:1}` executor bundles, `PACK` |
| `gpu_fraction < 1` | one `{GPU: fraction, CPU: 2}` bundle, `PACK` |
| plain single GPU / CPU | no placement group |

> vLLM v1's Ray executor allows **at most 1 GPU per bundle** — this is why both
> TP scenarios above emit one `{GPU:1}` bundle per shard rather than a single
> `{GPU: tp}` bundle, whether the bundles come from the defaults or from an
> explicit `placement_group_bundles` payload.

`scheduling.resources` labels are automatically merged into the **GPU-bearing
bundles** so every shard lands on the target nodes — not just the coordinating
actor. This is the difference between pinning the scheduler and pinning the
actual GPU work.

## Environments — `/api/v1/environments`

Manage the isolated venvs (`/home/cdsw/.venv-<name>`) that engine actors run
under. Creation runs `uv venv` + `uv pip install` in a background thread (heavy
engines like vLLM can take minutes), so `POST` returns `202` immediately — poll
to see readiness.

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

## Resources & nodes — `/api/v1/resources`

Add and inspect worker nodes. Each worker is a CML Application that joins the
cluster.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/resources/nodes` | Add a worker node (creates a CML App). Returns `201` with `app_id`. |
| `DELETE` | `/resources/nodes/{app_id}` | Remove a worker node (stops the CML App). |
| `GET` | `/resources/nodes` | List Ray nodes enriched with CML `app_id`, `app_name`, `cml_status`. |
| `GET` | `/resources/workers` | **Deprecated** — use `/resources/nodes`. |
| `GET` | `/resources/allocation` | API-tracked resource allocations. |
| `GET` | `/resources/capacity` | Live Ray cluster capacity & utilization. |

## Engines — `/api/v1/engines`

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/engines` | List registered engine types and the default engine. |
| `POST` | `/engines/register` | Dynamically register a custom engine (allowlist-gated). |

## Cluster & metrics

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/cluster/status` | Node counts, app counts, resource utilization. |
| `GET` | `/cluster/info` | Head address, dashboard URL, Ray version. |
| `GET` | `/cluster/gcs-address` | Internal Ray GCS address for workers to join. |
| `GET` | `/metrics` | Head node Prometheus metrics. |
| `GET` | `/metrics/all` | Aggregated metrics from all alive nodes (10s cache). |
| `GET` | `/metrics/apps` | Per-application metrics (e.g. vLLM). |
| `GET` | `/metrics/discovery` | Prometheus HTTP service-discovery targets. |

There is also `/api/v1/cml-apps` for launching/stopping generic (non-worker) CML
Applications on the cluster.

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

Copy `.env.example` to `.env` and fill in your values.

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

Design rationale for these choices (strict schemas, one deploy endpoint, env_var
denylist, etc.) is in [DESIGN.md](DESIGN.md).
