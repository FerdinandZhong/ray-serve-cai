# Nginx Reverse Proxy Design for Ray Cluster Management

## Problem

CML applications can only expose **one port** (specified by `CDSW_APP_PORT`), but we need to serve two services:
1. **Ray Dashboard** (port 8265) - Cluster monitoring and observability
2. **Management API** (Ray Serve on port 8000) - REST API for cluster management

## Solution: Nginx Reverse Proxy

Use Nginx as a reverse proxy listening on the single public port (8080) and routing requests to internal services.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CML Application (Head Node)               │
│                                                              │
│  Public Port 8080 (CDSW_APP_PORT)                           │
│  │                                                           │
│  └──> Nginx Reverse Proxy                                   │
│        │                                                     │
│        ├──> /dashboard/*  ────> Ray Dashboard (127.0.0.1:8265)
│        │                                                     │
│        ├──> /api/*        ────> Ray Serve (127.0.0.1:8000/api/*)
│        │                        └─> Management API (FastAPI) │
│        │                                                     │
│        ├──> /docs         ────> Ray Serve (127.0.0.1:8000/api/docs)
│        │                                                     │
│        ├──> /redoc        ────> Ray Serve (127.0.0.1:8000/api/redoc)
│        │                                                     │
│        └──> /             ────> Landing page (HTML)          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Port Allocation

| Service          | Host      | Port | Access      | Purpose                          |
|------------------|-----------|------|-------------|----------------------------------|
| Nginx            | 0.0.0.0   | 8080 | **Public**  | Reverse proxy (CML exposed port) |
| Ray Dashboard    | 127.0.0.1 | 8265 | Internal    | Cluster monitoring UI            |
| Ray Serve        | 127.0.0.1 | 8000 | Internal    | Management API (FastAPI)         |
| Ray GCS          | 0.0.0.0   | 6379 | Internal    | Ray cluster coordination         |

## URL Routing

### External URLs (accessed by users)

| URL Path                      | Maps To                    | Description                  |
|-------------------------------|----------------------------|------------------------------|
| `http://head:8080/`           | Landing page               | HTML with links              |
| `http://head:8080/dashboard/` | Ray Dashboard (8265)       | Cluster monitoring           |
| `http://head:8080/api/v1/*`   | Management API (8000)      | REST API endpoints           |
| `http://head:8080/docs`       | Management API docs (8000) | Swagger UI                   |
| `http://head:8080/redoc`      | Management API docs (8000) | ReDoc documentation          |
| `http://head:8080/health`     | Health check (8000)        | Service health status        |

### Internal Ray Serve Routing

Ray Serve is configured with `route_prefix="/api"`, which means:
- FastAPI routes defined as `/api/v1/cluster/status` become `/api/api/v1/cluster/status` in Ray Serve
- FastAPI docs at `/docs` become `/api/docs` in Ray Serve
- Nginx handles the mapping to expose clean URLs externally

## Implementation Files

### 1. Nginx Configuration Template
**File**: `ray_serve_cai/configs/nginx.conf.template`

- Defines upstream servers for Ray Dashboard and Ray Serve
- Routes requests based on path prefixes
- Substitutes `${CDSW_APP_PORT}` environment variable

**Key Features**:
- WebSocket support for Ray Dashboard
- CORS headers for API
- Serves static landing page from `ray_serve_cai/static/index.html`
- Large file uploads (100MB)

### 1a. Static Landing Page
**File**: `ray_serve_cai/static/index.html`

- Beautiful, responsive HTML landing page
- Links to all services (API docs, dashboard, endpoints)
- Modern CSS styling with gradient background
- Easy to customize and extend

### 2. Nginx Startup Script
**File**: `ray_serve_cai/scripts/start_nginx.py`

- Generates nginx.conf from template
- Creates necessary directories (/home/cdsw/logs)
- Starts Nginx process
- Tests if Nginx is responding

**Usage**:
```bash
/home/cdsw/.venv/bin/python /home/cdsw/ray_serve_cai/scripts/start_nginx.py
```

### 3. Ray Head Launcher
**File**: `cai_integration/launch_ray_cluster.py` → `ray_head_launcher.py`

Updated to start Ray Dashboard on **internal port 8265** (127.0.0.1):
```python
ray start --head --dashboard-host 127.0.0.1 --dashboard-port 8265
```

### 4. Management API Deployment
**File**: `cai_integration/launch_ray_cluster.py` → `deploy_management_app_to_ray()`

Deploys Management API to Ray Serve with:
- **Internal port**: 8000 (127.0.0.1)
- **Route prefix**: `/api`
- **Resources**: 4 CPU, 8GB RAM

Then starts Nginx proxy on public port 8080.

## Ray Serve Route Prefix Behavior

When Ray Serve deploys with `route_prefix="/api"`, all FastAPI routes are nested under `/api`:

| FastAPI Route       | Ray Serve URL         | Nginx Public URL    |
|---------------------|-----------------------|---------------------|
| `/api/v1/cluster/status` | `/api/api/v1/cluster/status` | `/api/v1/cluster/status` |
| `/docs`             | `/api/docs`           | `/docs`             |
| `/redoc`            | `/api/redoc`          | `/redoc`            |
| `/health`           | `/api/health`         | `/health`           |

Nginx `proxy_pass` directives handle the mapping:
```nginx
location /api/ {
    proxy_pass http://ray_serve/api/;  # Appends /api/ to target
}

location /docs {
    proxy_pass http://ray_serve/api/docs;  # Full target path
}
```

## Setup and Installation

### 1. Install Nginx (in setup_environment.py)

Nginx is installed **without sudo** by downloading a static binary from GitHub:

```python
def install_nginx():
    """Install nginx binary without sudo (download precompiled binary)."""
    # Download static nginx binary from GitHub releases
    # Source: https://github.com/just-containers/nginx-static
    # Installs to: /home/cdsw/.local/bin/nginx

    download_url = f"https://github.com/just-containers/nginx-static/releases/download/v1.25.3/nginx-linux-{arch_suffix}.tar.gz"

    # Downloads, extracts, and installs to user directory
    # No root/sudo privileges required!
```

**Key Benefits:**
- ✅ No sudo required
- ✅ Static binary with no dependencies
- ✅ Works in restricted CML environments
- ✅ Supports x86_64 (amd64) and ARM64 architectures

### 2. Start Ray Head with Internal Dashboard

```bash
ray start --head \
  --port 6379 \
  --dashboard-host 127.0.0.1 \  # Internal only
  --dashboard-port 8265
```

### 3. Deploy Management API to Ray Serve

```python
serve.start(detached=True, http_options={
    "host": "127.0.0.1",  # Internal only
    "port": 8000
})

@serve.deployment(route_prefix="/api", num_replicas=1)
@serve.ingress(app)
class ManagementAPI:
    pass

serve.run(deployment, route_prefix="/api")
```

### 4. Start Nginx Proxy

```bash
python /home/cdsw/ray_serve_cai/scripts/start_nginx.py
```

## Benefits

1. **Single Public Port**: Only port 8080 needs to be exposed by CML
2. **Clean URLs**: Users access `/dashboard/` and `/api/v1/*` without knowing internal ports
3. **Flexibility**: Easy to add more services later by updating Nginx config
4. **Security**: Internal services (8000, 8265) not directly accessible
5. **Standard Pattern**: Industry-standard reverse proxy architecture

## Troubleshooting

### Nginx not starting
```bash
# Check if nginx is installed
ls -la /home/cdsw/.local/bin/nginx
/home/cdsw/.local/bin/nginx -v

# Check nginx configuration syntax
/home/cdsw/.local/bin/nginx -t -c /home/cdsw/nginx.conf

# Check logs
tail -f /home/cdsw/logs/nginx_error.log

# Manual start for debugging
/home/cdsw/.local/bin/nginx -c /home/cdsw/nginx.conf -g 'daemon off;'
```

### Service not accessible
```bash
# Test internal services
curl http://127.0.0.1:8265  # Ray Dashboard
curl http://127.0.0.1:8000/api/health  # Management API

# Test Nginx
curl http://127.0.0.1:8080/health
```

### Port conflicts
```bash
# Check what's using ports
lsof -i :8080
lsof -i :8000
lsof -i :8265

# Kill processes if needed
kill -9 <PID>
```

## Configuration Variables

| Variable         | Default | Description                    |
|------------------|---------|--------------------------------|
| CDSW_APP_PORT    | 8080    | Public port (CML exposed)      |
| RAY_DASHBOARD_PORT | 8265  | Internal Ray Dashboard port    |
| RAY_SERVE_PORT   | 8000    | Internal Ray Serve port        |

## Customization

### Customize Landing Page

The landing page is a static HTML file that you can easily customize:

**File**: `ray_serve_cai/static/index.html`

```bash
# Edit the landing page
vim /home/cdsw/ray_serve_cai/static/index.html

# No restart needed - changes are immediately visible
# (Nginx serves static files directly)
```

You can:
- Add your company logo or branding
- Customize colors and styling (CSS is inline)
- Add additional links or documentation
- Include custom JavaScript for dynamic features

### Update Nginx Routing

If you need to modify Nginx routing:

1. **Edit Template**: `ray_serve_cai/configs/nginx.conf.template`

2. **Regenerate Config and Restart**:
   ```bash
   python /home/cdsw/ray_serve_cai/scripts/start_nginx.py
   ```
   This will regenerate `/home/cdsw/nginx.conf` and restart Nginx.

3. **Or Reload Config Without Restart**:
   ```bash
   /home/cdsw/.local/bin/nginx -s reload
   ```

## Future Enhancements

1. **HTTPS/TLS**: Add SSL certificates to Nginx
2. **Authentication**: Add auth layer in Nginx or FastAPI
3. **Rate Limiting**: Nginx rate limiting for API endpoints
4. **Caching**: Cache static assets in Nginx
5. **Load Balancing**: If multiple Management API replicas
