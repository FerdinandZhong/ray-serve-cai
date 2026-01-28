# Ray Serve CAI - Quick Reference

## After Ray Cluster + Management API are Ready

**Model:** Qwen3-30B-A3B (Qwen/Qwen3-30B-A3B)
**Cluster:** 4 GPU worker + management API head node

### 1. Basic Setup

```bash
# Replace with your head node's IP/hostname
HEAD_HOST="head-node-hostname"
MGMT_API="http://$HEAD_HOST:8080"
INFERENCE_API="$MGMT_API/v1"
```

### 2. Check Cluster Status

```bash
# Cluster health
curl -s "$MGMT_API/api/v1/cluster/status" | jq .

# Cluster info
curl -s "$MGMT_API/api/v1/cluster/info" | jq .

# Available resources
curl -s "$MGMT_API/api/v1/resources/capacity" | jq .

# List nodes
curl -s "$MGMT_API/api/v1/resources/nodes" | jq .
```

### 3. Deploy Qwen3-30B-A3B Model (Option A: Simple)

```bash
# Deploy with 4 GPUs tensor parallelism
curl -X POST "$MGMT_API/api/v1/applications" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "qwen3-30b",
    "import_path": "ray_serve_cai.engines.vllm_engine:create_vllm_deployment",
    "route_prefix": "/v1",
    "num_replicas": 1,
    "ray_actor_options": {
      "num_cpus": 4,
      "num_gpus": 4,
      "placement_group_bundles": [
        {"GPU": 1, "CPU": 1},
        {"GPU": 1, "CPU": 1},
        {"GPU": 1, "CPU": 1},
        {"GPU": 1, "CPU": 1}
      ],
      "placement_group_strategy": "PACK"
    }
  }' | jq .
```

### 3. Deploy Qwen2-A3B-30B Model (Option B: Using Python Script)

```bash
# Make script executable
chmod +x scripts/deploy_qwen2_model.py

# Deploy with defaults (Qwen3-30B-A3B, 4 GPUs, wait and test)
python scripts/deploy_qwen2_model.py \
  --mgmt-api "http://$HEAD_HOST:8080" \
  --wait \
  --test

# Or with specific model
python scripts/deploy_qwen2_model.py \
  --mgmt-api "http://$HEAD_HOST:8080" \
  --model "Qwen/Qwen3-30B-A3B" \
  --tensor-parallel 4 \
  --wait \
  --test
```

### 4. Check Deployment Status

```bash
# Get deployment status
curl -s "$MGMT_API/api/v1/applications/qwen2-30b" | jq .

# List all applications
curl -s "$MGMT_API/api/v1/applications" | jq .
```

### 5. Query Model (After It's Ready)

#### Chat Completion
```bash
curl -X POST "$INFERENCE_API/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-30B-A3B",
    "messages": [
      {"role": "user", "content": "What is machine learning?"}
    ],
    "temperature": 0.7,
    "max_tokens": 512
  }' | jq .
```

#### Text Completion
```bash
curl -X POST "$INFERENCE_API/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-30B-A3B",
    "prompt": "Machine learning is",
    "temperature": 0.7,
    "max_tokens": 512
  }' | jq .
```

#### List Models
```bash
curl -s "$INFERENCE_API/models" | jq .
```

#### Health Check
```bash
curl -s "$INFERENCE_API/health" | jq .
```

### 6. Manage Deployments

```bash
# List all deployments
curl -s "$MGMT_API/api/v1/applications" | jq .

# Get specific deployment info
curl -s "$MGMT_API/api/v1/applications/qwen2-30b" | jq .

# Delete deployment
curl -X DELETE "$MGMT_API/api/v1/applications/qwen2-30b" | jq .
```

### 7. Add Worker Nodes (Scale Up)

```bash
# Add a GPU worker with 8 CPUs and 32 GB memory
curl -X POST "$MGMT_API/api/v1/resources/nodes/add" \
  -H "Content-Type: application/json" \
  -d '{
    "cpu": 8,
    "memory": 32,
    "node_type": "worker"
  }' | jq .
```

### 8. Monitor Ray Dashboard

```
Open in browser: http://$HEAD_HOST:8265
```

## Common Workflows

### Workflow 1: Deploy and Use Model
```bash
# 1. Check cluster
curl -s "$MGMT_API/api/v1/cluster/status" | jq '.healthy'

# 2. Deploy
python scripts/deploy_qwen2_model.py \
  --mgmt-api "$MGMT_API" \
  --wait --test

# 3. Query
curl -X POST "$INFERENCE_API/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen2-A3B-30B",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 100
  }' | jq '.choices[0].message.content'
```

### Workflow 2: Check What's Deployed
```bash
curl -s "$MGMT_API/api/v1/applications" | jq '.applications[] | {name, status}'
```

### Workflow 3: Scale Down (Remove Deployment)
```bash
curl -X DELETE "$MGMT_API/api/v1/applications/qwen2-30b" | jq .
```

## API Endpoints Reference

### Management API (`/api/v1/*`)
- `GET /cluster/status` - Cluster health
- `GET /cluster/info` - Cluster configuration
- `GET /resources/capacity` - Available resources
- `GET /resources/nodes` - List nodes
- `POST /resources/nodes/add` - Add worker node
- `POST /applications` - Deploy application
- `GET /applications` - List applications
- `GET /applications/{name}` - Get app details
- `DELETE /applications/{name}` - Remove application

### Inference API (`/v1/*`)
- `POST /chat/completions` - Chat completion (OpenAI compatible)
- `POST /completions` - Text completion (OpenAI compatible)
- `GET /models` - List models
- `GET /health` - Health check

## Environment Variables

For `deploy_qwen2_model.py`:
```bash
export MGMT_API="http://head-host:8080"

# Optional: Set HuggingFace token for gated models
export HF_TOKEN="your-hf-token"
```

## Troubleshooting

### Model deployment times out
```bash
# Check what happened
curl -s "$MGMT_API/api/v1/applications/qwen2-30b" | jq .

# Check Ray dashboard: http://$HEAD_HOST:8265
# Check logs in dashboard

# Restart if needed
curl -X DELETE "$MGMT_API/api/v1/applications/qwen2-30b"
# Redeploy
```

### Out of memory
```bash
# Reduce tensor parallel or GPU memory utilization
python scripts/deploy_qwen2_model.py \
  --mgmt-api "$MGMT_API" \
  --tensor-parallel 2 \
  --gpu-memory-util 0.8
```

### Model not responding
```bash
# Check health
curl -s "$INFERENCE_API/health" | jq .

# Check if still deployed
curl -s "$MGMT_API/api/v1/applications" | jq .

# Check Ray status
curl -s "$MGMT_API/api/v1/cluster/status" | jq .
```

## Model Options

### Recommended Models for 4 GPUs
- `Qwen/Qwen3-30B-A3B` (Default, 30B A3B optimized)
- `Qwen/Qwen3-A2B-7B` (7B, faster inference)
- `Qwen/Qwen3-A14B` (14B, balanced)

### Configuration Examples

**For Qwen3-30B-A3B (Default):**
```bash
python scripts/deploy_qwen2_model.py \
  --model "Qwen/Qwen3-30B-A3B" \
  --tensor-parallel 4 \
  --dtype bfloat16 \
  --wait --test
```

**For smaller model (faster):**
```bash
python scripts/deploy_qwen2_model.py \
  --model "Qwen/Qwen3-A2B-7B" \
  --tensor-parallel 2 \
  --dtype bfloat16 \
  --wait --test
```

**For single GPU:**
```bash
python scripts/deploy_qwen2_model.py \
  --model "Qwen/Qwen3-A2B-7B" \
  --tensor-parallel 1 \
  --wait --test
```
