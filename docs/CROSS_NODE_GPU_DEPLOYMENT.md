# Cross-node GPU inference on Cloudera AI

This runbook configures **one vLLM replica with tensor parallelism across two
CML Application pods**, each exposing one GPU to Ray. The numbered manual steps
use **Bash** and start from the repository root. Run Kubernetes commands from an
administrator workstation with the correct cluster context.

The steps below
show the equivalent Bash commands and explain the security and rollback details.

## Scope and tested configuration

| Component | Tested configuration |
|---|---|
| GPU topology | Two Ray GPU workers, one NVIDIA L40S per CML pod |
| Models tested | `Qwen/Qwen3.8-27B-FP8`, `Qwen/Qwen3.6-35B-A3B-FP8` |
| vLLM | `0.29.0`, Ray distributed executor, TP=2 |
| Istio proxy | `1.30.3-solo-fips-distroless`, native sidecar |
| Namespace inbound mTLS | `PERMISSIVE` |
| Ephemeral TCP port range | `32768–60999` |
| Model-runner workaround | `VLLM_USE_V2_MODEL_RUNNER=0` |

A **Ray node is a process hosted by a CML Application pod**. A Ray Serve model
deployment is not a Kubernetes Deployment. Restarting the model does not recreate
its host pods. Multiple GPUs within one pod do not need this cross-pod exception.

This is a tested, narrowly scoped infrastructure workaround, not a blanket
recommendation for every Workbench. No documented Workbench UI/API setting for
this particular listener exception was identified during the investigation.
Automating it requires infrastructure management of the filter and the labels
on the intended GPU pods; a CML Application environment variable cannot change
Envoy listeners.

## 1. Identify the current GPU pods and save the baseline

Get the live Ray nodes from `GET /api/v1/resources/nodes` in the management API.
Match the GPU nodes' `pod_name` / `node_name` to Kubernetes pod names / IPs.
Use current names: CML creates new names when Applications are recreated.

```bash
kubectl config current-context
kubectl get pods -A -o wide

export NS=mlx-user-2
export POD0='<current-rank-0-GPU-host-pod>'
export POD1='<current-rank-1-GPU-host-pod>'
export VLLM_PY=/home/cdsw/.venv-vllm/bin/python
export FILTER=ray-collective-http-inspection-trial
export WORK_DIR="$(mktemp -d /tmp/ray-cross-node.XXXXXX)"
echo "Evidence and configuration: $WORK_DIR"

kubectl -n "$NS" get pods "$POD0" "$POD1" -o wide
kubectl -n "$NS" get pods "$POD0" "$POD1" -o json > "$WORK_DIR/pods.before.json"
kubectl -n "$NS" get envoyfilter "$FILTER" --ignore-not-found -o yaml \
  > "$WORK_DIR/filter.before.yaml"
kubectl get peerauthentication.security.istio.io -A -o yaml \
  > "$WORK_DIR/peerauth.before.yaml"

for pod in "$POD0" "$POD1"; do
  kubectl -n "$NS" exec "$pod" -c engine -- nvidia-smi -L
  kubectl -n "$NS" exec "$pod" -c engine -- \
    cat /proc/sys/net/ipv4/ip_local_port_range
done
```

Confirm the GPU pods have `istio-proxy` (including in `initContainers`), the
range is `32768 60999`, and the effective inbound policy allows plaintext
collectives. Account for mesh-wide and workload-specific PeerAuthentication
policies, not only the namespace policy. Do not apply this recipe unchanged to
a STRICT-only workload or to a different listener/port configuration.

Network policies and infrastructure firewalls must already permit the required
Ray and collective traffic between these pods. The filter below does not open
firewall rules. Keep such permissions scoped to the participating workloads.

## 2. Apply the protocol-inspection exception

Keep Istio injection enabled. In this environment, CML restored the namespace
injection label during Application creation; completely removing the sidecar
also broke LiveLog startup connectivity.

The [tested EnvoyFilter](../deploy/istio/ray-collective-protocol-inspection-trial.yaml)
replaces both `tls_inspector` and `http_inspector` on `virtualInbound`. It retains
the existing `15006–15007` exclusion and disables inspection on destination ports
`32768–60999` (`end: 61000` is exclusive). HTTP-only exclusion was insufficient.
Use `REPLACE`: the tested listener-filter `MERGE` path did not update
`filter_disabled`.

```bash
# Render the manifest's namespace; keep its existing resource name to avoid
# overlapping the earlier diagnostic filter.
sed "s/namespace: mlx-user-2/namespace: ${NS}/" \
  deploy/istio/ray-collective-protocol-inspection-trial.yaml \
  > "$WORK_DIR/collective-filter.yaml"

kubectl apply --dry-run=server -f "$WORK_DIR/collective-filter.yaml"
kubectl -n "$NS" label pods "$POD0" "$POD1" \
  ray-collective-inspection-trial=true --overwrite
kubectl apply -f "$WORK_DIR/collective-filter.yaml"
```

**Security boundary:** this changes protocol inspection, not all traffic capture.
Envoy no longer detects/terminates incoming mesh TLS on the excluded ports.
Treat the collective connections as plaintext and retain network isolation.
Outbound CML service handling remains in place. Do not select all project/session
pods merely to avoid maintaining the two GPU-pod labels.

## 3. Verify the effective configuration on both proxies

```bash
for pod in "$POD0" "$POD1"; do
  kubectl -n "$NS" exec "$pod" -c engine -- "$VLLM_PY" -c \
    'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:15000/config_dump", timeout=10).read().decode())' \
    > "$WORK_DIR/$pod-envoy.json"
  echo "$pod"
  jq '.. | objects | select(
    .name? == "envoy.filters.listener.tls_inspector" or
    .name? == "envoy.filters.listener.http_inspector")' \
    "$WORK_DIR/$pod-envoy.json"
done
```

Both inspectors on both pods must show the added destination-port exclusion.
Allow time for Istio configuration propagation and repeat this read if necessary.
The filter is applied dynamically; host-pod recreation is not required.

## 4. Test TCPStore, Gloo, and NCCL before loading the model

Run with the TP model stopped so its GPU memory does not interfere with the
probe. This creates a temporary probe locally and streams it into each pod;
there is no package installation or file copy into the containers.

```bash
cat > "$WORK_DIR/probe.py" <<'PY'
import argparse
import signal
from datetime import timedelta
import torch
import torch.distributed as dist

p = argparse.ArgumentParser()
p.add_argument('--rank', type=int, required=True)
p.add_argument('--master', required=True)
a = p.parse_args()
signal.alarm(180)
timeout = timedelta(seconds=90)
store = dist.TCPStore(a.master, 29601, 2, a.rank == 0, timeout)
store.set(f'rank{a.rank}', 'ready')
assert store.get(f'rank{1-a.rank}') == b'ready'
print(f'RANK {a.rank}: PASS TCPStore exchange', flush=True)
dist.init_process_group('gloo', store=store, rank=a.rank, world_size=2,
                        timeout=timeout)
x = torch.tensor([a.rank + 1.0])
dist.all_reduce(x)
assert x.item() == 3
print(f'RANK {a.rank}: PASS Gloo all_reduce = 3', flush=True)
torch.cuda.set_device(0)
group = dist.new_group([0, 1], backend='nccl', timeout=timeout)
y = torch.tensor([a.rank + 1.0], device='cuda')
dist.all_reduce(y, group=group)
torch.cuda.synchronize()
assert y.item() == 3
print(f'RANK {a.rank}: PASS NCCL all_reduce = 3', flush=True)
dist.destroy_process_group(group)
dist.destroy_process_group()
print(f'RANK {a.rank}: DONE', flush=True)
PY

MASTER_IP="$(kubectl -n "$NS" get pod "$POD0" -o jsonpath='{.status.podIP}')"
run_probe() {
  kubectl -n "$NS" exec -i "$1" -c engine -- \
    env NCCL_SOCKET_IFNAME=eth0 GLOO_SOCKET_IFNAME=eth0 \
    NCCL_IB_DISABLE=1 NCCL_DEBUG=INFO \
    "$VLLM_PY" -u - --rank "$2" --master "$MASTER_IP" \
    < "$WORK_DIR/probe.py"
}
run_probe "$POD0" 0 > "$WORK_DIR/rank0.log" 2>&1 &
PID0=$!
run_probe "$POD1" 1 > "$WORK_DIR/rank1.log" 2>&1 &
PID1=$!
STATUS0=0; STATUS1=0
wait "$PID0" || STATUS0=$?
wait "$PID1" || STATUS1=$?
cat "$WORK_DIR/rank0.log" "$WORK_DIR/rank1.log"
test "$STATUS0" -eq 0 && test "$STATUS1" -eq 0 && \
  grep -q 'RANK 0: DONE' "$WORK_DIR/rank0.log" && \
  grep -q 'RANK 1: DONE' "$WORK_DIR/rank1.log" && echo 'BOTH RANKS PASSED'
```

Require all three PASS messages and DONE from each rank. Rendezvous uses port
29601, outside the new exclusion; the observed environment already supported
that TCPStore connection. If this first phase fails, diagnose its actual port
path rather than treating an NCCL setting as a universal fix. `eth0` must be the
pod-to-pod interface. A passing small collective is necessary but is not a model
inference or throughput test.

## 5. Deploy one replica across the two GPUs

Submit this JSON through `POST /api/v1/applications` in the management API.
If replacing a deployment, use the existing management lifecycle to stop/remove
the old model first; do not delete its CML host pods.

```json
{
  "name": "qwen3-8-27b-fp8",
  "engine_type": "vllm",
  "model": "Qwen/Qwen3.8-27B-FP8",
  "route_prefix": "/qwen3-8",
  "num_replicas": 1,
  "tensor_parallel_size": 2,
  "multi_node": true,
  "engine_config": {
    "dtype": "auto",
    "gpu_memory_utilization": 0.9,
    "max_model_len": 32768,
    "enable_prefix_caching": true,
    "enable_auto_tool_choice": true,
    "tool_call_parser": "qwen3_xml",
    "reasoning_parser": "qwen3"
  },
  "scheduling": {
    "placement_group_bundles": [
      {"CPU": 4, "node_type:gpu-worker": 0.001},
      {"GPU": 1, "node_type:gpu-worker": 0.001},
      {"GPU": 1, "node_type:gpu-worker": 0.001}
    ],
    "placement_group_strategy": "PACK",
    "env_vars": {
      "VLLM_USE_V2_MODEL_RUNNER": "0",
      "VLLM_USE_FLASHINFER_SAMPLER": "0",
      "NCCL_SOCKET_IFNAME": "eth0",
      "GLOO_SOCKET_IFNAME": "eth0",
      "NCCL_IB_DISABLE": "1",
      "NCCL_DEBUG": "INFO"
    }
  }
}
```

Bundle 0 reserves scheduler CPUs on a GPU host without consuming a GPU; each
shard has its own one-GPU bundle. With exactly two eligible one-GPU Ray nodes,
the shards necessarily span them. `PACK` alone does not enforce cross-node
placement when eligible nodes have multiple GPUs. Verify actual worker placement.
`node_type:gpu-worker` must exist in the live Ray resource inventory.

`venv_name` selects the Python environment, not the Ray executor. Environment
variables belong under `scheduling.env_vars`; unknown top-level fields are
rejected. Recreate the model deployment for runner/environment changes to apply.

## 6. Verify generation, not only readiness

After loading completes, send one request. This example reads a single bearer
token from the local credential file without printing it or placing it in the
command-line arguments. Do not enable HTTP header debug logging.

```bash
export MODEL_BASE_URL='https://<ray-head-host>/qwen3-8/v1'
export TOKEN_FILE="$HOME/tokens/cdp_sandbox"
python3 - <<'PY'
import json, os, time, urllib.request
from pathlib import Path
token = Path(os.environ['TOKEN_FILE']).read_text().strip()
body = {'model': 'Qwen/Qwen3.8-27B-FP8',
        'messages': [{'role': 'user', 'content': 'Hi'}], 'max_tokens': 8}
request = urllib.request.Request(
    os.environ['MODEL_BASE_URL'].rstrip('/') + '/chat/completions',
    data=json.dumps(body).encode(),
    headers={'Authorization': 'Bearer ' + token,
             'Content-Type': 'application/json'})
start = time.monotonic()
with urllib.request.urlopen(request, timeout=30) as response:
    print('HTTP:', response.status)
    print('Elapsed seconds:', round(time.monotonic() - start, 2))
    print(response.read().decode())
PY
```

Our recorded test returned **HTTP 200 in 7.26 seconds**, with eight reasoning
tokens and `finish_reason: length`. An empty final-answer field is expected if
the token budget is exhausted during reasoning. This verifies generation, not
long-context behavior, sustained load, or tool-call correctness.

### Follow-up: 100 requests at concurrency 4

On 2026-09-24, a continuous client test maintained up to four in-flight requests
until all 100 finished, without retries. Each used `Reply with exactly OK.`,
`temperature: 0`, `max_tokens: 64`, and
`chat_template_kwargs: {"enable_thinking": false}`.

| Metric | Result |
|---|---|
| HTTP 200 and exact `OK` answer | 100 / 100 |
| Errors or timeouts | 0 |
| Total elapsed time | 45.267 seconds |
| Median request latency | 1.779 seconds |
| P95 request latency (nearest rank) | 1.903 seconds |
| Maximum request latency | 2.612 seconds |

This validates repeated short generation with concurrency below five on the
recorded Qwen3.8 deployment. Repeated identical prompts may benefit from prefix caching;
this is not a long-context, long-output, varied-prompt, or multi-hour soak test.

The same 100-request, concurrency-4 test against `Qwen/Qwen3.6-35B-A3B-FP8`
returned HTTP 200 with nonempty content on all requests, with no errors or
timeouts. It took 39.697 seconds; median and nearest-rank P95 latencies were
1.447 and 1.567 seconds. Every response was `OK.` rather than the requested
exact string `OK`; the punctuation difference is a prompt-following observation,
not evidence of low general response quality.

## 7. Pod replacement, upgrades, and rollback

- The filter persists, but ad-hoc labels do not transfer to replacement CML pods.
  Refresh the Ray/pod mapping, label the intended new GPU pods, and verify the
  effective filters before redeploying the model.
- For automation, manage this manifest declaratively and arrange narrowly scoped
  labeling through an administrator-controlled integration. Do not modify the
  platform pod evaluator or grant ordinary model processes cluster-wide access.
- Recheck listeners, port ranges, and collective tests after proxy upgrades.
- Rollback removes the exception and may restore the original NCCL hang. Stop
  affected model work before rollback.

```bash
# Restore a pre-existing filter, or remove only the resource introduced here.
if [ -s "$WORK_DIR/filter.before.yaml" ]; then
  kubectl apply -f "$WORK_DIR/filter.before.yaml"
else
  kubectl -n "$NS" delete envoyfilter "$FILTER" --ignore-not-found
fi

# Restore the original selector label on each pod.
for pod in "$POD0" "$POD1"; do
  old="$(jq -r --arg pod "$pod" '.items[] |
    select(.metadata.name == $pod) |
    .metadata.labels["ray-collective-inspection-trial"] // empty' \
    "$WORK_DIR/pods.before.json")"
  if [ -n "$old" ]; then
    kubectl -n "$NS" label pod "$pod" \
      "ray-collective-inspection-trial=$old" --overwrite
  else
    kubectl -n "$NS" label pod "$pod" ray-collective-inspection-trial-
  fi
done
```

Use the original saved baseline for rollback; rerunning the baseline step after
applying the exception records the modified state instead. Replaced pods need
their own baseline. The commands above do not alter namespace injection or mTLS.

## Diagnosis and discussion summary

**Problem 1: collective initialization.** Istio protocol inspection interfered
with raw cross-pod collective traffic. HTTP-only exclusion failed; excluding
both TLS and HTTP inspection on the tested inbound ephemeral range allowed the
standalone TCPStore, Gloo, and NCCL probe to pass. Keep the sidecar because CML
services such as LiveLog depended on the existing mesh path in this deployment.

**Problem 2: inference execution.** For the tested model, with networking working, vLLM V2 loaded the
model and captured CUDA graphs but failed to return the first inference result.
EngineCore timed out after five minutes waiting for `sample_tokens`. The
shared-memory response-queue traceback does not establish a shared-memory
capacity problem. V2 schedules GPU work and copies outputs asynchronously;
unfinished GPU work or a synchronization/collective stall can prevent the worker
from returning and surface as an RPC timeout. The exact blocked operation was
not captured in the worker logs.

**Working mitigation:** after selecting the older model runner using
`VLLM_USE_V2_MODEL_RUNNER=0`, one short generation request succeeded while
retaining Ray TP=2. This strongly motivates investigation of the V2 execution
path for this configuration, but does not prove a universal V2 defect or identify
the exact upstream bug. The overnight idle interval was not established as a
cause. The subsequent 100-request test above also passed; long-duration and
larger-workload validation remain outside the evidence collected so far.

An upstream report for the exact checkpoint describes a hang mitigated by eager
execution, but uses different GPUs and fails during startup. Do not present it
as our confirmed root cause. Also, the current configuration builder does not
forward `enforce_eager`; merely adding it to an API payload does not enable it.

## References

- [Istio EnvoyFilter scope and upgrade considerations](https://istio.io/latest/docs/reference/config/networking/envoy-filter/)
- [vLLM 0.29 V2 model runner](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/worker/gpu/model_runner.py)
- [vLLM asynchronous output synchronization](https://github.com/vllm-project/vllm/blob/v0.29.0/vllm/v1/worker/gpu/async_utils.py)
- [Exact-checkpoint startup hang report #52682](https://github.com/vllm-project/vllm/issues/52682)
- [Related sample_tokens timeout report #36921](https://github.com/vllm-project/vllm/issues/36921)
