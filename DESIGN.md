# DESIGN.md

> **Scope note:** this file is repurposed from its usual meaning. `ray-serve-cai` is
> a backend/infra project — there is no UI and no visual design system, so there
> are no color/typography/component rules to record here. Instead this file holds
> the project's **system & API design principles**: the decisions that shape how
> the code and the REST surface are structured. For runtime architecture and data
> flow, see [architecture.md](architecture.md).

## Core structural decision: one-way dependency

`cai_integration/` depends on `ray_serve_cai/`. Never the reverse.

- `ray_serve_cai` is generic Ray Serve orchestration — works on any Ray cluster,
  could be published to PyPI independently, has no CML knowledge.
- `cai_integration` is CML-specific deployment automation — launches CML
  Applications as Ray head/workers, builds engine venvs, wires nginx.

Why it's kept this way (from `docs/ARCHITECTURE.md` §Design Decisions):

- Users who want "Ray Serve + CAI" should be able to
  `from ray_serve_cai import CAIClusterManager` — CAI is a first-class cluster
  backend, not a bolt-on.
- Keeps the door open for other backends (AWS, GCP, K8s) following the same
  pattern without touching `cai_integration`.
- Lets the library be used (or published) without dragging in CML-specific
  deployment infrastructure.

## Why script-based CML deployment, not inline commands

CAI Applications require a Python script as an entry point. Scripts (not inline
shell) let the deployment layer activate the right venv, handle error conditions
with real exit codes, and be versioned/tested like any other code.

## API design principles (Management REST API)

- **One deploy endpoint, one discriminator.** `POST /api/v1/applications` handles
  both engine-registry deployments (`engine_type`) and raw Ray Serve apps
  (`import_path`). Exactly one must be present — both or neither is a `422`. Avoid
  growing parallel deploy endpoints per engine type.
- **Strict request schemas.** Pydantic models use `extra="forbid"` — unknown
  top-level fields fail loudly (`422`) instead of being silently ignored. This
  catches "I put the field in the wrong nesting level" mistakes immediately (e.g.
  `placement_group_bundles` must live under `scheduling`, not top-level).
- **Declarative placement over imperative placement-group code.** Callers describe
  *what* they want (`scheduling.resources`, `placement_group_bundles`,
  `placement_group_strategy`) and the engine factories derive Ray placement groups
  from sensible per-scenario defaults, while still allowing full override. See
  [component-api.md](component-api.md#scheduling--placement-groups).
- **Deny dangerous env var keys, don't sanitize them.** `SchedulingConfig.env_vars`
  rejects `PATH`/`LD_*`/`PYTHON*` outright rather than trying to safely merge them.
  If a fix legitimately needs one of those (e.g. propagating venv `bin/` onto a Ray
  worker's PATH for console-script resolution), it's injected in code at the
  `runtime_env` construction site, not exposed through the public request schema.
- **Async by default for heavy operations.** Venv creation (`POST
  /api/v1/environments`) and large-model deploys return immediately (`202` /
  `deploying`) with a poll endpoint, rather than blocking the request for minutes.
- **Allowlist, don't blocklist, for code execution paths.** `import_path` (raw Ray
  Serve app) and dynamic engine registration are both checked against an
  `ALLOWED_ENGINE_MODULES` allowlist before import, to prevent arbitrary module
  execution via the API.

## Isolation as a first-class constraint

Different inference engines (vLLM, SGLang) pin mutually incompatible dependency
versions (e.g. `llguidance`). Rather than picking one engine to support well, each
engine runs in its own venv (`/home/cdsw/.venv-<engine>`) selected via Ray's
`py_executable` runtime env — so engine choice is a deployment-time decision, not a
recompile-the-image decision.
