# Launch Qwen3-30B-A3B Model on Ray Cluster

This guide shows how to deploy and query the Qwen3-30B-A3B model using the Ray Serve management API after the Ray cluster is running.

## Prerequisites

1. ✅ Ray cluster is running with head + 1 GPU worker (4 GPUs, 32 CPUs)
2. ✅ Management API is deployed and ready
3. ✅ You have the cluster's management API URL (e.g., `http://<head-host>:8080`)

## Step 1: Get Cluster Information

First, check the cluster status to confirm it's ready:

```bash
# Get cluster info (shows head address, dashboard URL, etc.)
curl -s "http://<head-host>:8080/api/v1/cluster/info" | jq .

# Check cluster status
curl -s "http://<head-host>:8080/api/v1/cluster/status" | jq .

# List nodes
curl -s "http://<head-host>:8080/api/v1/resources/nodes" | jq .
```

Expected output shows:
- At least 1 head node + 1 worker node running
- Worker node has 4 GPUs available
- Ray cluster is healthy

## Step 2: Deploy Qwen3-30B-A3B Model

### Option A: Deploy with vLLM (Recommended for Qwen model)

Deploy the model using the vLLM engine with tensor parallelism across all 4 GPUs:

```bash
# Set your management API URL
MGMT_API="http://<head-host>:8080"

# Deploy Qwen3-30B-A3B with tensor parallelism on 4 GPUs
curl -X POST "$MGMT_API/api/v1/applications" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "qwen3-a3b-30b",
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

**Python equivalent:**

```python
import requests

MGMT_API = "http://<head-host>:8080"

# vLLM Engine Configuration
engine_config = {
    "model": "Qwen/Qwen2-57B-A14B",  # or Qwen/Qwen3-30B-A3B if available
    "tensor_parallel_size": 4,
    "dtype": "bfloat16",
    "gpu_memory_utilization": 0.9,
    "max_model_len": 32768,
    "load_format": "auto",
    "trust_remote_code": True
}

# Deployment request
deployment_request = {
    "name": "qwen3-a3b-30b",
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
}

response = requests.post(
    f"{MGMT_API}/api/v1/applications",
    json=deployment_request
)

print("Deployment status:", response.json())
```

### Step 3: Wait for Deployment to Complete

```bash
# Check deployment status
curl -s "http://<head-host>:8080/api/v1/applications/qwen3-a3b-30b" | jq .

# Expected output shows:
# {
#   "name": "qwen3-a3b-30b",
#   "status": "healthy",  # or "running"
#   "route_prefix": "/v1",
#   "num_replicas": 1,
#   "deployment_time": "2025-01-27T..."
# }
```

Wait for status to show `healthy` or `running` (this may take 5-10 minutes for model download + loading).

### Step 4: Query the Model

Once deployed, the model is available at:
- **Base URL**: `http://<head-host>:8080/v1`
- **Chat Completions**: `POST /v1/chat/completions`
- **Completions**: `POST /v1/completions`
- **List Models**: `GET /v1/models`
- **Health Check**: `GET /health`

#### Example 1: Chat Completion

```bash
curl -X POST "http://<head-host>:8080/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-30B-A3B",
    "messages": [
      {
        "role": "user",
        "content": "What is machine learning?"
      }
    ],
    "temperature": 0.7,
    "max_tokens": 512,
    "top_p": 0.95
  }' | jq .
```

**Python equivalent:**

```python
import requests

INFERENCE_API = "http://<head-host>:8080/v1"

response = requests.post(
    f"{INFERENCE_API}/chat/completions",
    json={
        "model": "Qwen/Qwen3-30B-A3B",
        "messages": [
            {
                "role": "user",
                "content": "What is machine learning?"
            }
        ],
        "temperature": 0.7,
        "max_tokens": 512,
        "top_p": 0.95
    }
)

result = response.json()
print("Response:", result['choices'][0]['message']['content'])
```

#### Example 2: Text Completion

```bash
curl -X POST "http://<head-host>:8080/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3-30B-A3B",
    "prompt": "Machine learning is",
    "temperature": 0.7,
    "max_tokens": 512
  }' | jq .
```

#### Example 3: List Models

```bash
curl "http://<head-host>:8080/v1/models" | jq .
```

#### Example 4: Health Check

```bash
curl "http://<head-host>:8080/health" | jq .
```

## Complete End-to-End Example

```python
#!/usr/bin/env python3
"""
Complete example: Deploy Qwen3-30B-A3B and query it
"""

import requests
import time
import sys

# Configuration
MGMT_API = "http://<head-host>:8080"
INFERENCE_API = f"{MGMT_API}/v1"
MODEL_NAME = "qwen3-a3b-30b"

def wait_for_deployment(app_name, max_retries=60, retry_interval=10):
    """Wait for deployment to be healthy"""
    print(f"⏳ Waiting for {app_name} to be ready...")

    for i in range(max_retries):
        try:
            response = requests.get(f"{MGMT_API}/api/v1/applications/{app_name}")
            if response.status_code == 200:
                status = response.json().get('status', '').lower()
                if status in ['healthy', 'running']:
                    print(f"✅ {app_name} is ready!")
                    return True
                else:
                    print(f"  Status: {status} (attempt {i+1}/{max_retries})")
        except Exception as e:
            print(f"  Checking status... (attempt {i+1}/{max_retries})")

        time.sleep(retry_interval)

    return False

def deploy_model():
    """Deploy Qwen3-30B-A3B model"""
    print("=" * 70)
    print("🚀 Deploying Qwen3-30B-A3B Model")
    print("=" * 70)

    deployment_request = {
        "name": MODEL_NAME,
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
    }

    response = requests.post(
        f"{MGMT_API}/api/v1/applications",
        json=deployment_request
    )

    if response.status_code in [200, 201]:
        print("✅ Deployment request submitted")
        return True
    else:
        print(f"❌ Deployment failed: {response.text}")
        return False

def query_model():
    """Query the deployed model"""
    print("\n" + "=" * 70)
    print("💬 Querying Qwen3-30B-A3B Model")
    print("=" * 70)

    messages = [
        {"role": "user", "content": "What is machine learning in one sentence?"}
    ]

    request_payload = {
        "model": "Qwen/Qwen3-30B-A3B",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 256,
        "top_p": 0.95
    }

    try:
        response = requests.post(
            f"{INFERENCE_API}/chat/completions",
            json=request_payload,
            timeout=120
        )

        if response.status_code == 200:
            result = response.json()
            answer = result['choices'][0]['message']['content']
            tokens_used = result['usage']['total_tokens']

            print(f"\n📝 Question: {messages[0]['content']}")
            print(f"\n🤖 Answer:\n{answer}")
            print(f"\n📊 Tokens used: {tokens_used}")
            return True
        else:
            print(f"❌ Query failed: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error querying model: {e}")
        return False

def check_cluster_health():
    """Check cluster health before deployment"""
    print("=" * 70)
    print("🔍 Checking Cluster Health")
    print("=" * 70)

    try:
        # Check cluster status
        status_response = requests.get(f"{MGMT_API}/api/v1/cluster/status")
        if status_response.status_code != 200:
            print("❌ Cluster is not responding")
            return False

        status = status_response.json()
        print(f"✅ Cluster Status:")
        print(f"   Running Nodes: {status['total_nodes']}")
        print(f"   Running Applications: {status['running_applications']}")
        print(f"   Health: {'Healthy' if status['healthy'] else 'Unhealthy'}")

        # Check resources
        resources_response = requests.get(f"{MGMT_API}/api/v1/resources/capacity")
        if resources_response.status_code == 200:
            resources = resources_response.json()
            print(f"\n📊 Available Resources:")
            print(f"   Total GPUs: {resources['total_gpus']}")
            print(f"   Available GPUs: {resources['available_gpus']}")
            print(f"   Total CPUs: {resources['total_cpus']}")
            print(f"   Available CPUs: {resources['available_cpus']}")

        return True
    except Exception as e:
        print(f"❌ Error checking cluster: {e}")
        return False

if __name__ == "__main__":
    print("\n🎯 Qwen3-30B-A3B Model Deployment & Query\n")

    # Check cluster health
    if not check_cluster_health():
        sys.exit(1)

    # Deploy model
    if not deploy_model():
        sys.exit(1)

    # Wait for deployment
    if not wait_for_deployment(MODEL_NAME):
        print("❌ Deployment timeout")
        sys.exit(1)

    # Query model
    if not query_model():
        sys.exit(1)

    print("\n" + "=" * 70)
    print("✅ Complete!")
    print("=" * 70)
    print(f"\n📌 Model API URL: {INFERENCE_API}")
    print(f"📌 Chat endpoint: POST {INFERENCE_API}/chat/completions")
    print(f"📌 Completion endpoint: POST {INFERENCE_API}/completions")
    print(f"📌 Models endpoint: GET {INFERENCE_API}/models")
```

## Key Configuration Parameters

### vLLM Engine Configuration

Available parameters when deploying with vLLM:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | str | - | Model name/path (e.g., `Qwen/Qwen3-30B-A3B`) |
| `tensor_parallel_size` | int | 1 | Number of GPUs for tensor parallelism (1-4 for Qwen3-30B-A3B) |
| `dtype` | str | auto | Data type: auto, float16, bfloat16, float32 |
| `gpu_memory_utilization` | float | 0.9 | GPU memory utilization (0.0-1.0) |
| `max_model_len` | int | auto | Maximum context length (32768 for Qwen3-30B-A3B) |
| `load_format` | str | auto | Model load format: auto, safetensors, pt |
| `trust_remote_code` | bool | False | Allow remote code execution (needed for some models) |

### Inference Parameters

Available parameters for model queries:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `temperature` | float | 1.0 | Sampling temperature (0.0-2.0) |
| `top_p` | float | 1.0 | Nucleus sampling (0.0-1.0) |
| `max_tokens` | int | 512 | Maximum response length |
| `repetition_penalty` | float | 1.0 | Penalty for repetition |
| `frequency_penalty` | float | 0.0 | Frequency penalty |

## Troubleshooting

### Issue: Model deployment times out

```bash
# Check deployment logs
curl "http://<head-host>:8080/api/v1/applications/qwen3-a3b-30b" | jq .

# Check Ray cluster status
curl "http://<head-host>:8080/api/v1/cluster/status" | jq .

# Check available resources
curl "http://<head-host>:8080/api/v1/resources/capacity" | jq .
```

### Issue: Model query returns error

```bash
# Test health endpoint
curl "http://<head-host>:8080/health" | jq .

# Check if model is still deployed
curl "http://<head-host>:8080/api/v1/applications" | jq .
```

### Issue: Out of GPU memory

Reduce `gpu_memory_utilization` to 0.8 or lower, or reduce `tensor_parallel_size`.

## Next Steps

- Check Ray dashboard: `http://<head-host>:8265`
- Monitor model performance: Check Ray logs in dashboard
- Scale model: Add more workers or adjust tensor parallelism
- Deploy multiple models: Repeat deployment with different model names and route prefixes
