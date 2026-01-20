# Architecture Diagram and Decision Guide

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER APPLICATIONS                            │
│                                                                      │
│  ┌──────────────────┐        ┌──────────────────┐                  │
│  │  Direct Ray Use  │        │  CML Deployment  │                  │
│  │  (Local/Cloud)   │        │  (via DevOps)    │                  │
│  └────────┬─────────┘        └────────┬─────────┘                  │
└───────────┼────────────────────────────┼──────────────────────────┘
            │                            │
            ▼                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     RAY_SERVE_CAI (Library)                          │
│                      Generic Ray Serve                              │
│                     Orchestration + LLM                             │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │              │  │              │  │              │              │
│  │ ray_backend  │  │   engines/   │  │ cai_cluster  │              │
│  │              │  │              │  │              │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│                                                                      │
│  Exports:                                                           │
│  • RayBackend - Main orchestration                                  │
│  • LLM engines (vLLM, SGLang)                                      │
│  • CAIClusterManager - CAI-specific                                │
└─────────────────────────────────────────────────────────────────────┘
            ▲
            │ (imports)
            │
┌─────────────────────────────────────────────────────────────────────┐
│                  CAI_INTEGRATION (Deployment)                       │
│              CML/CAI-Specific Orchestration                         │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │         Deploy Orchestrator (deploy_to_cml.py)              │  │
│  │  • Creates CML project                                       │  │
│  │  • Manages git cloning                                       │  │
│  │  • Creates/executes jobs                                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │              │  │              │  │              │              │
│  │   Git Sync   │→ │ Setup Env    │→ │  Launch Ray  │              │
│  │    (Job)     │  │   (Job)      │  │  (Job)       │              │
│  │              │  │              │  │ Uses ray_    │              │
│  │              │  │              │  │ serve_cai    │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
│                                                                      │
│  Testing:                                                           │
│  • local_test/ - Tests project creation                            │
│  • test_cai_deployment.py - Tests cluster creation                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Decision Tree: Which Component to Use?

```
                    START
                      │
                      ▼
          Do you need to deploy
            to Cloudera ML (CML)?
                      │
                ┌─────┴─────┐
               YES          NO
                │            │
                ▼            ▼
        Use cai_        Use ray_serve_cai
        integration/    directly
        deploy_to_
        cml.py              │
                            ▼
                   What infrastructure?
                            │
        ┌───────────┬───────┼────────┬──────────┐
        │           │       │        │          │
       Local      AWS      GCP      K8s      Other
        │           │       │        │          │
        ▼           ▼       ▼        ▼          ▼
      ✓Done       Coming   Coming  Coming    Contrib
```

---

## Dependency Flow

### Correct (Current) ✅

```
User Code
  ↓
  ├─→ ray_serve_cai        [Generic library]
  │     ├─→ ray_backend.py
  │     ├─→ engines/
  │     └─→ cai_cluster.py
  │
  └─→ cai_integration      [CML deployment]
        ├─→ deploy_to_cml.py
        ├─→ setup_environment.py
        ├─→ launch_ray_cluster.py [uses ray_serve_cai.cai_cluster]
        └─→ local_test/
```

### Anti-Pattern (Would be wrong) ❌

```
User Code
  ↓
  cai_integration
  ↓
  ray_serve_cai
  ↓
  cai_integration  [CIRCULAR!]
```

**Current design avoids this ✅**

---

## File Organization Rationale

### `ray_serve_cai/` - Library Code

**Should contain**: Code that users would `import`

```python
from ray_serve_cai import RayBackend              # ✅
from ray_serve_cai.engines import vllm_engine    # ✅
from ray_serve_cai import CAIClusterManager      # ✅ (for CML users)
```

**Currently**:
```
ray_serve_cai/
├── __init__.py           ← Exports public API
├── ray_backend.py        ← Core orchestration
├── cai_cluster.py        ← CAI-specific manager ⚠️
├── launch_cluster.py     ← Generic launcher
├── engines/              ← LLM engine plugins
│   ├── base.py
│   ├── registry.py
│   ├── vllm_engine.py
│   └── sglang_engine.py
└── configs/              ← Configuration templates
```

---

### `cai_integration/` - Deployment Scripts

**Should contain**: Scripts that users would run/execute, not import

```python
# Run as script:
$ python cai_integration/deploy_to_cml.py

# Or import for automation:
from cai_integration.deploy_to_cml import CAIDeployer  # Acceptable
```

**Currently**:
```
cai_integration/
├── deploy_to_cml.py              ← Main orchestrator
├── setup_environment.py          ← Job script
├── launch_ray_cluster.py         ← Job script
├── jobs_config.yaml              ← Job definitions
├── quick_start.sh                ← Helper script
├── local_test/
│   ├── test_project_creation.py
│   ├── run_test.sh
│   └── READMEs
└── README.md
```

---

### `tests/` - Test Suite

**Should contain**: All test code

**Currently**:
```
tests/
├── test_cai_deployment.py       ← Integration test
├── run_test.sh
└── README.md

⚠️ Missing: Unit tests for ray_serve_cai
⚠️ Extra: Tests also in cai_integration/local_test/
```

**Suggested reorganization**:
```
tests/
├── unit/                         ← Library unit tests
│   ├── test_ray_backend.py
│   ├── test_engines/
│   └── test_cai_cluster.py
│
├── integration/                  ← Integration tests
│   ├── test_cai_deployment.py
│   └── test_local_cluster.py
│
└── e2e/                          ← End-to-end tests
    └── cai_integration_flow/
```

---

## What Each Component Does

### `ray_serve_cai` - The Library

**Purpose**: Generic Ray Serve orchestration with LLM support

**What it provides**:
- RayBackend: Main orchestration interface
- Engine registry: Plugin system for LLM engines
- Engine implementations: vLLM, SGLang
- Cluster managers: CAI, Local, etc.

**Who uses it**: Data scientists, ML engineers running Ray Serve

**Example**:
```python
from ray_serve_cai import RayBackend

backend = RayBackend()
await backend.initialize_ray()
await backend.start_model({
    'model': 'meta-llama/Llama-2-7b-hf',
    'tensor_parallel_size': 2
}, engine='vllm')
```

### `cai_integration` - The Deployment Orchestrator

**Purpose**: Deploy Ray Serve to CML via automated jobs

**What it provides**:
- Job-based deployment orchestration
- Environment setup (venv, dependencies)
- Cluster launch on CAI applications
- GitHub Actions integration

**Who uses it**: DevOps, ML platform engineers for CML

**Example**:
```bash
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-key"
export CML_PROJECT_ID="your-project"

python cai_integration/deploy_to_cml.py
```

### `tests` - The Test Suite

**Purpose**: Verify correctness of both components

**What it tests**:
- Library functionality (unit tests)
- Deployment process (integration tests)
- End-to-end flows (e2e tests)

**Who uses it**: Developers, CI/CD pipelines

---

## Concerns and Clarity Issues

### ❓ Concern 1: Why is `cai_cluster.py` in `ray_serve_cai`?

**Answer**: Because it's part of the public API

```python
# Users who want CAI support should be able to:
from ray_serve_cai import CAIClusterManager

# Not:
from cai_integration.ray_serve_cai import CAIClusterManager
```

**However**: This makes the generic library CAI-aware

**Solution**: Eventually, abstract into multiple backends

```python
from ray_serve_cai.backends import CAICluster  # Same directory
```

---

### ❓ Concern 2: Is the naming `ray_serve_cai` confusing?

**Answer**: Yes, it suggests CAI-only. Better names:

- `ray_serve_llm` - Emphasizes LLM support
- `ray_serve_orchestration` - Emphasizes orchestration
- `ray_llm` - Shorter, clear purpose
- `ray_backends` - Emphasizes multi-backend

**Recommendation**: Consider renaming when publishing to PyPI

---

### ❓ Concern 3: Should `cai_integration` be in the repo?

**Answer**: Yes, because:

1. **Template for users**: Shows how to deploy
2. **Version coupling**: Needs to match ray_serve_cai version
3. **Testing**: Can test deployment in CI/CD
4. **Documentation**: Living documentation of deployment

**However**: Should be clearly marked as "deployment template"

---

## Recommended Architecture Improvements

### Phase 1: Current (No changes needed)

- ✅ Library + deployment separation is sound
- ✅ Dependency direction is correct
- ✅ Testing infrastructure exists

### Phase 2: Clarity and Documentation

```markdown
# In README.md

## Quick Start

**I want to use Ray Serve with LLM:**
→ `from ray_serve_cai import RayBackend`

**I want to deploy to CML:**
→ `python cai_integration/deploy_to_cml.py`

**I want to use Ray Serve locally:**
→ `from ray_serve_cai import RayBackend` + local Ray cluster

**I want to use with AWS/GCP/K8s:**
→ Not yet supported, coming soon
```

### Phase 3: Multi-Backend Support

```python
# ray_serve_cai/backends/ (new directory)
├── base.py          # ClusterBackend protocol
├── cai.py           # CAI implementation
├── aws.py           # AWS implementation (future)
├── kubernetes.py    # K8s implementation (future)
└── local.py         # Local implementation
```

### Phase 4: PyPI Package

- Publish `ray-serve-llm` to PyPI
- Keep `cai_integration` as template in repo
- Enable broader adoption beyond CML

---

## Summary: Is Current Structure Reasonable?

### ✅ YES - The Structure is Sound

**Reasons**:

1. **Clear separation of concerns**
   - Library: Generic orchestration
   - Deployment: CML-specific automation

2. **Correct dependency direction**
   - cai_integration depends on ray_serve_cai
   - Not circular

3. **Appropriate reusability**
   - ray_serve_cai can be used independently
   - cai_integration is optional for CML users

4. **Scalable for future**
   - Can add AWS, GCP, K8s backends
   - Library can become multi-platform

### ⚠️ WITH IMPROVEMENTS NEEDED

1. **Clarify naming** - Consider renaming for better positioning
2. **Improve documentation** - Add "which component when?" guide
3. **Expand testing** - Add unit tests for library
4. **Plan for multi-backend** - Design for extensibility

### 🚀 Vision for the Future

```
PyPI Package: ray-serve-llm
├── Multi-engine support (vLLM, SGLang, etc.)
├── Multi-backend support (Local, AWS, GCP, K8s, CAI)
├── Production-ready monitoring and health checks
└── Comprehensive documentation and examples

Repository: ray-serve-cai
├── Core library (ray-serve-llm)
├── Deployment templates (cai_integration, aws_deployment, etc.)
├── Examples and documentation
└── Tests and CI/CD
```

---

**Assessment**: ✅ APPROVED - Current structure is sound and well-positioned for growth
