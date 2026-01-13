# Ray-Serve-CAI Extraction Plan

## Overview
Extract the vllm_playground/ray_serve module as a standalone, production-ready package called **ray-serve-cai** for hosting Ray Clusters in Cloudera AI using the cmlkit package ecosystem.

**User Decisions:**
- Project name: `ray-serve-cai`
- Scope: Generic LLM support (vLLM + future engines)
- Repository: Create new local directory
- Distribution: PyPI-ready, production-ready

---

## Current State Analysis

### What We Have
- **Self-contained module** at `vllm_playground/ray_serve/`
- **Zero internal dependencies** (no imports from other vllm_playground modules)
- **Well-architected** with clear separation of concerns
  - `ray_backend.py` - Orchestrator (562 lines)
  - `engines/vllm_engine.py` - vLLM deployment (318 lines)
  - `engines/vllm_config.py` - Configuration builder (146 lines)
  - `launch_cluster.py` - Cluster CLI launcher (412 lines)
- **Comprehensive documentation** (README, CLUSTER_SETUP, QUICKSTART, INSTALLATION)
- **YAML configuration templates** for local/multi-node clusters
- **Optional dependency** in main project (good decoupling)

### Key Features to Preserve
- Auto-detect/connect to Ray clusters
- OpenAI-compatible API endpoints
- Tensor parallelism with placement groups
- Health checks and monitoring
- YAML-based cluster configuration
- Async/await interface

---

## Architecture: Generic Engine Support

### Design Pattern
**Plugin-based architecture** with three core abstractions:

1. **LLMEngineProtocol** - Interface all engines must implement
   - Async HTTP endpoints (completions, chat, models, health)
   - Engine metadata (type, model name)
   - Request routing

2. **ConfigBuilderProtocol** - Configuration handling per engine
   - Translate user config → engine-specific args
   - Validate configuration
   - Provide defaults

3. **DeploymentFactoryProtocol** - Ray Serve deployment creation
   - Create Ray deployments with proper resource allocation
   - Handle placement groups for tensor parallelism
   - Engine-specific deployment options

4. **EngineRegistry** - Central management
   - Register/unregister engines
   - Factory pattern for instantiation
   - List available engines
   - Error handling with clear messages

### Backward Compatibility
- All existing vLLM code continues to work
- Old function signatures delegated to new classes
- vLLM is default engine
- Zero breaking changes to public API

---

## Implementation Phases

### Phase 1: Foundation (Files to Create)
**Goal:** Build generic engine abstraction layer

**New Files:**
1. `engines/base.py`
   - `LLMEngineProtocol` - All engines implement
   - `ConfigBuilderProtocol` - Config handling interface
   - `DeploymentFactoryProtocol` - Deployment creation interface

2. `engines/registry.py`
   - `EngineRegistry` class - Central registry
   - `EngineInfo` - Container for engine components
   - `register_engine()` - Registration function
   - `get_registry()` - Access global registry

**Modified Files:**
1. `engines/vllm_config.py`
   - Wrap `build_vllm_engine_config()` in `VLLMConfigBuilder` class
   - Wrap config validation in `VLLMConfigBuilder.validate_config()`
   - Add `VLLMConfigBuilder.get_default_config()`
   - Create `VLLMDeploymentFactory` class
   - Keep legacy functions for backward compatibility

2. `engines/vllm_engine.py`
   - Add `@property engine_type()` → returns "vllm"
   - Add `@property model_name()` → returns loaded model
   - Verify all protocol methods present (most already are)

3. `engines/__init__.py`
   - Import and register vLLM engine on module load
   - Export protocols and registry
   - Lazy-import SGLang (optional, don't fail if missing)

### Phase 2: Integration (Files to Modify)
**Goal:** Wire up generic orchestrator

**Modified Files:**
1. `ray_backend.py`
   - Import `get_registry()` in `start_model()`
   - Add `engine` parameter to `start_model(config, engine=None)`
   - Engine detection: explicit param → config field → backend default → registry default
   - Use registry to: validate config, build config, create deployment
   - Preserve existing method signatures
   - Update logging with engine info

### Phase 3: Engine Support (Files to Create)
**Goal:** Framework ready for additional engines

**New Files (Skeleton Only):**
1. `engines/sglang_engine.py`
   - `SGLangEngine` class implementing `LLMEngineProtocol`
   - Basic structure (full implementation deferred)

2. `engines/sglang_config.py`
   - `SGLangConfigBuilder` implementing `ConfigBuilderProtocol`
   - `SGLangDeploymentFactory` implementing `DeploymentFactoryProtocol`
   - Basic structure (full implementation deferred)

### Phase 4: Project Extraction (New Repository)
**Goal:** Create standalone PyPI package

**New Directory Structure:**
```
~/Projects/ray-serve-cai/
├── pyproject.toml              (PyPI metadata)
├── README.md                   (Project README)
├── CHANGELOG.md                (Version history)
├── LICENSE                     (Apache 2.0)
├── .github/workflows/          (CI/CD)
├── ray_serve_cai/
│   ├── __init__.py
│   ├── ray_backend.py
│   ├── launch_cluster.py
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   ├── vllm_engine.py
│   │   ├── vllm_config.py
│   │   ├── sglang_engine.py
│   │   └── sglang_config.py
│   └── configs/
│       ├── local_basic.yaml
│       ├── local_cpu.yaml
│       ├── local_gpu.yaml
│       ├── multi_node_example.yaml
│       └── cloudera_ai.yaml    (NEW)
├── docs/
│   ├── README.md               (from ray_serve/README.md)
│   ├── CLUSTER_SETUP.md
│   ├── INSTALLATION.md
│   ├── QUICKSTART.md
│   ├── API.md                  (NEW - engine registry API)
│   ├── DEVELOPER_GUIDE.md      (NEW - adding engines)
│   └── architecture.md         (NEW - design docs)
├── examples/
│   ├── basic_vllm.py
│   ├── sglang_deployment.py    (NEW - future)
│   └── engine_selection.py     (NEW)
└── tests/
    ├── test_registry.py        (NEW)
    ├── test_engine_protocols.py (NEW)
    ├── test_vllm_config.py
    └── test_ray_backend.py
```

**Files to Copy (from vllm_playground/ray_serve/):**
- `ray_backend.py` → with modifications
- `launch_cluster.py` → as-is
- `engines/__init__.py` → with modifications
- `engines/vllm_engine.py` → with modifications
- `engines/vllm_config.py` → with modifications
- `configs/*.yaml` → as-is + new Cloudera config

**Files to Create:**
- `pyproject.toml` - Package metadata
- `README.md` - Project README
- `CHANGELOG.md` - Version history
- `.github/workflows/` - CI/CD pipelines

### Phase 5: Package Configuration
**Goal:** Production-ready PyPI package

**pyproject.toml:**
```toml
[project]
name = "ray-serve-cai"
version = "0.1.0"
description = "Ray Serve orchestration for LLM inference in Cloudera AI"
dependencies = [
    "ray[serve,cgraph]>=2.48.0",
    "pyyaml>=6.0",
    "aiohttp>=3.9.0",
    "vllm>=0.6.0",  # Required for vLLM engine
]
optional-dependencies = {
    "sglang": ["sglang>=0.1.0"],  # Optional SGLang support
    "dev": ["pytest>=7.0", "pytest-asyncio>=0.21"],
}
```

---

## Code Changes Summary

### Modified Imports
All imports remain INSIDE the modules - no external package name changes needed in ray_backend.py or vllm_engine.py. Only when extracted will package name change from `vllm_playground.ray_serve` → `ray_serve_cai`.

### Key Changes by File

| File | Change Type | What Changed |
|------|------------|--------------|
| `engines/base.py` | NEW | Protocol definitions |
| `engines/registry.py` | NEW | Engine management |
| `engines/__init__.py` | MODIFY | Add registration |
| `engines/vllm_config.py` | MODIFY | Wrap in classes, keep legacy |
| `engines/vllm_engine.py` | MODIFY | Add protocol properties |
| `ray_backend.py` | MODIFY | Use registry for engine selection |
| `launch_cluster.py` | MINIMAL | No changes needed |

### Backward Compatibility Matrix

| Usage | Before | After | Status |
|-------|--------|-------|--------|
| `from ...ray_serve import ray_backend` | ✓ Works | ✓ Works | No change |
| `await ray_backend.start_model(config)` | ✓ Works | ✓ Works | No change |
| `build_vllm_engine_config()` import | ✓ Works | ✓ Works | Legacy wrapper |
| `await ray_backend.start_model(config, engine='sglang')` | ✗ N/A | ✓ Works | NEW feature |

---

## Deliverables

### Upon Completion
1. ✅ Refactored vllm_playground/ray_serve with generic engine support
2. ✅ New standalone repository: ~/Projects/ray-serve-cai/
3. ✅ Git initialized with initial commit
4. ✅ PyPI-ready package configuration
5. ✅ Comprehensive documentation (API, developer guide, examples)
6. ✅ SGLang engine skeleton for future implementation
7. ✅ Cloudera AI-specific configuration templates
8. ✅ CI/CD pipeline setup (.github/workflows)
9. ✅ All tests passing
10. ✅ Ready for PyPI publication

### Not Included (Deferred)
- Full SGLang implementation (skeleton only)
- PyPI publication (ready but not published)
- Comprehensive integration tests (basic tests included)

---

## File Dependency Graph

```
ray_backend.py
├── engines/registry.py
│   └── engines/base.py
├── engines/vllm_engine.py
│   └── engines/vllm_config.py
└── launch_cluster.py (independent)

engines/__init__.py
├── engines/registry.py
├── engines/base.py
├── engines/vllm_engine.py
├── engines/vllm_config.py
└── engines/sglang_*.py (optional, fail gracefully)
```

---

## Success Criteria

- [ ] All existing vLLM functionality preserved (zero breaking changes)
- [ ] Engine registry fully functional with vLLM registered
- [ ] Configuration builder works with new class-based approach
- [ ] ray_backend accepts `engine` parameter and uses it correctly
- [ ] All tests pass (existing + new registry tests)
- [ ] SGLang skeleton ready for implementation
- [ ] New repository created and initialized with git
- [ ] PyPI configuration complete (ready to publish)
- [ ] Documentation comprehensive and clear
- [ ] No imports of vllm_playground outside ray_serve module
