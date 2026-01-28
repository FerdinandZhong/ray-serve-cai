#!/usr/bin/env python3
"""
Example: Deploy Qwen3-30B-A3B and Query It

This example shows how to:
1. Connect to the management API
2. Deploy a vLLM model with tensor parallelism
3. Wait for deployment
4. Query the model using OpenAI-compatible API
5. Handle streaming responses
"""

import requests
import json
import time
import sys
from typing import Iterator

# Configuration
MGMT_API_URL = "http://localhost:8080"
INFERENCE_API_URL = f"{MGMT_API_URL}/v1"
MODEL_NAME = "qwen3-30b"
MODEL_ID = "Qwen/Qwen3-30B-A3B"


class RayServeClient:
    """Client for Ray Serve Management API"""

    def __init__(self, mgmt_api_url: str, inference_api_url: str):
        self.mgmt_api = mgmt_api_url.rstrip('/')
        self.inference_api = inference_api_url.rstrip('/')

    def get_cluster_status(self) -> dict:
        """Get cluster status"""
        response = requests.get(f"{self.mgmt_api}/api/v1/cluster/status")
        return response.json()

    def deploy_model(
        self,
        name: str,
        model_id: str,
        tensor_parallel_size: int = 4,
        dtype: str = "bfloat16",
        gpu_memory_utilization: float = 0.9
    ) -> dict:
        """Deploy a vLLM model with tensor parallelism"""

        # Build placement groups
        placement_group_bundles = [
            {"GPU": 1, "CPU": 1} for _ in range(tensor_parallel_size)
        ]

        # Create deployment request
        request = {
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

        response = requests.post(
            f"{self.mgmt_api}/api/v1/applications",
            json=request
        )

        return response.json()

    def get_deployment_status(self, name: str) -> dict:
        """Get deployment status"""
        response = requests.get(
            f"{self.mgmt_api}/api/v1/applications/{name}"
        )
        return response.json()

    def list_deployments(self) -> list:
        """List all deployed applications"""
        response = requests.get(
            f"{self.mgmt_api}/api/v1/applications"
        )
        data = response.json()
        return data.get('applications', [])

    def wait_for_deployment(self, name: str, max_retries: int = 60, interval: int = 10) -> bool:
        """Wait for deployment to be ready"""
        for i in range(max_retries):
            try:
                status = self.get_deployment_status(name)
                if status.get('status', '').lower() in ['healthy', 'running']:
                    return True
                print(f"  Waiting... Status: {status.get('status')} ({i+1}/{max_retries})")
            except Exception as e:
                print(f"  Checking... ({i+1}/{max_retries})")

            time.sleep(interval)

        return False

    def chat_completion(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: int = 512,
        top_p: float = 0.95,
        stream: bool = False
    ) -> dict or Iterator[str]:
        """
        Query model with chat completion

        Args:
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum response length
            top_p: Nucleus sampling parameter
            stream: Whether to stream response

        Returns:
            Full response dict or iterator of tokens if streaming
        """
        payload = {
            "model": MODEL_ID,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "stream": stream
        }

        if stream:
            response = requests.post(
                f"{self.inference_api}/chat/completions",
                json=payload,
                stream=True
            )

            # Parse streaming response
            def token_generator():
                for line in response.iter_lines():
                    if line:
                        line = line.decode('utf-8')
                        if line.startswith('data: '):
                            data = line[6:]
                            if data != '[DONE]':
                                try:
                                    chunk = json.loads(data)
                                    token = chunk['choices'][0]['delta'].get('content', '')
                                    if token:
                                        yield token
                                except:
                                    pass

            return token_generator()
        else:
            response = requests.post(
                f"{self.inference_api}/chat/completions",
                json=payload
            )
            return response.json()

    def completion(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
        stop: list = None
    ) -> dict:
        """Query model with text completion"""
        payload = {
            "model": MODEL_ID,
            "prompt": prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if stop:
            payload["stop"] = stop

        response = requests.post(
            f"{self.inference_api}/completions",
            json=payload
        )

        return response.json()

    def health_check(self) -> dict:
        """Check model health"""
        response = requests.get(f"{self.inference_api}/health")
        return response.json()


def example_1_simple_deployment():
    """Example 1: Simple deployment and query"""
    print("\n" + "=" * 70)
    print("Example 1: Simple Deployment and Query")
    print("=" * 70)

    client = RayServeClient(MGMT_API_URL, INFERENCE_API_URL)

    # Check cluster health
    print("\n1️⃣  Checking cluster health...")
    status = client.get_cluster_status()
    print(f"✅ Cluster is {'healthy' if status.get('healthy') else 'unhealthy'}")
    print(f"   Nodes: {status.get('total_nodes')}")

    # Deploy model
    print("\n2️⃣  Deploying model...")
    client.deploy_model(
        name=MODEL_NAME,
        model_id=MODEL_ID,
        tensor_parallel_size=4
    )
    print(f"✅ Deployment request submitted")

    # Wait for deployment
    print("\n3️⃣  Waiting for deployment...")
    if not client.wait_for_deployment(MODEL_NAME):
        print("❌ Deployment timeout")
        return False

    # Query model
    print("\n4️⃣  Querying model...")
    response = client.chat_completion(
        messages=[
            {"role": "user", "content": "What is machine learning?"}
        ],
        max_tokens=256
    )

    answer = response['choices'][0]['message']['content']
    print(f"✅ Response:\n{answer}\n")
    print(f"📊 Tokens: {response['usage']['total_tokens']}")

    return True


def example_2_streaming_response():
    """Example 2: Streaming response"""
    print("\n" + "=" * 70)
    print("Example 2: Streaming Response")
    print("=" * 70)

    client = RayServeClient(MGMT_API_URL, INFERENCE_API_URL)

    print("\n🎯 Streaming query (token by token)...\n")

    # Stream response
    tokens = client.chat_completion(
        messages=[
            {"role": "user", "content": "Write a short poem about AI"}
        ],
        max_tokens=200,
        stream=True
    )

    print("🤖 Response: ", end="", flush=True)
    for token in tokens:
        print(token, end="", flush=True)
    print("\n")

    return True


def example_3_batch_queries():
    """Example 3: Multiple queries"""
    print("\n" + "=" * 70)
    print("Example 3: Batch Queries")
    print("=" * 70)

    client = RayServeClient(MGMT_API_URL, INFERENCE_API_URL)

    questions = [
        "What is artificial intelligence?",
        "How does machine learning work?",
        "What is deep learning?"
    ]

    for i, question in enumerate(questions, 1):
        print(f"\n{i}. Query: {question}")

        response = client.chat_completion(
            messages=[{"role": "user", "content": question}],
            max_tokens=128
        )

        answer = response['choices'][0]['message']['content']
        print(f"   Answer: {answer[:100]}...")


def example_4_system_prompt():
    """Example 4: Using system prompt"""
    print("\n" + "=" * 70)
    print("Example 4: With System Prompt")
    print("=" * 70)

    client = RayServeClient(MGMT_API_URL, INFERENCE_API_URL)

    messages = [
        {
            "role": "system",
            "content": "You are a helpful AI assistant that explains concepts in simple terms."
        },
        {
            "role": "user",
            "content": "What is neural networks?"
        }
    ]

    print("\n🎯 Query with system prompt...\n")

    response = client.chat_completion(
        messages=messages,
        temperature=0.5,  # Lower temperature for more focused responses
        max_tokens=256
    )

    answer = response['choices'][0]['message']['content']
    print(f"🤖 Response:\n{answer}\n")


def example_5_health_and_status():
    """Example 5: Check health and list deployments"""
    print("\n" + "=" * 70)
    print("Example 5: Health and Status")
    print("=" * 70)

    client = RayServeClient(MGMT_API_URL, INFERENCE_API_URL)

    # Health check
    print("\n1️⃣  Health Check...")
    health = client.health_check()
    print(f"✅ Model Status: {health.get('status')}")
    print(f"   Model: {health.get('model')}")
    print(f"   Engine: {health.get('engine')}")
    print(f"   Tensor Parallel: {health.get('tensor_parallel_size')}")

    # Deployment status
    print("\n2️⃣  Deployment Status...")
    deployment = client.get_deployment_status(MODEL_NAME)
    print(f"✅ Deployment: {deployment.get('name')}")
    print(f"   Status: {deployment.get('status')}")
    print(f"   Route: {deployment.get('route_prefix')}")
    print(f"   Replicas: {deployment.get('num_replicas')}")

    # List all deployments
    print("\n3️⃣  All Deployments...")
    deployments = client.list_deployments()
    for app in deployments:
        print(f"   - {app.get('name')}: {app.get('status')}")


def main():
    """Run all examples"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Ray Serve vLLM Model Examples"
    )

    parser.add_argument(
        '--mgmt-api',
        default=MGMT_API_URL,
        help=f'Management API URL (default: {MGMT_API_URL})'
    )

    parser.add_argument(
        '--example',
        type=int,
        choices=[1, 2, 3, 4, 5],
        help='Run specific example (1-5)'
    )

    parser.add_argument(
        '--no-deploy',
        action='store_true',
        help='Skip deployment (model must already be deployed)'
    )

    args = parser.parse_args()

    # Update URLs
    global MGMT_API_URL, INFERENCE_API_URL
    MGMT_API_URL = args.mgmt_api
    INFERENCE_API_URL = f"{MGMT_API_URL}/v1"

    try:
        if args.example == 1 or (not args.example and not args.no_deploy):
            example_1_simple_deployment()
        elif args.example == 2 or args.example:
            if not args.no_deploy:
                example_1_simple_deployment()
            example_2_streaming_response()
        elif args.example == 3:
            example_3_batch_queries()
        elif args.example == 4:
            example_4_system_prompt()
        elif args.example == 5:
            example_5_health_and_status()
        else:
            # Run all examples
            if not args.no_deploy:
                example_1_simple_deployment()
            example_2_streaming_response()
            example_3_batch_queries()
            example_4_system_prompt()
            example_5_health_and_status()

        print("\n" + "=" * 70)
        print("✅ All examples completed!")
        print("=" * 70)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
