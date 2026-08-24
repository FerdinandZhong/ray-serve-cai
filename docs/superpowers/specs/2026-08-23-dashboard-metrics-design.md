# Ray Dashboard Metrics Tab — Design Spec
Date: 2026-08-23
Branch: feature/dashboard

## Context

The Ray Dashboard metrics tab is blank because the head node's `ray start --head` process is never told where Prometheus or Grafana live. The tab embeds Grafana iframes: without `RAY_PROMETHEUS_HOST` and `RAY_GRAFANA_HOST` set in the process environment at startup, Ray Dashboard shows "Grafana not configured."

Prometheus and Grafana run as separate CML Applications (provisioned via `CAI_Monitoring`). This repo only needs to inject their URLs into the head node environment and optionally provision Ray's built-in Grafana dashboards.

## Scope

**In scope (this repo):**
1. `monitoring:` config block in `ray_cluster_config.yaml`
2. Wire through `load_config()` → Jinja2 context → `ray_head_launcher.py.j2`
3. Head launcher sets `RAY_PROMETHEUS_HOST`, `RAY_GRAFANA_HOST`, `RAY_GRAFANA_IFRAME_HOST` before `ray start`
4. Optional: `cai_integration/provision_monitoring.py` — pulls Ray's dashboard JSONs from the installed ray package and POSTs them to Grafana's HTTP API

**Out of scope:** Running Prometheus/Grafana themselves; provisioning Ray dashboards into Grafana (handled by `CAI_Monitoring`).

## Architecture

```
ray_cluster_config.yaml (monitoring: block)
    ↓ load_config()
launch_ray_cluster.py (context dict)
    ↓ render Jinja2
ray_head_launcher.py.j2
    → os.environ["RAY_PROMETHEUS_HOST"] = "..."
    → os.environ["RAY_GRAFANA_HOST"] = "..."
    → os.environ["RAY_GRAFANA_IFRAME_HOST"] = "..."
    → ray start --head ...  (inherits env)
```

## Config Schema

```yaml
# ray_cluster_config.yaml
monitoring:
  prometheus_host: null      # http://prometheus.<CDSW_DOMAIN>  (head-accessible)
  grafana_host: null         # http://grafana.<CDSW_DOMAIN>:3000  (head-accessible)
  grafana_iframe_host: null  # https://grafana.<CDSW_DOMAIN>  (browser-accessible)
                             # often same as grafana_host in CML
  grafana_org_id: "1"        # Grafana org ID, default 1
```

All fields optional/null → metrics tab stays inactive (no env vars set).

**Env var override** (consistent with existing pattern): `MONITORING_PROMETHEUS_HOST`, `MONITORING_GRAFANA_HOST`, `MONITORING_GRAFANA_IFRAME_HOST` override the config values at render time.

## Head Launcher Changes (`ray_head_launcher.py.j2`)

Insert before the `ray start` subprocess block:

```python
# ── Monitoring (Ray Dashboard metrics tab) ──────────────────────────────────
{% if prometheus_host %}os.environ["RAY_PROMETHEUS_HOST"] = "{{ prometheus_host }}"
{% endif %}{% if grafana_host %}os.environ["RAY_GRAFANA_HOST"] = "{{ grafana_host }}"
{% endif %}{% if grafana_iframe_host %}os.environ["RAY_GRAFANA_IFRAME_HOST"] = "{{ grafana_iframe_host }}"
{% endif %}{% if grafana_org_id %}os.environ["RAY_GRAFANA_ORG_ID"] = "{{ grafana_org_id }}"
{% endif %}
```

Only set vars that are configured — no empty strings.

## Grafana Pre-requisites (not this repo)

The Grafana CML app must:
- Have `allow_embedding = true` in `grafana.ini` (otherwise iframes blocked by X-Frame-Options)
- Be accessible without CML auth for the iframe paths (bypass-authentication on the CML app, or anonymous access in Grafana)
- Have Ray's dashboard JSONs provisioned (either via `CAI_Monitoring/grafana/provisioning/dashboards/` or via `provision_monitoring.py`)

## Optional: `provision_monitoring.py`

Standalone script, runnable anytime after Grafana is up:

1. Locate Ray's built-in dashboard templates: `ray.dashboard.modules.metrics` package path → `grafana_dashboard_templates/` directory
2. For each JSON file, `POST /api/dashboards/import` to `GRAFANA_HOST` with `Bearer` token
3. Print success/failure per dashboard

**Not called from `start_cluster()`.** Must be run separately.

## Data Flow: Metrics Scraping

```
Prometheus (CML app)
  ├─ scrapes http_sd from Management API /api/v1/metrics/discovery
  └─ scrapes /metrics on each Ray node (port from RAY_METRICS_PORT, default 9090)

Ray Dashboard
  └─ embeds Grafana iframes → Grafana queries Prometheus datasource
```

Prometheus datasource in Grafana must point to the Prometheus CML app URL. This is `CAI_Monitoring`'s responsibility.

## Verification

1. Set `monitoring:` block in `ray_cluster_config.yaml` with real Prometheus/Grafana URLs
2. Re-render and launch a fresh head node
3. In `ray start` output, confirm no startup errors related to Prometheus/Grafana
4. Open `/dashboard/` → Metrics tab → should show Grafana panels (not "not configured")
5. If panels blank: check `grafana.ini` allow_embedding, check anonymous/bypass-auth, check dashboard provisioning
6. `GET /api/v1/metrics` (Management API) → should return Prometheus text
7. `GET /api/v1/metrics/discovery` → should return SD JSON

## Files Changed

| File | Change |
|---|---|
| `configs/ray_cluster_config.yaml` | Add `monitoring:` block |
| `cai_integration/launch_ray_cluster.py` | Read monitoring keys in `load_config()`, pass to launcher context |
| `cai_integration/templates/ray_head_launcher.py.j2` | Set monitoring env vars before `ray start` |
| `cai_integration/provision_monitoring.py` | New — optional Grafana dashboard provisioning helper |
