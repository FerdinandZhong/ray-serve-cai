# Nginx Installation Notes

## Installation Approaches

**Primary: Automatic Compilation from Source**

The `setup_environment.py` script automatically compiles and installs nginx from the official nginx.org source (version 1.28.1).

### Why Compile from Source?

- Official source is reliable and stable (nginx.org)
- No dependency on pre-built binaries (which may not be available)
- Minimal compilation requirements (just gcc and make)
- Works in CML environments without sudo
- Compiled with minimal modules (no PCRE, no zlib needed)

### Fallback Options

If automatic installation fails, the system will look for nginx in these locations (in order):

1. `/home/cdsw/.local/bin/nginx` (user-installed)
2. `/usr/sbin/nginx` (system package)
3. `/usr/bin/nginx` (alternative system location)
4. Result of `which nginx` (PATH search)

## How Automatic Installation Works

The `setup_environment.py` script performs these steps:

1. **Check for existing nginx**: If nginx is already installed at `/home/cdsw/.local/bin/nginx` or available system-wide, it skips installation

2. **Download source**: Downloads nginx-1.28.1.tar.gz from https://nginx.org/download/

3. **Extract**: Extracts the source in a temporary directory

4. **Configure**: Runs `./configure` with these options:
   - `--prefix=/home/cdsw/.local/nginx`
   - `--sbin-path=/home/cdsw/.local/bin/nginx`
   - `--without-http_rewrite_module` (no PCRE dependency)
   - `--without-http_gzip_module` (no zlib dependency)
   - `--with-http_ssl_module`
   - `--with-http_v2_module`

5. **Compile**: Runs `make -j<cores>` (parallel compilation)

6. **Install**: Runs `make install` (installs to user directory, no sudo needed)

7. **Verify**: Tests that the binary works with `nginx -v`

**Time**: Compilation takes 2-3 minutes on typical CML environments.

### Manual Installation Options

#### Option 1: Pre-install in Runtime Image (Fastest)

Add nginx to your CML runtime image to skip compilation:

```dockerfile
# In your custom runtime Dockerfile
RUN yum install -y nginx  # For RHEL/CentOS
# OR
RUN apt-get update && apt-get install -y nginx  # For Debian/Ubuntu
```

#### Option 2: Manual Compilation (if automatic fails)

```bash
cd /tmp
curl -L -o nginx.tar.gz https://nginx.org/download/nginx-1.28.1.tar.gz
tar xzf nginx.tar.gz
cd nginx-1.28.1

./configure \
  --prefix=/home/cdsw/.local/nginx \
  --sbin-path=/home/cdsw/.local/bin/nginx \
  --without-http_rewrite_module \
  --without-http_gzip_module \
  --with-http_ssl_module \
  --with-http_v2_module

make -j$(nproc)
make install

# Verify
/home/cdsw/.local/bin/nginx -v
```

#### Option 3: Skip Nginx (Alternative Architecture)

If nginx installation is not feasible, you can modify the architecture to:

1. **Expose Ray Serve directly** on port 8080 (no Nginx)
   - Change Ray Serve http_options: `{"host": "0.0.0.0", "port": 8080}`
   - Access Management API at: `/api/v1/*`
   - Ray Dashboard not accessible (only via Ray client)

2. **Use Ray Serve for both** (serve dashboard via proxy deployment)
   - More complex but avoids needing Nginx
   - Ray Dashboard proxied through Ray Serve

## Troubleshooting

### "Nginx binary not found" Error

```bash
# Check if nginx exists anywhere on system
which nginx
find /usr -name nginx 2>/dev/null

# If found, create symlink
ln -s /path/to/system/nginx /home/cdsw/.local/bin/nginx
```

### Download Failures

If openresty.org is not accessible:

1. **Check network/firewall**: `curl -I https://openresty.org`
2. **Use proxy if needed**: `export http_proxy=...`
3. **Download externally** and upload to CML:
   - Download on local machine
   - Upload via CML Files UI
   - Extract in CML session

### Architecture Not Supported

For architectures other than x86_64 or aarch64:

```bash
# Check your architecture
uname -m

# You'll need to:
# 1. Compile nginx from source, OR
# 2. Use system package manager (if available), OR
# 3. Skip nginx and expose Ray Serve directly
```

## Testing Nginx Installation

```bash
# Check if installed
ls -la /home/cdsw/.local/bin/nginx

# Check version
/home/cdsw/.local/bin/nginx -v

# Test configuration syntax
/home/cdsw/.local/bin/nginx -t -c /path/to/nginx.conf

# Start nginx (test)
/home/cdsw/.local/bin/nginx -c /path/to/nginx.conf -g 'daemon off;'
```

## Current Implementation Status

As of the latest update:

- ✅ Automatic compilation from official nginx.org source (nginx-1.28.1)
- ✅ Fallback to system nginx if compilation fails
- ✅ Minimal dependencies (no PCRE, no zlib required)
- ✅ Clear error messages if nginx unavailable
- ✅ Graceful degradation (setup continues even if nginx fails)
- ✅ Nginx compiled with SSL and HTTP/2 support
- ℹ️  Nginx is treated as optional component

## Recommendations

1. **For Production**: Pre-install nginx in the runtime image
2. **For Development**: Use automatic installation (good enough)
3. **For Restricted Environments**: Pre-download and upload nginx binary

## Alternative: Run Without Nginx

If you cannot install nginx, modify [launch_ray_cluster.py](cai_integration/launch_ray_cluster.py):

```python
# In deploy_management_app_to_ray()

# Change from:
serve.start(detached=True, http_options={
    "host": "127.0.0.1",  # Internal only
    "port": 8000
})

# To:
public_port = int(os.environ.get('CDSW_APP_PORT', 8080))
serve.start(detached=True, http_options={
    "host": "0.0.0.0",   # Public
    "port": public_port  # Use CML public port
})

# Skip nginx startup section
# Users will access Management API directly at:
# http://head:8080/api/v1/*
```

**Trade-offs**:
- ✅ No nginx dependency
- ❌ Ray Dashboard not accessible via web
- ❌ No unified landing page
- ❌ Less flexibility for future routing
