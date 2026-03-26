#!/usr/bin/env python3
"""
Generic Ray Serve application deployer.

Connects to a running Ray cluster and deploys a FastAPI application as a
Ray Serve ingress deployment.  Run this script with whichever Python
interpreter has Ray and the target app's dependencies installed — typically
a per-app virtual environment.

Usage:
    python deploy_ray_app.py \\
        --app-import  ray_serve_cai.management.app:app \\
        --name        management-api \\
        --route-prefix /api \\
        --num-cpus    4 \\
        --memory-gb   16 \\
        [--pin-to-head] \\
        [--serve-host 127.0.0.1] \\
        [--serve-port 8000]

The script exits with 0 on success.  Because Ray Serve is started with
detached=True the deployment persists after this process exits.
"""
import argparse
import importlib
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy a FastAPI app via Ray Serve")
    parser.add_argument(
        "--app-import", required=True,
        help="Dotted import path to the FastAPI app object, e.g. 'mypackage.module:app'",
    )
    parser.add_argument("--name", required=True, help="Ray Serve deployment name")
    parser.add_argument("--route-prefix", default="/", help="HTTP route prefix")
    parser.add_argument("--num-cpus", type=float, default=1.0, help="CPU quota for the deployment actor")
    parser.add_argument("--memory-gb", type=float, default=4.0, help="Memory quota (GB) for the deployment actor")
    parser.add_argument(
        "--pin-to-head", action="store_true",
        help="Pin the deployment to the Ray head node using NodeAffinitySchedulingStrategy",
    )
    parser.add_argument("--serve-host", default="127.0.0.1", help="Ray Serve HTTP host (default: 127.0.0.1)")
    parser.add_argument("--serve-port", type=int, default=8000, help="Ray Serve HTTP port (default: 8000)")
    args = parser.parse_args()

    import ray
    from ray import serve

    print(f"  Connecting to Ray cluster (address=auto)...")
    ray.init(address="auto", ignore_reinit_error=True)

    # ── Optionally locate head node for pinning ──────────────────────────────
    scheduling_strategy = None
    if args.pin_to_head:
        from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy
        head_node_id = None
        for node in ray.nodes():
            if node.get("Alive") and "node:__internal_head__" in node.get("Resources", {}):
                head_node_id = node["NodeID"]
                break
        if head_node_id is None:
            head_node_id = ray.get_runtime_context().get_node_id()
        print(f"  Pinning to head node: {head_node_id}")
        scheduling_strategy = NodeAffinitySchedulingStrategy(node_id=head_node_id, soft=False)

    # ── Start Ray Serve (idempotent) ─────────────────────────────────────────
    try:
        serve.start(
            detached=True,
            http_options={"host": args.serve_host, "port": args.serve_port},
        )
        print(f"  Ray Serve started on {args.serve_host}:{args.serve_port}")
    except Exception as exc:
        if any(kw in str(exc).lower() for kw in ("already", "running", "exists")):
            print("  Ray Serve already running — reusing existing instance")
        else:
            raise

    # ── Import the FastAPI application object ────────────────────────────────
    module_path, _, attr = args.app_import.partition(":")
    if not attr:
        print(f"ERROR: --app-import must be in the form 'module:attribute', got: {args.app_import!r}")
        return 1
    print(f"  Importing {module_path}:{attr} ...")
    module = importlib.import_module(module_path)
    fastapi_app = getattr(module, attr)

    # ── Build actor options ──────────────────────────────────────────────────
    ray_actor_options = {
        "num_cpus": args.num_cpus,
        "memory": int(args.memory_gb * 1024 ** 3),
    }
    if scheduling_strategy is not None:
        ray_actor_options["scheduling_strategy"] = scheduling_strategy

    # ── Deploy ───────────────────────────────────────────────────────────────
    # Dynamically create the deployment class; using a fixed class name avoids
    # Ray Serve treating each invocation as a different deployment type.
    @serve.deployment(
        name=args.name,
        num_replicas=1,
        ray_actor_options=ray_actor_options,
    )
    @serve.ingress(fastapi_app)
    class _RayApp:
        pass

    serve.run(_RayApp.bind(), name=args.name, route_prefix=args.route_prefix)
    print(f"  Deployed '{args.name}' at route_prefix='{args.route_prefix}'")
    print(f"  URL: http://{args.serve_host}:{args.serve_port}{args.route_prefix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
