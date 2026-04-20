# Ray Serve on Cloudera CAI: A Multi-Engine ML Serving Platform

*A technical deep-dive into building a production ML serving platform that handles LLMs, vision models, and tool servers — all on one Ray cluster.*

---

## The Problem

You have a Kubernetes cluster on Cloudera AI (CAI). You need to serve a mix of models: large language models (Qwen, Llama), a YOLO object detector, maybe an OCR pipeline, and some MCP tool servers. Each model has different resource requirements — some need full GPUs, others share a fractional GPU, and CPU-only workloads run alongside.

Cloud-native LLM platforms like vLLM-on-Kubernetes exist, but they're single-engine. You'd need separate infrastructure for each model type, separate scaling policies, separate metrics pipelines. That's a lot of moving parts for a platform team to maintain.

**Ray Serve** solves this by acting as the orchestration layer. It handles replica placement, request routing, autoscaling, and batching — for *any* Python-based model server. Combined with a management API and an engine plugin system, we built a platform where deploying a new model is a single API call:

```bash
curl -X POST http://host/api/v1/applications/model \
  -d '{"engine_type": "vllm", "model": "Qwen/Qwen3.5-4B", "gpu_fraction": 0.5}'
```

This post walks through the architecture, the engine plugin system, the streaming and serialization challenges we solved, and what we learned from benchmarking.

![Architecture Overview](figures/architecture-overview.html)

---

## Architecture Overview

The cluster has two node types: a **head node** (CPU-only) and **worker nodes** (GPU-enabled).

### Head Node

The head node runs three services inside a single CML Application pod:

1. **Ray GCS + Dashboard** — cluster state, actor scheduling, the Ray Dashboard on port 8265, and Prometheus metrics export on port 9090.
2. **Ray Serve** — hosts the Management API and all deployed model applications on port 5000.
3. **nginx** — reverse proxy on port 8080 (the CAI-exposed `CDSW_APP_PORT`), routing by path prefix.

nginx is the single entry point. It routes `/api/*` to the Management API, `/dashboard/` to Ray Dashboard, `/metrics` to the aggregated Prometheus endpoint, and everything else (`/{prefix}/*`) to the corresponding model application:

```nginx
location /api/ {
    proxy_pass http://ray_serve/api/;
    proxy_buffering off;    # SSE streaming support
}

location /metrics {
    proxy_pass http://ray_serve/api/v1/metrics;
    access_log off;
}

location / {
    proxy_pass http://ray_serve/;
    proxy_buffering off;    # token-by-token streaming
}
```

### Worker Nodes

Worker nodes are separate CML Application pods that join the cluster via `ray start --address=<head_gcs>:6379`. Each worker is labeled with a `node_type` (e.g., `t4-gpu-worker`, `cpu-worker`) that the Management API uses for placement decisions.

GPU workers run vLLM and SGLang deployments. CPU workers handle YOLO detection and MCP tool servers. A single T4 GPU can host two small models (e.g., Qwen3.5-2B + PaddleOCR) via fractional GPU allocation (`gpu_fraction: 0.5`).

### Management API

The Management API is a FastAPI application deployed as a Ray Serve app via `@serve.ingress`. It exposes:

- `POST /api/v1/applications/model` — deploy a model (any engine type)
- `DELETE /api/v1/applications/{name}` — tear down a deployment
- `GET /api/v1/applications` — list running applications
- `GET /api/v1/cluster/nodes` — cluster topology and resources
- `GET /api/v1/metrics/all` — aggregated Prometheus metrics from all nodes

Internally, it delegates to a **Coordinator** that wraps both `RayService` (Ray Serve operations) and `CAIService` (CML API for node management).

---

## The Engine Plugin System

Every model type — vLLM, SGLang, YOLO, MCP — is an **engine plugin**. The system uses a registry pattern with three protocol contracts:

![Engine Plugin System](figures/engine-plugin-system.html)

```python
# Three protocols every engine must satisfy:
LLMEngineProtocol       # The runtime class (deployed on workers)
ConfigBuilderProtocol   # Validates and transforms user config → engine config
DeploymentFactoryProtocol  # Creates the Ray Serve application graph
```

The `EngineRegistry` is a singleton factory. At import time, `engines/__init__.py` attempts to register each engine with lazy imports and a stub fallback:

```python
try:
    from .vllm_config import VLLMConfigBuilder, VLLMDeploymentFactory

    try:
        from .vllm_engine import VLLMEngine, create_vllm_deployment
    except Exception:
        VLLMEngine = type("VLLMEngine", (), {})  # stub for CPU-only head
        create_vllm_deployment = None

    register_engine(
        engine_type="vllm",
        engine_class=VLLMEngine,
        config_builder=VLLMConfigBuilder(),
        deployment_factory=VLLMDeploymentFactory(),
        set_as_default=True,
    )
except Exception as e:
    logger.warning("Failed to register vLLM engine: %s", e)
```

This two-layer import is critical. The config builder and deployment factory live in `vllm_config.py`, which has *no* `import vllm` at module level — it's lightweight. The actual engine class in `vllm_engine.py` imports vLLM, which requires CUDA. On the CPU-only head node, the engine import fails but the config builder still registers successfully. The actual `VLLMEngine` class is only instantiated on GPU workers when Ray Serve deploys a replica.

### Adding a New Engine

To add a new engine type:

1. Implement `ConfigBuilderProtocol` — validate user-facing config, produce internal engine config.
2. Implement `DeploymentFactoryProtocol` — call `serve.run()` with the right resources and options.
3. Register in `engines/__init__.py` with the lazy import pattern.

The deployment flow is always the same:

```
POST /api/v1/applications/model
  → ConfigBuilder.build_config()      # validate & transform
  → DeploymentFactory.create_deployment()  # build Ray Serve app
  → serve.run(app, route_prefix=...)  # deploy to cluster
```

### Four Engines Today

| Engine | Type | Key Pattern |
|--------|------|-------------|
| **vLLM** | In-process LLM | `AsyncLLMEngine` + `RayPrometheusStatLogger` for Ray-native metrics |
| **SGLang** | Subprocess LLM | Launches `sglang.launch_server` as subprocess, proxies via `httpx` |
| **YOLO** | Vision (batching) | `@serve.batch` for dynamic batching of concurrent detection requests |
| **MCP** | Tool protocol | Dynamic module import + FastMCP auto-discovery |

---

## Request Lifecycle

A request to a deployed model (e.g., `POST /qwen-2b/v1/chat/completions`) passes through five layers:

![Request Lifecycle](figures/request-flow.html)

1. **nginx** matches `/{prefix}/*` and proxies to Ray Serve with `proxy_buffering off` for streaming.
2. **Ray Serve HTTP Proxy** routes by prefix and load-balances across replicas (`max_ongoing_requests=100`).
3. **FastAPI** (`@serve.ingress`) runs `_RoutePathMiddleware` to strip the prefix from `scope["path"]`, then routes to the handler.
4. **Engine handler** (e.g., `chat_completion()`) calls the underlying inference engine.
5. **Model** runs inference — continuous batching for LLMs, `@serve.batch` for YOLO.

### The Streaming Fix

The most challenging bug was getting SSE streaming to work across Ray Serve + FastAPI + vLLM. The symptom: `jsonable_encoder` trying to serialize an `async_generator` object.

**Root cause:** FastAPI's `make_fastapi_class_based_view` (used by `@serve.ingress`) loses the `response_model` inference from return type annotations. Without an explicit `response_model=None`, FastAPI attempts JSON serialization on *every* response — including `StreamingResponse`.

**Compounding factor:** vLLM may use a *different* Starlette installation than the serving app, so `isinstance(result, StreamingResponse)` returns `False` even when the object is stream-shaped.

The fix: a normalizer function that duck-types on the result:

```python
def _normalize_vllm_stream_result(result, *, op_name):
    if inspect.isasyncgen(result):
        body_iter = result
        stream_kw = {"status_code": 200, "media_type": "text/event-stream"}
    else:
        maybe_iter = getattr(result, "body_iterator", None)
        if maybe_iter is not None:
            body_iter = maybe_iter
            stream_kw = {
                "status_code": getattr(result, "status_code", 200),
                "media_type": getattr(result, "media_type", None) or "text/event-stream",
            }
        else:
            return result  # Not a stream — return as-is

    return StreamingResponse(content=_logged_stream(body_iter), **stream_kw)
```

Every route decorator uses `response_model=None`, and every response goes through normalization. This handles all vLLM return types across versions 0.13.x through 0.18+.

### The Serialization Constraint

Ray Serve serializes the entire `@serve.ingress` FastAPI app to ship it to worker replicas. Any module-level object with a thread lock — `FastAPI` subclasses, `prometheus_client` registries, signal handlers — breaks serialization with `cannot pickle '_thread.lock'`.

The rules we learned:

- Use **plain `FastAPI()`** instances, never subclasses.
- Apply middleware via `app.add_middleware()`, not by subclassing.
- Create `prometheus_client` objects inside `__init__`, not at module level.
- Use `ray.util.metrics` instead of `prometheus_client` where possible.

---

## GPU Sharing & Placement Groups

The Management API supports two GPU optimization patterns:

### Fractional GPU

Set `gpu_fraction: 0.5` to run two models on one GPU. The deployment factory translates this into Ray resource requests:

```python
ray_actor_options={"num_gpus": 0.5}
```

Two Qwen3.5-2B replicas can share a single T4, each getting ~8 GB VRAM.

### Placement Groups for Tensor Parallelism

For multi-GPU models, `placement_group_bundles` ensures all shards land on the same node:

```python
# Tensor parallelism across 2 GPUs — STRICT_PACK keeps them together
placement_group_bundles=[{"GPU": 1, "CPU": 1}, {"GPU": 1}]
placement_group_strategy="STRICT_PACK"
```

The deployment factory auto-detects these patterns:
- `tensor_parallel_size > 1` → single bundle with all GPUs (`STRICT_PACK`)
- `gpu_fraction < 1` → `PACK` strategy for bin-packing

---

## Metrics & Observability

Each engine type exposes Prometheus metrics differently. The Management API unifies them behind a single scrape target.

![Metrics Pipeline](figures/metrics-pipeline.html)

### Engine-Level Metrics

**vLLM** uses `RayPrometheusStatLogger` — a vLLM-provided wrapper that replaces all `prometheus_client` metrics with `ray.util.metrics` equivalents:

```python
from vllm.v1.metrics.ray_wrappers import RayPrometheusStatLogger

self.engine = AsyncLLMEngine.from_engine_args(
    self.engine_args,
    stat_loggers=[RayPrometheusStatLogger],
)
```

This exports `vllm:prompt_tokens_total`, `vllm:e2e_request_latency_seconds`, `vllm:time_to_first_token_seconds`, and more — all via Ray's port 9090 alongside system metrics. No custom `/metrics` route needed.

**SGLang** uses its native `--enable-metrics` flag, which starts a Prometheus-compatible HTTP server. The SGLang engine's FastAPI app proxies `/metrics` to this internal server:

```python
# Proxy SGLang's native Prometheus metrics
@app.get("/metrics")
async def metrics():
    async with httpx.AsyncClient(base_url=self._base_url) as client:
        resp = await client.get("/metrics")
        return PlainTextResponse(resp.text)
```

**YOLO and MCP** use `ray.util.metrics` directly — `Counter`, `Histogram`, and `Gauge` objects created in `__init__`:

```python
from ray.util.metrics import Counter, Histogram

self._m_requests = Counter("yolo_requests_total", tag_keys=("status",))
self._m_inference = Histogram(
    "yolo_inference_seconds",
    boundaries=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
self._m_batch_size = Histogram("yolo_batch_size", boundaries=[1, 2, 4, 8, 16, 32])
```

### Central Scraper

The Management API provides two aggregation endpoints:

- **`GET /api/v1/metrics/all`** — scrapes every alive Ray node's port 9090 in parallel using `asyncio.gather()`, with a 10-second cache. Picks up Ray system metrics plus all `ray.util.metrics` from vLLM, YOLO, and MCP.
- **`GET /api/v1/metrics/apps`** — scrapes each app's `{route_prefix}/metrics` route. Picks up SGLang native metrics.

For external Prometheus, `GET /api/v1/metrics/discovery` returns Ray's auto-generated `prom_metrics_service_discovery.json` for `http_sd_configs`.

---

## Dynamic Batching Deep-Dive

The YOLO engine demonstrates Ray Serve's `@serve.batch` — a decorator that transparently collects concurrent requests into GPU-efficient batches.

![Batching Comparison](figures/batching-comparison.html)

### How It Works

Individual `detect(image_bytes)` calls arrive concurrently. Ray Serve holds them in a queue until either `max_batch_size` images accumulate or `batch_wait_timeout_s` elapses. Then it calls `_detect_batch(image_bytes_list)` once with the entire batch:

```python
@serve.batch(max_batch_size=8, batch_wait_timeout_s=0.05)
async def _detect_batch(self, image_bytes_list: List[bytes]) -> List[Dict]:
    images = [Image.open(io.BytesIO(b)).convert("RGB") for b in image_bytes_list]
    return self._run_inference(images)
```

The model processes 8 images in roughly the same time as 1 (vectorized ops in `ultralytics`). Each caller gets back only their result — Ray Serve handles the fan-out.

### Benchmark Results

We benchmarked YOLO at 24 RPS on a 4-core CPU node, comparing standalone FastAPI (uvicorn, single-threaded) against Ray Serve with `@serve.batch`:

| Metric | Standalone | Ray Serve @serve.batch |
|--------|-----------|----------------------|
| p50 latency | 230 ms | ~350 ms |
| p95 latency | **21,000 ms** | **~400 ms** |
| p95/p50 ratio | **91x** | **~1.1x** |
| Throughput | ~24 RPS (plateau) | ~24+ RPS (stable) |

Without batching, the p95 explodes because requests queue behind each other — the 8th request waits for 7 sequential inferences. With batching, all 8 requests process together. The p50 is slightly higher (batch collection overhead), but p95 and p50 **track together**.

### Tuning

- `max_batch_size`: Match your GPU memory. For YOLO on CPU, 8-16 is effective. For GPU inference, go higher.
- `batch_wait_timeout_s`: Lower = lower latency at low load, but smaller batches. We use 50ms.
- `num_replicas`: Scale horizontally for throughput beyond what batching provides.

---

## Stress Testing

We built a benchmark suite (`ray-serve-cai-bench/`) with three tools, each serving a different purpose:

### Locust (Interactive)

Browser-based UI for exploratory load testing. Ramp up users, watch latency curves in real-time. Best for finding breaking points and understanding behavior under gradual load increases.

### Vegeta (Constant-Rate)

Command-line tool for precise, constant-rate benchmarking. Send exactly N requests/second and measure the result. Best for reproducible latency percentile measurements:

```bash
echo "POST http://host/yolo/detect" | \
  vegeta attack -rate=24/s -duration=60s | \
  vegeta report
```

### vLLM Benchmark (Upstream Comparison)

vLLM's built-in `benchmark_serving.py` for apples-to-apples comparison with published benchmarks. Uses ShareGPT dataset for realistic conversation workloads.

### Key Findings

1. **Batching is the single biggest latency win** for vision models under concurrent load. The p95/p50 ratio dropped from 91x to 1.1x.
2. **Streaming TTFT** (time to first token) matters more than total latency for LLM user experience. SGLang's RadixAttention gives measurably better TTFT on repeated prompts.
3. **Fractional GPU** (`gpu_fraction: 0.5`) works well for small models but requires careful VRAM budgeting — two 2B models on a T4 is fine, but two 4B models will OOM.

---

## What's Next

From our [ROADMAP](../ROADMAP.md):

1. **Isolated environments per engine** — Ray `runtime_env` with separate virtualenvs to avoid dependency conflicts between vLLM, SGLang, and ultralytics.
2. **Auto-scaling** — node-level (add/remove CML worker pods) and app-level (adjust replica count), with drain-before-kill safety and cooldown periods.
3. **Dynamic engine registration** — `POST /api/v1/engines/register` to hot-add new engine types without restarting the platform.
4. **Model pulling** — `POST /api/v1/models/pull` to download HuggingFace models to shared NFS before deployment.
5. **MCP tool directory** — standalone `mcps/` directory with auto-discovery and per-module dependency isolation.

---

## Closing Thoughts

Ray Serve turned out to be the right abstraction layer for multi-engine ML serving on enterprise Kubernetes. It handles the hard parts — replica placement, request routing, GPU sharing, batching — while staying out of the way for engine-specific concerns like vLLM's continuous batching or SGLang's RadixAttention.

The engine plugin system means adding a new model type is a matter of implementing two interfaces (config builder + deployment factory) and registering them. The Management API means deploying a model is a single HTTP call. And the unified metrics pipeline means one Grafana dashboard covers everything.

The code is structured so that the head node stays lightweight (CPU-only, no engine dependencies) while workers pull in only what they need. This separation — plus Ray's built-in fault tolerance — makes the platform resilient to worker crashes and GPU failures.

If you're building ML serving infrastructure on Cloudera AI or any Kubernetes platform, Ray Serve is worth evaluating. The investment in the plugin system and management layer pays for itself the moment you need to serve your second model type.

---

*Built on Ray 2.44+, vLLM 0.13–0.18+, SGLang, Ultralytics YOLOv8/11, and FastMCP. Running on Cloudera AI (CML) with T4/A10/L40 GPUs.*
