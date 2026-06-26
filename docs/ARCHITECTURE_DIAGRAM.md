<!-- Space: ~7120206ec1ac3f0d904b368e459afb34c73d9a -->
<!-- Title: ray-serve-cai Architecture -->

# Architecture — ray-serve-cai

> **Last updated**: 2026-05  
> Replaces the previous architecture diagram which referenced removed components (`RayBackend`, `deploy_to_cml.py`, `cai_cluster.py`).

---

## 1. System Overview

ray-serve-cai is a **REST-based AI inference gateway** that runs inside Cloudera Machine Learning (CML). It exposes a single nginx-fronted HTTPS endpoint that routes to one or more Ray Serve deployments — each hosting an isolated inference engine (vLLM, SGLang, LiteLLM, YOLO, or MCP).

A lightweight **Management API** handles cluster lifecycle (deploy, delete, status) so operators never need to touch Ray internals directly.

```mermaid
graph TB
    subgraph External["External Clients"]
        User["User / App"]
    end

    subgraph CML["Cloudera Machine Learning - shared NFS project at /home/cdsw"]
        direction TB

        nginx["nginx\nTLS termination\nreverse proxy"]

        subgraph RayCluster["Ray Cluster (1 head + N worker pods)"]
            direction LR

            subgraph Head["Head Node"]
                GCS["GCS\n:6379 gRPC"]
                Dash["Dashboard\n:8265"]
                ServeCtrl["Serve Controller\n(actor)"]
                MgmtAPI["Management API\n:8001 FastAPI"]
            end

            subgraph W1["Worker Pod - GPU"]
                Raylet1["Raylet"]
                OS1["Object Store"]
                vLLM["vLLM Engine\nactor · isolated venv"]
            end

            subgraph W2["Worker Pod - CPU"]
                Raylet2["Raylet"]
                OS2["Object Store"]
                LiteLLM["LiteLLM Proxy\nactor · isolated venv"]
            end

            subgraph W3["Worker Pod - GPU"]
                Raylet3["Raylet"]
                OS3["Object Store"]
                SGLang["SGLang Engine\nactor · isolated venv"]
            end
        end
    end

    User -->|HTTPS| nginx
    nginx -->|"/api/v1/* → :8001"| MgmtAPI
    nginx -->|"/vllm/* → :8000"| ServeCtrl
    nginx -->|"/openai/* → :8000"| ServeCtrl
    nginx -->|"/sglang/* → :8000"| ServeCtrl
    ServeCtrl -->|gRPC actor call| vLLM
    ServeCtrl -->|gRPC actor call| LiteLLM
    ServeCtrl -->|gRPC actor call| SGLang
    GCS -.->|heartbeat + resource mgmt| Raylet1
    GCS -.->|heartbeat + resource mgmt| Raylet2
    GCS -.->|heartbeat + resource mgmt| Raylet3
```

---

## 2. Ray Cluster — gRPC Orchestration Deep Dive

Ray's inter-node communication is **entirely gRPC-based**. Understanding the topology is essential for debugging scheduling failures, resource starvation, and deployment timeouts.

### 2.1 Core daemons and their gRPC roles

| Daemon | Host | Port | Role |
|--------|------|------|------|
| **GCS Server** | Head only | 6379 | Central cluster brain — actor registry, placement groups, resource accounting, node membership |
| **Raylet** | Every node | ephemeral | Local scheduler + resource manager; bridges GCS ↔ local worker processes |
| **Object Store (Plasma)** | Every node | shared-mem | In-memory object cache; cross-node transfers use gRPC for metadata, raw TCP for bulk data |
| **Dashboard Agent** | Every node | ephemeral | Metrics and profiling, aggregates to Dashboard on :8265 |
| **Serve HTTP Proxy** | Every node | 8000 | Receives HTTP from nginx, routes to replica actors via `ServeHandle` |

### 2.2 gRPC message flows

```mermaid
sequenceDiagram
    participant nginx as nginx
    participant Proxy as Serve HTTP Proxy (every node)
    participant Ctrl as Serve Controller (head actor)
    participant GCS as GCS Server (6379)
    participant Raylet as Worker Raylet
    participant EngineActor as Engine Actor (worker process)

    Note over nginx,EngineActor: Request path (steady state - actor already scheduled)

    nginx->>Proxy: HTTP POST /vllm/v1/chat/completions
    Proxy->>EngineActor: gRPC actor method call handle(scope, receive, send)
    EngineActor-->>Proxy: ASGI response (streaming or buffered)
    Proxy-->>nginx: HTTP response

    Note over Ctrl,EngineActor: Deployment lifecycle (first deploy or replica scale-up)

    Ctrl->>GCS: RequestResources(num_cpus=8, num_gpus=1) [gRPC ResourceRequest]
    GCS->>Raylet: GrantResources + schedule actor [gRPC ResourceGrant]
    Raylet->>EngineActor: Spawn worker process, activate virtualenv
    EngineActor->>GCS: RegisterActor (actor_id, address) [gRPC ActorTableData]
    GCS->>Ctrl: ActorCreated notification [gRPC PubSub push]
    Ctrl->>Proxy: UpdateRoute (deployment ready) [gRPC actor call]
```

### 2.3 Resource reporting loop

Every Raylet sends a **heartbeat** to GCS every ~1 second via gRPC. The heartbeat carries:
- Available CPU / GPU / custom resources
- Currently running actor count and memory pressure
- Node health status

GCS aggregates all heartbeats into a global resource table. When Ray Serve needs to place a new replica, it reads this table and picks the node with the most available resources matching the actor's `ray_actor_options`.

```mermaid
graph LR
    subgraph HeadNode["Head Node"]
        GCS["GCS :6379"]
        ResourceTable["Resource Table\n(in-memory)"]
    end

    subgraph WorkerA["Worker A - 8 CPU 1 GPU"]
        RayletA["Raylet"]
        ActorA["Engine Actor\ncpu=8 gpu=1"]
    end

    subgraph WorkerB["Worker B - 4 CPU 0 GPU"]
        RayletB["Raylet"]
        ActorB["Engine Actor\ncpu=1 gpu=0"]
    end

    RayletA -->|"heartbeat every ~1s\nfree: cpu=0, gpu=0"| GCS
    RayletB -->|"heartbeat every ~1s\nfree: cpu=3, gpu=0"| GCS
    GCS --- ResourceTable
    ResourceTable -->|"schedule decisions"| GCS
```

### 2.4 Cross-node object transfer

When a Ray task passes a large object (e.g., model weights, batched tensors) to a remote node, Plasma Object Store uses gRPC **only for the control plane** (locate / pin / unpin). The actual bytes travel over a direct TCP connection between the two Plasma daemons — bypassing gRPC to avoid its 4 MB default message size limit.

---

## 3. Request Flow — Client to Inference

```mermaid
flowchart LR
    C["Client"] -->|HTTPS POST /openai/v1/chat/completions| N["nginx\n:443"]
    N -->|"strip TLS\nrewrite host"| SP["Serve HTTP Proxy\n:8000\n(Ray actor on head)"]
    SP -->|"match route prefix /openai\nload-balance across replicas"| LA["LiteLLM Engine Actor\n(worker pod)"]
    LA -->|"httpx proxy\nlocalhost:4000"| LL["LiteLLM subprocess\n(port 4000)"]
    LL -->|"OpenAI / Bedrock /\nAnthropic API"| Provider["Cloud Provider API"]
    Provider -->|response| LL
    LL -->|streaming chunks| LA
    LA -->|SSE stream| SP
    SP -->|SSE stream| N
    N -->|SSE stream| C
```

**Key invariant**: the LiteLLM subprocess runs on `127.0.0.1` — it is never directly reachable from outside the pod. All external traffic arrives via nginx → Ray Serve → actor proxy.

---

## 4. Engine Plugin Architecture

All engines share the same four-part contract, registered in `ray_serve_cai/engines/__init__.py`:

```mermaid
classDiagram
    class EngineRegistry {
        +register(engine_type, cls, config_builder, factory)
        +get_config_builder(engine_type)
        +get_deployment_factory(engine_type)
        +list_engines()
    }

    class ConfigBuilderProtocol {
        <<protocol>>
        +build_config(user_config) Dict
        +validate_config(user_config) Tuple
        +get_default_config() Dict
    }

    class DeploymentFactoryProtocol {
        <<protocol>>
        +create_deployment(engine_config, num_replicas, ...) serve.Application
    }

    class vLLMConfigBuilder
    class SGLangConfigBuilder
    class LiteLLMConfigBuilder
    class YOLOConfigBuilder
    class MCPConfigBuilder

    class vLLMDeploymentFactory
    class SGLangDeploymentFactory
    class LiteLLMDeploymentFactory
    class YOLODeploymentFactory
    class MCPDeploymentFactory

    ConfigBuilderProtocol <|.. vLLMConfigBuilder
    ConfigBuilderProtocol <|.. SGLangConfigBuilder
    ConfigBuilderProtocol <|.. LiteLLMConfigBuilder
    ConfigBuilderProtocol <|.. YOLOConfigBuilder
    ConfigBuilderProtocol <|.. MCPConfigBuilder

    DeploymentFactoryProtocol <|.. vLLMDeploymentFactory
    DeploymentFactoryProtocol <|.. SGLangDeploymentFactory
    DeploymentFactoryProtocol <|.. LiteLLMDeploymentFactory
    DeploymentFactoryProtocol <|.. YOLODeploymentFactory
    DeploymentFactoryProtocol <|.. MCPDeploymentFactory

    EngineRegistry --> ConfigBuilderProtocol
    EngineRegistry --> DeploymentFactoryProtocol
```

### Subprocess-based engines (LiteLLM, SGLang)

LiteLLM and SGLang follow the same subprocess pattern:

```
Ray actor __init__()
  ├─ write config YAML/JSON to tempfile
  ├─ Popen([python_bin, server_script, --port, ...])
  ├─ poll /health until HTTP 200 (up to 120s)
  └─ self._base_url = "http://127.0.0.1:<port>"

incoming HTTP request
  └─ httpx.AsyncClient.request() → localhost:<port>
       └─ SSE stream or JSON response proxied back
```

The subprocess runs as a **child of the Ray actor process**, so it shares its lifecycle and isolated venv.

---

## 5. Per-Engine Isolated Virtual Environments

vLLM and SGLang conflict on the `llguidance` package and **cannot share a Python environment**. Every engine gets its own venv under `/home/cdsw`:

```mermaid
graph TB
    NFS["NFS Shared Filesystem\n/home/cdsw"]

    subgraph Venvs["Per-Engine Isolated venvs"]
        VenvCore[".venv\nray serve · fastapi · httpx\nyolo · core dependencies"]
        VenvVLLM[".venv-vllm\nvllm >= 0.13.0\nninja"]
        VenvSGLang[".venv-sglang\nsglang >= 0.5.7"]
        VenvLiteLLM[".venv-litellm\nlitellm[proxy] >= 1.83.0\npyyaml"]
        VenvMCP[".venv-mcp\nmcp >= 1.0.0\nhttpx"]
    end

    NFS --- Venvs

    subgraph RayActors["Ray Actor Workers"]
        AHead["Head node worker\nruntimeenv: .venv"]
        AvLLM["vLLM actor\nruntimeenv: .venv-vllm"]
        ASGLang["SGLang actor\nruntimeenv: .venv-sglang"]
        ALiteLLM["LiteLLM actor\nruntimeenv: .venv-litellm"]
        AMCP["MCP actor\nruntimeenv: .venv-mcp"]
    end

    VenvCore --> AHead
    VenvVLLM --> AvLLM
    VenvSGLang --> ASGLang
    VenvLiteLLM --> ALiteLLM
    VenvMCP --> AMCP
```

`ray_actor_options["runtime_env"]["virtualenv"]` tells Ray to activate the given venv inside the actor worker process before importing any user code. Ray validates the venv path during actor scheduling; if the path is missing the deployment fails immediately with a clear error.

**NFS-safe concurrent creation**: `setup_environment.py:setup_engine_venv()` uses `fcntl.flock` (exclusive file lock) so multiple CML application pods spinning up simultaneously do not corrupt a shared venv during creation.

---

## 6. CML Deployment Job Chain

The cluster bootstraps via a sequence of CML jobs. Each job runs to completion before the next starts.

```mermaid
flowchart TD
    J1["Job 1\nsetup_environment.py\n\nCreates /home/cdsw/.venv\nInstalls ray[serve] + core deps\nBuilds engine venvs for\nvLLM · SGLang · YOLO · MCP"]
    J2["Job 2\nsetup_litellm_env.py\n\nCreates /home/cdsw/.venv-litellm\nInstalls litellm[proxy] >= 1.83.0\npyyaml >= 6.0.3\n(Python 3.11 pinned)"]
    J3["Job 3\nlaunch_ray_cluster.py\n\nCalls CAIClusterManager\nCreates head-node CML app\nCreates worker-node CML apps\nConnects workers to head via\nRAY_ADDRESS env var"]

    J1 -->|"venv ready"| J2
    J2 -->|"litellm venv ready"| J3
    J3 -->|"cluster healthy"| ClusterReady["Ray cluster running\nManagement API :8001\nnginx :443 routing to :8000"]

    style J1 fill:#e8f4fd,stroke:#2196F3
    style J2 fill:#e8f4fd,stroke:#2196F3
    style J3 fill:#e8f4fd,stroke:#2196F3
    style ClusterReady fill:#e8f5e9,stroke:#4CAF50
```

After the cluster is healthy, operators use the Management REST API to deploy inference engines:

```
POST /api/v1/models/deploy
  → RayService.deploy_model()
  → EngineRegistry.get_config_builder(engine_type).build_config()
  → EngineRegistry.get_deployment_factory(engine_type).create_deployment()
  → serve.run(app, name=name, route_prefix=route_prefix)
```

---

## 7. Management API — Request Lifecycle

```mermaid
sequenceDiagram
    participant Ops as Operator
    participant API as Management API<br/>:8001 FastAPI
    participant RS as RayService
    participant Reg as EngineRegistry
    participant Serve as Ray Serve

    Ops->>API: POST /api/v1/models/deploy\n{engine_type:"litellm", route_prefix:"/openai", ...}
    API->>RS: deploy_model(name, engine_type, ...)
    RS->>Reg: get_config_builder("litellm")
    Reg-->>RS: LiteLLMConfigBuilder
    RS->>RS: builder.build_config(user_config)
    RS->>Reg: get_deployment_factory("litellm")
    Reg-->>RS: LiteLLMDeploymentFactory
    RS->>RS: factory.create_deployment(built_config, num_replicas=1)
    RS->>Serve: serve.run(app, name="openai-gateway", route_prefix="/openai")
    Note over RS,Serve: serve.run() runs in a thread with 30s timeout.<br/>Returns "deploying" if cluster not ready yet.
    Serve-->>RS: deployed (or timeout → "deploying")
    RS-->>API: {status:"deployed", name:"openai-gateway", ...}
    API-->>Ops: 200 OK
```

---

## 8. Current Module Structure

```
ray_serve_cai/                          ← importable library
├── __init__.py
├── engines/
│   ├── __init__.py                     ← registers all engines at import time
│   ├── registry.py                     ← EngineRegistry singleton
│   ├── base.py                         ← protocols: ConfigBuilder, DeploymentFactory
│   ├── engine_utils.py                 ← shared ASGI helpers
│   ├── vllm_engine.py / vllm_config.py
│   ├── sglang_engine.py / sglang_config.py
│   ├── litellm_engine.py / litellm_config.py   ← subprocess proxy (100+ providers)
│   ├── yolo_engine.py / yolo_config.py
│   └── mcp_engine.py / mcp_config.py
│   └── mcps/                           ← bundled MCP server implementations
└── management/
    ├── app.py                          ← FastAPI app, mounts all routers
    ├── api/
    │   ├── models.py                   ← /api/v1/models  (deploy / delete / list)
    │   ├── applications.py             ← /api/v1/applications
    │   ├── cluster.py                  ← /api/v1/cluster
    │   ├── nodes.py                    ← /api/v1/nodes
    │   └── metrics.py                  ← /api/v1/metrics
    ├── models/                         ← Pydantic request/response schemas
    └── services/
        ├── ray_service.py              ← serve.run / serve.delete / serve.status
        └── cai_service.py              ← CML application / node lifecycle

cai_integration/                        ← CML-specific job scripts (run, not imported)
├── setup_environment.py                ← Job 1: create .venv + engine venvs
├── setup_litellm_env.py                ← Job 2: create .venv-litellm
├── launch_ray_cluster_job.py           ← Job 3: start head + worker apps
└── ...

demo_configs/                           ← example deploy payloads
├── litellm_openai.json
├── vllm_model.json
└── ...
```

---

## 9. LiteLLM Engine — Internal Architecture

LiteLLM proxies to 100+ providers (OpenAI, Anthropic, AWS Bedrock, Azure, Vertex AI, Ollama, local vLLM, etc.) behind a single OpenAI-compatible interface.

```mermaid
flowchart TB
    subgraph RayActor["LiteLLM Engine Actor (isolated .venv-litellm)"]
        direction TB
        FastAPIApp["FastAPI app\n@serve.ingress"]
        
        subgraph Routes["Routes"]
            R1["POST /v1/chat/completions"]
            R2["POST /v1/completions"]
            R3["GET  /v1/models"]
            R4["GET  /metrics"]
            R5["GET  /health"]
            R6["GET/POST /{path}  ← catch-all for /ui/*"]
        end
        
        Proxy["httpx AsyncClient\nlocalhost:4000"]
        
        subgraph Subprocess["LiteLLM subprocess (Popen)"]
            LLServer["litellm proxy server\nuvicorn :4000"]
            Router["LiteLLM Router\n(model_list from YAML)"]
        end
    end

    subgraph Providers["Cloud Providers"]
        OAI["OpenAI"]
        Ant["Anthropic"]
        Bed["AWS Bedrock"]
        Other["...100+ more"]
    end

    FastAPIApp --> Routes
    Routes --> Proxy
    Proxy --> LLServer
    LLServer --> Router
    Router --> OAI
    Router --> Ant
    Router --> Bed
    Router --> Other
```

**UI proxying**: LiteLLM ships a Next.js management UI at `/ui`. The catch-all route proxies all unmatched paths, rewrites `Location` headers (redirects), and patches root-relative asset paths (`/_next/`) in HTML responses so the browser stays within the `/openai` route prefix.

---

## 10. Key Design Invariants

| Invariant | Why it matters |
|-----------|---------------|
| Engine subprocesses bind to `127.0.0.1` only | Never exposed to the network directly; all ingress goes through the Ray actor HTTP layer |
| `runtime_env["virtualenv"]` is always set | Engine actors always activate the correct isolated venv, regardless of whether the path currently exists on the head node |
| `fcntl.flock` on venv creation | NFS-shared `/home/cdsw` is accessible by all CML pods simultaneously; without the lock, concurrent venv creation corrupts the environment |
| `route_prefix` threaded to `server_root_path` | Subprocess-based engines (LiteLLM, SGLang) need to know their public mount prefix to generate correct redirect and asset URLs |
| Management API runs on a separate port (:8001) | Keeps inference traffic (port 8000) separate from control-plane traffic; simplifies nginx ACLs |
