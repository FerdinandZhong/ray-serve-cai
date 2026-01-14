# Ray Cluster Deployment Troubleshooting Guide

## Common Issues and Solutions

### CML Connection Issues

#### Error: `Missing required environment variables`

**Problem**: CML_HOST or CML_API_KEY not set

**Solution**:
```bash
# Set environment variables
export CML_HOST="https://ml.example.cloudera.site"
export CML_API_KEY="your-api-key"

# Verify they're set
echo $CML_HOST
echo $CML_API_KEY
```

#### Error: `API request failed: 401`

**Problem**: Invalid CML_API_KEY

**Solution**:
1. Check your API key in CML settings
2. Generate a new key if expired
3. Verify key doesn't have leading/trailing whitespace

```bash
# Test connection manually
curl -H "Authorization: Bearer YOUR_API_KEY" \
  https://your-cml-host/api/v2/projects
```

#### Error: `Connection refused` or `No route to host`

**Problem**: CML_HOST is unreachable

**Solution**:
1. Verify CML instance is running
2. Check network connectivity
3. Ensure URL is correct (https://, not http://)

```bash
# Test connectivity
ping $(echo $CML_HOST | cut -d/ -f3)
curl -I $CML_HOST
```

### Project Creation Issues

#### Error: `Failed to get/create project`

**Problem**: Project creation failed

**Solution**:
1. Check if project already exists in CML UI
2. Verify API key has project creation permissions
3. Check CML instance has available resources

```bash
# Check existing projects
curl -H "Authorization: Bearer $CML_API_KEY" \
  "$CML_HOST/api/v2/projects?search_filter={\"name\":\"ray-cluster\"}"
```

#### Error: `Problem with field Subdomain: invalid pattern`

**Problem**: Invalid subdomain format

**Solution**:
Subdomains must match regex `^[a-z0-9]+(-[a-z0-9]+)*$`
- Use lowercase letters and numbers only
- Use hyphens (not underscores) for separation

The scripts already use valid subdomains:
- `ray-cluster-head`
- `ray-cluster-worker-1`

### Git Repository Issues

#### Error: `Git clone failed` or timeout

**Problem**: Repository not cloning

**Solution**:
1. Verify GitHub repository URL is correct
2. If private repo, ensure GitHub token is set
3. Check network access to GitHub

```bash
export GITHUB_REPOSITORY="owner/ray-serve-cai"
export GH_PAT="your-github-token"
```

#### Error: `Repository not found (404)`

**Problem**: Invalid repository path

**Solution**:
```bash
# Verify repository exists
git ls-remote https://github.com/owner/ray-serve-cai

# Correct format
export GITHUB_REPOSITORY="owner/repo-name"
```

### Job Execution Issues

#### Error: `Job failed` or `Job timeout`

**Problem**: Job execution failed or took too long

**Solution**:
1. Check job logs in CML UI: Projects > Jobs > Runs > View Log
2. Increase timeout in jobs_config.yaml:
   ```yaml
   setup_environment:
     timeout: 3600  # 1 hour instead of 30 minutes
   ```
3. Check project resources are available

#### Error: `Script not found` or `No such file`

**Problem**: Job script path is incorrect

**Solution**:
- Scripts must use relative paths from project root
- Correct: `cai_integration/setup_environment.py`
- Incorrect: `/home/cdsw/cai_integration/setup_environment.py`

Edit `jobs_config.yaml`:
```yaml
jobs:
  setup_environment:
    script: "cai_integration/setup_environment.py"  # Relative path
```

### Environment Setup Issues

#### Error: `Failed to create virtual environment`

**Problem**: venv creation failed

**Solution**:
1. Check available disk space: `df -h /home/cdsw`
2. Check Python version: `python3 --version`
3. Try manual setup:
   ```bash
   python3 -m venv /home/cdsw/.venv
   source /home/cdsw/.venv/bin/activate
   pip install ray
   ```

#### Error: `Ray verification failed` or `Module not found`

**Problem**: Ray installation incomplete

**Solution**:
1. Check installation logs in CML job output
2. Verify venv is activated:
   ```bash
   source /home/cdsw/.venv/bin/activate
   python -c "import ray; print(ray.__version__)"
   ```
3. Reinstall Ray:
   ```bash
   /home/cdsw/.venv/bin/pip install --upgrade ray[default]
   ```

### Ray Cluster Launch Issues

#### Error: `runtime image must be specified`

**Problem**: No Docker runtime configured for project

**Solution**:
Provide valid runtime_identifier:
```bash
export RUNTIME_IDENTIFIER="docker.repository.cloudera.com/cloudera/cdsw/ml-runtime-pbj-jupyterlab-python3.11-standard:2025.09.1-b5"
```

Check available runtimes in CML UI or try different versions.

#### Error: `Head node failed to start` or timeout

**Problem**: Ray head application not starting

**Solution**:
1. Check application status in CML UI
2. View application logs
3. Increase timeout in start_cluster call:
   ```python
   cluster_info = manager.start_cluster(
       timeout=900  # 15 minutes instead of 10
   )
   ```
4. Check head node has sufficient resources

#### Error: `Could not determine head node address`

**Problem**: Cannot extract head node address

**Solution**:
1. Check application metadata in CML API response
2. Manually set address:
   ```python
   manager.head_address = "ray-cluster-head:6379"
   ```

### Ray Connection Issues

#### Error: `Failed to connect to Ray cluster`

**Problem**: Ray applications cannot connect to head

**Solution**:
1. Verify head node is running (CML UI > Applications)
2. Check network connectivity between applications
3. Verify correct head address:
   ```python
   ray.init(address='ray://head-address:6379')
   ```
4. Check Ray port is not blocked by firewall

#### Error: `Task failed on remote worker`

**Problem**: Worker task execution failed

**Solution**:
1. Check worker node logs in CML UI
2. Verify worker resources are sufficient
3. Check worker can reach head node:
   ```bash
   # On worker node
   ping head-node-address
   telnet head-node-address 6379
   ```

### GitHub Actions Issues

#### Error: Workflow fails with `Missing secrets`

**Problem**: CML_HOST or CML_API_KEY secrets not configured

**Solution**:
1. Go to repository Settings > Secrets and variables > Actions
2. Add required secrets:
   - `CML_HOST`
   - `CML_API_KEY`
3. Re-run workflow

#### Error: `No such file or directory: cai_integration/deploy_to_cml.py`

**Problem**: Workflow runs in wrong directory

**Solution**:
Workflow runs from repository root. Verify file exists:
```bash
ls cai_integration/deploy_to_cml.py
```

#### Error: Workflow hangs or times out

**Problem**: Deployment taking too long

**Solution**:
1. Increase timeout in workflow:
   ```yaml
   timeout-minutes: 60  # instead of 30
   ```
2. Check job logs in CML UI
3. Consider running manual deployment instead

### Performance Issues

#### Cluster Creation Takes Too Long

**Problem**: Ray cluster startup is slow

**Solution**:
1. Reduce worker CPU/memory requirements
2. Reduce number of workers
3. Use smaller Docker runtime image
4. Check CML instance load

#### Slow Job Execution

**Problem**: Jobs run slowly

**Solution**:
1. Increase worker resources
2. Add more worker nodes
3. Check Ray cluster health:
   ```python
   import ray
   print(ray.cluster_resources())
   ```

### Debugging Steps

#### Enable Verbose Logging

```bash
# For deployment script
python cai_integration/deploy_to_cml.py --verbose

# For CAI cluster manager (Python)
manager = CAIClusterManager(
    cml_host=host,
    cml_api_key=key,
    project_id=pid,
    verbose=True  # Enable verbose output
)
```

#### Check Intermediate Files

```bash
# View ray cluster info
cat /home/cdsw/ray_cluster_info.json

# Check venv
ls -la /home/cdsw/.venv/lib/python*/site-packages/ | grep ray
```

#### Test Components Individually

```bash
# Test Ray installation
/home/cdsw/.venv/bin/python -c "import ray; print(ray.__version__)"

# Test Ray head start
/home/cdsw/.venv/bin/ray start --head --port 6379

# Test connection
/home/cdsw/.venv/bin/python -c "import ray; ray.init(address='ray://localhost:6379')"
```

#### Collect Diagnostic Information

```bash
# System info
uname -a
python3 --version
/home/cdsw/.venv/bin/pip list

# CML logs (in job output)
# Ray logs (in /tmp/ray/ on head node)
```

## Getting Help

If you continue to have issues:

1. **Check logs**: View complete output in CML job runs
2. **Review documentation**: See README.md for usage
3. **Test manually**: Run setup_environment.py in CML job
4. **Verify configuration**: Check jobs_config.yaml and ray_cluster_config.yaml
5. **Check CML status**: Verify CML instance is healthy
6. **Test connectivity**: Verify network access to CML

For Ray-specific issues:
- [Ray Troubleshooting](https://docs.ray.io/en/latest/ray-core/troubleshooting.html)
- [Ray Community](https://discuss.ray.io/)

For CML issues:
- Check CML documentation
- Contact Cloudera support
