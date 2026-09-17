# Blueprint release acceptance

Local regression tests cover setup and failure handling. Release acceptance also
requires a fresh CAI project run; the existing Qwen service does not prove that
the AMP can reproduce its environment.

## Clean AMP run

1. Import the candidate repository revision into a disposable CAI project with
   the configured runtime images and a worker that can allocate two GPUs in one
   pod. Retain the revision and selected AMP inputs, excluding tokens.
2. Retain successful logs from base, vLLM, and LiteLLM environment setup. Record
   installed Ray, vLLM, torch, FlashInfer, and CUDA wheel versions and dependency
   check results. Installation or dependency conflicts must fail setup.
3. Confirm head and worker applications are running and Ray reports the expected
   two GPUs and node-type label. Confirm authenticated Swagger access.
4. Confirm Prometheus readiness, Grafana database health, imported Ray dashboards,
   and a healthy Ray scrape target. Open a dashboard and verify that its panels
   contain current metrics; HTTP health alone does not prove scraping works.
5. Confirm the AMP Qwen deployment reaches a serving state and its completion
   request returns nonempty output. Retain model ID, route, TP, sampler mode, and
   sanitized request/result. A timeout or failed deployment must fail the demo.

## CUDA and sampler verification

- Verify that the toolkit root points into the actor's actual venv even when
  `bin/python` is a symlink. Record `nvcc` and runtime-header versions and the
  resolved ninja executable in the GPU worker environment.
- For TP=2, verify the environment in the GPU Ray workers as well as the CPU
  scheduler. A scheduler's lack of visible GPUs does not identify GPU hardware.
- Preserve the existing production Qwen sampler opt-out during acceptance of
  the default configuration. To validate FlashInfer itself, use a separate
  canary with the sampler explicitly enabled and sufficient available GPUs.
  Confirm successful kernel compilation/cache loading, engine startup, and
  inference; retain logs. Merely discovering `nvcc` does not prove JIT succeeds.
- Repeat a GPU canary on L40S before making an L40S-specific success claim.

## Reviewer artifacts

- Reprise: AMP import, all setup stages, monitoring, Swagger, Qwen completion,
  and live dashboard panels.
- Benchmark: repository revision, GPU SKU/count, package versions, exact payload,
  sampler mode, input/output lengths, concurrency, duration, failures, TTFT/E2E
  percentiles, and reproduction command. Keep benchmark results separate from
  the functional completion record.

Status: these live acceptance steps remain pending until their logs and artifacts
are retained. Never include bearer tokens, Hugging Face tokens, or credentials in
the evidence.
