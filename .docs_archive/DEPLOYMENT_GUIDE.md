# Ray Cluster Deployment Guide

Complete guide for deploying Ray clusters on Cloudera Machine Learning (CML) using CAI (Cloudera Applications) infrastructure.

## Quick Start

### 1. Set up GitHub Secrets (for automated deployment)

Go to your repository Settings > Secrets and variables > Actions and add:

```
CML_HOST: https://ml.your-company.cloudera.site
CML_API_KEY: your-api-key-here
```

### 2. Configure Ray Cluster

Edit `ray_cluster_config.yaml`:

```yaml
ray_cluster:
  num_workers: 1           # Number of worker nodes
  head_cpu: 4              # Head node CPU cores
  head_memory: 16          # Head node memory (GB)
  worker_cpu: 8            # Worker CPU cores each
  worker_memory: 32        # Worker memory (GB) each
  worker_gpus: 1           # GPUs per worker (0 for CPU-only)
```

### 3. Deploy

**Option A: GitHub Actions (Automated)**
```bash
git push origin main
# Workflow triggers automatically on push to main
# Monitor in Actions tab
```

**Option B: Manual Local Deployment**
```bash
export CML_HOST="https://ml.your-company.cloudera.site"
export CML_API_KEY="your-api-key"
python cai_integration/deploy_to_cml.py
```

### 4. Monitor Deployment

1. **GitHub Actions**: Check Actions tab for workflow status
2. **CML UI**: Check Projects > Jobs > Runs for job execution status
3. **Cluster Info**: Access `/home/cdsw/ray_cluster_info.json` after deployment

## Architecture Overview

```
Your Code Repository (GitHub)
          ↓
      .git_sync job (clones code)
          ↓
   setup_environment job (installs Ray)
          ↓
   launch_ray_cluster job (starts cluster)
          ↓
   Ray Cluster Ready! (head + N workers)
```

## File Structure

```
ray-serve-cai/
├── .github/workflows/
│   └── deploy-ray-cluster.yml          # GitHub Actions workflow
├── cai_integration/
│   ├── jobs_config.yaml                # Job definitions
│   ├── deploy_to_cml.py                # Deployment orchestrator
│   ├── setup_environment.py            # Environment setup job
│   ├── launch_ray_cluster.py           # Cluster launch job
│   ├── README.md                       # Detailed documentation
│   └── TROUBLESHOOTING.md              # Troubleshooting guide
├── ray_cluster_config.yaml             # Ray cluster configuration
├── ray_serve_cai/
│   └── cai_cluster.py                  # CAI cluster manager
└── tests/
    └── test_cai_deployment.py          # Deployment tests
```

## Deployment Flow

### Step 1: Project Setup
- Searches for existing "ray-cluster" project in CML
- Creates new project with git repository if not found
- Waits for git clone to complete

### Step 2: Job Configuration
- Loads `jobs_config.yaml`
- Creates or updates jobs in project:
  - git_sync (dependency: none)
  - setup_environment (dependency: git_sync)
  - launch_ray_cluster (dependency: setup_environment)

### Step 3: Job Execution
- Executes each job in sequence
- Waits for completion before proceeding
- Skips already-successful jobs (unless FORCE_REBUILD=true)

### Step 4: Ray Cluster Deployment
- setup_environment job:
  - Creates Python venv at `/home/cdsw/.venv`
  - Installs Ray and dependencies
  - Verifies installation

- launch_ray_cluster job:
  - Loads cluster configuration
  - Creates head node CAI application
  - Creates worker node CAI applications
  - Monitors cluster startup
  - Saves cluster info to `/home/cdsw/ray_cluster_info.json`

## Using the Ray Cluster

### From Python Application

```python
import ray

# Connect to deployed cluster
ray.init(address='ray://head-node-address:6379')

# Define and execute remote functions
@ray.remote
def compute(x):
    return x * 2

futures = [compute.remote(i) for i in range(10)]
results = ray.get(futures)
print(results)

# Shutdown when done
ray.shutdown()
```

### Access Cluster Info

```python
import json

with open('/home/cdsw/ray_cluster_info.json') as f:
    cluster_info = json.load(f)

head_address = cluster_info['head_address']
num_workers = cluster_info['num_workers']
print(f"Head: {head_address}, Workers: {num_workers}")
```

### View Ray Dashboard

Open in browser:
```
http://head-node-address:8265
```

## Customization

### Change Cluster Resources

Edit `ray_cluster_config.yaml`:

```yaml
ray_cluster:
  num_workers: 4              # Increase workers
  worker_cpu: 16              # More CPU
  worker_memory: 64           # More memory
  worker_gpus: 2              # More GPUs
```

### Use Different Docker Runtime

```bash
export RUNTIME_IDENTIFIER="docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-gpu:2025.09.1-b5"
python cai_integration/deploy_to_cml.py
```

### Force Full Rebuild

```bash
export FORCE_REBUILD=true
python cai_integration/deploy_to_cml.py
```

### Skip Cache in GitHub Actions

In GitHub Actions UI:
1. Go to Actions tab
2. Select "Deploy Ray Cluster to CML"
3. Click "Run workflow"
4. Check "Force rebuild" checkbox
5. Click "Run workflow"

## Environment Variables

### Required
- `CML_HOST`: CML instance URL
- `CML_API_KEY`: CML API authentication key

### Optional
- `GITHUB_REPOSITORY`: GitHub repo (format: "owner/repo")
- `GH_PAT`: GitHub token (for private repos)
- `FORCE_REBUILD`: Set to "true" to skip cache
- `RUNTIME_IDENTIFIER`: Docker runtime image to use
- `RAY_NUM_WORKERS`: Override number of workers
- `RAY_WORKER_GPUS`: Override GPUs per worker

## Monitoring

### During Deployment

1. **Local Terminal**: Watch output from `deploy_to_cml.py`
2. **GitHub Actions**: Monitor workflow in Actions tab
3. **CML UI**: Check Projects > Jobs > Runs for job status

### After Deployment

1. **Ray Dashboard**: View at `http://head-address:8265`
2. **Cluster Info**: Check `/home/cdsw/ray_cluster_info.json`
3. **Ray Status**: Check via Python:
   ```python
   import ray
   ray.init(address='ray://head-address:6379')
   print(ray.cluster_resources())
   ```

## Troubleshooting

See [cai_integration/TROUBLESHOOTING.md](cai_integration/TROUBLESHOOTING.md) for detailed troubleshooting guide.

### Quick Checks

```bash
# Check venv exists
ls /home/cdsw/.venv/bin/python

# Verify Ray installation
/home/cdsw/.venv/bin/python -c "import ray; print(ray.__version__)"

# View cluster info
cat /home/cdsw/ray_cluster_info.json

# Check job logs (in CML UI)
# Projects > Jobs > Runs > Click run > View Log
```

## Advanced Topics

### Custom Jobs

Add custom jobs in `jobs_config.yaml`:

```yaml
jobs:
  custom_setup:
    name: "Custom Setup"
    script: "cai_integration/custom_setup.py"
    parent_job_key: "setup_environment"
    timeout: 1800
```

### Environment Variables in Jobs

Set in `deploy_to_cml.py` or as environment variables:

```python
job_data = {
    "name": "Setup",
    "environment": {
        "RAY_MEMORY": "50000000000",
        "CUSTOM_VAR": "value"
    }
}
```

### Multi-Region Deployment

Deploy to multiple CML instances:

```bash
# Instance 1
export CML_HOST="https://ml-instance-1.cloudera.site"
python cai_integration/deploy_to_cml.py

# Instance 2
export CML_HOST="https://ml-instance-2.cloudera.site"
python cai_integration/deploy_to_cml.py
```

## Performance Tips

1. **Right-size resources**: Avoid over-provisioning
   ```yaml
   worker_memory: 32  # Sufficient for most workloads
   ```

2. **Use GPUs when needed**: Only enable for ML workloads
   ```yaml
   worker_gpus: 1     # For CUDA workloads
   ```

3. **Scale workers for parallelism**: More workers = more parallelism
   ```yaml
   num_workers: 4     # For parallel processing
   ```

4. **Monitor cluster health**:
   ```python
   print(ray.cluster_resources())
   print(ray.available_resources())
   ```

## Cost Optimization

1. **Deploy only when needed**: Don't keep idle clusters running
2. **Right-size head node**: Use small head, large workers
   ```yaml
   head_cpu: 4        # Small head for coordination
   worker_cpu: 16     # Large workers for computation
   ```
3. **Minimize runtime**: Run jobs efficiently
4. **Clean up after use**: Delete cluster in CML UI

## Security

### Protecting Credentials

- **Never commit credentials**: Use environment variables
- **Use GitHub Secrets**: Store API keys in repository secrets
- **Limit API key permissions**: Use minimal required permissions
- **Rotate keys regularly**: Update API keys periodically

### Network Security

- **Use HTTPS**: All CML URLs should use HTTPS
- **Firewall Ray ports**: Restrict Ray port (6379) to trusted networks
- **VPN access**: Use VPN for CML access when possible

## Production Checklist

- [ ] Configure GitHub secrets (CML_HOST, CML_API_KEY)
- [ ] Customize ray_cluster_config.yaml
- [ ] Test deployment locally first
- [ ] Verify Ray cluster starts correctly
- [ ] Test application connections to cluster
- [ ] Set up monitoring/alerts
- [ ] Document cluster configuration
- [ ] Plan for disaster recovery
- [ ] Test scaling up/down workers
- [ ] Validate performance benchmarks

## Additional Resources

- **Ray Documentation**: https://docs.ray.io/
- **CML Documentation**: https://docs.cloudera.com/machine-learning/
- **CAI Documentation**: https://docs.cloudera.com/cai/
- **GitHub Actions**: https://docs.github.com/en/actions

## Support

For issues:

1. Check [TROUBLESHOOTING.md](cai_integration/TROUBLESHOOTING.md)
2. Review logs in CML UI
3. Test components individually
4. Check network connectivity
5. Verify environment variables

## Contributing

Improvements welcome! Please:

1. Test changes locally first
2. Document changes in README
3. Update version in setup.py
4. Submit pull request with description

## License

See LICENSE file in repository root.
