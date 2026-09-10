# Cross-Node Training Readiness — Design Spec

_Date: 2026-09-06_
_Status: draft (awaiting user review)_
_Scope: (A) harden cross-node collective networking; (B) a minimal runnable cross-node
training slice on top, matching the v2 `cai_ray` conventions._

---

## 1. Problem & Goal

We want the Ray-on-CML cluster **ready for future cross-node distributed training**
(the v2 plan's Train phase — NeMo/TRL/DDP on Ray). Today, any workload that needs a
cross-node collective (multi-node tensor-parallel inference, and by extension DDP
training) **hangs at the `torch.distributed` TCPStore → NCCL rendezvous**, even though
Ray schedules the GPUs on both nodes correctly.

**Two-part goal:**
- **Part A — Networking foundation:** make cross-node NCCL/TCPStore rendezvous reliable
  under Istio, validated by a 2-node all-reduce smoke test.
- **Part B — Training slice:** a minimal, runnable **Ray Train + PyTorch DDP**
  (`TorchTrainer`) example across 2 GPU nodes, wired into the existing v2 `cai_ray`
  Prepare→Train→Serve seam, using a **toy model** first.

### Evidence (root cause, already established)
- PG acquired under `STRICT_SPREAD` (`ray_utils.py:612 Using the existing placement group`)
  and `rank=1 local_rank=0 ... backend=nccl` — both GPU workers launch across two nodes.
  ⇒ GPU scheduling cross-node **works**.
- The hang is at `distributed_init_method=tcp://<ip>:<port>` (TCPStore rendezvous) — a
  raw cross-pod TCP stream the Envoy sidecar cannot proxy. ⇒ **networking**, not GPU
  discovery.

---

## 2. Part A — Networking Foundation

### 2.1 Verified constraint (drives the approach)

CML API v2 `create_application` accepts a **fixed payload**:
`name, script, cpu, memory, runtime_identifier, subdomain, bypass_authentication,
nvidia_gpu, environment`. Pod placement is steered only by **CML-interpreted env vars**
(`NODE_SELECTOR_KEY` / `NODE_SELECTOR_VALUE`, set in `cai_cluster.py:781`). There is:
- **No pod-annotation or pod-label channel** in the API.
- **No `kubectl`/kubernetes client** anywhere in the repo.

Istio sidecar traffic-exclusion (`traffic.sidecar.istio.io/excludeOutboundPorts` /
`excludeInboundPorts`) and injection control (`sidecar.istio.io/inject: "false"`) are
**pod annotations consumed by the injector at admission** — therefore they **cannot be
set in-repo through the CML app API**.

### 2.2 Approach (chosen)

Because the fix cannot be stamped per-pod via the API, it is split:

**In-repo (this codebase):**
1. **Pin the collective ports to a known, finite range** so the admin exclude list is
   correct and stable across pod restarts:
   - `MASTER_PORT` (TCPStore rendezvous) — fixed value (e.g. `29500`).
   - NCCL/Gloo ephemeral range constrained where possible.
   - Ray already uses known ports (GCS 6379, dashboard 8265, client 10001, metrics 9090).
   These are injected as env vars on the ephemeral **train** worker group launch (the
   only channel we have), and consumed by the Train entrypoint (Part B).
2. **Ship the cluster-admin manifest as a versioned artifact** under
   `deploy/istio/` (not an API call): a documented `kubectl`/YAML that a cluster-admin
   applies once.

**Out-of-band (cluster-admin, one-time):**
- Keep namespace-wide `PeerAuthentication` **PERMISSIVE** (already applied for GCS).
- Apply the mesh-side raw-TCP bypass for the collective ports, **scoped by the label
  CML stamps on app/session pods** (`ds-role: session`, per the working PeerAuthentication
  in the `ray-with-cai` runbook). Preferred mechanism, in order:
  1. **Pod exclude annotations via the sidecar injector default / `Sidecar` CR** for the
     pinned port range.
  2. If (1) is not viable in this CML/Istio build, **`sidecar.istio.io/inject: "false"`**
     for the train worker pods via the injector template keyed on `ds-role`.

> **Security-posture note:** because CML gives all session/application pods the same
> `ds-role: session` label (no per-app custom label channel), the admin exclude is
> **namespace-broad for CML workloads**, not train-only. This is an accepted tradeoff and
> is documented in the manifest. A narrower scope would require CML to expose pod labels.

### 2.3 Rejected alternatives
- **Per-pod annotation via API** — impossible (§2.1).
- **hostNetwork for train pods** — most reliable for NCCL but heaviest K8s change and
  port-collision risk; deferred unless the chosen approach fails validation.
- **Cross-node TP inference as the "fix"** — orthogonal; the 2-replica/TP=1 serving
  workaround remains the serving recommendation and is out of scope here.

### 2.4 Part A deliverables
- `deploy/istio/ray-collective-bypass.yaml` — admin manifest + apply/rollback notes.
- Port-pinning env wiring on the ephemeral train worker group launch.
- `cai-ray train --smoke` — 2-node all-reduce validation (see §3).
- `ray-with-cai` skill: add a "cross-node collectives (NCCL/TCPStore) under Istio"
  section capturing the namespace-vs-pod policy nuance and this manifest.

---

## 3. Part B — Minimal Cross-Node Training Slice

Fills in the **existing v2 blueprint stub** (`cai_ray/modeltypes/causal_lm.py` currently
has `TrainStage(name="sft", framework="trl", run=None)`) and implements the `cai-ray
train` CLI (currently a `_cmd_stub`). Nothing parallel is invented — it mirrors the
proven `serve/batch_embed.py` slice + `run_batch_embed_job.py` job entry.

### 3.1 Components

| Path | New/changed | Role | Mirrors |
|------|-------------|------|---------|
| `cai_ray/train/__init__.py` | new | package | `cai_ray/serve/__init__.py` |
| `cai_ray/train/torch_ddp.py` | new | `run(...)` — Ray Train `TorchTrainer`, `ScalingConfig(num_workers=2, use_gpu=True)`, toy-model DDP loop; also `smoke()` all-reduce | `serve/batch_embed.py` |
| `cai_ray/train/run_manager.py` | new | minimal Training Run Manager: ensure ephemeral cluster → submit → stream → push MFU → teardown | plan §2.2 `train/run_manager.py` |
| `cai_ray/modeltypes/causal_lm.py` | edit | wire `TrainStage(name="sft", framework="ray-train-ddp", run=torch_ddp.run)` | existing seam |
| `cai_ray/cli.py` | edit | implement `_cmd_train` (replace stub) + `--smoke` | `_cmd_serve` / `_cmd_run` |
| `cai_integration/run_train_job.py` | new | CAI Job entry: re-exec into venv, read workload YAML | `run_batch_embed_job.py` (near-exact) |
| `configs/train_workload.yaml` | new | job config (model=toy, num_workers, epochs, ckpt out, ports) | `batch_embed_workload.yaml` |
| `cai_integration/jobs_config.yaml` | edit | register `train` job (`parent_job_key: null`) | existing `batch_embed` entry |
| `cai_integration/setup_environment.py` | edit | add `.venv-train` package set (`ray[train]`, `torch`) | `_ENGINE_PACKAGES` |

### 3.2 Data flow

```
CAI Job (CPU-light driver: run_train_job.py)
  → ClusterLifecycle.up(ephemeral=True, workload="train", workers={"l40-gpu-worker": 2})
  → RaySubmitter.submit(TorchTrainer entrypoint, runtime_env={virtualenv: .venv-train})
        └── 2 workers × 1 GPU  ── NCCL DDP all-reduce ──  (uses pinned MASTER_PORT)
  → checkpoint → object storage / NFS
  → cai_ray.monitoring.metrics.push_metrics(MFU, tokens/s)   (no-op if PUSHGATEWAY_URL unset)
  → ClusterLifecycle.down(ephemeral=True, run_id=...)        (serving cluster untouched)
```

Cluster separation (§2.9 of the plan) is preserved: training runs on an **ephemeral**
cluster with a distinct `head_app_name` prefix and job-scoped `cluster_info` path; the
persistent serving cluster is never launched or torn down.

### 3.3 Toy model (chosen)
A tiny MLP (or 2-layer transformer block) on synthetic tensors — **zero data
dependencies**, fastest path to prove cross-node NCCL end-to-end. The `TrainStage.run`
signature and `run_manager` orchestration are the reusable scaffolding; swapping the toy
loop for a real HF/TRL SFT or NeMo AutoModel loop later is a config/framework change, not
a structural one.

### 3.4 Testing (TDD, layered)
1. **Unit (no GPU):** `torch_ddp` config building + `run_manager` orchestration with an
   injected fake `RaySubmitter`/`ClusterLifecycle`. Mirrors `test_batch_embed.py` /
   `test_cluster_lifecycle.py`. Runs in CI.
2. **Integration gate (2 GPU nodes):** `cai-ray train --smoke` → 2-node all-reduce
   returns the correct summed tensor. **Go/no-go for the entire Part A foundation.**
3. **End-to-end (2 GPU nodes):** toy DDP for N steps, loss decreases, checkpoint written,
   ephemeral cluster torn down.

---

## 4. Out of Scope (explicit)
- Real training loops (HF/TRL SFT, NeMo AutoModel HSDP) — later sub-plans; the seam is
  built to accept them.
- Prepare phase (tokeniser, packed corpus), governance/lineage wiring.
- Node/app autoscaling.
- Multi-node TP **inference** fix (serving keeps the 2-replica/TP=1 recommendation).
- Provisioning a Pushgateway instance (metrics push is a no-op until its URL is set).

## 5. Acceptance Criteria
- `cai-ray train --smoke` passes across 2 `l40-gpu-worker` nodes (all-reduce correct) —
  proving the Part A networking foundation.
- `cai-ray train` runs the toy DDP end-to-end on the ephemeral cluster, writes a
  checkpoint, and tears the cluster down without touching the serving cluster.
- Unit tests for `torch_ddp` + `run_manager` pass in CI with no GPU.
- `deploy/istio/ray-collective-bypass.yaml` + `ray-with-cai` skill section committed.

## 6. Open Questions / Risks
- **CML pod-label granularity:** confirm the exact label(s) CML stamps on app pods at
  implementation time (`kubectl get pod --show-labels`) to scope the admin manifest as
  tightly as possible (falls back to `ds-role: session`, which is namespace-broad).
- **Port pinning reach:** confirm `MASTER_PORT` propagates from the launcher env through
  Ray Train workers (Ray Train sets its own rendezvous; verify we can pin or exclude the
  range it uses).
- **Ray Train reachability via CML nginx** (`:8265`/`:10001`) — same open dependency the
  v2 batch slices carry; fall back to in-process `ray.init(address="ray://<head>:10001")`.
