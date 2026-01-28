#!/usr/bin/env python3
"""
Quick script to deploy Qwen3-30B-A3B model on Ray Cluster

Usage:
    python deploy_qwen3_model.py --mgmt-api http://head-host:8080

Or with custom settings:
    python deploy_qwen3_model.py \
        --mgmt-api http://head-host:8080 \
        --model "Qwen/Qwen3-30B-A3B" \
        --tensor-parallel 4 \
        --wait
"""

import requests
import argparse
import time
import json
from typing import Dict, Any, Optional

class QwenDeployer:
    """Deploy and manage Qwen2 models on Ray Cluster"""

    def __init__(self, mgmt_api: str):
        self.mgmt_api = mgmt_api.rstrip('/')
        self.inference_api = f"{self.mgmt_api}/v1"

    def check_cluster_health(self) -> bool:
        """Check if cluster is ready"""
        try:
            response = requests.get(f"{self.mgmt_api}/api/v1/cluster/status", timeout=5)
            if response.status_code != 200:
                print("❌ Cluster API not responding")
                return False

            data = response.json()
            print(f"✅ Cluster is healthy")
            print(f"   Nodes: {data.get('total_nodes', 0)}")
            print(f"   Health: {'Healthy' if data.get('healthy') else 'Unhealthy'}")
            return data.get('healthy', False)
        except Exception as e:
            print(f"❌ Error checking cluster: {e}")
            return False

    def deploy_model(
        self,
        model: str,
        name: str,
        tensor_parallel_size: int = 4,
        dtype: str = "bfloat16",
        gpu_memory_utilization: float = 0.9,
        max_model_len: Optional[int] = None,
        trust_remote_code: bool = True
    ) -> bool:
        """Deploy Qwen2 model with vLLM"""
        print(f"\n🚀 Deploying {model}...")
        print(f"   Name: {name}")
        print(f"   Tensor Parallel: {tensor_parallel_size}")
        print(f"   DType: {dtype}")

        # Build engine config
        engine_config = {
            "model": model,
            "tensor_parallel_size": tensor_parallel_size,
            "dtype": dtype,
            "gpu_memory_utilization": gpu_memory_utilization,
            "load_format": "auto",
            "trust_remote_code": trust_remote_code,
        }

        if max_model_len:
            engine_config["max_model_len"] = max_model_len

        # Build placement groups for tensor parallelism
        placement_group_bundles = [
            {"GPU": 1, "CPU": 1} for _ in range(tensor_parallel_size)
        ]

        # Build deployment request
        deployment_request = {
            "name": name,
            "import_path": "ray_serve_cai.engines.vllm_engine:create_vllm_deployment",
            "route_prefix": "/v1",
            "num_replicas": 1,
            "ray_actor_options": {
                "num_cpus": tensor_parallel_size,
                "num_gpus": tensor_parallel_size,
                "placement_group_bundles": placement_group_bundles,
                "placement_group_strategy": "PACK"
            }
        }

        try:
            response = requests.post(
                f"{self.mgmt_api}/api/v1/applications",
                json=deployment_request,
                timeout=10
            )

            if response.status_code in [200, 201]:
                print(f"✅ Deployment submitted successfully")
                return True
            else:
                print(f"❌ Deployment failed: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Error deploying model: {e}")
            return False

    def wait_for_deployment(self, name: str, max_retries: int = 60, interval: int = 10) -> bool:
        """Wait for model to be ready"""
        print(f"\n⏳ Waiting for {name} to be ready...")

        for i in range(max_retries):
            try:
                response = requests.get(
                    f"{self.mgmt_api}/api/v1/applications/{name}",
                    timeout=5
                )

                if response.status_code == 200:
                    status = response.json().get('status', '').lower()
                    if status in ['healthy', 'running']:
                        print(f"✅ {name} is ready!")
                        return True
                    else:
                        print(f"  Status: {status} (attempt {i+1}/{max_retries})")
                else:
                    print(f"  Checking... (attempt {i+1}/{max_retries})")
            except Exception as e:
                if i % 5 == 0:
                    print(f"  Checking... (attempt {i+1}/{max_retries})")

            time.sleep(interval)

        print(f"❌ Timeout waiting for {name}")
        return False

    def test_model(self, query: str = "What is machine learning?") -> bool:
        """Test model with a simple query"""
        print(f"\n💬 Testing model...")
        print(f"   Query: {query}")

        try:
            response = requests.post(
                f"{self.inference_api}/chat/completions",
                json={
                    "model": "default",
                    "messages": [{"role": "user", "content": query}],
                    "temperature": 0.7,
                    "max_tokens": 256
                },
                timeout=120
            )

            if response.status_code == 200:
                result = response.json()
                answer = result['choices'][0]['message']['content']
                tokens = result['usage']['total_tokens']
                print(f"\n✅ Model response received:")
                print(f"   {answer[:200]}...")
                print(f"   Tokens: {tokens}")
                return True
            else:
                print(f"❌ Query failed: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Error querying model: {e}")
            return False

    def list_models(self) -> bool:
        """List deployed models"""
        try:
            response = requests.get(
                f"{self.inference_api}/models",
                timeout=5
            )

            if response.status_code == 200:
                models = response.json()
                print(f"\n📋 Available models:")
                for model in models.get('data', []):
                    print(f"   - {model.get('id', 'unknown')}")
                return True
            else:
                print(f"❌ Failed to list models: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Error listing models: {e}")
            return False

    def show_info(self) -> None:
        """Show cluster and API info"""
        print("\n" + "=" * 70)
        print("📌 API Information")
        print("=" * 70)
        print(f"Management API: {self.mgmt_api}")
        print(f"Inference API: {self.inference_api}")
        print(f"\nEndpoints:")
        print(f"  Chat: POST {self.inference_api}/chat/completions")
        print(f"  Complete: POST {self.inference_api}/completions")
        print(f"  Models: GET {self.inference_api}/models")
        print(f"  Health: GET {self.mgmt_api}/health")


def main():
    parser = argparse.ArgumentParser(
        description='Deploy Qwen2 model on Ray Cluster',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Deploy with defaults (4 GPUs, bfloat16)
  python deploy_qwen2_model.py --mgmt-api http://localhost:8080

  # Deploy Qwen2-57B-A14B with tensor parallelism and wait
  python deploy_qwen2_model.py \
    --mgmt-api http://localhost:8080 \
    --model "Qwen/Qwen2-57B-A14B" \
    --tensor-parallel 4 \
    --wait \
    --test

  # Deploy with custom settings
  python deploy_qwen2_model.py \
    --mgmt-api http://localhost:8080 \
    --model "Qwen/Qwen3-A3B-30B" \
    --name my-qwen-model \
    --dtype float16 \
    --max-model-len 8192
        """
    )

    parser.add_argument(
        '--mgmt-api',
        required=True,
        help='Management API URL (e.g., http://head-host:8080)'
    )

    parser.add_argument(
        '--model',
        default='Qwen/Qwen3-30B-A3B',
        help='Model name from HuggingFace (default: Qwen/Qwen3-30B-A3B)'
    )

    parser.add_argument(
        '--name',
        default='qwen2-model',
        help='Deployment name (default: qwen2-model)'
    )

    parser.add_argument(
        '--tensor-parallel',
        type=int,
        default=4,
        help='Tensor parallelism size (1-4, default: 4)'
    )

    parser.add_argument(
        '--dtype',
        choices=['auto', 'float16', 'bfloat16', 'float32'],
        default='bfloat16',
        help='Data type (default: bfloat16)'
    )

    parser.add_argument(
        '--gpu-memory-util',
        type=float,
        default=0.9,
        help='GPU memory utilization (0.0-1.0, default: 0.9)'
    )

    parser.add_argument(
        '--max-model-len',
        type=int,
        help='Maximum model length (optional)'
    )

    parser.add_argument(
        '--wait',
        action='store_true',
        help='Wait for deployment to be ready'
    )

    parser.add_argument(
        '--test',
        action='store_true',
        help='Test model after deployment'
    )

    parser.add_argument(
        '--check-only',
        action='store_true',
        help='Only check cluster health'
    )

    parser.add_argument(
        '--list-models',
        action='store_true',
        help='List deployed models'
    )

    args = parser.parse_args()

    # Initialize deployer
    deployer = QwenDeployer(args.mgmt_api)

    # Show info
    deployer.show_info()

    # Check cluster
    print("\n" + "=" * 70)
    print("🔍 Checking Cluster")
    print("=" * 70)
    if not deployer.check_cluster_health():
        print("\n❌ Cluster is not healthy. Cannot proceed.")
        return 1

    # List models if requested
    if args.list_models:
        deployer.list_models()
        return 0

    # Check only if requested
    if args.check_only:
        return 0

    # Deploy model
    print("\n" + "=" * 70)
    print("🚀 Deployment")
    print("=" * 70)
    if not deployer.deploy_model(
        model=args.model,
        name=args.name,
        tensor_parallel_size=args.tensor_parallel,
        dtype=args.dtype,
        gpu_memory_utilization=args.gpu_memory_util,
        max_model_len=args.max_model_len
    ):
        return 1

    # Wait for deployment
    if args.wait:
        if not deployer.wait_for_deployment(args.name):
            return 1

        # Test model
        if args.test:
            print("\n" + "=" * 70)
            print("🧪 Testing")
            print("=" * 70)
            if not deployer.test_model():
                print("\n⚠️  Model test failed, but deployment may still be working")

    print("\n" + "=" * 70)
    print("✅ Complete!")
    print("=" * 70)
    print(f"\n📌 Deployment: {args.name}")
    print(f"📌 Model: {args.model}")
    print(f"📌 Status URL: {args.mgmt_api}/api/v1/applications/{args.name}")

    return 0


if __name__ == "__main__":
    exit(main())
