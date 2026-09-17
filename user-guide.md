# User Guide

How to launch a cluster, run the Management API, deploy a model, and query it.
For the REST API contract details, see [component-api.md](component-api.md).

## 1. Launch a cluster

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
`start-cai`, `stop-cai`, `status-cai`. See
[docs/cai_cluster_guide.md](docs/cai_cluster_guide.md).

## 2. Run the Management API

On CML the head-node Application serves the Management API automatically. To run
it directly (e.g. locally against `RAY_ADDRESS=auto`):

```bash
python -m ray_serve_cai.management.app
# → http://<host>:<CDSW_APP_PORT|8080>
#   Swagger UI at /docs, ReDoc at /redoc, health at /api/health
```

## 3. Deploy a model

Everything deploys through one endpoint — `POST /api/v1/applications`:

```bash
# Strong model (27B FP8, TP=2) — confirmed working on RTX PRO 6000 / L40s
curl -X POST http://<head>/api/v1/applications \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "qwen3-27b",
        "engine_type": "vllm",
        "model": "Qwen/Qwen3.8-27B-FP8",
        "route_prefix": "/qwen3",
        "tensor_parallel_size": 2,
        "engine_config": {
          "dtype": "auto",
          "max_model_len": 131072,
          "gpu_memory_utilization": 0.95,
          "enable_prefix_caching": true,
          "trust_remote_code": true
        },
        "scheduling": {
          "resources": {"node_type:rtxpro6000-gpu-worker": 0.001}
        }
      }'

# Lighter option (7B, single GPU)
curl -X POST http://<head>/api/v1/applications \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "qwen2-7b",
        "engine_type": "vllm",
        "model": "Qwen/Qwen2.5-7B-Instruct",
        "route_prefix": "/qwen2",
        "tensor_parallel_size": 1,
        "engine_config": {"dtype": "bfloat16", "gpu_memory_utilization": 0.9},
        "scheduling": {"resources": {"node_type:l40-gpu-worker": 0.001}}
      }'
```

Deployment is asynchronous: the call returns `deploying` and Ray Serve brings the
replica up in the background. Poll `GET /api/v1/applications/qwen3-27b` for status.

## 4. Query it

Each LLM engine serves OpenAI-compatible routes under its `route_prefix`:

```python
from openai import OpenAI

client = OpenAI(base_url="http://<head>/qwen3/v1", api_key="not-required")
resp = client.chat.completions.create(
    model="Qwen/Qwen3.8-27B-FP8",
    messages=[{"role": "user", "content": "Explain tensor parallelism in two sentences."}],
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

## Stress-testing a deployment

Use the companion `ray-serve-cai-bench` project's Locust suite:

```bash
cd ray-serve-cai-bench
# edit configs/cluster.env: BASE_URL, VLLM_ROUTE, VLLM_MODEL
locust -f locust/locustfile_chat.py --headless -u 10 -r 2 -t 60s   # headless
locust -f locust/locustfile_chat.py                                # web UI at :8089
```
