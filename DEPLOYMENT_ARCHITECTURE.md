# Ray Serve CAI Deployment Architecture

## Overview

The Ray cluster deployment on Cloudera Machine Learning (CML) has been refactored from a monolithic script into focused, composable job scripts that run in GitHub Actions. This architecture provides better visibility, idempotency, and failure recovery.

## Architecture

### GitHub Actions Workflow

The deployment workflow consists of 4 sequential jobs:

```
setup-project (30 min)
    ↓
create-jobs (5 min)
    ↓
trigger-deployment (120 min)
    ↓
verify-deployment (15 min)
```

Each job has a clear responsibility and independent timeout.

### Job Scripts

#### 1. `cai_integration/setup_project.py`

**Purpose:** Get or create CML project with git repository

**Responsibilities:**
- Search for existing project by name
- If not found, create new project with git template
- Wait for git clone to complete (with status updates every 10 seconds)
- Output project_id for subsequent jobs

**Key Features:**
- Idempotent: Safe to rerun
- Status tracking: Prevents "hung" appearance
- Timeout: 15 minutes (configurable)
- Output: Writes project_id to `/tmp/project_id.txt` and GitHub Actions output

**Environment Variables:**
```bash
CML_HOST              # CML instance URL
CML_API_KEY           # CML API authentication
GITHUB_REPOSITORY     # GitHub repo for project creation
GH_PAT                # GitHub token (optional, for private repos)
```

#### 2. `cai_integration/create_jobs.py`

**Purpose:** Create or update CML jobs from configuration

**Responsibilities:**
- Load `jobs_config.yaml` with job definitions
- Create or update all 3 jobs:
  - `git_sync`: Pull latest code
  - `setup_environment`: Create Python venv and install Ray
  - `launch_ray_cluster`: Start Ray cluster with head + workers
- Set up parent job dependencies
- Save job IDs for next step

**Key Features:**
- Idempotent: Updates existing jobs instead of failing
- Fast: <1 minute (just API calls)
- No waiting: Only creates jobs, doesn't execute them

**Command:**
```bash
python cai_integration/create_jobs.py --project-id <project_id>
```

#### 3. `cai_integration/trigger_jobs.py`

**Purpose:** Trigger and monitor job execution

**Responsibilities:**
- Get job IDs by name from `jobs_config.yaml`
- Check if jobs already succeeded (skip if not force rebuild)
- Trigger jobs in sequence: git_sync → setup_environment → launch_ray_cluster
- Wait for each to complete with status updates every 10 seconds

**Key Features:**
- Idempotent: Skips already-successful jobs
- Status tracking: Updates every 10 seconds (prevents hangs)
- Force rebuild: Honors `FORCE_REBUILD` environment variable
- Proper sequencing: Respects job dependencies

**Environment Variables:**
```bash
CML_HOST        # CML instance URL
CML_API_KEY     # CML API authentication
FORCE_REBUILD   # Set to "true" to rerun all jobs
```

**Command:**
```bash
python cai_integration/trigger_jobs.py \
  --project-id <project_id> \
  --jobs-config cai_integration/jobs_config.yaml
```

## Configuration

### `cai_integration/jobs_config.yaml`

Defines the job sequence and their parameters:

```yaml
jobs:
  git_sync:
    name: "Git Repository Sync"
    script: ".git_sync.py"
    cpu: 4
    memory: 16
    timeout: 300
    parent_job_key: null

  setup_environment:
    name: "Setup Python Environment"
    script: "cai_integration/setup_environment.py"
    cpu: 4
    memory: 16
    timeout: 1800  # 30 minutes
    parent_job_key: "git_sync"

  launch_ray_cluster:
    name: "Launch Ray Cluster"
    script: "cai_integration/launch_ray_cluster.py"
    cpu: 4
    memory: 16
    timeout: 600
    parent_job_key: "setup_environment"
```

The `parent_job_key` creates a dependency chain so jobs execute in order on the CML side.

## Workflow: `.github/workflows/deploy-ray-cluster.yml`

### Job 1: `setup-project`

```yaml
- Sets up CML project (creates if needed)
- Waits for git clone completion
- Outputs: project_id
- Timeout: 30 minutes
```

### Job 2: `create-jobs`

```yaml
- Depends on: setup-project
- Creates/updates CML jobs from jobs_config.yaml
- No waiting: Just API calls
- Timeout: 5 minutes
```

### Job 3: `trigger-deployment`

```yaml
- Depends on: setup-project, create-jobs
- Triggers git_sync job
- Waits for completion
- Triggers setup_environment job
- Waits for completion
- Triggers launch_ray_cluster job
- Waits for completion
- Timeout: 120 minutes (2 hours)
```

### Job 4: `verify-deployment`

```yaml
- Depends on: trigger-deployment
- Runs test suite to verify cluster
- Timeout: 15 minutes
- Only runs if all previous jobs succeeded
```

## GitHub Actions Inputs

The workflow supports manual input:

- **force_rebuild**: Set to `true` to rerun all jobs even if they succeeded previously
  - Default: `false`
  - Usage: GitHub UI → Actions → "Deploy Ray Cluster to CML" → "Run workflow" → Set checkbox

## Key Advantages

### 1. No More Hangs

**Problem:** Monolithic script could run for 1+ hour with no output, appearing hung to GitHub Actions.

**Solution:** Each job runs independently with status updates every 10 seconds. GitHub UI shows job progress in real-time.

### 2. Idempotent Execution

**Problem:** If workflow failed partway through, had to restart from scratch.

**Solution:** Each job checks if its work is already done and skips it. Running workflow twice without `force_rebuild` completes in <5 minutes.

**Behavior:**
```
First run:  setup-project (5 min) → create-jobs (1 min) → trigger-deployment (45 min)
Second run: setup-project (skip) → create-jobs (skip) → trigger-deployment (skip)
Result: Total 30 seconds for second run
```

### 3. Better Failure Debugging

**Problem:** Failed workflow buried error 50+ lines deep in log output.

**Solution:** GitHub UI shows which exact job failed. Can click on failed job and see its specific logs.

### 4. Job Retry Support

**Problem:** If job failed, had to restart entire workflow.

**Solution:** Can manually re-run individual failed jobs from GitHub UI without restarting earlier jobs.

### 5. Proven Pattern

This architecture follows the successful open-webui-cai implementation, which deploys Open-WebUI to CML without issues.

## Testing Locally

### 1. Setup Project
```bash
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"
export GITHUB_REPOSITORY="owner/ray-serve-cai"

python cai_integration/setup_project.py
PROJECT_ID=$(cat /tmp/project_id.txt)
```

### 2. Create Jobs
```bash
python cai_integration/create_jobs.py --project-id $PROJECT_ID
```

### 3. Trigger Deployment
```bash
python cai_integration/trigger_jobs.py \
  --project-id $PROJECT_ID \
  --jobs-config cai_integration/jobs_config.yaml
```

Or with force rebuild:
```bash
export FORCE_REBUILD=true
python cai_integration/trigger_jobs.py \
  --project-id $PROJECT_ID \
  --jobs-config cai_integration/jobs_config.yaml
```

## Status Output

The jobs produce clear, concise output showing progress:

```
🚀 CML Project Setup
======================================================================
🔍 Searching for project: ray-cluster
✅ Found existing project: abc123def456

⏳ Waiting for git clone to complete...
   (timeout: 900s)

   [0s] Status: unknown
   [10s] Status: creating
   [20s] Status: creating
   [45s] Status: success
✅ Git clone complete
   Waiting 30 seconds for files to be written...

======================================================================
✅ Project Setup Complete!
======================================================================

Project ID: abc123def456
```

## Maintenance

### Adding a New Job

1. Add job definition to `cai_integration/jobs_config.yaml`:
```yaml
new_job:
  name: "New Job Name"
  script: "path/to/script.py"
  cpu: 4
  memory: 16
  timeout: 600
  parent_job_key: "previous_job_key"
```

2. Job will be automatically created and triggered by existing scripts

### Modifying Job Parameters

Edit `cai_integration/jobs_config.yaml` and commit. Next deployment will update the job configuration.

### Debugging

Enable verbose output by adding `--verbose` flag or checking script logs in GitHub Actions UI.

## Environment Variables (CML)

When CML executes the jobs, it provides environment variables:

```bash
CDSW_PROJECT_ID    # Project ID
CML_PROJECT_ID     # Project ID (fallback)
RUNTIME_IDENTIFIER # Docker runtime image
CML_HOST          # CML instance URL
CML_API_KEY       # API key for subsequent API calls
```

The scripts use `CDSW_PROJECT_ID` or `CML_PROJECT_ID` to know their project context.
