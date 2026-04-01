# Roadmap

## 1. Centralised Metrics Exposure

**Goal:** Single `/metrics` endpoint on the head node that aggregates Prometheus metrics from all nodes and all Ray Serve applications.

**Architecture:**
```
HEAD /metrics  ──►  Management API scraper
                      ├── Ray node metrics:  <node_ip>:8080/metrics  (each node)
                      ├── vLLM app metrics:  <node_ip>:<vllm_metrics_port>/metrics
                      ├── YOLO app metrics:  custom counters (request count, latency, batch size)
                      └── MCP app metrics:   custom counters (tool call count, latency)
```

**Implementation plan:**
1. **Node-level metrics scraper** — background task in the management app that periodically scrapes `<node_ip>:8080/metrics` (Ray's built-in Prometheus exporter) for every known node. Discover node IPs from `ray.nodes()`.
2. **Application-level metrics** — vLLM already exposes Ray-compatible metrics; scrape those from the same node IP on vLLM's metrics port. For custom engines (YOLO, MCP), define a `MetricsProtocol` in `engines/base.py` with a `get_metrics() -> str` method returning Prometheus text format. Engine authors implement it; the scraper calls it.
3. **Aggregation** — merge all scraped text into one `/metrics` response. Use labels (`node_ip`, `app_name`, `engine_type`) to distinguish sources. Optionally cache for 5–15 s to avoid thundering-herd scrapes.
4. **`GET /api/v1/metrics`** — new management API route returning aggregated Prometheus text. Grafana / external Prometheus can scrape this single endpoint.

**Key considerations:**
- Ray exposes metrics on port 8080 by default (`--metrics-export-port`). Confirm this is set in head/worker launcher templates.
- vLLM metrics port may differ per deployment; store in engine_config and pass to scraper.
- For custom engines, provide a `prometheus_client` helper in `engines/base.py` so engines can register counters/histograms with minimal boilerplate.

---

## 2. Management API Reorganisation & Dynamic Engine Registration

**Goal:** Well-structured endpoint groups; model pulling from HuggingFace to NFS; hot-register new engines without cluster restart.

**Endpoint groups (proposed):**
```
/api/v1/cluster/         — cluster status, GCS address, node management
/api/v1/applications/    — deploy, list, delete Ray Serve apps
/api/v1/engines/         — list, register, deregister engines (NEW)
/api/v1/models/          — pull, list, delete model weights on NFS (NEW)
/api/v1/metrics/         — aggregated Prometheus metrics (NEW)
```

**Model pulling:**
- `POST /api/v1/models/pull` — accepts `{"repo_id": "Qwen/Qwen3.5-2B", "local_path": "/home/cdsw/models/Qwen3.5-2B", "revision": "main"}`. Runs `huggingface_hub.snapshot_download()` in a background thread. Returns a task ID.
- `GET /api/v1/models/pull/{task_id}` — poll download progress (bytes downloaded, ETA).
- `GET /api/v1/models` — list models on NFS (`/home/cdsw/models/`).
- `DELETE /api/v1/models/{name}` — remove model from NFS.

**Dynamic engine registration:**
- `POST /api/v1/engines/register` — accepts `{"engine_type": "my_engine", "module_path": "my_package.my_engine", "config_builder": "my_package.my_config.MyConfigBuilder", "deployment_factory": "my_package.my_config.MyDeploymentFactory"}`. Dynamically imports the module and calls `register_engine()`. No restart needed.
- `DELETE /api/v1/engines/{engine_type}` — deregister an engine.
- `GET /api/v1/engines` — list all registered engines (already exists, move to new group).
- User workflow: upload engine script to NFS or project dir → call register → deploy with the new engine_type.

**Key considerations:**
- Dynamic imports need safety: validate module path, catch import errors, don't break existing engines.
- Model pulling can be long-running; use `asyncio.to_thread` + progress tracking.
- Consider auth/RBAC for destructive operations (delete model, deregister engine).

---

## 3. Standalone MCP Deployment Directory

**Goal:** Isolated sub-directory for MCP server definitions and registration, separate from the core engine code.

**Proposed structure:**
```
ray_serve_cai/
  engines/
    mcp_engine.py          — generic MCP engine (unchanged)
    mcp_config.py          — config builder + factory (unchanged)
  mcps/                    — standalone MCP servers directory (NEW top-level)
    __init__.py
    weather_tools.py       — weather MCP tools (moved from engines/mcps/)
    finance_tools.py       — example: stock/crypto price tools
    database_tools.py      — example: SQL query tools
    README.md              — how to add a new MCP server
```

**Auto-discovery:** On startup or via `POST /api/v1/engines/register`, scan `mcps/` for modules exporting a `FastMCP` instance. Each discovered module can be deployed via `engine_config.mcp_module`.

**Key considerations:**
- Keep `mcps/` at the project root (not nested under `engines/`) so it's easy to add new tools without touching engine code.
- Each MCP module is self-contained: its own deps, its own `FastMCP` instance.
- `requirements.txt` per MCP module (for isolated env support — see item 4).

---

## 4. Isolated Environments per Engine

**Goal:** Separate venvs per engine type to avoid dependency conflicts (e.g. vLLM vs SGLang). Head node has a control venv; each engine type has its own runtime venv.

**Architecture:**
```
/home/cdsw/.venv/              — head control venv (management API, Ray, FastAPI)
/home/cdsw/.venv_vllm/         — vLLM engine venv (vllm, torch, transformers)
/home/cdsw/.venv_sglang/       — SGLang engine venv
/home/cdsw/.venv_yolo/         — YOLO engine venv (ultralytics, Pillow)
/home/cdsw/.venv_mcp/          — MCP engine venv (mcp, httpx)
```

**Implementation plan:**
1. **Venv provisioning** — add `POST /api/v1/engines/{engine_type}/setup` endpoint (or a setup script) that creates the venv and installs engine-specific requirements from a `requirements_<engine>.txt` file.
2. **Ray runtime_env** — when deploying, set `runtime_env={"pip": [...]}` or `runtime_env={"virtualenv": "/home/cdsw/.venv_vllm/"}` in `ray_actor_options`. Ray will use the specified venv for the actor process.
3. **Worker launcher** — workers install all venvs at startup (or on-demand when an engine is first deployed). The Jinja2 template can loop over engine requirements files.
4. **Engine config** — add optional `venv_path` field to engine_config. If set, the deployment factory includes it in `runtime_env`. If not set, fall back to the default venv.

**Key considerations:**
- `runtime_env` with `virtualenv` requires the venv to exist on every node that might run the actor. Either pre-provision on all workers or use `pip` runtime_env (slower, installs on-demand).
- Head node only needs the control venv — it doesn't run engine actors.
- Test with `runtime_env={"pip": ["vllm==0.8.5"]}` first (simpler, Ray handles isolation) before moving to pre-built venvs (faster startup).
- NFS-mounted venvs (`/home/cdsw/.venv_*`) are shared across all nodes — install once, available everywhere.

---

## 5. Auto-scaling (Node + Application)

**Goal:** Automatically scale Ray worker nodes (via CML API) and Ray Serve applications based on user-defined metrics. Two-step process: node-level then app-level.

**Architecture:**
```
Metrics ──► Scaling Policy ──► Decision Engine
                                  │
                        ┌─────────┴─────────┐
                        ▼                   ▼
                  Scale UP              Scale DOWN
                  1. Add node           1. Scale down app replicas
                  2. Wait for ready     2. Wait for requests to drain
                  3. Scale up app       3. Identify empty nodes
                                        4. Kill empty nodes
```

**Implementation plan:**

1. **Scaling policy config** — define in `ray_cluster_config.yaml` or per-app:
   ```yaml
   autoscaling:
     enabled: true
     metrics_source: "prometheus"        # or "ray_serve" or "custom"
     scale_up_metric: "avg_ongoing_requests"
     scale_up_threshold: 0.8             # 80% of max_ongoing_requests
     scale_down_metric: "avg_ongoing_requests"
     scale_down_threshold: 0.1           # 10% — nearly idle
     cooldown_seconds: 300               # 5 min between scaling actions
     min_nodes: 1
     max_nodes: 8
     node_type: "t4-gpu-worker"          # which CML node spec to add
   ```

2. **Metrics collector** — background asyncio task in the management app that polls metrics (from `/api/v1/metrics` or Ray Serve's internal metrics). Computes rolling averages over a configurable window.

3. **Scale-up flow:**
   - Metric exceeds `scale_up_threshold` → check if `num_nodes < max_nodes`
   - Call `POST /api/v1/resources/nodes/add` with the configured `node_type`
   - Wait for node to become `ALIVE` in `ray.nodes()`
   - Call `serve.get_app_handle().options(num_replicas=current+1)` or redeploy with increased replicas

4. **Scale-down flow (the hard part):**
   - Metric below `scale_down_threshold` for `cooldown_seconds`
   - Step 1: Reduce app replicas first — `serve.run()` with `num_replicas=current-1`. Wait for Ray Serve to drain the replica.
   - Step 2: After replica is removed, identify nodes with **zero running actors** (empty nodes) — `ray.nodes()` cross-referenced with `ray.state.actors()`.
   - Step 3: Remove the empty node via `DELETE /api/v1/resources/nodes/{app_id}` (CML API kills the pod).
   - **Never kill a node that still has actors** — always drain first.

5. **Safety guards:**
   - `min_nodes` floor — never scale below this.
   - `cooldown_seconds` — prevent thrashing.
   - **Graceful drain** — set `SIGTERM` grace period on the node; Ray migrates actors before the node dies.
   - **Blacklist recently-added nodes** — don't remove a node within 5 min of adding it.
   - Separate scale-up and scale-down cooldowns.

6. **Custom metrics support:**
   - Users define custom metrics in engine config (e.g. `"scale_metric": "gpu_utilization"`).
   - The scaling policy reads from the aggregated `/api/v1/metrics` endpoint.
   - Engines expose custom metrics via the `MetricsProtocol` from item 1.

**Key considerations:**
- Scale-down is the hard problem. The invariant is: **app replicas decrease before nodes disappear**.
- CML node creation takes 1–3 min (pod scheduling + venv setup + `ray start`). Factor this latency into the scale-up decision — trigger early based on trend, not just threshold.
- Consider a "pre-warm pool" of 1 standby node to reduce scale-up latency.
- Log all scaling decisions with timestamps and metrics values for auditability.
