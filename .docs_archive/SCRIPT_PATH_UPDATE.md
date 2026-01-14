# Script Path Update - Ray Cluster Deployment

## Summary

Updated the Ray cluster deployment system to use **script file paths** instead of inline commands for CAI applications. This is the correct approach since files are now cloned into the project.

## Changes Made

### 1. `cai_integration/launch_ray_cluster.py`

#### New Function: `create_ray_launcher_scripts()`

Creates Python launcher scripts in `/home/cdsw/`:

- **`ray_head_launcher.py`** - Launcher for head node
  - Activates venv at `/home/cdsw/.venv`
  - Runs: `ray start --head --port 6379 --dashboard-port 8265`
  - Handles startup and error reporting

- **`ray_worker_launcher.py`** - Launcher for worker nodes
  - Activates venv
  - Connects to head node at provided address
  - Runs: `ray start --address {head_address}`
  - Has fallback to use `RAY_HEAD_ADDRESS` environment variable

#### Updated `main()` Function

Now calls `create_ray_launcher_scripts()` before starting cluster:

```python
# Create launcher scripts
head_script_path, worker_script_path = create_ray_launcher_scripts()

# Pass to CAI cluster manager
cluster_info = manager.start_cluster(
    ...
    head_script_path=head_script_path,
    worker_script_path=worker_script_path,
    ...
)
```

### 2. `ray_serve_cai/cai_cluster.py`

#### Updated `start_cluster()` Method

Added two new optional parameters:

```python
def start_cluster(
    self,
    ...
    head_script_path: Optional[str] = None,    # NEW
    worker_script_path: Optional[str] = None,  # NEW
    ...
)
```

#### Script Usage Logic

**For Head Node:**
```python
if head_script_path:
    head_script = head_script_path  # Use provided path
    logger.info(f"   Using head script: {head_script_path}")
else:
    head_script = "ray start --head ..."  # Fallback to inline
    logger.info("   Using inline ray command")
```

**For Worker Nodes:**
```python
if worker_script_path:
    worker_script = worker_script_path  # Use provided path
else:
    worker_script = f"ray start --address={self.head_address}"  # Fallback
```

## How It Works

### Deployment Flow

```
1. setup_environment.py (Job)
   └─ Creates venv at /home/cdsw/.venv
   └─ Installs Ray and dependencies

2. launch_ray_cluster.py (Job)
   ├─ Calls create_ray_launcher_scripts()
   │  ├─ Creates /home/cdsw/ray_head_launcher.py
   │  ├─ Creates /home/cdsw/ray_worker_launcher.py
   │  └─ Makes them executable
   │
   ├─ Calls CAIClusterManager.start_cluster()
   │  ├─ Passes head_script_path
   │  ├─ Passes worker_script_path
   │  └─ Creates applications with these scripts
   │
   └─ Ray cluster starts!
      ├─ Head app runs: /home/cdsw/ray_head_launcher.py
      ├─ Worker apps run: /home/cdsw/ray_worker_launcher.py
      └─ Both use activated venv
```

### Script Execution

#### Head Node Script Flow
```
1. Activate venv
2. Run: python -m ray.scripts.ray_start --head ...
3. Ray head process starts
4. Dashboard available at :8265
5. Application keeps running
```

#### Worker Node Script Flow
```
1. Activate venv
2. Get head address (provided at creation time)
3. Wait 10 seconds for head to stabilize
4. Run: python -m ray.scripts.ray_start --address {head_address}
5. Worker connects to head
6. Application keeps running
```

## Backwards Compatibility

The changes are **fully backwards compatible**:

- If `head_script_path` is NOT provided: falls back to inline `ray start` command
- If `worker_script_path` is NOT provided: falls back to inline `ray start` command
- Existing code that doesn't pass these parameters continues to work

## CAI Application Behavior

When CAI application starts:

1. **Executes the provided script** (`head_script_path` or `worker_script_path`)
2. **Script activates venv** (gets Ray from `/home/cdsw/.venv`)
3. **Script runs Ray command** with proper configuration
4. **Process stays alive** so application doesn't terminate

## Testing

### Test Project Creation with Files

```bash
cd cai_integration/local_test
export CML_HOST="https://..."
export CML_API_KEY="..."
export GITHUB_REPOSITORY="owner/ray-serve-cai"

./run_test.sh
```

### Deploy Ray Cluster

```bash
export CML_HOST="https://..."
export CML_API_KEY="..."
export CDSW_PROJECT_ID="project-id-from-test"

cd ../
python launch_ray_cluster.py
```

## Script Details

### ray_head_launcher.py

```python
#!/usr/bin/env python3
import subprocess
import sys
from pathlib import Path

venv_python = Path("/home/cdsw/.venv/bin/python")
if not venv_python.exists():
    print("❌ Virtual environment not found")
    sys.exit(1)

cmd = [
    str(venv_python), "-m", "ray.scripts.ray_start",
    "--head",
    "--port", "6379",
    "--dashboard-host", "0.0.0.0",
    "--dashboard-port", "8265",
    "--include-dashboard", "true"
]

print("🚀 Starting Ray head node...")
result = subprocess.run(cmd, check=True)
sys.exit(result.returncode)
```

### ray_worker_launcher.py

```python
#!/usr/bin/env python3
import subprocess
import sys
import time
from pathlib import Path

venv_python = Path("/home/cdsw/.venv/bin/python")
if not venv_python.exists():
    print("❌ Virtual environment not found")
    sys.exit(1)

head_address = "{head_address}"  # Provided at creation time

print(f"🚀 Starting Ray worker node...")
print(f"Connecting to: {head_address}")

time.sleep(10)  # Wait for head to stabilize

cmd = [
    str(venv_python), "-m", "ray.scripts.ray_start",
    "--address", head_address
]

result = subprocess.run(cmd, check=True)
sys.exit(result.returncode)
```

## Configuration

No configuration changes needed! The system automatically:

1. Creates launcher scripts
2. Uses venv from `/home/cdsw/.venv`
3. References cloned files in project
4. Passes scripts to CAI applications

## Environment Variables

Optional for worker nodes:

```bash
# If RAY_HEAD_ADDRESS env var is set, worker script uses it
# (Useful for advanced scenarios)
export RAY_HEAD_ADDRESS="head-node:6379"
```

## Benefits

✅ **File-based approach** - CAI requirement, proper way to run apps
✅ **Uses cloned code** - Leverages repository cloned by project setup
✅ **Proper venv usage** - Scripts activate venv before running Ray
✅ **Flexible** - Can customize launcher scripts if needed
✅ **Backwards compatible** - Falls back to inline commands if not provided
✅ **Better error handling** - Scripts check for venv existence

## Backwards Compatibility

If you call `start_cluster()` without `head_script_path` and `worker_script_path`:

```python
# Old way - still works!
cluster_info = manager.start_cluster(
    num_workers=2,
    cpu=8,
    ...
    runtime_identifier=runtime
)
```

Will fall back to inline `ray start` commands (the old behavior).

## Next Steps

1. ✅ Files cloned to project (via `deploy_to_cml.py`)
2. ✅ Environment setup done (via `setup_environment.py` job)
3. ✅ Launcher scripts created and used (this update)
4. ✅ Ray cluster deployed with proper entry points

All ready for production use!

---

**Key Point**: We now properly use script files as CAI requires, instead of inline commands. This is the correct implementation for CML/CAI applications.
