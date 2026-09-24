# Nginx installation for the Ray head

The Ray head uses nginx as its public entry point. When Grafana is configured,
nginx also proxies `/grafana/` to a separate CAI application over HTTPS. CAI
terminates the browser's TLS connection, but nginx still needs its own HTTP SSL
module for this outbound connection.

The `setup_base_env` job installs an SSL-capable nginx at
`/home/cdsw/.local/bin/nginx-ssl`. It reuses a system nginx only if `nginx -V`
shows `--with-http_ssl_module`. Otherwise it compiles nginx 1.29.7 from the
official source with that module enabled. If the runtime lacks OpenSSL
development headers, setup builds a checksum-verified OpenSSL source release
with nginx. The previous HTTP-only binary at `/home/cdsw/.local/bin/nginx` is
left in place, and the head prefers `nginx-ssl`.

After updating the project code, rerun `setup_base_env` before restarting the
head application. A head restart alone will keep using the previously installed
HTTP-only binary. The setup job has a 30-minute timeout to allow for an OpenSSL
source build.

To verify the installation in a CAI terminal:

```bash
/home/cdsw/.local/bin/nginx-ssl -V 2>&1 | grep -- --with-http_ssl_module
/home/cdsw/.local/bin/nginx-ssl -t -c /tmp/ray_serve_cai_nginx/nginx.conf
```

The second command is useful after the head has rendered its nginx config. If
the first command fails, inspect the `setup_base_env` job log. If startup says
`https protocol requires SSL support`, the head is still using an HTTP-only
nginx binary. The launcher now rejects that binary when Grafana proxying is
enabled and reports that `setup_base_env` must be rerun.
