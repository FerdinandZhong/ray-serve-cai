# Changelog

All notable changes to ray-serve-cai will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Full SGLang engine implementation
- LoRA adapter support
- Quantization support (FP8, INT8, AWQ)
- Streaming response support
- Request queuing and prioritization
- Comprehensive metrics and monitoring dashboard
- Kubernetes deployment templates
- Multi-model serving
- A/B testing support

## [0.1.0] - 2026-01-13

### Added
- Initial release of ray-serve-cai
- Multi-engine plugin architecture with protocol-based design
- Full vLLM engine support with OpenAI-compatible API
- SGLang engine skeleton (placeholder for future implementation)
- Engine registry for managing multiple LLM engines
- Automatic Ray cluster detection and management
- Tensor parallelism support with placement groups
- YAML-based configuration system
- Health monitoring and deployment status endpoints
- Comprehensive logging and error handling
- CLI tool for launching clusters from YAML configs
- Production-ready deployment lifecycle management
- Support for local, multi-node, and Cloudera AI deployments

### Documentation
- Quickstart guide
- Installation instructions
- Cluster setup guide
- API reference
- Developer guide for adding custom engines
- Architecture documentation
- Example configurations

### Engine Support
- ✅ vLLM v0.6.0+ (fully supported)
- 🚧 SGLang (skeleton only, coming soon)

### Features
- Auto-detect existing Ray clusters
- Connect to remote Ray clusters
- Start local Ray clusters on demand
- OpenAI-compatible `/v1/completions` endpoint
- OpenAI-compatible `/v1/chat/completions` endpoint
- Model listing via `/v1/models`
- Health check endpoint `/health`
- Async/await interface for all operations
- Graceful deployment shutdown
- Configuration validation
- Multi-GPU tensor parallelism
- CPU-only mode support
- Customizable GPU memory utilization
- HuggingFace model loading
- Trust remote code option
- Custom chat templates
- Prefix caching support (vLLM)

### Developer Experience
- Type hints throughout codebase
- Protocol-based extensibility
- Clear separation of concerns
- Comprehensive docstrings
- Example code and configurations
- Testing utilities
- CI/CD pipeline setup

### Known Limitations
- SGLang engine not yet implemented (skeleton only)
- No streaming response support yet
- No LoRA adapter support yet
- No built-in quantization support yet
- Limited metrics and monitoring
- No request queuing yet

### Breaking Changes
- N/A (initial release)

### Migration Guide
- N/A (initial release)

## Release Notes Format

### Types of Changes
- `Added` for new features
- `Changed` for changes in existing functionality
- `Deprecated` for soon-to-be removed features
- `Removed` for now removed features
- `Fixed` for any bug fixes
- `Security` for vulnerability fixes

---

## Future Versions

### [0.2.0] - Planned
**Focus: SGLang Engine Support**
- Full SGLang engine implementation
- RadixAttention support
- Structured generation support
- Multi-LoRA support
- Chunked prefill

### [0.3.0] - Planned
**Focus: Production Features**
- Streaming responses
- Request queuing
- Priority scheduling
- Comprehensive metrics
- Prometheus integration
- Grafana dashboards

### [0.4.0] - Planned
**Focus: Advanced Features**
- Quantization support (FP8, INT8, AWQ)
- LoRA adapters
- Multi-model serving
- A/B testing support
- Dynamic batching optimization

### [1.0.0] - Planned
**Focus: Stability & Enterprise**
- Production stability guarantees
- Enterprise support options
- Complete test coverage
- Performance benchmarks
- Kubernetes operators
- Helm charts
- CI/CD templates

---

For more details on each release, see the [GitHub Releases](https://github.com/cloudera/ray-serve-cai/releases) page.
