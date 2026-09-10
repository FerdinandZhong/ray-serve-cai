# TODO.md

Live, short-horizon task list. For longer-horizon design proposals (metrics
reorg, dynamic engine registration, autoscaling), see [docs/ROADMAP.md](docs/ROADMAP.md) —
some of those items (e.g. per-engine isolated venvs, `/metrics/all` aggregation)
are now implemented; ROADMAP.md itself isn't fully up to date on status.

## Open now

- [x] **`flashinfer-python` version drift** — fixed. Now capped
      `>=0.6.16.post4,<0.6.17` in `_ENGINE_PACKAGES["vllm"]` so it can't drift to
      0.6.18.x (whose bundled CCCL enforces a strict nvcc==runtime-header guard).
      This is what caused the `CUDA compiler and CUDA toolkit headers are
      incompatible` FlashInfer JIT failure on Blackwell.
- [ ] **Reinstall the pinned toolchain on the node** and redeploy with the
      FlashInfer sampler ON (drop `VLLM_USE_FLASHINFER_SAMPLER=0`) to confirm the
      CUDA-wheel pin resolves the JIT compile end-to-end. Clear
      `/home/cdsw/.cache/flashinfer` first (stale failed-build artifacts).
- [ ] **`node_label` unset for `rtxpro6000-gpu-worker`** in
      `configs/ray_cluster_config.yaml`. The real `nvidia.com/gpu.product` value
      for this SKU wasn't confirmed from live node data — add it once known
      (`kubectl get pod --show-labels`) so K8s pod placement can target it
      precisely, matching the pattern used for `l40-gpu-workers`.
- [ ] **Cross-pod tensor parallelism** is currently avoided (single-pod
      multi-GPU only) because Istio/Envoy intercepts the cross-pod TCPStore
      rendezvous. Plan for the admin-assisted IP-range sidecar exclusion exists at
      `docs/superpowers/specs/2026-09-08-cross-pod-collectives-plan.md` — needed
      before hosting a model whose shards exceed one node's GPUs, or scaling
      training beyond one pod's GPUs.
- [~] **Blackwell FlashInfer JIT toolkit fix — half in code now.** The CUDA-13
      toolkit wheels (`nvidia-cuda-nvcc`/`runtime`/`cccl`) are now pinned to
      `==13.0.*` in `_ENGINE_PACKAGES["vllm"]` so the venv build installs a
      consistent toolchain (no more nvcc↔header minor drift). Still TODO:
      auto-detect the wheel toolkit and set `CUDA_HOME` in `VLLMEngine.__init__`
      (mirrors the `ninja`-PATH guard) so deploys don't have to pass `CUDA_HOME`
      via the payload. See `docs/BLACKWELL_CUDA_NVCC_NCCL.md`.

## Recently closed (for context, not action)

- [x] Single-node TP placement group now correctly generates a scheduler-only
      actor + N×`{GPU:1}` bundles (vLLM v1 Ray executor requires ≤1 GPU/bundle) —
      fixed for both the auto-generated AND caller-supplied bundle paths.
- [x] `ninja` PATH resolution for Ray worker actors (`RayWorkerProc`), not just
      the deployment actor.
- [x] `setup_vllm_env.py`'s `_ensure_ninja_resolvable` self-symlink corruption bug
      (probe ran under the wrong PATH, causing it to symlink the real binary onto
      itself) — fixed at the source so it won't recur on future CML job runs.

## From docs/ROADMAP.md (longer-horizon, check status before starting)

1. Centralised `/metrics` aggregation across all nodes/apps — partially exists
   (`/api/v1/metrics/all`); verify against the roadmap's original design.
2. Management API reorg into `/cluster`, `/applications`, `/engines`, `/models`,
   `/metrics` groups + model pulling from HuggingFace to NFS.
3. Standalone MCP server definitions directory.
4. Auto-scaling — node-level (CML API) then app-level (Ray Serve autoscaling).
