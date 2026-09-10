# Blackwell (RTX PRO 6000 / sm_120) — CUDA, nvcc, and NCCL Configuration

Reference for running vLLM on the Blackwell GPU workers in this cluster
(`node_type: rtxpro6000-gpu-worker`, AWS `g7e.12xlarge`, 2× NVIDIA RTX PRO
6000 96 GB, compute capability **sm_120**). Captures the root cause of the
startup crash we hit and the fix, so it doesn't have to be re-derived.

---

## TL;DR

- The crash `Failed to get device capability: SM 12.x requires CUDA >= 12.9.`
  is **FlashInfer's runtime JIT**, not PyTorch. Torch is fine.
- FlashInfer JIT-compiles kernels against the **local CUDA toolkit / `nvcc`
  it finds via `CUDA_HOME`** — *not* torch's bundled CUDA runtime.
- Fix: install a CUDA 13 `nvcc` toolkit into the shared `.venv-vllm` and set
  `CUDA_HOME` for the engine (via the deploy payload). Quick unblock without a
  toolkit: `VLLM_USE_FLASHINFER_SAMPLER=0`.
- The venv lives on **shared project NFS** (`/home/cdsw`), so you install the
  toolkit **once** and every worker pod sees it.
- `CUDA_HOME` must point at the toolkit **root** (dir with `bin/` + `include/`),
  not the `nvcc` binary, and must be set via the **deploy payload** so it
  propagates to every TP worker — a shell `export` does not reach the workers.

---

## The symptom

vLLM replica startup logs (repeated per Ray worker):

```
(EngineCore pid=...) (RayWorkerProc pid=...) (Worker pid=...) Failed to get device capability: SM 12.x requires CUDA >= 12.9.
```

Sometimes followed by the misleading:

```
RuntimeError: FlashInfer requires GPUs with sm75 or higher
```

The `sm75` message is a swallowed follow-on; the real cause is the line above.
Tracked upstream in [vllm#50705](https://github.com/vllm-project/vllm/issues/50705)
and [flashinfer#3675](https://github.com/flashinfer-ai/flashinfer/issues/3675).

### Second variant: toolkit version mismatch (NOT a capability problem)

Once a CUDA-13 toolkit is present, a *different* FlashInfer JIT failure can appear —
a ninja build error ending in:

```
.../flashinfer/data/cccl/libcudacxx/include/cuda/std/__cccl/cuda_toolkit.h:41:8:
error: "CUDA compiler and CUDA toolkit headers are incompatible, please check your include paths"
```

This is **not** about GPU capability. The compile command still correctly targets
the GPU (`-gencode=arch=compute_120f,code=sm_120f`). It is CCCL's compiler↔header
guard: `#if !_CCCL_CUDACC_EQUAL((CUDART_VERSION/1000), (CUDART_VERSION%1000)/10)` —
it errors when the **nvcc compiler version** (`nvidia-cuda-nvcc`) and the **CUDA
runtime headers version** (`CUDART_VERSION` from `nvidia-cuda-runtime`) differ at
major.minor. Root cause here: the CUDA wheels were installed unpinned and drifted
to different minors, while `flashinfer-python` drifted to 0.6.18.x (which bundles a
CCCL that enforces this guard). Fix: pin `nvidia-cuda-{nvcc,runtime,cccl}==13.0.*`
and cap `flashinfer-python<0.6.17` in `_ENGINE_PACKAGES` (done — see
setup_environment.py), then clear `~/.cache/flashinfer` and rebuild the venv.

---

## Root cause

Blackwell RTX PRO 6000 is **sm_120**. sm_120 kernels require **CUDA ≥ 12.9**
to compile. There are two independent CUDA layers in the venv:

| Layer | What it is | Our status |
|---|---|---|
| **PyTorch bundled CUDA runtime** | cudart/cublas/cudnn shipped inside the torch wheel | ✅ `torch 2.13.0+cu130`, CUDA 13.0, `arch_list` includes `sm_120` |
| **Local CUDA toolkit (`nvcc`)** | the *compiler* FlashInfer shells out to for runtime JIT, resolved via `CUDA_HOME` / `nvcc` on PATH | ❌ image toolkit was `< 12.9` (or absent) |

vLLM 0.26 enables FlashInfer paths **by default** on sm_120. When FlashInfer
tries to JIT-compile a kernel for sm_120 and the resolved toolkit is < 12.9,
the JIT throws and kills `EngineCore` during warmup/cudagraph capture.

Diagnostic that confirmed torch is NOT the problem:

```bash
/home/cdsw/.venv-vllm/bin/python -c "import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.get_arch_list())"
# torch 2.13.0+cu130 cuda 13.0
# ['sm_75', 'sm_80', 'sm_86', 'sm_90', 'sm_100', 'sm_120']

nvidia-smi   # Driver 580.178.04, CUDA Version: 13.0  (driver is fine, >= 575.51)
```

---

## The four FlashInfer JIT paths on sm_120

Which one fires depends on the model/config. All share the same root cause
(toolkit < 12.9); giving FlashInfer a CUDA ≥ 12.9 `nvcc` fixes **all** of them.
The per-path env/arg controls are the header-free fallback if you can't provide
a toolkit:

| # | Path | Trigger | Control (fallback) |
|---|---|---|---|
| 1 | Sampler (top-k/top-p) | **default ON, always** | `VLLM_USE_FLASHINFER_SAMPLER=0` |
| 2 | Fused MoE (CUTLASS) | MoE models only | `moe_backend=triton` (engine_config) |
| 3 | fp8 KV attention | `kv_cache_dtype=fp8` only | `VLLM_ATTENTION_BACKEND=TRITON_ATTN` |
| 4 | NVFP4 / some fp8 linear GEMM | NVFP4 (and some fp8) models | `linear_backend=cutlass` (engine_config) |

For our `Qwen3.8-27B-FP8` (FP8 **weights**, default bf16 KV): path 1 always
applies; paths 2–4 only if the model is MoE / you enable fp8 KV / NVFP4. Do
**not** set `kv_cache_dtype=fp8` unless you also handle path 3.

---

## The fix (recommended): provide a CUDA 13 `nvcc` toolkit

### 1. Install the toolkit into the shared vLLM venv (once)

NVIDIA unified these packages — the `-cu13` suffixed names are now **deprecated
stubs** that fail the build with a redirect message. Use the unsuffixed names:

```bash
uv pip install --python /home/cdsw/.venv-vllm/bin/python \
  nvidia-cuda-nvcc nvidia-cuda-cccl nvidia-cuda-runtime
```

The unified wheels install under `.../site-packages/nvidia/cu13/`.

> **Deprecated (do not use):** `nvidia-cuda-nvcc-cu13`, `nvidia-cuda-cccl-cu13`
> — they resolve to empty `0.0.1` stubs and error out.

### 2. Find the toolkit root and verify

```bash
find /home/cdsw/.venv-vllm -name nvcc -type f
# -> /home/cdsw/.venv-vllm/lib/python3.11/site-packages/nvidia/cu13/bin/nvcc

export CUDA_HOME=/home/cdsw/.venv-vllm/lib/python3.11/site-packages/nvidia/cu13
"$CUDA_HOME/bin/nvcc" --version                 # expect release 13.x (>= 12.9)
ls "$CUDA_HOME/include/cuda_runtime.h"           # headers must exist (nvidia-cuda-runtime)
```

> **`CUDA_HOME` = toolkit ROOT**, i.e. the directory that contains `bin/` and
> `include/` (`.../nvidia/cu13`) — **not** the `nvcc` binary path. Pointing it
> at the binary is a common mistake and FlashInfer will not find the headers.

### 3. Set `CUDA_HOME` in the deploy payload (reaches every TP worker)

A shell `export` only affects that interactive shell. For a TP deployment the
JIT runs inside each `RayWorkerProc`, so set it in `scheduling.env_vars` — Ray/
vLLM propagates `VLLM_*` and `CUDA_HOME` to all TP workers on both pods.

`CUDA_HOME` is allowed through `scheduling.env_vars` (only `PATH`, `LD_*`,
`PYTHON*` are denylisted for linker/interpreter-hijack safety).

---

## Shared NFS venv — install once, not per pod

`/home/cdsw` (and thus `/home/cdsw/.venv-vllm`) is the **shared project NFS
mount**. Every CML application/job/session for the project mounts the same
directory. Consequences:

- Install the toolkit **once** — both `rtxpro6000-gpu-worker` pods
  (`100.100.207.14`, `100.100.207.15`) immediately see `nvidia/cu13`.
- This is why the setup jobs build `.venv-*` there, and why
  `setup_engine_venv()` uses an `fcntl.flock` for NFS-safe concurrent creation.
- You do **not** re-run the install on the second pod.

---

## Quick unblock (no toolkit)

If you can't/won't provide a toolkit, disable the always-on FlashInfer sampler
(covers our FP8-weight, bf16-KV case). Add per-path controls from the table if
another path then fires.

```json
"scheduling": {
  "resources": { "node_type:rtxpro6000-gpu-worker": 0.001 },
  "env_vars": { "VLLM_USE_FLASHINFER_SAMPLER": "0" }
}
```

Cost: FlashInfer's fused sampler is disabled (vLLM's native sampler is used);
output is unchanged, minor throughput impact.

---

## NCCL / cross-node tensor parallelism

The two GPUs live on **two separate single-GPU pods/nodes**, so `TP=2` here is
**cross-node** tensor parallelism (`multi_node: true`):

- vLLM's `RayDistributedExecutor` runs one `RayWorkerProc` per shard; shard-to-
  shard communication is **NCCL over the pod network**.
- **No NVLink** on these boxes — interconnect is **PCIe Gen5 (~128 GB/s)**.
  TP=2 is correct but bandwidth-limited vs. an NVLink pair; for pure throughput,
  one model per GPU (2 independent single-GPU replicas) is often better than a
  cross-node TP=2 replica. Use TP=2 when a model+context needs > 1 GPU of VRAM.
- The auto placement group for multi-node TP is: bundle 0 = `{CPU:4}` scheduler
  (no GPU, runs anywhere e.g. head), bundles 1..tp = `{GPU:1, node_type:...}`
  with `PACK` — which forces one shard onto each single-GPU node.
- If NCCL has trouble auto-selecting an interface across the CNI (Calico), the
  usual knob is `NCCL_SOCKET_IFNAME` (set via `scheduling.env_vars`).
  `NCCL_DEBUG=INFO` surfaces the transport/interface it picked. Not needed so
  far — noted here for troubleshooting.

---

## Reference: full deploy payload (Qwen3.8-27B-FP8, TP=2, toolkit fix)

```json
{
  "name": "qwen3-8-27b",
  "engine_type": "vllm",
  "model": "Qwen/Qwen3.8-27B-FP8",
  "route_prefix": "/qwen3-8",
  "num_replicas": 1,
  "tensor_parallel_size": 2,
  "multi_node": true,
  "scheduling": {
    "resources": { "node_type:rtxpro6000-gpu-worker": 0.001 },
    "env_vars": { "CUDA_HOME": "/home/cdsw/.venv-vllm/lib/python3.11/site-packages/nvidia/cu13" }
  },
  "engine_config": {
    "dtype": "auto",
    "max_model_len": 131072,
    "gpu_memory_utilization": 0.95,
    "enable_prefix_caching": true,
    "trust_remote_code": true,
    "enable_auto_tool_choice": true,
    "tool_call_parser": "qwen3_xml",
    "reasoning_parser": "qwen3"
  }
}
```

---

## Environment facts (for future reference)

| Item | Value |
|---|---|
| GPU | NVIDIA RTX PRO 6000 Blackwell, 96 GB, **sm_120** |
| Instance | AWS `g7e.12xlarge`, 2× GPU, 48 vCPU, ~498 GiB RAM |
| Node type | `rtxpro6000-gpu-worker` (worker pods `100.100.207.14/.15`) |
| Driver | `580.178.04`, CUDA 13.0 (≥ 575.51 required for Blackwell) |
| torch | `2.13.0+cu130` (arch_list includes `sm_120`) — OK |
| vLLM | 0.26.x (enables FlashInfer paths by default on sm_120) |
| Toolkit fix | `nvidia-cuda-nvcc` + `nvidia-cuda-cccl` + `nvidia-cuda-runtime` → `.../nvidia/cu13`; set `CUDA_HOME` |
| venv | `/home/cdsw/.venv-vllm` on shared project NFS (install once) |

---

## Planned permanent fix (code)

To make Blackwell work out-of-the-box on every deploy / worker pod:

1. ✅ **Done.** `cai_integration/setup_environment.py` now pins
   `nvidia-cuda-nvcc==13.0.*`, `nvidia-cuda-runtime==13.0.*`,
   `nvidia-cuda-cccl==13.0.*` in `_ENGINE_PACKAGES["vllm"]` (matching torch's cu130
   and keeping nvcc↔headers on the same minor), and caps
   `flashinfer-python>=0.6.16.post4,<0.6.17`.
2. `ray_serve_cai/engines/vllm_engine.py` (`VLLMEngine.__init__`) — detect
   sm_120 (device capability ≥ 12.0); if a `nvidia/cu*/bin/nvcc` toolkit is
   present in the venv, set `CUDA_HOME` to its root; otherwise fall back to
   `VLLM_USE_FLASHINFER_SAMPLER=0`. Mirrors the existing `ninja`-PATH guard.
