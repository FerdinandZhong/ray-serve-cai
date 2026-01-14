# Architecture Guide

## Overview

ray-serve-cai is organized as a **library + deployment template** pattern with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────┐
│  Users & Applications                                        │
│  (Data Scientists, ML Engineers, DevOps)                    │
└──────────────┬──────────────────────────────────────────────┘
               │
        ┌──────┴──────────────────────┐
        │                             │
        ▼                             ▼
    ┌────────────────┐         ┌─────────────────┐
    │  ray_serve_cai │         │ cai_integration │
    │    (Library)   │         │  (Deployment)   │
    │                │         │                 │
    │ • RayBackend   │         │ • Orchestrator  │
    │ • Engines      │         │ • Job Scripts   │
    │ • Cluster Mgmt │         │ • Testing Suite │
    └────────────────┘         └─────────────────┘
```

---

## Component Architecture

### 1. `ray_serve_cai/` - Core Library

**Purpose**: Generic Ray Serve orchestration with LLM engine support

**Located in**: `/ray_serve_cai/`

**Components**:

| Component | Purpose | Files |
|-----------|---------|-------|
| **RayBackend** | Main orchestration interface | `ray_backend.py`, `launch_cluster.py` |
| **Engine Registry** | Plugin system for LLM engines | `engines/registry.py` |
| **Engine Implementations** | vLLM, SGLang engines | `engines/vllm_engine.py`, `engines/sglang_engine.py` |
| **CAI Cluster Manager** | CAI-specific cluster management | `cai_cluster.py` |
| **Configs** | Configuration templates | `configs/` |

**Public API**:
```python
from ray_serve_cai import RayBackend
from ray_serve_cai.engines import get_registry, register_engine
from ray_serve_cai import CAIClusterManager
```

**Audience**: Data scientists, ML engineers, anyone using Ray Serve

**Use Cases**:
- Run Ray Serve locally
- Run Ray Serve on cloud clusters (AWS, GCP)
- Run Ray Serve on Kubernetes
- Run Ray Serve on CML

---

### 2. `cai_integration/` - CML Deployment Template

**Purpose**: Deploy ray_serve_cai to Cloudera Machine Learning (CML)

**Located in**: `/cai_integration/`

**Components**:

| Component | Purpose | Files |
|-----------|---------|-------|
| **Orchestrator** | Main deployment automation | `deploy_to_cml.py` |
| **Setup Job** | Create venv and install dependencies | `setup_environment.py` |
| **Launch Job** | Launch Ray cluster on CAI apps | `launch_ray_cluster.py` |
| **Job Config** | Define job sequence and dependencies | `jobs_config.yaml` |
| **Testing Suite** | Test project creation and deployment | `local_test/` |

**Audience**: DevOps, infrastructure engineers, Cloudera customers

**Deployment Flow**:
```
1. GitHub Actions (optional)
   ↓
2. Deploy Orchestrator (deploy_to_cml.py)
   ├─ Create/find CML project
   ├─ Wait for git clone
   └─ Create jobs
   ↓
3. Execute Job Sequence
   ├─ Git Sync (clone repository)
   ├─ Setup Environment (create venv, install Ray)
   └─ Launch Ray Cluster (deploy to CAI applications)
   ↓
4. Ray Cluster Ready
   └─ Users can now run workloads
```

---

## Dependency Flow (Correct Design)

```
User Code
  │
  ├─────────────────────────────────────────────┐
  │                                             │
  ▼                                             ▼
┌────────────────┐                    ┌─────────────────┐
│  ray_serve_cai │                    │ cai_integration │
│   (Library)    │                    │  (Deployment)   │
│                │                    │                 │
│ • Ray Serve    │◄───────────────────┤ imports         │
│ • Engines      │                    │                 │
│ • CAI Cluster  │                    │ • Orchestrator  │
└────────────────┘                    │ • Jobs          │
                                      │ • Tests         │
                                      └─────────────────┘
```

**Key Points**:
- ✅ One-way dependency: cai_integration → ray_serve_cai
- ✅ No circular dependencies
- ✅ Library can be used independently
- ✅ Deployment is CML-specific
- ✅ Clean separation of concerns

---

## Module Organization

### ray_serve_cai/ Structure

```
ray_serve_cai/
├── __init__.py              # Public API exports
├── ray_backend.py           # Main orchestration (RayBackend)
├── cai_cluster.py           # CAI cluster manager (CAIClusterManager)
├── launch_cluster.py        # Generic cluster launcher
├── engines/                 # Engine plugin system
│   ├── __init__.py
│   ├── base.py              # Base classes and protocols
│   ├── registry.py          # Engine registry
│   ├── vllm_engine.py       # vLLM implementation
│   └── sglang_engine.py     # SGLang implementation
└── configs/                 # Configuration templates
    ├── local_basic.yaml
    ├── local_cpu.yaml
    ├── local_gpu.yaml
    └── multi_node_example.yaml
```

### cai_integration/ Structure

```
cai_integration/
├── deploy_to_cml.py         # Main orchestrator script
├── setup_environment.py     # Runs as CML job (setup venv)
├── launch_ray_cluster.py    # Runs as CML job (launch cluster)
├── jobs_config.yaml         # Job sequence definitions
├── quick_start.sh           # Interactive setup helper
├── local_test/              # Local testing suite
│   ├── test_project_creation.py
│   ├── run_test.sh
│   ├── README.md
│   ├── TESTING_GUIDE.md
│   └── INDEX.md
├── README.md                # CML deployment guide
└── TROUBLESHOOTING.md       # Common issues
```

---

## Component Responsibilities

### What `ray_serve_cai` Does

**Core Library Responsibilities**:
1. ✅ Orchestrate Ray Serve deployments
2. ✅ Manage LLM engine lifecycle (vLLM, SGLang)
3. ✅ Provide plugin architecture for custom engines
4. ✅ Support multiple cluster types (local, cloud, K8s, CAI)
5. ✅ Handle cluster initialization and connection
6. ✅ Expose OpenAI-compatible API

**What it DOES NOT do**:
- ❌ CML-specific project creation
- ❌ CML job orchestration
- ❌ CML infrastructure setup

### What `cai_integration` Does

**Deployment Responsibilities**:
1. ✅ Create/find CML projects
2. ✅ Manage git cloning
3. ✅ Define job sequences
4. ✅ Create and execute CML jobs
5. ✅ Set up environment on CML
6. ✅ Launch Ray cluster on CAI applications
7. ✅ Provide testing and validation

**What it DOES NOT do**:
- ❌ Generic Ray orchestration (that's ray_serve_cai's job)
- ❌ LLM engine implementation
- ❌ Non-CML deployment

---

## Data Flow

### Local Deployment

```
User Code
  ↓
from ray_serve_cai import RayBackend
  ↓
RayBackend()
  ├─ Initialize Ray (local or existing cluster)
  ├─ Load config
  └─ Deploy model
  ↓
Ray Serve + vLLM/SGLang
  ↓
OpenAI-compatible API
  ↓
curl http://localhost:8000/v1/chat/completions
```

### CML Deployment

```
GitHub (optional)
  ↓
GitHub Actions Workflow
  ↓
cai_integration/deploy_to_cml.py
  ├─ Create CML project (with git clone)
  ├─ Create CML jobs
  └─ Execute jobs in sequence
  ↓
Job 1: Git Sync
  └─ Clone repository to CML project
  ↓
Job 2: Setup Environment
  └─ Create /home/cdsw/.venv
  └─ pip install ray[serve] vllm
  ↓
Job 3: Launch Ray Cluster
  ├─ imports ray_serve_cai
  ├─ Create launcher scripts
  └─ Call CAIClusterManager.start_cluster()
  ↓
CAIClusterManager
  ├─ Create head node application (0 GPUs)
  ├─ Create worker node applications (with GPUs)
  └─ Monitor startup
  ↓
Ray Head Node                Ray Worker Nodes
├─ Ray GCS                  ├─ Ray Worker 1
├─ Ray Dashboard            ├─ Ray Worker 2
└─ Serve Controller         └─ Ray Worker N
  ↓
OpenAI-compatible API
  ↓
curl https://ray-cluster-head/v1/chat/completions
```

---

## Design Decisions

### 1. Why is CAI Support in ray_serve_cai Library?

**Decision**: Keep `CAIClusterManager` in ray_serve_cai

**Rationale**:
- ✅ Users who want Ray Serve + CAI should import: `from ray_serve_cai import CAIClusterManager`
- ✅ Makes CAI a first-class cluster backend
- ✅ Supports library's multi-platform vision
- ✅ Allows future AWS, GCP, K8s backends with same pattern

**Alternative Considered**: Move to cai_integration
- ❌ Would force users to import from deployment package
- ❌ Confuses library vs. deployment
- ❌ Breaks future multi-backend design

### 2. Why Script-Based Approach for CAI?

**Decision**: Use launcher scripts instead of inline commands

**Rationale**:
- ✅ CAI applications require Python scripts as entry points
- ✅ Scripts can properly activate venv
- ✅ Scripts can handle error conditions
- ✅ Scripts can be versioned and tested
- ✅ Proper exit code handling

### 3. Why One-Way Dependencies?

**Decision**: cai_integration depends on ray_serve_cai only

**Rationale**:
- ✅ Allows library usage without deployment infrastructure
- ✅ Enables multi-platform deployment templates
- ✅ Keeps concerns separate
- ✅ Enables PyPI publication of library independently

---

## When to Use Each Component

### Use `ray_serve_cai` When You Want To:
- Deploy Ray Serve applications
- Use LLM inference engines (vLLM, SGLang)
- Run locally for development
- Connect to existing Ray clusters
- Implement custom engines
- Use OpenAI-compatible API

**Example**:
```python
from ray_serve_cai import RayBackend

backend = RayBackend()
await backend.initialize_ray()
await backend.start_model(config, engine='vllm')
```

### Use `cai_integration` When You Want To:
- Deploy to Cloudera Machine Learning (CML)
- Automate project creation
- Orchestrate job sequences
- Test deployment on CML
- Set up infrastructure automatically

**Example**:
```bash
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-key"
export CML_PROJECT_ID="your-project"

python cai_integration/deploy_to_cml.py
```

### Use Both When You Want To:
- Deploy Ray Serve LLM applications to CML
- Use CAI applications as cluster nodes
- Have infrastructure management + application orchestration
- Production CML deployments

**Example**: Full CML deployment with automatic infrastructure

---

## Future Architecture

### Current State (1.0)
```
ray_serve_cai (library)
├─ vLLM engine
├─ SGLang engine
├─ CAI cluster backend
└─ Local cluster support

cai_integration (CML deployment template)
└─ Job-based orchestration for CML
```

### Vision (2.0)
```
ray_serve_llm (library - renamed)
├─ Multiple engines (vLLM, SGLang, Ollama, etc.)
├─ Cluster backends abstraction
│  ├─ Local backend
│  ├─ CAI backend
│  ├─ AWS backend
│  ├─ GCP backend
│  └─ Kubernetes backend
└─ Production monitoring

Deployment Templates
├─ cai_integration (CML)
├─ aws_integration (AWS)
├─ gcp_integration (GCP)
└─ kubernetes_integration (K8s)
```

---

## Testing Architecture

```
tests/
├── unit/                    # Library unit tests (future)
├── integration/
│   └── test_cai_deployment.py    # CAI integration tests
└── e2e/
    └── cai_integration/local_test/  # End-to-end tests

cai_integration/local_test/
├── test_project_creation.py  # Tests project creation
├── run_test.sh              # Test runner
└── Documentation
```

---

## Configuration Flow

```
User YAML Config
  ↓
ray_serve_cai.RayBackend
  ├─ Load config
  ├─ Initialize Ray
  ├─ Select engine
  └─ Start deployment
  ↓
Engine-Specific Config Builder
  ├─ Transform user config
  └─ Create engine config
  ↓
Engine Deployment
  ├─ vLLM runtime, OR
  ├─ SGLang runtime, OR
  └─ Custom runtime
  ↓
Ray Serve deployment
  └─ OpenAI-compatible API
```

---

## Summary

| Aspect | ray_serve_cai | cai_integration |
|--------|---------------|-----------------|
| **Purpose** | Generic Ray Serve orchestration | CML deployment automation |
| **Audience** | Data scientists, ML engineers | DevOps, infra engineers |
| **Scope** | Universal (any Ray cluster) | CML-specific |
| **Distribution** | Can be published to PyPI | Stays in repository |
| **Dependency** | None (except Ray, engines) | Depends on ray_serve_cai |
| **Reusability** | Very high (any platform) | CML-only |
| **Testing** | Unit + integration | Integration + e2e |
| **Maintenance** | Evolves with Ray/engines | Evolves with CML/CAI |

---

## Next Steps

For users, see:
- **Library Users**: [docs/QUICKSTART.md](QUICKSTART.md)
- **CML Users**: [cai_integration/README.md](../cai_integration/README.md)
- **Developers**: [docs/DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)

For architecture decisions, see:
- **Structure Review**: [STRUCTURE_REVIEW.md](../STRUCTURE_REVIEW.md)
- **Architecture Diagrams**: [ARCHITECTURE_DIAGRAM.md](../ARCHITECTURE_DIAGRAM.md)
