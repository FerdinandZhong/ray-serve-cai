# Live Qwen Validation — 2026-09-13

This is a small functional validation of the existing CAI deployment, not a
throughput or latency benchmark. The bearer token used for the checks was read
locally and is not recorded here.

## Target

- Head application: `ray-cluster-head.<CAI-domain>`
- Deployment: `qwen3-8-27b`
- Route: `/qwen3-8`
- Engine: vLLM
- Model: `Qwen/Qwen3.8-27B-FP8`
- Tensor parallelism: 2
- Replicas: 1

The authenticated Management API intent record reported this deployment as
`live: true`; its application-list entry reported `ApplicationStatus.RUNNING`.

## Management API checks

| Endpoint | Result |
|---|---|
| `/docs` | HTTP 200 |
| `/openapi.json` | HTTP 200; title `Ray Cluster Management API` |
| `/api/health` | HTTP 200 |
| `/api/v1/applications` | HTTP 200; `qwen3-8-27b` listed as running |
| `/api/v1/applications/intents` | HTTP 200; supplied route, model, TP, and placement details above |

## FlashInfer mode observed

The authenticated deployment intent included both the venv CUDA 13 toolkit root
in `CUDA_HOME` and `VLLM_USE_FLASHINFER_SAMPLER=0`. Therefore this running Qwen
example uses vLLM's native sampler rather than FlashInfer's fused sampler. The
setting does not prove that every FlashInfer component/package is absent; it
specifically disables the sampler path. This is the safe current Blackwell
configuration, not evidence of a successful FlashInfer JIT build.

## Completion check

The standard OpenAI-compatible completion endpoint was exercised with a short,
deterministic prompt:

```text
POST /qwen3-8/v1/completions
model: Qwen/Qwen3.8-27B-FP8
prompt: Complete exactly this sentence: Ray Serve CAI is
max_tokens: 32
temperature: 0
```

Result: HTTP 200, `finish_reason: stop`, end-to-end elapsed time **1.262 s**.
The response began: “Ray Serve CAI is a cloud-native AI inference platform.”

Two preceding chat-completion probes also returned HTTP 200 but emitted no
visible content before `finish_reason: length`; they are not treated as model
failures or performance data. The completion endpoint provides the successful
functional demonstration corresponding to the reviewer’s original API example.

## Still required for reviewer closure

- A reproducible Locust benchmark artifact (concurrency, duration, token
  counts, TTFT/E2E percentiles, failures, GPU/version/configuration details).
- Reprise recording of AMP import, all job stages including monitoring, Swagger,
  the Qwen completion, and dashboard access.
- Live monitoring dashboard/app evidence from the AMP-created monitoring stage.
