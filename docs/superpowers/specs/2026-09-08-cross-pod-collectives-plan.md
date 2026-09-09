# Cross-Pod Collectives under Istio — Plan (serving TP + training)

_Date: 2026-09-08_
_Status: plan (root cause now empirically confirmed)_
_Extends: `2026-09-06-cross-node-training-readiness-design.md` (that spec = training slice +
networking foundation, scoped to training; this doc adds the **serving multi-node TP** path
and records the confirmed evidence)._

---

## 1. Root cause — CONFIRMED (no longer a hypothesis)

Symptom: deploying a vLLM model with `tensor_parallel_size=2` split across two 1-GPU pods
hangs immediately after
`[parallel_state.py] ... distributed_init_method=tcp://<rank0-ip>:<port> backend=nccl`.

The hang is the `torch.distributed` **TCPStore rendezvous**, before NCCL starts. It is the
**Istio/Envoy sidecar** intercepting cross-pod TCP. Proven from inside the rank-1 pod:

- `socket.create_connection(('<rank0-ip>', 59999), 5)` → **OPEN** on a port where *nothing
  listens*. A direct connection to a closed port must return `Connection refused`; it
  "opened" because Envoy accepts every outbound TCP locally (iptables → `:15001`) before it
  knows the upstream is unreachable. ⇒ interception proven.
- `curl 127.0.0.1:15000/server_info` → Envoy admin **LIVE** (istio-proxy 1.38.4 FIPS);
  `:15021/healthz/ready` → 200. ⇒ sidecar present.

So rank-1's connect to rank-0's TCPStore "succeeds" at L4 but the raw bidirectional stream is
never proxied end-to-end → rendezvous hangs. **Same physical node does not help** — each pod
has its own sidecar. NCCL socket-interface / IB / SHM tuning is irrelevant (the launcher
already sets `NCCL_SOCKET_IFNAME=eth0`, `NCCL_IB_DISABLE=1`; the hang is upstream of NCCL).

> Side note: the observed rendezvous port `:100` is abnormal (vLLM's `get_open_port()`
> normally returns an ephemeral high port). Something is setting a deterministic low port in
> the deploy env — check the replica env (`env | grep -iE 'VLLM_PORT|VLLM_HOST'`). Fix it to a
> pinned high port regardless (privileged port 100 is its own latent bug).

## 2. Current resolution (default going forward)

**Keep collectives intra-pod.** Loopback (`127.0.0.1`) is excluded from the sidecar's iptables
capture, so if all TP shards live in one pod the rendezvous never crosses a pod boundary.

- Deploy on a multi-GPU pod: node type `l40_gpu_node_2_gpus` (`cai_cluster.py:26,68`,
  `nvidia_gpu=2`), `tensor_parallel_size=2`, `multi_node` false → vLLM single-node
  `STRICT_PACK` path (`vllm_engine.py:983`), intra-pod NCCL. Works under the mesh, no admin.
- **Rule of thumb:** if the model (or training job) fits within one node's GPUs, use ONE pod
  with N GPUs. Only reach for cross-pod when it genuinely doesn't fit.

## 3. Cross-pod plan (when >1 node is truly required)

Needed for: hosting a model whose shards exceed one node's GPUs, and multi-node
DDP/FSDP training with more GPUs than one pod provides.

### 3.1 The real networking requirement (correction to the 2026-09-06 approach)

Pinning `MASTER_PORT` alone is **not sufficient**. The TCPStore rendezvous is only the first
hop; once past it, **NCCL opens its own sockets on ephemeral ports** for the ring/tree
all-reduce, and those also cross pods and get intercepted. NCCL gives no clean single-port
pin. Therefore the robust bypass is **IP-range / pod-scoped**, not per-port:

- Exclude the worker-pod CIDR from the sidecar via
  `traffic.sidecar.istio.io/excludeOutboundIPRanges` + `excludeInboundIPRanges` (covers both
  rendezvous and NCCL data ports at once), **or** disable injection for collective pods
  (`sidecar.istio.io/inject: "false"`).
- Keep namespace `PeerAuthentication` **PERMISSIVE** (already applied for Ray GCS).
- Still pin the rendezvous port (below) so behaviour is deterministic and documented, even
  though the exclude is IP-range based.

### 3.2 Steps

1. **Pin the rendezvous port** (deterministic, documented):
   - *Serving:* inject a fixed port into every replica/worker via the deployment's
     `scheduling_env_vars` (→ `runtime_env['env_vars']` in `vllm_engine.py:1033`). Confirm the
     exact knob for the installed vLLM (`VLLM_PORT` vs `VLLM_HOST_IP`; else a small patch to
     pass a fixed port into the executor's `get_distributed_init_method`). **Open item —
     verify against the pinned vLLM version before relying on it.**
   - *Training:* set `MASTER_PORT` (e.g. 29500) on the ephemeral train worker-group launch env
     (per 2026-09-06 §2.2); verify Ray Train's own rendezvous port is pinnable/excludable.
2. **Ship the admin bypass manifest** `deploy/istio/ray-collective-bypass.yaml` (does not exist
   yet): IP-range sidecar exclusion (§3.1) scoped by the label CML stamps on pods
   (`ds-role: session` fallback = namespace-broad; document the tradeoff). Applied once by a
   cluster-admin — **cannot** be set per-pod via the CML app API (no annotation channel; see
   2026-09-06 §2.1).
3. **Enable the multi-node path in config:** `multi_node: true` + `node_type` on a GPU-worker
   group; the engine's multi-node placement (`vllm_engine.py:958` — CPU scheduler bundle 0 +
   GPU executor bundles 1..tp) already spreads shards correctly.

### 3.3 Validation
- `cai-ray train --smoke` — 2-node all-reduce returns the correct summed tensor (go/no-go for
  the whole networking foundation; from 2026-09-06 §3).
- Serving bring-up: vLLM `tensor_parallel_size=2`, `multi_node: true` across 2 pods reaches
  `load_model` and serves a completion.

## 4. Open items
- Exact CML pod labels (`kubectl get pod --show-labels`) to scope the admin manifest tightly.
- vLLM rendezvous-port pin mechanism for the installed version (§3.2.1); source of the
  anomalous `:100`.
- Whether this CML/Istio build honours injector-default annotations vs requiring
  `inject: "false"`.
- Security review: pod-CIDR sidecar exclusion is broader than per-port; document and get admin
  sign-off.
