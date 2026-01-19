# Quick Start: Nginx Proxy Setup

## Overview

This guide covers the Nginx reverse proxy setup that allows serving both Ray Dashboard and Management API through a single CML application port.

## Architecture Summary

```
Public Port 8080 (CML Exposed)
  │
  └─> Nginx Reverse Proxy
       ├─> /dashboard/*  → Ray Dashboard (127.0.0.1:8265)
       ├─> /api/v1/*     → Management API (127.0.0.1:8000)
       ├─> /docs         → API Documentation (Swagger)
       └─> /             → Landing Page
```

## Automatic Setup

The setup is **fully automated** when deploying a Ray cluster:

1. `setup_environment.py` - Installs Nginx binary (no sudo needed)
2. `launch_ray_cluster.py` - Deploys Management API and starts Nginx

## Manual Verification

### 1. Check Nginx Installation

```bash
# Verify nginx binary exists
ls -la /home/cdsw/.local/bin/nginx

# Check version
/home/cdsw/.local/bin/nginx -v
```

**Expected Output:**
```
nginx version: nginx/1.25.3
```

### 2. Check Services are Running

```bash
# Check Ray Dashboard (internal)
curl http://127.0.0.1:8265

# Check Management API (internal)
curl http://127.0.0.1:8000/api/health

# Check Nginx (public)
curl http://127.0.0.1:8080/health
```

### 3. Access from Browser

Assuming your head node is accessible at `https://ray-head-abc123.cml.example.com`:

- **Landing Page**: https://ray-head-abc123.cml.example.com/
- **Ray Dashboard**: https://ray-head-abc123.cml.example.com/dashboard/
- **API Docs**: https://ray-head-abc123.cml.example.com/docs
- **Cluster Status**: https://ray-head-abc123.cml.example.com/api/v1/cluster/status

## Configuration Files

| File | Purpose |
|------|---------|
| `/home/cdsw/ray_serve_cai/configs/nginx.conf.template` | Nginx config template |
| `/home/cdsw/nginx.conf` | Generated Nginx config (runtime) |
| `/home/cdsw/logs/nginx_access.log` | Nginx access logs |
| `/home/cdsw/logs/nginx_error.log` | Nginx error logs |

## Troubleshooting

### Nginx Won't Start

```bash
# Test configuration syntax
/home/cdsw/.local/bin/nginx -t -c /home/cdsw/nginx.conf

# Check error logs
tail -50 /home/cdsw/logs/nginx_error.log

# Try starting in foreground (for debugging)
/home/cdsw/.local/bin/nginx -c /home/cdsw/nginx.conf -g 'daemon off;'
```

### Port Already in Use

```bash
# Check what's using port 8080
lsof -i :8080

# Kill process if needed
kill -9 <PID>
```

### Service Returns 502 Bad Gateway

This means Nginx is running but can't reach backend services:

```bash
# Check if Ray Dashboard is running
curl http://127.0.0.1:8265
# Should return HTML

# Check if Ray Serve is running
curl http://127.0.0.1:8000/api/health
# Should return JSON: {"status": "healthy", ...}

# If not, restart Ray Serve from Ray cluster
ray.serve.status()  # Check status
```

### Can't Access from Browser

1. **Check CML Application Status**
   - Go to CML UI → Applications
   - Verify head node application is "Running"
   - Check the application URL

2. **Check Firewall/Network**
   - Verify CML security groups allow port 8080
   - Check if VPN/network access is required

3. **Check Nginx is Listening**
   ```bash
   netstat -tlnp | grep 8080
   # Should show nginx listening on *:8080
   ```

## Manual Restart

If you need to restart Nginx:

```bash
# Stop Nginx
pkill -f 'nginx: master process'

# Or use nginx command
/home/cdsw/.local/bin/nginx -s stop

# Start Nginx
python /home/cdsw/ray_serve_cai/scripts/start_nginx.py
```

## Logs

### View Nginx Logs

```bash
# Access logs (HTTP requests)
tail -f /home/cdsw/logs/nginx_access.log

# Error logs (problems/warnings)
tail -f /home/cdsw/logs/nginx_error.log
```

### View Ray Serve Logs

```bash
# Ray Serve logs are in Ray Dashboard
# Access at: http://127.0.0.1:8265/logs
# Or via CLI:
ray logs serve --follow
```

## Updating Configuration

If you need to modify Nginx routing:

1. **Edit Template**: `ray_serve_cai/configs/nginx.conf.template`

2. **Regenerate Config**:
   ```bash
   python /home/cdsw/ray_serve_cai/scripts/start_nginx.py
   ```
   This will regenerate `/home/cdsw/nginx.conf` and restart Nginx.

3. **Or Reload Manually**:
   ```bash
   /home/cdsw/.local/bin/nginx -s reload
   ```

## Port Reference

| Service | Host | Port | Access | URL |
|---------|------|------|--------|-----|
| Nginx Proxy | 0.0.0.0 | 8080 | **Public** | `http://head:8080/` |
| Ray Dashboard | 127.0.0.1 | 8265 | Internal | `http://127.0.0.1:8265/` |
| Ray Serve | 127.0.0.1 | 8000 | Internal | `http://127.0.0.1:8000/` |
| Ray GCS | 0.0.0.0 | 6379 | Internal | - |

## Key Points

✅ **No sudo required** - Nginx installed as static binary in user directory
✅ **Fully automated** - Setup happens during cluster launch
✅ **Single public port** - All services accessible through port 8080
✅ **Clean URLs** - No port numbers needed in URLs
✅ **Production-ready** - WebSocket support, proper headers, logging

## Further Reading

- [Full Nginx Proxy Design](./NGINX_PROXY_DESIGN.md) - Complete architecture documentation
- [Management API README](./ray_serve_cai/management/README.md) - API endpoints reference
- [Ray Serve Documentation](https://docs.ray.io/en/latest/serve/) - Official Ray Serve docs
