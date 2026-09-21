# Input-safe AMP import

Some Workbench AMP form edits have been observed to persist serialized browser
events instead of the entered text. A null event target does not retain the input;
replacing it with a manifest default loses the user's choice. The Workbench form
implementation is not part of this repository.

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
  --git-ref feature/blueprint_fix \
  --worker-cpu 12 \
  --worker-memory 64 \
  --worker-accelerator-type L40S
```

This validates inputs only; it does not create anything. The remote Git ref must
contain the intended AMP changes before deploying; local edits are not uploaded.

All other AMP inputs, including head resources and GPU count, can be supplied in
`--env-file inputs.json`, a JSON object whose values are strings. Explicit CLI
worker flags take precedence over that file. Unknown keys and object values are
errors; omitted fields use manifest defaults. Values are not printed, so files
can carry tokens without displaying them in preview output. Keep secret files
out of Git.

Only when intentionally creating a **new project and running its AMP jobs**, add
`--host https://<workbench> --token-file /path/to/token --apply`. The normal AMP
job chain runs, including monitoring. No workers start by default; retain
`RAY_LAUNCH_INITIAL_WORKERS=false`. API acceptance does not prove job readiness.

The read-only project-environment preflight also checks existing project values
before setup/application creation. It detects corruption, but does not repair the
Workbench UI or rewrite existing settings. Existing monitoring authentication
limitations are independent of this input workaround.

## Verification

```bash
python -m pytest -q --no-cov tests/test_create_amp.py tests/test_project_environment.py tests/test_amp_manifest.py tests/test_amp_cluster_inputs.py
```

Tests cover the actual manifest → payload → cluster config → worker template
path for `12 CPU / 64 GB / L40S`, zero initial workers, rejected objects, explicit
empty accelerator values, dry-run isolation and mocked API submission. No live AMP
is created by these tests.

API contract: [Cloudera: Creating New AMPs using API](https://docs.cloudera.com/machine-learning/cloud/manage-amp/topics/ml-amp-create-new-amp-api.html).
