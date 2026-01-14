# Ray Serve CAI Testing Suite

Comprehensive tests for Ray cluster deployment on Cloudera Machine Learning (CML).

## Test Files

### 1. `test_e2e_deployment.py` - End-to-End Test

Tests the complete workflow: project creation → Ray cluster deployment

**Run:**
```bash
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export GITHUB_REPOSITORY="owner/ray-serve-cai"  # Optional

python test_e2e_deployment.py
```

**What it tests:**
- CML connectivity
- Project creation/discovery
- Git repository cloning
- Ray cluster deployment on CAI applications
- Cluster status verification
- Cleanup

### 2. `test_cluster_deployment.py` - Cluster-Only Test

Tests cluster deployment on an existing project

**Run:**
```bash
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export CML_PROJECT_ID="your-project-id"

python test_cluster_deployment.py
```

**What it tests:**
- CML connectivity with specific project
- Ray cluster creation (head + workers)
- Cluster status verification
- Cleanup

### 3. `run_test.sh` - Shell Wrapper

Helper script for running tests

**Usage:**
```bash
./run_test.sh --e2e          # Run end-to-end test
./run_test.sh --cluster       # Run cluster test
./run_test.sh --e2e --workers 2  # E2E with 2 workers
```

## Configuration Options

Both tests support these options:

```
--workers N          Number of worker nodes (default: 1)
--cpu N              CPU cores per worker (default: 4)
--memory N           Memory GB per worker (default: 16)
--gpu N              GPUs per worker (default: 0)
--head-cpu N         CPU cores for head (default: 2)
--head-memory N      Memory GB for head (default: 8)
--no-cleanup         Leave cluster running
--no-wait            Skip wait time before cleanup
--wait-time N        Seconds to wait (default: 30)
```

## Quick Examples

### End-to-End Test (Complete Workflow)
```bash
python test_e2e_deployment.py
```

### Cluster Test with 2 Workers
```bash
python test_cluster_deployment.py --workers 2 --gpu 1
```

### Quick Smoke Test
```bash
python test_e2e_deployment.py --no-wait
```

### Leave Cluster Running
```bash
python test_cluster_deployment.py --no-cleanup
```

## Prerequisites

1. **CML Access**: Valid CML instance with API credentials
2. **Python Path**: ray-serve-cai must be importable
3. **Caikit** (optional): For advanced cluster management
   ```bash
   export PYTHONPATH=/path/to/caikit:$PYTHONPATH
   ```

## For Detailed Information

See [TESTING_GUIDE.md](TESTING_GUIDE.md) for comprehensive documentation including:
- Prerequisites and setup
- Test scenarios and workflows
- Troubleshooting guide
- Output interpretation
- Manual cleanup procedures

## Related Files

- [TESTING_GUIDE.md](TESTING_GUIDE.md) - Complete testing guide
- [../docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) - Project structure
- [../docs/cai_cluster_guide.md](../docs/cai_cluster_guide.md) - Cluster documentation
- [../cai_integration/README.md](../cai_integration/README.md) - Production deployment
