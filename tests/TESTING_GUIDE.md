# Ray Serve CAI Testing Guide

Complete testing guide for Ray cluster deployment on Cloudera Machine Learning (CML).

## Overview

The testing suite provides comprehensive validation of CAI cluster deployment:

- **`test_e2e_deployment.py`** - Complete end-to-end test (project creation → cluster deployment)
- **`test_cluster_deployment.py`** - Focused cluster deployment test (for existing projects)
- **`run_test.sh`** - Helper script for easy test execution

## Prerequisites

### 1. CML Access

You need:
- Access to a CML instance
- A project with sufficient resource quota
- API key with permissions to create applications

### 2. Get Your Credentials

**CML Host:**
Your CML instance URL, e.g., `https://ml.example.cloudera.site`

**API Key:**
1. Log into your CML instance
2. Go to User Settings → API Keys
3. Create a new API key
4. Copy the key (you won't see it again!)

**GitHub Repository (Optional):**
For end-to-end tests that create new projects:
- Set `GITHUB_REPOSITORY` (e.g., `owner/ray-serve-cai`)
- Optionally set `GH_PAT` for private repos

**Project ID (For cluster-only tests):**
1. Navigate to your project in CML
2. Look at the URL: `https://ml.example.com/projects/{PROJECT_ID}`
3. Or use the API to list projects

### 3. Install Dependencies

```bash
# Navigate to project
cd /path/to/ray-serve-cai

# Ensure caikit is available (if using caikit)
export PYTHONPATH=/path/to/caikit:$PYTHONPATH

# Or install caikit
pip install /path/to/caikit
```

## Test Types

### 1. End-to-End Deployment Test

**File:** `tests/test_e2e_deployment.py`

**What it does:**
- Creates or finds a project
- Clones git repository (if specified)
- Deploys Ray cluster on CAI applications
- Verifies cluster is running
- Cleans up resources

**When to use:**
- Testing complete deployment workflow
- Validating project setup and cluster together
- First-time setup verification

**Run it:**

```bash
# Set environment variables
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export GITHUB_REPOSITORY="owner/ray-serve-cai"  # Optional

# Run the test
python tests/test_e2e_deployment.py

# Or use the shell wrapper
bash tests/run_test.sh --e2e

# With options
python tests/test_e2e_deployment.py --workers 2 --gpu 1 --no-wait
```

**Configuration options:**

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

### 2. Cluster Deployment Test (Existing Project)

**File:** `tests/test_cluster_deployment.py`

**What it does:**
- Uses an existing project
- Deploys Ray cluster on CAI applications
- Verifies cluster is running
- Checks status
- Cleans up resources

**When to use:**
- Testing cluster deployment in isolation
- Testing on existing projects
- Debugging cluster-specific issues

**Run it:**

```bash
# Set environment variables
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export CML_PROJECT_ID="your-project-id"

# Run the test
python tests/test_cluster_deployment.py

# Or use the shell wrapper
bash tests/run_test.sh --cluster

# With options
python tests/test_cluster_deployment.py --workers 2 --gpu 1
```

**Configuration options:**

Same as E2E test (see above)

## Quick Start Examples

### Example 1: Complete E2E Test

```bash
# Set credentials
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export GITHUB_REPOSITORY="cloudera/ray-serve-cai"

# Run full end-to-end test
python tests/test_e2e_deployment.py

# Expected output:
# 🚀 End-to-End CML Deployment Test
# ======================================================================
# 1️⃣  Testing CML Connection
# ✅ CML connection successful
#
# 2️⃣  Project Setup
# ✅ Project created: project-abc123
#
# 3️⃣  Waiting for Git Clone
# ✅ Git clone complete
#
# ... (cluster creation) ...
#
# ✅ End-to-end test completed successfully!
```

### Example 2: Cluster Test on Existing Project

```bash
# Set credentials
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export CML_PROJECT_ID="my-project-id"

# Run cluster deployment test
python tests/test_cluster_deployment.py --workers 2 --gpu 1

# Expected output:
# 🧪 CAI Cluster Deployment Test
# 1️⃣  Testing CAI Connection
# ✅ Connected to CML successfully
#
# 2️⃣  Testing Cluster Creation
# ✅ Cluster created successfully!
#
# ... (status check) ...
#
# ✅ All tests completed!
```

### Example 3: Quick Smoke Test

```bash
# Minimal resources, quick test, auto-cleanup
python tests/test_e2e_deployment.py --no-wait

# Or with just cluster test
export CML_PROJECT_ID="existing-project"
python tests/test_cluster_deployment.py --cpu 2 --memory 8 --no-wait
```

### Example 4: Production-Like Test

```bash
# Multi-worker with GPUs, leave running for inspection
python tests/test_e2e_deployment.py \
  --workers 2 \
  --cpu 16 \
  --memory 64 \
  --gpu 1 \
  --head-cpu 8 \
  --head-memory 32 \
  --no-cleanup \
  --wait-time 120
```

## Using the Shell Wrapper

The `run_test.sh` script simplifies running tests:

```bash
# Show help
./run_test.sh --help

# Run E2E test
./run_test.sh --e2e

# Run cluster test
./run_test.sh --cluster

# Run with workers
./run_test.sh --e2e --workers 2

# Run quick test
./run_test.sh --e2e --no-wait
```

## Test Scenarios

### Scenario 1: First-Time Setup

```bash
# Goal: Validate complete workflow from scratch
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export GITHUB_REPOSITORY="owner/ray-serve-cai"

# Run E2E test
python tests/test_e2e_deployment.py --workers 1 --no-wait
```

**Expected flow:**
1. Creates new project
2. Clones repository
3. Deploys cluster
4. Verifies deployment
5. Cleans up

### Scenario 2: Development Testing

```bash
# Goal: Test on existing project, leave cluster running
export CML_PROJECT_ID="my-dev-project"

# Run cluster test without cleanup
python tests/test_cluster_deployment.py --no-cleanup
```

**Next steps:**
1. Check CML UI to see applications
2. Verify Ray dashboard is accessible
3. Test your workloads
4. Manually delete applications from CML UI

### Scenario 3: Production Validation

```bash
# Goal: Full test with realistic resources
python tests/test_e2e_deployment.py \
  --workers 2 \
  --cpu 16 \
  --memory 64 \
  --gpu 1 \
  --head-cpu 8 \
  --head-memory 32
```

**Validates:**
1. Project creation
2. Multi-node cluster creation
3. GPU allocation
4. Cluster cleanup

## Troubleshooting

### Error: Missing Environment Variables

```
❌ Error: Missing required environment variables
   Required: CML_HOST, CML_API_KEY
```

**Solution:** Set all required environment variables:
```bash
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
```

### Error: CML Connection Failed

```
❌ CML connection failed
```

**Solutions:**
1. Check CML_HOST is correct and accessible
2. Verify API key is valid
3. Test connectivity manually:
   ```bash
   curl -H "Authorization: Bearer $CML_API_KEY" \
     "$CML_HOST/api/v2/projects?page_size=1"
   ```

### Error: Git Clone Timeout

```
❌ Timeout waiting for git clone
```

**Solutions:**
1. Check GitHub token for private repos
2. Verify repository is accessible
3. Increase timeout in code if needed
4. Check CML UI for actual status

### Error: Insufficient Resources

```
❌ Failed to create application
```

**Solutions:**
1. Check CML project quotas
2. Reduce resource requirements:
   ```bash
   python tests/test_e2e_deployment.py --cpu 2 --memory 8
   ```
3. Increase project quota in CML UI

### Error: Timeout Waiting for Nodes

```
⏰ Timeout waiting for application
```

**Solutions:**
1. CML may be slow; wait longer
2. Check CML UI for actual status
3. Verify runtime image exists
4. Check CML logs for errors

### Error: Import caikit Failed

```
❌ Failed to import caikit library
```

**Solutions:**
1. Add caikit to PYTHONPATH:
   ```bash
   export PYTHONPATH=/path/to/caikit:$PYTHONPATH
   ```
2. Or install caikit:
   ```bash
   pip install /path/to/caikit
   ```

## Test Output Interpretation

### Phase 1: Project Setup (E2E Only)

```
PHASE 1: PROJECT SETUP
======================================================================
1️⃣  Testing CML Connection
✅ CML connection successful

2️⃣  Project Setup
✅ Found existing project: project-abc123

3️⃣  Waiting for Git Clone
✅ Git clone complete
```

**Success indicators:**
- ✅ Connection successful
- Project ID found or created
- Git clone completed

### Phase 2: Ray Cluster Deployment

```
PHASE 2: RAY CLUSTER DEPLOYMENT
======================================================================
1️⃣  Initializing Cluster Manager
✅ Manager initialized

2️⃣  Creating Launcher Scripts
📄 Created launcher scripts

3️⃣  Starting Ray Cluster
✅ Cluster created successfully!

4️⃣  Verifying Cluster Status
✅ Running: True
   Head Node: running
   Worker Nodes: 1 running

5️⃣  Cluster Cleanup
✅ Cluster cleaned up successfully
```

**Success indicators:**
- ✅ All phases completed
- Cluster created with correct nodes
- Status shows running
- Cleanup successful (if not --no-cleanup)

## Manual Cleanup

If a test fails to clean up:

1. **Log into CML UI**
2. **Navigate to Applications**
3. **Find and delete:**
   - `ray-serve-cluster-head` (head node)
   - `ray-serve-cluster-worker-*` (worker nodes)

## Next Steps

After successful testing:

1. ✅ Verify tests pass consistently
2. ✅ Check CML UI to confirm cluster behavior
3. ✅ Run with your production configuration
4. ✅ Deploy actual workloads to cluster

## See Also

- [Architecture Guide](../docs/ARCHITECTURE.md) - Project structure and design
- [CAI Cluster Guide](../docs/cai_cluster_guide.md) - Detailed cluster documentation
- [CML Deployment Guide](../cai_integration/README.md) - Production deployment guide
- [Main README](../README.md) - Project overview

## Support

For issues:

1. Check test output for error messages
2. Verify all environment variables are set
3. Check CML UI for cluster status
4. Review CML logs for infrastructure errors
5. Check GitHub connectivity (for private repos)
