# Input-safe AMP import

Some Workbench AMP form edits have been observed to persist serialized browser
events instead of the entered text. A null event target does not retain the input;
replacing it with a manifest default loses the user's choice. The Workbench form
implementation is not part of this repository. The AMP no longer presents a
Hugging Face token field in that form. Add a token after import only when a gated
model needs one.

`cai_integration.create_amp` is a code-controlled import alternative. It resolves
the manifest defaults once, overlays explicit user values, rejects nested objects,
and sends a flat string-valued environment to API v2 `/amps`. It never updates an
existing project or reconstructs lost input from defaults. The manifest remains
in Cloudera's documented format so sites with working forms can still use it.

## Preview, without API requests

From the repository root, with `requests` and `PyYAML` installed:

```bash
python -m cai_integration.create_amp \
  --name ray-cai-l40s \
  --runtime '<approved-runtime-identifier>' \
  --git-ref feature/blueprint_fix
```

This validates inputs only; it does not create anything. The remote Git ref must
contain the intended AMP changes before deploying; local edits are not uploaded.

AMP inputs, including head resources, can be supplied in
`--env-file inputs.json`, a JSON object whose values are strings. Worker resources
are no longer AMP inputs; define them after startup. Unknown keys and object values are
errors; omitted fields use manifest defaults. Input values are not printed.
Keep secret files out of Git. The Hugging Face token is not an
AMP form or `--env-file` input; set it on the project after import.

Only when intentionally creating a **new project and running its AMP jobs**, add
`--host https://<workbench> --token-file /path/to/token --apply`. The normal AMP
job chain first sets and verifies the project-wide `/dev/shm` limit, then runs
environment setup, cluster launch, and monitoring. The setup stage sets the
project limit to 40,000 MB without relying on an AMP form input. This applies to
applications created after that setup stage. The shipped configuration has an explicit
empty worker-group list: no worker templates or workers are created at startup.
API acceptance does not prove job readiness.

## Add workers after startup — no type registration required

Open the authenticated head's Swagger UI and call
`POST /api/v1/resources/nodes` (admin authorization required):

```json
{
  "name": "l40s-worker-01",
  "node_type": "gpu-worker",
  "cpu": 12,
  "memory": 64,
  "gpus": 1,
  "accelerator_type": "L40S",
  "labels": {"purpose": "inference"}
}
```

This launches one worker directly. `node_type` is optional; `gpus: 1` is an example;
choose the actual GPU count and any required `node_label` for your resource pool.
The accelerator label alone does not establish Kubernetes pool placement.
No registration is needed. CPU and memory define the node's resources. Runtime
defaults to the cluster worker runtime; override `runtime_identifier` when needed.
Optional `/node-types` templates remain supported for resource-omitting requests.

The result returns `worker_id` (stable identity), `app_id` (CAI deletion target),
and `ray_node_id: null` until the worker joins. Use `GET /resources/nodes` for the
current Ray ID and readiness; `GET /resources/worker-apps` shows persisted specs
even before joining. Names and `labels` are descriptive, not unique identities.
The legacy `node_type` Ray resource remains available for explicit scheduling;
a request for it is a hard constraint, not a soft placement preference.

For the optional model demo, match `TENSOR_PARALLEL_SIZE` to available GPUs and
the supported placement strategy; the manifest's TP=2 does not fit this single-GPU
example unchanged. Recovery restores each worker's saved resources, not template
defaults. Uncertain application creation requires reconciliation before retrying.
For existing clusters that only recorded template counts, adding a new tracked
worker flags legacy workers as untracked; automatic recovery stops before
restarting the head until those old workers are reconciled. Fresh head-only AMP
clusters do not need this migration. Persisted `joined`/Ray ID values are last
observations; GET `/resources/nodes` is the live membership view.

**Existing corrupted projects and gated models:** updating the repository does
not remove already saved malformed project variables. To set or replace only
`HUGGING_FACE_HUB_TOKEN`, run the following command from the project terminal.
Enter the original Hugging Face token at the hidden prompt:

```bash
python -m cai_integration.repair_project_hf_token
```

The command reads the current project environment, replaces only that token,
and verifies the API result without printing the token. It refuses to rewrite
other malformed variables. You can also run it from a local checkout with
`--host https://<workbench> --project-id <id> --cml-token-file /path/to/cai-token
--hf-token-file /path/to/hf-token`. Run the failed AMP job again after repair.

The read-only project-environment preflight also checks existing project values
before setup/application creation. It detects corruption, but does not repair the
Workbench UI or rewrite existing settings. Existing monitoring authentication
limitations are independent of this input workaround.

## Verification

```bash
python -m pytest -q --no-cov tests/test_create_amp.py tests/test_project_environment.py tests/test_amp_manifest.py tests/test_amp_cluster_inputs.py
```

Tests cover manifest → payload → head-only cluster configuration, post-start
`12 CPU / 64 GB / L40S` request validation, rejected objects,
dry-run isolation and mocked API submission. No live AMP
is created by these tests.

API contract: [Cloudera: Creating New AMPs using API](https://docs.cloudera.com/machine-learning/cloud/manage-amp/topics/ml-amp-create-new-amp-api.html).
