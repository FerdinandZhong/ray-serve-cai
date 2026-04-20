# Platform Features Plan v2 (Revised)

> **Supersedes:** `PLAN_isolated_inference_envs.md` (merged into Section 2 below).
> **Revision:** Incorporates Architect and Critic feedback from RALPLAN consensus review.

---

## RALPLAN-DR Summary

### Principles
1. **Metrics must be engine-agnostic** — each engine mounts `/metrics` on its own FastAPI app; the central endpoint scrapes all of them the same way.
2. **Isolation by default** — each engine runs in its own NFS-mounted venv, selected at build time by `setup_environment.py`. Head node never imports engine packages.
3. **Extensibility without restart** — new engines register via gated API call; the cluster stays live.
4. **Scale-down safety** — replicas drain before nodes die; never kill a node with running actors.
5. **Composition over inheritance** — provide utility functions (`create_engine_app()`, `mount_health()`, `mount_metrics()`), not an ABC. The protocol interface remains the contract.

### Decision Drivers
1. **Operational simplicity** — platform team manages 1 cluster serving N engines.
2. **Prometheus compatibility** — all metrics scrapable by standard Prometheus.
3. **CML/CAI constraints** — NFS shared filesystem; CML API for node lifecycle (1-3 min latency); ephemeral pods.

---

## 1. Engine Metrics Exposure

### Architecture (Hybrid — Option C)

```
External Prometheus ──► /api/v1/metrics/discovery (SD file)
                        ──► scrapes each node:9090/metrics (Ray system)
                        ──► scrapes each <route_prefix>/metrics (engine)

Quick check ──► /api/v1/metrics       (head Ray metrics only)
             ──► /api/v1/metrics/apps  (all engine metrics aggregated)
             ──► /api/v1/metrics/all   (all node Ray metrics aggregated)
```

### Current State (done)
- Ray nodes: `--metrics-export-port 9090` on head/worker launchers.
- vLLM: `attach_router(_vllm_app)` mounts `/metrics` with vLLM instrumentator.
- Central API: `/api/v1/metrics`, `/api/v1/metrics/all`, `/api/v1/metrics/apps`, `/api/v1/metrics/discovery`.

### Remaining Work

#### 1a. SGLang metrics
SGLang exposes metrics at `localhost:<port>/metrics` with `--enable-metrics`.

Key metrics: `sglang:num_running_reqs`, `sglang:num_queue_reqs`, `sglang:gen_throughput`, `sglang:cache_hit_rate`, `sglang:time_to_first_token_seconds`, `sglang:e2e_request_latency_seconds`, `sglang:prompt_tokens_total`, `sglang:generation_tokens_total`.

**Implementation:**
- Add `metrics_port` to SGLang engine_config.
- Start sglang subprocess with `--enable-metrics --metrics-port <port>`.
- Mount a `/metrics` proxy route on the SGLang FastAPI app that fetches `localhost:<port>/metrics`.
- The existing `/api/v1/metrics/apps` discovers and scrapes it automatically.

**Acceptance criteria:** `curl -s <host>/<route_prefix>/metrics | grep -c "^sglang:"` returns > 0.

#### 1b. YOLO custom metrics
Define custom metrics using `prometheus_client`:
- `yolo_requests_total` (counter)
- `yolo_request_latency_seconds` (histogram)
- `yolo_batch_size` (histogram)
- `yolo_detections_per_image` (histogram)

**Implementation:** Create a `CollectorRegistry` in `yolo_engine.py`, instrument `_detect_batch`, mount via `prometheus_client.make_asgi_app(registry=...)` on `_yolo_app`.

**Acceptance criteria:** `curl -s <host>/yolo/metrics | grep "yolo_batch_size"` returns histogram data.

#### 1c. MCP custom metrics
- `mcp_tool_calls_total` (counter, label: `tool_name`)
- `mcp_tool_latency_seconds` (histogram, label: `tool_name`)

Mount on `_mcp_app` with same pattern as YOLO.

#### 1d. Scrape resilience
Add 2s per-engine timeout and skip unresponsive engines in `/api/v1/metrics/apps` (already implemented with `_fetch_metrics` timeout; add logged warning on skip).

### vLLM Metrics Quick Reference
| Metric | Signal |
|--------|--------|
| `vllm:num_requests_waiting` | Queue depth (scaling trigger) |
| `vllm:kv_cache_usage_perc` | Memory pressure |
| `vllm:time_to_first_token_seconds` | User-perceived latency |
| `vllm:e2e_request_latency_seconds` | Total request time |
| `vllm:prompt_tokens_total` | Throughput |

---

## 2. Isolated Environments

> Supersedes `PLAN_isolated_inference_envs.md`. Adopts the build-time NFS venv strategy with `uv`, enhanced with Ray `runtime_env` for deployment-time wiring.

### Architecture

```
/home/cdsw/.venv/              — head control plane (management API, Ray, FastAPI)
/home/cdsw/.venv-vllm/         — vLLM (vllm, torch, transformers, flash-attn)
/home/cdsw/.venv-sglang/       — SGLang (sglang[all], torch)
/home/cdsw/.venv-yolo/         — YOLO (ultralytics, Pillow, opencv)
/home/cdsw/.venv-mcp/          — MCP (mcp, httpx)
```

Dash naming convention (`.venv-<engine>`), matching the existing plan.

### Phase 1: Build-time provisioning (setup_environment.py)

Update `cai_integration/setup_environment.py`:

```python
def setup_engine_venv(engine: str, packages: list[str]):
    venv_path = Path(f"/home/cdsw/.venv-{engine}")
    lock_path = venv_path.with_suffix(".lock")

    # NFS-safe file lock prevents concurrent creation race
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        if not (venv_path / "bin/python").exists():
            subprocess.run(["uv", "venv", str(venv_path)])
            subprocess.run(["uv", "pip", "install", "--python",
                            str(venv_path / "bin/python")] + packages)
```

Requirements per engine in `requirements/`:
- `requirements_vllm.txt`: `ray[serve]>=2.53.0`, `vllm>=0.13.0`
- `requirements_sglang.txt`: `ray[serve]>=2.53.0`, `sglang>=0.5.7`
- `requirements_yolo.txt`: `ray[serve]>=2.53.0`, `ultralytics`, `Pillow`
- `requirements_mcp.txt`: `ray[serve]>=2.53.0`, `mcp`, `httpx`

Worker launcher creates only the venvs needed for its assigned engine (via `engine` template variable from `ray_cluster_config.yaml`). CPU workers don't install vLLM.

### Phase 2: runtime_env wiring in deployment factories

Each factory checks if the venv exists and wires it:

```python
venv_path = f"/home/cdsw/.venv-{engine_type}"
if Path(venv_path).exists():
    ray_actor_options["runtime_env"] = {"virtualenv": venv_path}
```

No `pip` fallback — if the venv doesn't exist, fail loudly with a clear error directing the user to run `setup_environment.py` or `POST /api/v1/engines/{type}/setup`.

### Key Rules
- Head node NEVER imports engine packages. `engines/__init__.py` catches `ImportError`.
- `fcntl.flock` on `.venv-<engine>.lock` prevents NFS concurrent-write corruption.
- NFS-mounted venvs are shared across all nodes — install once, available everywhere.
- No `runtime_env={"pip": [...]}` fallback (avoids per-restart re-install on ephemeral pods).

**Acceptance criteria:** Inside a vLLM actor: `ray.get_runtime_context().runtime_env` shows `{"virtualenv": "/home/cdsw/.venv-vllm"}`. The head node's `import vllm` raises `ImportError`.

---

## 3. Engine Utility Functions (not ABC)

### Problem
Every engine reimplements: FastAPI app with path-stripping `__call__`, `/health` route, `/metrics` mount, Swagger UI config. ~30 lines duplicated across vLLM, YOLO, MCP.

### Solution: Composable utility functions

```python
# engines/engine_utils.py

def create_engine_app(title: str, description: str = "", **fastapi_kwargs) -> FastAPI:
    """Create a FastAPI app with root_path stripping __call__."""
    class _App(FastAPI):
        async def __call__(self, scope, receive, send):
            # strip root_path... (shared 8-line logic)
            await super().__call__(scope, receive, send)
    return _App(title=title, description=description,
                root_path_in_servers=True, **fastapi_kwargs)

def mount_health(app: FastAPI, engine_type: str, extra: dict = None):
    """Add GET /health returning engine status."""
    @app.get("/health", tags=["Health"])
    async def health():
        result = {"status": "healthy", "engine": engine_type}
        if extra:
            result.update(extra)
        return result

def mount_metrics(app: FastAPI, registry=None):
    """Mount /metrics with prometheus_client ASGI app."""
    from prometheus_client import make_asgi_app, REGISTRY
    import regex as re
    from starlette.routing import Mount
    reg = registry or REGISTRY
    metrics_route = Mount("/metrics", make_asgi_app(registry=reg))
    metrics_route.path_regex = re.compile("^/metrics(?P<path>.*)$")
    app.routes.append(metrics_route)
```

### Usage in a new engine

```python
from ray_serve_cai.engines.engine_utils import create_engine_app, mount_health, mount_metrics

_my_app = create_engine_app("My Custom Engine API")
mount_health(_my_app, "my_engine")
mount_metrics(_my_app)

@serve.deployment
@serve.ingress(_my_app)
class MyEngine:
    def __init__(self, engine_config):
        ...

    @_my_app.post("/v1/predict")
    async def predict(self, ...):
        ...
```

### Migration
- New engines use the utility functions.
- Existing engines (vLLM, YOLO, MCP) are NOT required to migrate — they work fine as-is.
- Over time, refactor existing engines to use utilities if beneficial.

### LLMEngineProtocol disposition
- **Deprecate** `LLMEngineProtocol` in `base.py` — no engine implements it. It was aspirational.
- **Keep** `ConfigBuilderProtocol` and `DeploymentFactoryProtocol` — these are the real contracts, actively used by the registry.
- Add a deprecation comment to `LLMEngineProtocol`. Remove it in a future release.

**Acceptance criteria:** A minimal test engine using utility functions deploys and responds to `GET /health` with `{"status": "healthy"}` and `GET /metrics` with Prometheus text.

---

## 4. Dynamic Engine Registration

### API

```
POST /api/v1/engines/register
{
  "engine_type": "my_engine",
  "module_path": "custom_engines.my_engine",
  "config_builder": "custom_engines.my_config.MyConfigBuilder",
  "deployment_factory": "custom_engines.my_config.MyFactory"
}
```

### Safety Gates

1. **Module path allowlist:** Only modules under allowed prefixes can be imported. Configured via env var `ALLOWED_ENGINE_MODULES` (default: `custom_engines,ray_serve_cai.engines`). Reject with 403 if prefix doesn't match.
2. **Import isolation:** `importlib.import_module` runs in a try/except; failures return 400 with the import error, never crash the management API.
3. **Conflict detection:** If `engine_type` already registered, return 409 Conflict. Support `force=true` query param to override.
4. **Audit logging:** Log every registration with timestamp, module path, and caller IP.

### Head-node import tradeoff
Dynamic registration does `importlib.import_module` on the head node. This means the head briefly loads the engine module's top-level code. This is acceptable because:
- The module only defines classes (no heavy imports like `torch` at module level if written correctly).
- The `ConfigBuilder` and `DeploymentFactory` are lightweight Python objects.
- The actual engine class is only instantiated inside Ray actors on workers.

Document this in the engine authoring guide: **engine modules must not import heavy dependencies (torch, vllm, ultralytics) at module level. Use lazy imports inside `__init__`.**

### User Workflow
1. Write engine + config builder + factory (use utility functions from Section 3).
2. Place files on NFS under `/home/cdsw/custom_engines/`.
3. `POST /api/v1/engines/register` with module paths.
4. `POST /api/v1/applications/model` with `engine_type: "my_engine"`.

**Acceptance criteria:**
- `POST /api/v1/engines/register` with allowed module → 200, engine listed in `GET /api/v1/engines`.
- `POST /api/v1/engines/register` with disallowed prefix → 403.
- `POST /api/v1/engines/register` with duplicate type → 409.

---

## 5. Auto-scaling

### Layer 1: Application Autoscaling (Ray Serve native)

Pass `autoscaling_config` through the deployment pipeline:

**Schema change** — add to `DeployModelRequest`:
```python
autoscaling_config: Optional[Dict[str, Any]] = Field(
    default=None,
    description="Ray Serve autoscaling config. When set, num_replicas is ignored."
)
```

**Thread through:** `DeployModelRequest` → `applications.py` → `RayService.deploy_model()` → factory → `.options(autoscaling_config=...)`.

**Validation:** When `autoscaling_config` is set and `num_replicas > 1`, return 400 with message "autoscaling_config and num_replicas > 1 are mutually exclusive."

**Example payload:**
```json
{
  "name": "qwen3.5-2b",
  "engine_type": "vllm",
  "model": "/home/cdsw/models/Qwen3.5-2B",
  "route_prefix": "/qwen-2b",
  "autoscaling_config": {
    "min_replicas": 1,
    "max_replicas": 4,
    "target_ongoing_requests": 5,
    "upscale_delay_s": 30,
    "downscale_delay_s": 300
  }
}
```

**Acceptance criteria:** Deploy with autoscaling_config. `ray serve status` shows `autoscaling_config` on the deployment. Under load, replica count increases within 60s.

### Layer 2: Node Autoscaling (CML-aware)

Background service monitoring cluster capacity:

```python
# management/services/autoscaler.py
class ClusterAutoscaler:
    async def _check_and_scale(self):
        # Scale-up: check ray.util.placement_group_table() for PENDING groups
        pending = [pg for pg in ray.util.placement_group_table().values()
                   if pg["state"] == "PENDING"]
        if pending and self._can_scale_up():
            await self._scale_up()

        # Scale-down: check ray.state.actors() for empty nodes
        empty = self._find_empty_nodes()  # nodes with zero actors for > idle_timeout_s
        if empty and self._can_scale_down():
            await self._scale_down(empty)
```

**Scale-up data source:** `ray.util.placement_group_table()` — detects when Ray can't schedule replicas. NOT `ResourceMap` (which tracks CML-level capacity, not Ray scheduling state).

**Scale-down invariant:** Replicas drain before nodes die.
1. Identify nodes idle for > `idle_timeout_s`.
2. Verify zero actors: `ray.state.actors(filters=[("node_id", "=", nid)])`.
3. Remove via `DELETE /api/v1/resources/nodes/{app_id}`.

**Configuration** (in `ray_cluster_config.yaml`):
```yaml
autoscaling:
  enabled: false                    # opt-in
  check_interval_s: 30
  scale_up_cooldown_s: 120
  scale_down_cooldown_s: 600
  idle_timeout_s: 600
  min_workers: 1
  max_workers: 8
  default_node_type: "t4-gpu-worker"
```

**Safety:** `min_workers` floor, cooldown timers, dry-run mode, audit log.

**Acceptance criteria:** With `autoscaling.enabled: true` and `max_workers: 3`: deploy an app that needs 2 GPUs when only 1 worker exists → autoscaler adds a worker within 3 min → app becomes healthy. Remove the app → idle node removed after `idle_timeout_s`.

---

## Implementation Priority

| Phase | Feature | Effort | Dependencies |
|-------|---------|--------|--------------|
| **P0** | `autoscaling_config` passthrough in DeployModelRequest | Small | None |
| **P0** | SGLang metrics mount (`/metrics` proxy) | Small | SGLang engine working |
| **P1** | Engine utility functions (`engine_utils.py`) | Small | None |
| **P1** | YOLO/MCP custom prometheus_client metrics | Medium | prometheus_client |
| **P1** | Dynamic engine registration API (gated) | Medium | None |
| **P1** | Per-engine venvs Phase 1 (setup_environment.py + NFS lock) | Medium | NFS access |
| **P2** | Per-engine venvs Phase 2 (runtime_env wiring in factories) | Medium | Phase 1 |
| **P2** | Node-level autoscaler (Layer 2) | Large | Metrics, placement_group_table |
| **P2** | Deprecate LLMEngineProtocol | Small | engine_utils.py exists |
| **P3** | Grafana dashboard templates | Medium | All metrics endpoints |

---

## ADR

**Decision:** Hybrid metrics + build-time NFS venvs + utility functions (not ABC) + gated API registration + two-layer autoscaling.

**Drivers:** Operational simplicity; Prometheus compatibility; CML/CAI constraints.

**Alternatives rejected:**
- Shared Prometheus registry (breaks isolation — engines can't share head process)
- Docker-per-engine (CML doesn't support arbitrary images per Ray actor)
- `runtime_env={"pip": [...]}` fallback (re-installs on every pod restart on ephemeral CML pods)
- ABC inheritance (`BaseServeEngine`) — utility functions achieve same dedup with less coupling
- Kubernetes HPA for node scaling (Ray nodes are CML apps, not raw K8s deployments)

**Consequences:**
- Every engine should mount `/metrics` (utility function makes this 1 line).
- Venv provisioning adds 1-2 min to first worker startup per engine type.
- Node autoscaler is P2 — start with app-level autoscaling only.

**Follow-ups:**
- Grafana dashboard templates for vLLM / SGLang / YOLO.
- Alert rules for KV cache saturation, queue depth > threshold.
- Engine authoring guide (how to write, test, register a custom engine).
