# Mason Review — Fix-Continue Plan

**Prepared:** 2026-09-13; reviewed again 2026-09-16
**Source reviewed:** `~/Downloads/Ray Cluster on CAI for Micro Services.md` (Mason, 2026-08-27)
**Repository state reviewed:** `feature/dashboard` at `50589af`

## Executive status

The documentation, architecture graphic, and AMP manifest are present locally.
The 2026-09-13 checks establish that the existing Qwen deployment served a
completion. The 2026-09-16 review found bootstrap import, CUDA discovery/worker
configuration, package enforcement, and AMP/monitoring readiness defects; the
earlier local test results did not cover those cases. Release requires regression
coverage for those defects and a clean CAI AMP import with Qwen inference and
monitoring. Reprise and a reproducible benchmark remain open. Earlier passing
checks below are dated evidence, not a claim of complete blueprint readiness.

## Verification evidence retained from 2026-09-13

| Check | Result | Scope / limitation |
|---|---|---|
| AMP manifest parse (2026-09-13) | PASS | Historical 11-task manifest loaded with PyYAML and had five matching create/run pairs. The current manifest replaces this with six pairs (12 tasks); its contract is covered by `tests/test_amp_manifest.py`. Neither result proves a CAI import succeeds. |
| AMP demo syntax | PASS | `python -m py_compile cai_integration/amp_demo.py` passed. |
| Relevant unit tests | PASS | `python -m pytest -q tests/test_node_types.py tests/test_engine_import_safety.py tests/test_deployment_store.py tests/test_auth_cml_identity.py`: 33 passed. |
| Python lint | PASS | P0 fixed the scoped lint findings. `ruff check` passed for the changed bootstrap, AMP, setup, and environment-test files. |
| Catalog image | PASS | `assets/ray-serve-cai.png` is present and embedded in `README.md` as the architecture graphic. |
| CAI runtime fallback | PRESENT | Management code supports `CML_*` or `CDSW_*` fallbacks. |
| CAI bootstrap fallback | PASS | `cai_integration/cml_env.py` resolves legacy `CML_*` and Workbench `CDSW_*` variables; tests cover legacy, CDSW-only, blank, and explicit-override cases. |
| Live CAI Swagger/API | PASS | On 2026-09-13, authenticated `GET /docs`, `/openapi.json`, and `/api/health` against the supplied head URL each returned HTTP 200. OpenAPI title: `Ray Cluster Management API`. |
| Running example | PASS | Authenticated `GET /api/v1/applications` returned `qwen3-8-27b` with `ApplicationStatus.RUNNING`; its detail endpoint also returned 200. The list/detail response does not expose model, route, engine, or TP fields, so do not infer them from this response. |
| Qwen functional completion | PASS | Authenticated `POST /qwen3-8/v1/completions` returned HTTP 200, `finish_reason: stop`, and a complete response from `Qwen/Qwen3.8-27B-FP8` in 1.262 s. Full sanitized evidence: `docs/validation/qwen_live_validation_2026-09-13.md`. This is not a benchmark. |
| AMP monitoring stage | PRESENT | `launch_monitoring` uses 2 CPU/8 GiB and a 15-minute timeout in the corrected manifest, followed by `run_job`. Live monitoring app status remains unverified; the earlier `GET /api/v1/cml-apps` probe returned 405. |
| Benchmark/Reprise evidence | NOT FOUND | No reproducible Qwen benchmark artifact or Reprise URL/recording was found in this repository. |

## Original-review traceability

| Mason request | Current evidence | Honest status | Closure criterion |
|---|---|---|---|
| Replace ASCII architecture with a sophisticated graphic and Cloudera-product wrappers | README contains Mermaid diagrams plus `assets/ray-serve-cai.png`, with CAI Workbench, CML application, Ray head/workers, Management API, and client boundaries. | Closed locally. | Keep the committed PNG embedded and visually review it in the target catalog. |
| Reprise demo showing AMP cluster deployment, LLM deployment, and query | README has a Reprise placeholder; `.project-metadata.yaml` and `amp_demo.py` implement the intended flow; supplied CAI Swagger and running Qwen application are live. | Open. | Record and link a successful Reprise walkthrough showing import, job chain (including monitoring), `/docs`, the Qwen application, and a model response. |
| Rename “Why this exists” to Overview; add Use Cases, Free Sources, Target Audience | Present in `README.md` / `project-overview.md`. | Closed (static documentation). | Keep headings and links valid in the final documentation review. |
| Align to Cloudera Blueprints Standard | README section structure and `assets/` exist; `METADATA.yaml` now uses the fields in the public standard template, verified 2026-09-13. | Closed locally; publication fields remain intentionally blank until authoritative values exist. | Set `reprise_link` and `public_github_link` only when their final URLs are available. |
| Assess/convert to AMP | Committed `17a17a2`: manifest + demo task; it creates and waits for `launch_monitoring` as stage 5. | Implemented, pending an import/job-chain run. | Import into a disposable CAI project; preserve job logs for all five stages plus monitoring and prove the demo reaches a queried model or records a clear deployment failure. |
| Swagger API UI | FastAPI Swagger is available at `/docs` and documented; authenticated access to `/docs` on the supplied head returned HTTP 200. | Closed for implementation and existing-cluster access; AMP-import accessibility unverified. | Verify `/docs` through the head application created by an AMP import. |
| Dynamically populate required CAI Workbench environment variables | Runtime services and bootstrap scripts resolve `CML_*` with `CDSW_*` fallbacks; focused tests cover legacy, CDSW-only, blank, and explicit-override environments. | Closed locally. | Preserve secret-safe handling and validate during the AMP import run. |
| Show UI optimization | README `Optimizations` section covers FP8, TP, prefix caching, CUDA graphs, chunked prefill, fractional GPUs, isolation, and pinning. | Documented, not empirically closed. | Tie each claimed active optimization to the exact deployment payload and benchmark configuration. |
| Show benchmark performance | README links the functional Qwen check and specifies the planned benchmark; unsupported numerical claims were removed on 2026-09-16. | Evidence gap remains. | Capture a sanitized Locust summary from the supplied Qwen example with environment/model/GPU details, payload, date, and reproduction command. |
| Discuss efficient reasoning/cost | README `Cost` section provides decision rules and $/1M-token method. | Closed as guidance; pricing/throughput needs benchmark evidence. | Attach the benchmark evidence above; use platform-specific rates only when authorized. |
| Use a stronger model instead of SLM examples | README/user guide/AMP default use `Qwen/Qwen3.8-27B-FP8`, TP=2; the 2026-09-13 intent and completion checks identify the model and route. | Functional check recorded; fresh AMP deployment pending. | Include this model in the successful demo recording and benchmark artifact. |

## Continue plan — ordered by closure value

### Review fixes implemented locally — 2026-09-16

Final AMP contract verification used the
[Cloudera AMP specification](https://docs.cloudera.com/machine-learning/1.5.5/applied-ml-prototypes/topics/ml-amp-project-spec.html)
and [JupyterLab runtime guidance](https://docs.cloudera.com/machine-learning/1.5.5/runtimes-release-notes/topics/ml-runtimes-whats-new-2025-01-1.html).
It corrected second-valued job timeouts to minutes and replaced the final
session with a 1 CPU/2 GiB, 45-minute job. There are now six sequential create/run
pairs (12 tasks), with Python 3.11 runtime recommendations. The new manifest
tests verify stage order, budgets, scripts, pairing, and default worker capacity.
Historical 11-task parse results above describe the earlier manifest only.
Final combined regression run with the manifest tests: **98 tests passed**.
Scoped lint and `git diff --check` passed. No fresh CAI import was run.

- Bootstrap scripts support direct and module execution without PYTHONPATH.
- CUDA discovery uses active venv roots without following interpreter symlinks;
  generated Ray runtime environments carry toolkit/sampler configuration for
  both generated and supplied TP placement groups. Missing toolkit defaults to
  the native sampler on any isolated GPU deployment; caller overrides remain.
- Setup enforces full package constraints in a single resolver transaction,
  reconciles existing environments, and checks transitive dependencies.
- AMP fails on terminal deployment errors, timeouts, and invalid/empty inference.
  Monitoring fails when service readiness or dashboard provisioning fails.
- README no longer presents unsupported benchmark numbers or derived cost claims.

The final combined local regression run passed **98 tests**; scoped lint, syntax,
YAML, and diff checks passed. Full-file vLLM lint still has pre-existing findings.
These fixes are implemented in the worktree and have not been deployed to CAI.
Use [the release acceptance checklist](docs/validation/blueprint_acceptance.md)
for the next run; real GPU/JIT and fresh AMP evidence are still required.

### P0 — Initial local checks (2026-09-13; insufficient for release)

1. Preserved the AMP venv re-exec and applied a narrow lint exemption to its
   post-re-exec import; cleaned the related setup-script lint findings.
2. Added safe `CML_*` / `CDSW_*` connection resolution and tests without logging
   credentials or relaxing request-model environment-variable deny-lists.
3. Ran `ruff check` on touched files and the focused suite: **37 tests passed**.

These checks covered environment-value resolution but missed direct-script
imports, venv symlinks, GPU-less TP schedulers, dependency installation failures,
and terminal AMP failures. The 2026-09-16 implementation must cover those paths
before the clean CAI acceptance run.

### P1 — Completed locally: close the blueprint visual and metadata gap

1. Produced a reviewable architecture graphic based on the Mermaid diagrams,
   with clear CAI Workbench, CML application, Ray head/worker, Management API,
   and client boundaries.
2. Added `assets/ray-serve-cai.png` and embedded it in the root README.
3. Validated `METADATA.yaml` against the public standard template; removed
   non-standard image metadata and left unknown publication URLs empty.

**Acceptance met locally:** the README graphic is present, no metadata image
reference is broken, and metadata uses the public standard-template schema.

### P2 — In progress: generate the Qwen empirical proof and Reprise demo

1. In a sanctioned CAI project, import the AMP and retain sanitized logs for
   the five job stages, including `launch_monitoring`, and `amp_llm_demo`.
2. Use the supplied running `qwen3-8-27b` application as the sole example.
   Swagger, application status, intent configuration, and a successful
   completion are now captured in
   `docs/validation/qwen_live_validation_2026-09-13.md`. Capture the monitoring
   application/dashboard next and use the material for the Reprise recording.
3. Run the documented Locust benchmark against that Qwen route and save a compact result artifact with
   model, GPU SKU/count, tensor parallelism, vLLM/Ray versions, concurrency,
   duration, TTFT/E2E percentiles, failures, and command/config.

**Acceptance:** README claims cite or embed reproducible evidence, and Reprise
contains the end-to-end AMP-to-Qwen-query path Mason requested, including
monitoring deployment/access.

## Reporting rules until P2 is complete

- Say **“documented”** for README-only optimizations and cost guidance.
- Say **“implemented, pending CAI validation”** for AMP and Swagger deployment
  paths.
- Do not call the Locust numbers “verified” without their artifact.
- Keep `reviewer_fix_progress.md` synchronized with this table and add it to
  version control only when the intended review record is ready to be shared.
