# Project Overview

**Ray Serve orchestration for model inference on Cloudera AI.**

`ray-serve-cai` turns a set of Cloudera AI (CAI) / Cloudera Machine Learning (CML)
Applications into a live Ray cluster and gives you a single REST API to deploy,
scale, place, and monitor inference workloads on it — vLLM and SGLang LLMs, a
LiteLLM gateway, YOLO vision models, MCP tool servers, or any custom Ray Serve app.

Each engine runs in its own isolated Python virtual environment so mutually
incompatible dependency stacks (e.g. vLLM vs SGLang) coexist on the same cluster,
and every deployment can be pinned to specific nodes and GPU topologies through a
declarative scheduling block.

## Overview

Serving models on CAI/CML has three recurring pain points this project solves:

1. **No native multi-node Ray on CML.** CML exposes *Applications* (long-running
   containers) but no first-class Ray cluster. `cai_integration` launches one CML
   Application as the Ray head and N more as workers, wiring them into a single
   cluster over the pod network.
2. **Engine dependency conflicts.** vLLM and SGLang require incompatible
   `llguidance` versions and cannot share one environment. Each engine is
   installed into its own venv (`/home/cdsw/.venv-<engine>`) on shared NFS, and the
   actor for that engine is launched under that interpreter via Ray's
   `py_executable` runtime env.
3. **Hard-to-express placement.** Getting tensor-parallel shards onto the right
   GPUs, or a fractional-GPU + KV-cache topology onto one node, normally means
   hand-writing Ray placement groups. This project derives sensible placement
   groups automatically and lets you override any part of them declaratively.

## Target Audience

- **ML Engineers** deploying LLMs or vision models at scale on Cloudera AI
- **Data Scientists** who need GPU-backed inference without managing Kubernetes directly
- **Platform Engineers** operating CAI workbench clusters and needing multi-engine, multi-tenant serving
- **Application Developers** building services on top of OpenAI-compatible inference endpoints

## Use Cases

| Use case | How this project covers it |
|---|---|
| **LLM serving microservice** | Deploy vLLM or SGLang behind a stable HTTP route; query via OpenAI client |
| **Multi-model A/B or routing** | Deploy N models simultaneously; route traffic at the application layer via LiteLLM gateway |
| **Large-model tensor parallelism** | Spread a 27B–70B+ model across 2+ GPUs with one declarative `tensor_parallel_size` field |
| **Fractional-GPU multi-tenancy** | Pack multiple small models onto one GPU node with `gpu_fraction` |
| **Vision / multi-modal inference** | YOLO object detection engine; batched image requests via the same REST API |
| **Tool servers (MCP)** | Host Model Context Protocol servers as Ray Serve apps on the same cluster |
| **Cost-optimised batch routing** | Use LiteLLM to route to cheaper external providers when GPU capacity is at peak |

## Free & Open-Source Model Sources

Models can be pulled directly from [HuggingFace Hub](https://huggingface.co/models) by passing a HF model ID in the deploy payload — no separate download step. Recommended starting points:

| Model | HF ID | Size | Notes |
|---|---|---|---|
| Qwen3.8-27B FP8 | `Qwen/Qwen3.8-27B-FP8` | 27B (FP8) | Strong reasoning; confirmed on RTX PRO 6000 TP=2 |
| Qwen2.5-7B Instruct | `Qwen/Qwen2.5-7B-Instruct` | 7B | Single-GPU capable; good for development |
| Llama 3.1 8B Instruct | `meta-llama/Llama-3.1-8B-Instruct` | 8B | Gated; requires HF token |
| Mistral 7B Instruct | `mistralai/Mistral-7B-Instruct-v0.3` | 7B | Open weights; single-GPU |
| YOLO v11 | `ultralytics/assets` (auto-download) | — | Vision; use with `engine_type: yolo` |

Set `HUGGING_FACE_HUB_TOKEN` in `scheduling.env_vars` for gated models.

## Two packages, one direction

```
ray_serve_cai/       generic Ray Serve orchestration — engines, Management API,
                      programmatic Python API. Works on any Ray cluster.
        ▲
        │ imports (one-way)
        │
cai_integration/      CML-specific: launches Ray head/workers as CML Apps,
                      sets up nginx, builds per-engine venvs.
```

## Where to go next

- Using it day-to-day → [user-guide.md](user-guide.md)
- How it fits together → [architecture.md](architecture.md)
- Full REST API reference → [component-api.md](component-api.md)
- Contributing / running checks → [development.md](development.md)
- Design rationale behind the structure → [DESIGN.md](DESIGN.md)
- What an AI agent should know first → [AGENTS.md](AGENTS.md)
- What's in flight → [TODO.md](TODO.md)
