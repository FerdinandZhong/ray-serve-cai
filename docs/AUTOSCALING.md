# CAI worker autoscaling

Implementation status: capacity reconciliation is built into new clusters, with optional initial pools and idle-worker retirement.
Offline tests and a real local Ray idle-drain smoke test have passed. CAI worker
provisioning, GPU inference under changing load, and network inheritance still
require the target-cluster canary. Automatic consolidation of occupied workers
is not enabled. Do not treat this branch as a completed production rollout.

## Two independent scaling policies

Ray Serve decides how many model replicas to run. A head-local supervisor checks
Ray's pending tasks, actors and complete placement groups, then uses Ray 2.56.1's
resource scheduler to calculate missing worker capacity. The supervisor calls
the existing CAI worker launcher only for the missing capacity.

Manual workers count as available capacity but are never automatically deleted.
Starting workers count as launch commitments. Pool requirements and per-bundle
constraints must match; aggregate free GPU count alone is not sufficient.

An offline scheduler compatibility test found that Ray skips nodes without an
instance-manager record, which omitted our existing manual workers. The
implementation therefore supplies a complete inventory to Ray's
scheduler through an adapter and uses one CAI reconciliation loop. It does not
run a second Ray worker autoscaler alongside this loop.

## New clusters: choose initial resources

The generated head starts the capacity controller by default, using pinned Ray
**2.56.1**, autoscaler-v2 resource reporting and `--no-monitor`. There is no
additional enablement environment variable for new clusters. The default policy
is `full`: demand-driven growth plus idle-worker retirement. An empty pool list
starts no workers and reports `waiting_for_pools` until a policy is supplied.

At AMP import, set **`RAY_INITIAL_WORKER_POOLS`** to a JSON array, for example:

```json
[
  {
    "id": "l40s",
    "initial_workers": 2,
    "min_workers": 0,
    "worker_spec": {
      "cpu": 16,
      "memory": 64,
      "gpus": 1,
      "node_type": "gpu-worker",
      "accelerator_type": "L40S"
    }
  }
]
```

The AMP input must be a **string containing JSON**, not a nested environment
object. Leaving it blank uses YAML; an explicit `[]` overrides YAML with no pools.
Alternatively configure `ray_cluster.worker_pools` in
`configs/ray_cluster_config.yaml`. Multiple CPU/GPU pool shapes are supported;
set each pool's runtime and Kubernetes `node_label` where required. CPU and
memory are explicit. No node-type registration or maximum count is required.

`initial_workers` is a one-time creation request. It is not reissued after idle
retirement or a controller restart. `min_workers` is a separate retained
baseline and defaults to zero. Set it to two if those two workers should remain
when idle. Initial workers are autoscaler-owned; later manual `POST /nodes`
workers remain protected from automatic deletion. Bootstrap retries preserve
existing policy edits and ownership records.

Optional cluster budgets can be configured in YAML:

```yaml
ray_cluster:
  autoscaling:
    enabled: true
    limits: null
    max_launch_batch: 2
  worker_pools: []
```

`limits: null` means no additional application-level ceiling. To impose one,
use an object such as `{max_workers: 8, max_gpus: 8}`. CAI admission constraints
still apply. `max_launch_batch` limits each reconciliation batch, not eventual
pool size. Legacy `worker_groups` remain available for manual initial workers;
do not combine counted legacy groups with new managed pools.

The bootstrap publishes the policy after the head GCS address is saved. It
queues initial provisioning; completion of the bootstrap job is not proof that
all workers have joined or that a model is ready.

## Networking and existing clusters

Explicitly requested initial workers can start before the GPU networking check,
so an administrator can inspect/probe them. Automatic **additional GPU worker
creation** waits for `RAY_AUTOSCALING_NETWORK_READY=1` in the head environment.
CPU pool growth and safe idle retirement do not depend on this GPU growth gate.

Follow [cross-node GPU deployment](CROSS_NODE_GPU_DEPLOYMENT.md), ensuring the
infrastructure policy covers **future** pods. A probe of two existing pods is
insufficient if the mesh selector names only those pods. API `labels` are not
Kubernetes pod labels. The readiness flag is an administrator assertion, not an
automated NCCL check, and does not prevent users deploying onto existing GPUs.
Restart the head with recovery when changing its environment; existing process
environments do not update automatically.

For an existing cluster, deploy the updated head launcher before enabling worker
scaling through the policy API below. The controller must be the only worker
autoscaler. Explicit `RAY_CAI_AUTOSCALING=0` disables this controller; absent or
`1` enables it in the new launcher. Existing saved policies are not overwritten.

State lives in `/home/cdsw/ray_autoscaling.json`, with leader and lifecycle locks
shared with head recovery. Keep it with the project's recovery state.

## Start in observation mode

Use the authenticated management API. Policy writes require an administrator;
reads require the normal management identity.

```http
PUT /api/v1/cluster/autoscaling
```

```json
{
  "enabled": true,
  "mode": "observe",
  "startup_timeout_s": 1800,
  "max_launch_batch": 2,
  "pools": [
    {
      "id": "l40s",
      "min_workers": 0,
      "idle_timeout_s": 900,
      "worker_spec": {
        "cpu": 16,
        "memory": 64,
        "gpus": 1,
        "node_type": "gpu-worker",
        "accelerator_type": "L40S"
      }
    }
  ]
}
```

`worker_spec` is the direct-worker request shape. Supply `runtime_identifier`
and `node_label` when needed by your Workbench. No `/resources/node-types`
registration is required. Each pool needs explicit CPU and memory.

All `max_workers`, `max_gpus`, `max_cpus`, and `max_memory_gb` fields are
optional; omitted or null means no extra cap. An explicit zero is a zero budget.
A pool may also have an optional `max_workers`. API budgets use these top-level
fields; `autoscaling.limits` is the cluster-bootstrap configuration wrapper.

Caps count autoscaler-owned workers, including pending creates and unconfirmed
deletions, but exclude manual workers and the head. CAI itself admits requests
against its applicable limits and other workloads. Pool maxima do not need to
add up to the global cap: each create checks actual commitments under the lock.
Minimum/initial capacity must fit configured budgets. The controller currently
has **no CAI quota-discovery API integration** and reports that in status; it
cannot guarantee physical GPU availability in advance.

Do not change a pool's launch spec or remove its definition while it has active
workers. `initial_workers` cannot be changed for an existing pool; use
`min_workers` for ongoing baseline changes. Lowering a maximum never force-deletes
occupied capacity. CAI pod RAM is not advertised as equivalent Ray `memory`
because runtime/object-store overhead differs.

Inspect:

```http
GET /api/v1/cluster/autoscaling
GET /api/v1/cluster/autoscaling/status
GET /api/v1/cluster/autoscaling/events
```

Status includes the latest demand/resource snapshot, proposed launches/drains,
infeasible demand counts, journaled workers and a supervisor freshness flag.
No fresh heartbeat means the controller is unavailable; it does not mean the
policy has taken effect. Proposed launches in `observe` mode create no CAI apps.

## Enable replica autoscaling

Add these top-level fields to a vLLM deployment request:

```json
{
  "autoscaling_config": {
    "min_replicas": 1,
    "max_replicas": 3,
    "target_ongoing_requests": 4,
    "upscale_delay_s": 30,
    "downscale_delay_s": 300
  },
  "max_ongoing_requests": 16
}
```

These are a request fragment, not a complete model payload. Keep the model,
engine and placement settings in the existing deployment payload. Omit
`num_replicas` or leave it at 1 when using `autoscaling_config`. The request
target must be below `max_ongoing_requests` (default 100). Serve settings are
consumed before binding vLLM's engine arguments. Other engines and raw import
paths currently reject these autoscaling overrides rather than ignore them.

Use capability constraints such as `node_type:gpu-worker` or
`accelerator_type:L40S`. A `0.001` custom-resource request remains a hard match.
Permanent pod-IP or worker-ID pins cannot be satisfied by a newly created worker.
Each TP replica needs its entire scheduler/executor placement group. Existing
capacity is considered before creating any of its missing workers.

## Rollout and idle retirement

Change the same policy to `mode: "scale_up_only"` for the first canary. This can
launch workers but never drains or deletes them. Then use `mode: "full"` to
allow idle-worker retirement. Here `full` means scale-up plus **empty-worker**
scale-down, not migration or consolidation.

The retirement sequence is:

1. Serve reduces replicas and releases their actors and placement groups.
2. The planner selects an owned worker after its idle timeout, considering
   pending demand and pool minimums.
3. The supervisor journals retirement intent and requests Ray's rejectable
   idle drain. New work can cause rejection, in which case the worker stays.
4. The launcher holds the stopped application instead of rejoining Ray. A
   per-launch marker on shared storage preserves this across pod restarts.
5. After fresh confirmation that Ray stopped, the backend deletes the exact
   owned CAI application and confirms its disappearance before recording it as
   terminated. A timeout preserves identity and blocks premature quota reuse.

Manual worker deletion also requests idle draining and requires a subsequent
DELETE after the worker stops. Busy/unknown workers are retained. Older launchers
without the retirement guard must be recreated before this safe API path can
manage them. Generic `/cml-apps` deletion cannot bypass managed-worker retirement.

To pause new worker mutations, submit the existing policy with `enabled: false`
and `mode: "disabled"`. Already accepted operations may finish. Replica
autoscaling continues under its separate Serve policy. No occupied worker is
force-terminated to satisfy a lower limit.

## Failure handling and remaining gates

- Confirmed HTTP creation rejections (400/401/403/404/422/429) are recorded as
  rejected, release the failed commitment, and retry with persisted exponential
  backoff from 30 seconds to 5 minutes. Status records the HTTP code without
  assuming it identifies a particular quota. Configuration/authentication
  failures require correction.
- Ambiguous CAI creates, including timeouts and 5xx responses, are journaled as `launch_unknown` and block subsequent
  provisioning. Reconcile the application identity before retrying; deleting the
  journal to clear an error can create duplicate GPU applications.
- A startup deadline blocks further provisioning instead of repeatedly creating
  workers. CAI application readiness, Ray join and model readiness are distinct.
- Worker mutation and head recovery use a shared lifecycle lock. Recovery
  preserves worker ownership and respects persisted retirement intent.
- Partially occupied workers can remain indefinitely. Moving one requires a
  complete replacement replica, readiness and request-draining verification,
  including every TP executor and the CPU scheduler. That integration is not
  enabled by this implementation.
- Native Ray string labels are separate from the existing numeric `ray_labels`.
  This implementation preserves existing field meanings.

Required live canary: verify automatic CAI create/join, inherited mesh behavior,
real GPU inference under load, reuse of manual capacity, no duplicate launches
while workers start, streaming request completion, idle retirement, and head
recovery. Use at least two deployments and one cross-pod TP=2 deployment before
enabling a wider rollout.

## Local verification

Run the offline suite in the pinned Ray 2.56.1 environment, with the project's
FastAPI dependency range:

```bash
python -m pytest --no-cov --disable-warnings \
  --ignore=tests/test_e2e_deployment.py \
  --ignore=tests/test_cluster_deployment.py -o addopts='' -q
```

The separate local Ray test starts real CPU-only Ray processes. It verifies that
both a live actor and a reserved placement-group bundle block idle retirement,
then checks that the worker reaches DEAD after releasing both:

```bash
RAY_LOCAL_AUTOSCALING_TEST=1 python -m pytest --no-cov \
  tests/test_autoscaling_local_ray.py
```

These tests ran locally with Python 3.14.3, Ray 2.56.1 and FastAPI 0.136.3.
They do not replace validation on the AMP's Python 3.11 CUDA runtime or exercise
real CAI application creation/deletion.

For the integrated CPU-only check, run:

```bash
RAY_LOCAL_AUTOSCALING_TEST=1 python -m pytest --no-cov -s \
  tests/test_autoscaling_local_cluster.py
```

This starts one head with zero schedulable CPUs and at most two one-CPU workers,
all with zero GPUs and 80 MiB object stores. It uses the real bootstrap, policy
store, resource scheduler, reconciler, lifecycle backend, GCS drain and Ray Serve
replica autoscaler. Only CAI application operations are replaced with a local
worker-process adapter. The adapter starts lightweight local nodes instead of
allocating the CAI worker spec's requested RAM. Logical resource settings are
not OS-enforced process memory limits.

Four asynchronous Serve-handle requests exercise one-to-two replica scaling.
The test checks reuse of initial capacity, observe/disabled modes, optional
worker caps, a simulated HTTP 429 and persisted 30-second retry backoff, successful
responses from both workers, rejection of busy drains, real 60-second idle
retirement, retention of an empty minimum worker beyond that timeout, and no
recreation of the initial pool after a controller restart. Cleanup runs in a
`finally` block and verifies the remaining cluster processes stopped. Allow
about three minutes for the real backoff and idle timers.

This is not an HTTP ingress/load benchmark, CAI quota-discovery test, pod-restart
test, or GPU/NCCL validation. It verifies CPU scheduling and scaling behavior
through a real local Ray cluster without using Workbench credentials.

Verified on 2026-09-29: the integrated test passed in 181.47 seconds. All four
requests completed across the two workers. The empty minimum worker remained
alive after 65 seconds of idleness, retired after the minimum changed to zero,
and was not recreated by bootstrap/controller restart. Process cleanup assertions
passed. The offline suite also passed: 292 tests, with the two opt-in local Ray
tests skipped in that separate run. No CAI applications or GPUs were used.
