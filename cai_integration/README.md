# Ray Cluster Deployment on CML

This directory contains scripts and configurations for deploying Ray clusters on Cloudera Machine Learning (CML) using CAI (Cloudera Applications) infrastructure.

## Overview

The deployment system provides:

1. **Automated Project Creation** - Creates CML projects with git repository cloning
2. **Environment Setup** - Installs Ray and dependencies via Python virtual environment
3. **Cluster Launch** - Deploys head and worker node applications
4. **Job Orchestration** - Manages job dependencies and sequencing
5. **CI/CD Integration** - GitHub Actions workflows for automated deployment

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│           GitHub Actions Workflow                            │
│           (deploy-ray-cluster.yml)                           │
└──┬───────────────┬──────────────────┬──────────────────────┘
   │               │                  │
   ▼               ▼                  ▼
┌─────────┐  ┌──────────┐  ┌──────────────────────┐
│ Setup   │  │ Create   │  │ Trigger              │
│ Project │─▶│ Jobs     │─▶│ Deployment           │
└─────────┘  └──────────┘  └──────────────────────┘
     │            │               │
     │            │               ▼
     │            │         ┌──────────┬──────────┬──────────┐
     │            │         │ Git Sync │  Setup   │ Launch   │
     │            └────────▶│ Job      │─▶Env Job │─▶Cluster │
     │                      └──────────┘  └────────┘ └────────┘
     └────────────────────────────────────────────────┘
                         Creates CML Jobs
```

## Components

### 1. Jobs Configuration (`jobs_config.yaml`)

Defines the job sequence:
- **git_sync**: Clones/syncs repository
- **setup_environment**: Installs Ray and dependencies
- **launch_ray_cluster**: Deploys Ray cluster

### 2. Setup Environment Script (`setup_environment.py`)

Runs as a CML job to:
- Create Python virtual environment at `/home/cdsw/.venv`
- Install Ray and dependencies
- Verify Ray installation

### 3. CAI Job Entry Points

#### Python Wrappers (For CAI Jobs)

These scripts serve as CAI job entry points and call bash wrappers via subprocess:

- **`launch_ray_cluster_job.py`** - Entry point for Ray cluster launch job
  - Python script (required by CAI)
  - Calls `./launch_ray_cluster.sh` via subprocess
  - Bash wrapper activates venv and runs `launch_ray_cluster.py`
  - Used in `jobs_config.yaml` for job execution

- **`test_cluster_deployment_job.py`** - Entry point for cluster test job
  - Python script (required by CAI)
  - Calls `./test_cluster.sh` via subprocess
  - Bash wrapper activates venv and runs `test_cluster_deployment.py`

#### Core Scripts (Application Logic)

- **`launch_ray_cluster.py`** - Ray cluster launch logic
  - Runs inside bash wrapper (activated venv)
  - Loads cluster configuration
  - Creates Ray head node application
  - Creates Ray worker node applications
  - Monitors startup and saves connection info

- **`test_cluster_deployment.py`** - Cluster testing logic
  - Runs inside bash wrapper (activated venv)
  - Tests cluster deployment capabilities

### 4. Bash Wrapper Scripts (`./`)

These scripts handle virtual environment activation:

- **`launch_ray_cluster.sh`** - Activates venv and calls `launch_ray_cluster.py`
  - Can be used manually: `bash cai_integration/launch_ray_cluster.sh`
  - Called from `launch_ray_cluster_job.py` for CAI jobs

- **`test_cluster.sh`** - Activates venv and calls `test_cluster_deployment.py`
  - Can be used manually: `bash cai_integration/test_cluster.sh`
  - Called from `test_cluster_deployment_job.py` for CAI jobs

**Why this architecture?**
- **CAI Requirement**: CAI jobs must use Python entry points (not bash scripts directly)
- **Clean venv Activation**: Bash wrapper handles environment activation cleanly
- **Follows Best Practices**: Matches patterns used in CAI_AMP_Synthetic_Data_Studio
- **Flexibility**: Bash wrappers can be called directly for manual testing

### 5. Script Execution Flow

**For CAI Jobs (via jobs_config.yaml):**
```
CAI Job Execution
  ↓
Python Entry Point (cai_integration/launch_ray_cluster_job.py)
  ↓
subprocess.run(["bash", "cai_integration/launch_ray_cluster.sh"])
  ↓
Bash Wrapper Script (cai_integration/launch_ray_cluster.sh)
  ├─ Auto-detect project root
  ├─ Activate venv (.venv/bin/activate)
  └─ Run Python Script
      ↓
    launch_ray_cluster.py (Application Logic)
```

**For Manual Execution:**
```
Option 1: Via Bash Wrapper
  bash cai_integration/launch_ray_cluster.sh
  ├─ Activate venv
  └─ Run launch_ray_cluster.py

Option 2: Direct Python (if venv already activated)
  python cai_integration/launch_ray_cluster.py

Option 3: Via Python Wrapper (simulates CAI)
  python cai_integration/launch_ray_cluster_job.py
```

### 6. Deployment Scripts

The deployment is broken into three focused scripts:

**`setup_project.py`** - Project initialization:
- Creates/discovers CML project
- Waits for git repository cloning
- Outputs project ID for subsequent jobs

**`create_jobs.py`** - Job management:
- Creates or updates CML jobs from `jobs_config.yaml`
- Sets up job dependencies
- Outputs job IDs for execution

**`trigger_jobs.py`** - Job execution:
- Triggers jobs in sequence
- Monitors job completion with status updates
- Handles idempotency (skips already-successful jobs)

### 5. GitHub Actions Workflow (`.github/workflows/deploy-ray-cluster.yml`)

Automates deployment with sequential jobs:
- **setup-project**: Creates CML project and waits for git clone
- **create-jobs**: Creates/updates CML job definitions
- **trigger-deployment**: Executes jobs in sequence
- **verify-deployment**: Runs test suite to verify cluster

## Setup Instructions

### Prerequisites

1. **CML Instance Access**
   - CML host URL
   - API key with project creation permissions

2. **GitHub Repository**
   - Repository with ray-serve-cai code
   - GitHub token for private repos (optional)

3. **Required Secrets** (for GitHub Actions)
   - `CML_HOST`: Your CML instance URL
   - `CML_API_KEY`: CML API authentication key
   - `GITHUB_TOKEN`: GitHub token (auto-provided by Actions)

### Local Deployment

#### Option 1: Using Deployment Scripts

```bash
# Set environment variables
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export GITHUB_REPOSITORY="owner/ray-serve-cai"

# Step 1: Setup project
python cai_integration/setup_project.py
PROJECT_ID=$(cat /tmp/project_id.txt)

# Step 2: Create jobs
python cai_integration/create_jobs.py --project-id $PROJECT_ID

# Step 3: Trigger deployment
python cai_integration/trigger_jobs.py \
  --project-id $PROJECT_ID \
  --jobs-config cai_integration/jobs_config.yaml
```

#### Option 2: Manual Job Execution

**Simulating CAI Job Execution (Recommended for Testing):**
```bash
# 1. Create project manually in CML UI
# 2. Clone repository
git clone https://github.com/owner/ray-serve-cai.git
cd ray-serve-cai

# 3. Run setup job
python cai_integration/setup_environment.py

# 4. Run cluster launch job using Python wrapper (simulates CAI)
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export CDSW_PROJECT_ID="your-project-id"
python cai_integration/launch_ray_cluster_job.py
```

**Alternative: Via Bash Wrapper (if testing manually):**
```bash
bash cai_integration/launch_ray_cluster.sh
```

**Direct Python (if venv is already activated):**
```bash
python cai_integration/launch_ray_cluster.py
```

### GitHub Actions Deployment

#### 1. Configure Secrets

Add to your repository settings:

```
Settings > Secrets and variables > Actions

CML_HOST: https://ml.example.cloudera.site
CML_API_KEY: your-api-key
```

#### 2. Trigger Deployment

**Automatic (on push to main):**
```bash
git push origin main
```

**Manual (workflow_dispatch):**
- Go to Actions > Deploy Ray Cluster to CML
- Click "Run workflow"
- Optionally set "Force rebuild" = true

#### 3. Monitor Deployment

- Check GitHub Actions logs
- Monitor CML project jobs in CML UI
- View cluster info: `/home/cdsw/ray_cluster_info.json`

## Configuration

### Ray Cluster Configuration (`../ray_cluster_config.yaml`)

Customize cluster resources:

```yaml
ray_cluster:
  num_workers: 1              # Number of worker nodes
  head_cpu: 4                 # Head node CPU cores
  head_memory: 16             # Head node memory (GB)
  worker_cpu: 8               # Worker CPU cores
  worker_memory: 32           # Worker memory (GB)
  worker_gpus: 1              # Worker GPUs (0 for CPU-only)
  ray_port: 6379              # Ray daemon port
  dashboard_port: 8265        # Ray dashboard port
```

### Environment Variables

Set in GitHub Actions secrets or local environment:

```bash
# CML Configuration
CML_HOST="https://ml.example.cloudera.site"
CML_API_KEY="your-api-key"
CDSW_PROJECT_ID="project-id"  # For manual jobs

# Git Configuration (optional)
GITHUB_REPOSITORY="owner/repo"
GH_PAT="github-token"

# Deployment Options
FORCE_REBUILD="false"  # Set to "true" to skip cache

# Ray Configuration (overrides config file)
RAY_NUM_WORKERS=2
RAY_WORKER_GPUS=1
RUNTIME_IDENTIFIER="docker.repository.cloudera.com/.../"
```

## Monitoring & Troubleshooting

### View Cluster Information

```bash
# SSH into head node or check CML project files
cat /home/cdsw/ray_cluster_info.json
```

### Cluster Status

```bash
# From another Ray application
import ray
ray.init(address='ray://head-address:6379')
print(ray.cluster_resources())
```

### Logs

- **Project creation**: Check CML API responses
- **Git clone**: Check CML project status
- **Job execution**: View in CML UI > Project > Jobs > Runs
- **Ray startup**: View in CML UI > Applications

### Common Issues

**Issue**: `runtime image must be specified`
- **Solution**: Provide valid `runtime_identifier` for your CML instance

**Issue**: `Failed to get project`
- **Solution**: Check CML_HOST and CML_API_KEY are correct

**Issue**: Jobs stuck in queued state
- **Solution**: Check CML project has available resources

**Issue**: Ray head node not starting
- **Solution**: Check Ray installation in setup_environment logs

## Advanced Usage

### Custom Runtime

To use a different Docker runtime, edit `ray_cluster_config.yaml`:

```yaml
cai:
  runtime_identifier: "docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-gpu:2025.09.1-b5"
```

### Force Rebuild

Skip job cache and rebuild everything:

```bash
export FORCE_REBUILD="true"
python cai_integration/trigger_jobs.py \
  --project-id $PROJECT_ID \
  --jobs-config cai_integration/jobs_config.yaml
```

Or in GitHub Actions, check the "Force rebuild" option when triggering the workflow.

### Custom Job Dependencies

Edit `jobs_config.yaml` to modify job sequence:

```yaml
jobs:
  custom_job:
    name: "Custom Setup"
    script: "custom_script.py"
    parent_job_key: "setup_environment"
    timeout: 1800
```

## Integration with Applications

### Connect from Ray Client

```python
import ray

# Connect to deployed cluster
ray.init(address='ray://head-node-address:6379')

# Use Ray
@ray.remote
def hello():
    return "Hello from Ray!"

result = ray.get(hello.remote())
print(result)
```

### Using Ray Tune

```python
from ray import tune
from ray.tune import CLIReporter

# Run hyperparameter tuning
tuner = tune.Tuner(
    "PPO",
    param_space={...},
    run_config=RunConfig(
        name="my_experiment",
        progress_reporter=CLIReporter(),
    ),
)
results = tuner.fit()
```

## Performance Tuning

### Memory Configuration

Adjust worker memory based on workload:

```yaml
ray_cluster:
  worker_memory: 64  # For data-heavy workloads
```

### GPU Allocation

Enable GPUs for compute-intensive workloads:

```yaml
ray_cluster:
  worker_gpus: 2     # 2 GPUs per worker
```

### Scaling

Increase worker count for parallelism:

```yaml
ray_cluster:
  num_workers: 4     # 4 worker nodes
```

## References

- [Ray Documentation](https://docs.ray.io/)
- [Cloudera Machine Learning](https://docs.cloudera.com/machine-learning/)
- [CAI Documentation](https://docs.cloudera.com/cai/)
- [GitHub Actions](https://docs.github.com/en/actions)

## Troubleshooting Guide

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed troubleshooting steps.

## Contributing

To contribute improvements:

1. Fork the repository
2. Create a feature branch
3. Make changes and test locally
4. Submit a pull request

## License

See LICENSE file in repository root.
