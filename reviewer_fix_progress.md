# Reviewer (Mason) Fix — Progress & Requirements

Source: `~/Downloads/Ray Cluster on CAI for Micro Services.md` (Aug 27, 2026, reviewer Mason + 3 screenshots).
Repo: `ray-serve-cai` (branch `feature/dashboard`).

---

## 1. Requirements extracted (19 items)

| # | Requirement | Status |
|---|---|---|
| R1 | Architecture must be a **graphic diagram**, not ASCII | ✅ DONE (Track C — 2 Mermaid diagrams replace ASCII) |
| R2 | **Reprise demo** missing (project + README) | 🟡 OPEN — README has a placeholder; recording still needs a Reprise account and an AMP-to-Qwen walkthrough. |
| R3 | Show deploying Ray cluster **via AMP** + LLM running & queried | 🟡 IMPLEMENTED — AMP manifest + demo task exist; live Qwen completion is validated, but an AMP import recording/job log remains open. |
| R4 | Rename "Why this exists" → **"Overview"** | ✅ DONE |
| R5 | Add **"Use Cases"** section | ✅ DONE (README + project-overview) |
| R6 | Add **"Free Sources"** (open model sources) | ✅ DONE (project-overview "Free & Open-Source Model Sources") |
| R7 | Add **"Target Audience"** | ✅ DONE (README + project-overview) |
| R8 | Align with **Cloudera Blueprints Standard** | ✅ DONE LOCALLY — metadata follows the public standard template, README sections/assets are present; Reprise/public-repo URLs intentionally remain blank until authoritative. |
| R9 | Diagram: wrap **Cloudera product visuals** | ✅ DONE (Track C — CAI Workbench/CML app subgraphs in Mermaid) |
| R10 | Assess/convert to **AMP** | ✅ DONE (Track A — `.project-metadata.yaml`) |
| R11 | **Swagger API UI** | ✅ ALREADY EXISTS (`/docs`, `/redoc`) — documented in README + user-guide |
| R12 | **Dynamically populate env vars** from CAI workbench (CDSW_*) | ✅ DONE — runtime and bootstrap scripts resolve `CML_*` or Workbench `CDSW_*` equivalents; regression tests cover both paths. |
| R13 | Env-var auto-pickup w/ **AMP metadata file** | ✅ DONE (Track A — 4 promptable vars in manifest; CDSW_* runtime-injected, documented in header) |
| R14 | Architecture graphical, not strings (Mason's explicit) | ✅ DONE (Track C) |
| R15 | Show **UI optimization** | ✅ DONE (Track D — Optimizations table: FP8, TP, prefix caching, CUDA graphs, chunked prefill, fractional GPU, venv isolation, node pinning) |
| R16 | Show **benchmark performance** | 🟡 OPEN — unsupported README numbers removed on 2026-09-16; functional Qwen evidence and a benchmark protocol are documented, but a reproducible load-test artifact is still required. |
| R17 | Show **cost reasoning** | ✅ DONE (Track D — Cost section: GPU-hour math, FP8-first rule, single-pod TP rule, LiteLLM off-peak routing, $/1M-tok method) |
| R18 | Show **multi-modal performance** (YOLO) | ⏸️ OUT OF SCOPE — superseded by user direction to use the live Qwen example as the sole reviewer showcase. |
| R19 | Demos use a **stronger model** (not SLMs) | ✅ DONE (Qwen3.8-27B-FP8 in README + user-guide) |

---

## 2. Work already committed

**Commit `2b37f97`** — "docs: add 8-file vibe coding doc structure, README overhaul, UI, and engine fixes" (17 files, +2137/-19):

### Doc content fixes (R4/R5/R6/R7/R16/R19)
- `README.md`: "Why this exists"→"Overview"; added **Target Audience**, **Use Cases**, **Performance** (Locust: 312 req/60s, 0 fail, TTFT 330ms, E2E 3.0s); quick-start upgraded Llama-3.1-8B → **Qwen3.8-27B-FP8 (TP=2)** + 7B secondary; fixed stale single-node TP placement-group table row.
- `project-overview.md`: "Overview" rename; Target Audience; Use Cases; **Free & Open-Source Model Sources** table.
- `user-guide.md`: deploy + query examples → Qwen3.8-27B-FP8.
- 8-file vibe-coding doc set added (AGENTS/DESIGN/TODO/project-overview/architecture/user-guide/development/component-api).

### Engine fixes (2026-09-13 evidence; superseded by the 2026-09-16 review)
- The intended package constraints are `vllm>=0.13.0`,
  `flashinfer-python>=0.6.16.post4,<0.6.17`, and
  `nvidia-cuda-{nvcc,runtime,cccl}==13.0.*`. The review found that setup did
  not enforce these constraints on existing environments and tolerated install
  failures. Constraint declarations alone did not establish a valid setup.
- `setup_vllm_env.py` contains a ninja PATH probe and a self-symlink guard.
- Five initial CUDA environment tests passed but missed symlinked Python
  executables and GPU-less TP scheduler actors. Those results do not establish
  correct automatic discovery or a successful GPU JIT build.

### Additional user-requested doc (post-review)
- `README.md` new **Troubleshooting** section (between Development and Documentation, TOC-linked):
  "Ray cluster pods fail to connect — Istio STRICT mTLS" — root cause (CML
  namespace-wide STRICT PeerAuthentication blocking Ray's plain gRPC incl. GCS
  dynamic NodeManager health-check ports), the Critical note that pod-level
  PERMISSIVE cannot override namespace STRICT (portLevelMtls can't cover
  dynamic ports), and the 3-step kubectl fix (get / patch to PERMISSIVE /
  delete superseded port policy). TOC anchor verified against GitHub slug.

### NOT committed (left unstaged intentionally)
- `docs/BRANCH_STATUS_V2_AND_AUTH_RECOVERY.md` (internal notes)
- `docs/Mastercard Foundational Model.pdf` (third-party)
- `scripts/build_inferencing_deck_v2.py` (unreviewed)

---

## 3. Remaining "tough tasks" — plan (full at `~/.claude/plans/reviewer-tough-tasks-plan.md`)

### Anchor insight
Project already deploys via imperative CML job chain (`cai_integration/create_jobs.py` reading `jobs_config.yaml`, driven by GitHub Actions). AMP `.project-metadata.yaml` is the declarative form of the SAME chain → AMP conversion is mostly translation, and subsumes R3/R12/R13.

Job chain: `git_sync -> setup_base_env -> setup_vllm_env -> setup_litellm_env -> launch_ray_cluster -> launch_monitoring`.

Runtime env fallback already present (`management/app.py:48`, `services/cai_service.py:36`, `auth/cml_identity.py:88`): `CML_PROJECT_ID or CDSW_PROJECT_ID`, `CML_API_KEY or CDSW_APIV2_KEY`, `CDSW_DOMAIN`.

### Track A — AMP conversion (R10/R3/R13/R12) — ✅ DONE
Delivered (2 new files, validated):
- `.project-metadata.yaml` — final verification converted the demo to a sixth create/run job pair: 12 tasks total. AMP job timeouts are minutes (15/30/10/20/15/45); they are not copied verbatim from the second-valued job configuration. The demo job has 1 CPU/2 GiB and a 45-minute budget. Python 3.11 JupyterLab Standard is recommended to match the configured application runtimes. Four model/placement/token inputs remain promptable.
- `cai_integration/amp_demo.py` — deploy via Management API (Bearer CDSW_APIV2_KEY) → wait for serving → standard completion. As of 2026-09-16, deployment failure, timeout, or empty/invalid completion returns nonzero; successful inference is required for exit 0.
- No `start_application` task: head CML app (Management API + /docs) is created by launch_ray_cluster_job.py via CML REST API, same as production.
- git_sync stage intentionally excluded (code arrives with AMP import).
R12 now has a shared CML/CDSW resolver. The 2026-09-16 review additionally
requires that both bootstrap scripts work with the direct-script invocation
used by the GitHub workflow, without a preconfigured PYTHONPATH.

### Track B — Blueprints Standard + graphics (R1/R5/R7/R8/R9/R14) — ✅ COMPLETE LOCALLY
- README has Mermaid diagrams plus `assets/ray-serve-cai.png`, showing CAI,
  Ray head, Qwen GPU workers, and Prometheus/Grafana.
- `METADATA.yaml` was aligned with the public Blueprint standard template;
  empty Reprise/public-repo fields are deliberate until final URLs exist.

### Track C — CAI environment fallback (R12) — ✅ COMPLETE
- `cai_integration/cml_env.py` centralizes `CML_*`/`CDSW_*` resolution for the
  bootstrap scripts without logging credentials; tests cover both environments.

### Track D — Qwen showcase evidence (R3/R16/R19) — 🟡 IN PROGRESS
- Authenticated live checks proved Swagger, Management API, a running
  `qwen3-8-27b` deployment (Qwen3.8-27B-FP8, TP=2), and a successful standard
  completion. Evidence: `docs/validation/qwen_live_validation_2026-09-13.md`.
- Still needed: reproducible Locust artifact and AMP-import job logs.

### Track E — Reprise (R2) — OPEN
Needs a recording that shows AMP import, monitoring stage, Swagger, Qwen
completion, and dashboard access. See `fix-continue-plan.md` for acceptance.

---

## 4. Implementation follow-up — 2026-09-16

Three GPT-5.6 implementation agents handled bootstrap/setup, CUDA runtime
configuration, and AMP/monitoring readiness. Root reviewed the combined changes.

- Bootstrap entry points now support direct-script and module invocations with
  no PYTHONPATH; no-network subprocess tests cover both forms.
- Engine setup installs each complete constrained bundle in one resolver
  transaction, validates declared versions and transitive dependencies, and
  reconciles existing venvs under the NFS lock. Failed installation or validation
  fails setup. Reuse dependency checks do not bootstrap pip or modify the venv.
- CUDA discovery handles interpreter symlinks and stale inherited VIRTUAL_ENV.
  The factory places CUDA_HOME/sampler settings in Ray's runtime environment.
  Both generated and supplied TP bundles retain CPU-only bundle 0 and GPU≤1
  per bundle. Missing toolkit defaults to the native sampler for all isolated
  GPU deployments, including L40S; explicit caller settings are preserved.
- AMP rejects failed/expired deployments and requires a nonempty standard
  completion. Monitoring requires service readiness and dashboard provisioning.
- Unsupported benchmark and cost/performance conclusions were removed from
  README. Historical numerical claims above describe the earlier commit only.
- Combined local regression run: **98 tests passed**. Scoped bootstrap,
  AMP/monitoring, and regression-test lint passed; YAML and syntax checks passed.
  The vLLM engine retains pre-existing full-file lint findings.

These are local implementation results. Real package resolution in the CAI
runtime, GPU-worker environment inheritance, FlashInfer JIT, fresh AMP import,
live dashboard panels, benchmark, and Reprise remain acceptance work. See
`docs/validation/blueprint_acceptance.md`.

Final AMP verification added manifest contract tests and corrected the
task/time-budget configuration above; the earlier 98-test result predates the
zero-worker AMP input refinement described below.

## 5. Key facts for Track A (from `configs/ray_cluster_config.yaml`)

- Head: `head_cpu:12`, `head_memory:32`; Management API derives a safe half-head
  allocation after an AMP head-resource override (or can be explicitly set with
  `RAY_MANAGEMENT_API_CPU` / `RAY_MANAGEMENT_API_MEMORY`).
- Worker group `rtxpro6000-gpu-workers` is a zero-count AMP input template:
  `RAY_WORKER_NODE_TYPE`, CPU, memory, GPU, and accelerator inputs define it at
  import time. No worker is launched until the user calls the Management API.
- CPU worker group `cpu-workers`: count 0, cpu 16, mem 32.
- Runtimes: head = `...ml-runtime-pbj-jupyterlab-python3.11-standard:2026.04.1-b7`; worker = `...python3.11-cuda:2026.04.1-b7`.
- `project_name: ray-cluster`; `head_app_name: ray-cluster-head` → URL `https://ray-cluster-head.<CDSW_DOMAIN>`.
- Env overrides supported: `RAY_HEAD_CPU`, `RAY_HEAD_MEMORY`,
  `RAY_WORKER_NODE_TYPE`, `RAY_WORKER_{CPU,MEMORY,GPUS}`, `MONITORING_*`, etc.
- Ports: ray 6379, dashboard 8265.

AMP resource/timeout: setup_base_env 15min/4cpu/16GiB; setup_vllm_env 30min/4/16;
setup_litellm_env 10min/2/8; launch_ray_cluster 20min/2/8;
launch_monitoring 15min/2/8; `amp_llm_demo` is created (45min/1/2) but manually
run only after the user provisions a compatible worker.
