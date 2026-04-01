# Disaggregated Prefill-Decode Serving Design

## Motivation

The current `VLLMEngine` is a single Ray Serve deployment that handles the
entire request lifecycle — tokenisation, prefill (KV-cache computation over the
prompt), and decode (autoregressive token generation) — inside one actor.

This is simple and correct, but leaves performance on the table:

| Phase   | GPU characteristics needed | Bottleneck |
|---------|---------------------------|------------|
| Prefill | High compute (FLOPS), large batch sizes | FLOPS-bound |
| Decode  | High memory bandwidth, continuous batching | Memory-bandwidth-bound |

Running both phases on the same GPU forces a compromise. Separating them onto
dedicated hardware (or at least dedicated Ray actors) allows each phase to be
tuned independently and scaled at different replica counts.

The native Python approach kept in `vllm_engine.py` — using vLLM's
`AsyncLLMEngine` and its OpenAI serving classes in-process — is the right
foundation for this. Because we construct the deployment graph explicitly in
Python, we can compose multiple `VLLMEngine`-derived deployments into a single
Ray Serve application using Ray's chained-handler (deployment graph) mechanism.

---

## Target Architecture

```
                         ┌─────────────────────────────────────────────┐
                         │           Ray Serve Application             │
                         │                                             │
  HTTP Request           │  ┌──────────────────────────────────────┐  │
 ──────────────►  nginx  │  │        InterfaceDeployment           │  │
                  :5000  │  │  - OpenAI-compatible HTTP router     │  │
                         │  │  - Parses / validates request        │  │
                         │  │  - Holds handles to engine replicas  │  │
                         │  │  - Assembles streamed responses      │  │
                         │  └──────┬───────────────────┬───────────┘  │
                         │         │ DeploymentHandle  │              │
                         │  ┌──────▼──────┐   ┌────────▼──────────┐  │
                         │  │  Prefill    │   │    Decode         │  │
                         │  │  Deployment │   │    Deployment     │  │
                         │  │  (N replicas│   │   (M replicas)    │  │
                         │  │  GPU-A)     │   │   GPU-B)          │  │
                         │  └─────────────┘   └───────────────────┘  │
                         └─────────────────────────────────────────────┘
```

Alternatively, when disaggregation is not needed, the interface simply wraps a
single engine deployment (backward-compatible with the current design):

```
  InterfaceDeployment
       │
       └─► VLLMEngine (single deployment, current behaviour)
```

---

## Ray Serve Deployment Graph

Ray Serve supports deployment composition via `DeploymentHandle`.  A
deployment's constructor can accept handles to other deployments, which Ray
wires up automatically when `serve.run()` processes the application graph:

```python
@serve.deployment
class InterfaceDeployment:
    def __init__(
        self,
        *engine_handles,          # one handle → standard; two handles → P-D split
    ):
        # engine_handles[0] is the prefill (or unified) engine
        # engine_handles[1], if present, is the decode engine
        self._engines = list(engine_handles)

    async def __call__(self, request: Request):
        ...

# Standard (single engine) — current behaviour
app = InterfaceDeployment.bind(
    VLLMEngine.options(num_replicas=1, ray_actor_options={"num_gpus": 1})
             .bind(engine_config)
)

# Disaggregated (two separate engines)
app = InterfaceDeployment.bind(
    PrefillEngine.options(num_replicas=N, ray_actor_options={"num_gpus": 1})
                .bind(prefill_config),
    DecodeEngine.options(num_replicas=M, ray_actor_options={"num_gpus": 1})
               .bind(decode_config),
)

serve.run(app, route_prefix="/model")
```

---

## Component Responsibilities

### InterfaceDeployment

- Single public entry point; owns the `route_prefix`.
- Parses the OpenAI request and selects the routing strategy.
- For the **standard** path: forwards the full request to the unified engine
  and streams the response back.
- For the **disaggregated** path:
  1. Sends the prompt to the prefill engine; receives a KV-cache token or a
     pre-computed token-ID sequence.
  2. Passes the result to the decode engine and streams generated tokens back
     to the caller.
- Implements request-level load balancing across multiple prefill/decode
  replica pools via `DeploymentHandle.options(use_new_handle_api=True)`.

### PrefillEngine (disaggregated mode only)

- Runs `AsyncLLMEngine` configured for prefill-only workloads.
- May use a smaller or cheaper GPU (prefill is FLOPS-bound, not
  memory-bandwidth-bound).
- Returns either:
  - A KV-cache reference (vLLM P-D transfer protocol), or
  - The generated first token + context for handoff to decode.

### DecodeEngine (disaggregated mode only)

- Runs `AsyncLLMEngine` configured for decode workloads.
- Receives the KV-cache / context from prefill.
- Runs continuous-batching autoregressive generation.
- Streams tokens back to the interface.

### VLLMEngine (standard / unified mode)

- The existing single-deployment engine — unchanged.
- Used directly by `InterfaceDeployment` when no disaggregation is configured.

---

## vLLM Disaggregated Serving Integration

vLLM ≥ 0.6 supports prefill-decode disaggregation natively via
`--kv-transfer-config`.  Two transfer backends are available:

| Backend | Transport | Notes |
|---------|-----------|-------|
| `PyNcclConnector` | NVLink / IB | Best for intra-node or high-speed inter-node |
| `MooncakeConnector` | RDMA / TCP | Designed for cross-node KV transfer |

Each vLLM instance is started with a role flag:

```bash
# Prefill instance
python -m vllm.entrypoints.openai.api_server \
    --model <model> \
    --kv-transfer-config '{"kv_connector":"PyNcclConnector","kv_role":"kv_producer",...}'

# Decode instance
python -m vllm.entrypoints.openai.api_server \
    --model <model> \
    --kv-transfer-config '{"kv_connector":"PyNcclConnector","kv_role":"kv_consumer",...}'
```

Our `PrefillEngine` and `DecodeEngine` deployments would pass the appropriate
`kv_transfer_config` to `AsyncEngineArgs` (or via env/CLI when launched as
subprocesses) and the interface coordinates the two-phase request lifecycle.

Reference: https://docs.vllm.ai/en/stable/features/disagg_prefill.html

---

## Configuration Schema (proposed)

The current `DeployModelRequest` schema would be extended with an optional
`topology` block.  Absence of `topology` means the current single-deployment
behaviour:

```json
{
  "name": "llama3-disagg",
  "engine_type": "vllm",
  "model": "/models/llama3-70b",
  "route_prefix": "/llama3",

  "topology": {
    "mode": "disaggregated",

    "interface": {
      "num_replicas": 1
    },

    "prefill": {
      "num_replicas": 2,
      "node_type": "prefill-worker",
      "engine_config": {
        "dtype": "bfloat16",
        "tensor_parallel_size": 1,
        "gpu_memory_utilization": 0.8,
        "kv_transfer_config": {
          "kv_connector": "PyNcclConnector",
          "kv_role": "kv_producer",
          "kv_rank": 0,
          "kv_parallel_size": 2
        }
      }
    },

    "decode": {
      "num_replicas": 4,
      "node_type": "decode-worker",
      "engine_config": {
        "dtype": "bfloat16",
        "tensor_parallel_size": 1,
        "gpu_memory_utilization": 0.9,
        "kv_transfer_config": {
          "kv_connector": "PyNcclConnector",
          "kv_role": "kv_consumer",
          "kv_rank": 1,
          "kv_parallel_size": 2
        }
      }
    }
  }
}
```

For the standard (non-disaggregated) path the schema is identical to today —
no changes to existing configs.

---

## Engine Registry Extension Plan

The current registry maps `engine_type → (engine_class, config_builder, deployment_factory)`.
The factory pattern already abstracts deployment creation; extending it for
disaggregated topologies requires changes only to the factory:

```
Current:
  DeploymentFactoryProtocol.create_deployment(engine_config, ...) → serve.Application

Extended:
  DeploymentFactoryProtocol.create_deployment(engine_config, topology=None, ...) → serve.Application
```

When `topology` is present and `topology.mode == "disaggregated"`:
1. Build `PrefillEngine` deployment from `topology.prefill` config.
2. Build `DecodeEngine` deployment from `topology.decode` config.
3. Compose both under `InterfaceDeployment`.
4. Return the bound `InterfaceDeployment` as the application root.

When `topology` is absent (default): existing code path, zero change.

---

## Implementation Roadmap

### Phase 0 — Foundation (done)
- [x] `VLLMEngine` in-process native implementation with `AsyncLLMEngine`
- [x] Version-aware initialisation (v0.13.x / v0.18.0+)
- [x] Engine registry with `ConfigBuilderProtocol` + `DeploymentFactoryProtocol`

### Phase 1 — Interface layer
- [ ] Extract `InterfaceDeployment` as a thin pass-through wrapping `VLLMEngine`
- [ ] `InterfaceDeployment` holds a `DeploymentHandle` and proxies all requests
- [ ] Deployment factory builds the two-layer graph instead of a bare engine

This phase is purely structural — no behaviour change.  It establishes the
graph topology that Phase 2 fills in.

### Phase 2 — Disaggregated prefill-decode
- [ ] `topology` field in `DeployModelRequest` + validation
- [ ] `PrefillEngine` and `DecodeEngine` subclasses of `VLLMEngine` that pass
  `kv_transfer_config` to `AsyncEngineArgs`
- [ ] `InterfaceDeployment` two-phase routing:
  prefill handle → KV cache reference → decode handle → stream
- [ ] Ray `node_type` resource pinning for prefill vs decode workers
  (re-uses the `resources: {"node_type:prefill-worker": 0.001}` pattern
   already in `YOLODeploymentFactory`)

### Phase 3 — Multi-model chaining (future)
- [ ] Support chaining heterogeneous engines under one interface
  (e.g., a reranker deployment before the generation engine)
- [ ] Weighted routing across multiple model versions (A/B, canary)

---

## Why the Native In-Process Approach Matters Here

The subprocess proxy alternative (`subprocess.Popen` + httpx) would work for a
single engine but cannot participate in Ray's deployment graph:

- `DeploymentHandle` operates over Ray's object store and actor protocol,
  not HTTP.
- Intermediate results (KV-cache references, token IDs) between prefill and
  decode must travel over Ray channels, not TCP sockets.
- Ray Serve's built-in backpressure, replica routing, and autoscaling apply
  per-deployment-handle; a subprocess has none of this.

Keeping `AsyncLLMEngine` in-process inside each Ray actor is therefore the
prerequisite for the disaggregated architecture described above.
