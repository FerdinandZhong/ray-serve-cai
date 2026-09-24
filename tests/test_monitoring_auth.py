"""Offline coverage for authenticated monitoring application ingress."""
import io
import json
import stat
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request

import yaml

from cai_integration import launch_monitoring as launch
from cai_integration import launch_ray_cluster as cluster
from cai_integration import provision_monitoring as provision
from cai_integration.monitoring import grafana_launcher as grafana
from ray_serve_cai.scripts import start_nginx


def test_management_prefers_its_own_application_key(monkeypatch):
    from ray_serve_cai.management.services import cai_service

    monkeypatch.setenv("CML_API_KEY", "expired-launch-job-key")
    monkeypatch.setenv("CDSW_APIV2_KEY", "current-application-key")
    manager = Mock()
    monkeypatch.setattr(cai_service, "CAIClusterManager", manager)
    cai_service.CAIService(project_id="p", cml_host="https://example.test")
    assert manager.call_args.kwargs["cml_api_key"] == "current-application-key"


def test_monitoring_creation_and_readiness_use_auth(monkeypatch):
    for key, value in {"CML_HOST": "https://example.test", "CML_API_KEY": "expired-job-key",
                       "CDSW_APIV2_KEY": "current-job-key", "CDSW_PROJECT_ID": "p",
                       "CDSW_DOMAIN": "example.test"}.items():
        monkeypatch.setenv(key, value)
    client = Mock()
    monkeypatch.setattr(launch, "CAIClusterManager", lambda **kw: SimpleNamespace(cml_client=client))
    monkeypatch.setattr(launch, "_load_config", lambda: {})
    health = Mock(return_value=True)
    monkeypatch.setattr(launch, "_wait_healthy", health)
    assert launch.main() == 0
    assert len(client.create_application.call_args_list) == 2
    for call in client.create_application.call_args_list:
        assert call.kwargs["bypass_authentication"] is False
    assert "RAY_METRICS_BEARER_TOKEN" not in client.create_application.call_args_list[0].kwargs["environment"]
    assert "PROMETHEUS_BEARER_TOKEN" not in client.create_application.call_args_list[1].kwargs["environment"]
    assert client.create_application.call_args_list[1].kwargs["environment"]["GRAFANA_ROOT_URL"] == "https://ray-cluster-head.example.test/grafana/"
    assert all(c.kwargs["token"] == "current-job-key" for c in health.call_args_list)


def test_ray_dashboard_uses_authenticated_head_proxy(monkeypatch, tmp_path):
    from jinja2 import Environment, FileSystemLoader

    monkeypatch.setattr(cluster, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("CDSW_DOMAIN", "example.test")
    config = cluster.load_config()["monitoring"]
    assert config["grafana_host"] == "https://grafana-server.example.test"
    assert config["grafana_iframe_host"] == "https://ray-cluster-head.example.test/grafana"
    monkeypatch.setenv("MONITORING_GRAFANA_IFRAME_HOST", config["grafana_host"])
    assert cluster.load_config()["monitoring"]["grafana_iframe_host"] == config["grafana_iframe_host"]
    monkeypatch.setenv("RAY_HEAD_SUBDOMAIN", "custom-ray-head")
    custom_config = cluster.load_config()
    assert custom_config["head_app_name"] == "custom-ray-head"
    assert custom_config["monitoring"]["grafana_iframe_host"] == "https://custom-ray-head.example.test/grafana"
    monkeypatch.delenv("RAY_HEAD_SUBDOMAIN")

    template_dir = cluster.TEMPLATE_DIR
    script = Environment(loader=FileSystemLoader(str(template_dir))).get_template(
        "ray_head_launcher.py.j2"
    ).render(
        venv_python="/home/cdsw/.venv/bin/python", project_dir="/home/cdsw",
        ray_port=6379, dashboard_port=8265, metrics_port=9090,
        mgmt_cpu=1, mgmt_memory_gb=2, prometheus_host=config["prometheus_host"],
        grafana_host=config["grafana_host"],
        grafana_iframe_host=config["grafana_iframe_host"], grafana_org_id="1",
        proxy_health_check_period_s=None, proxy_health_check_timeout_s=None,
        proxy_ready_check_timeout_s=None, proxy_min_draining_period_s=None,
    )
    compile(script, "ray_head_launcher.py", "exec")
    assert 'os.environ["RAY_PROMETHEUS_HEADERS"]' in script
    assert 'os.environ["RAY_GRAFANA_HOST"]' in script
    assert 'os.environ["GRAFANA_PROXY_BEARER_TOKEN"]' in script
    assert '_monitoring_token = os.environ.get("CDSW_APIV2_KEY") or os.environ.get("CML_API_KEY")' in script


def test_nginx_grafana_proxy_keeps_token_on_local_private_disk(monkeypatch, tmp_path):
    monkeypatch.setenv("GRAFANA_PROXY_UPSTREAM", "https://grafana.example.test")
    monkeypatch.setenv("GRAFANA_PROXY_BEARER_TOKEN", "secret-token")
    runtime_dir = tmp_path / "nginx"
    static_dir = tmp_path / "static"
    context = start_nginx.build_context(runtime_dir, static_dir)
    start_nginx.create_runtime_dirs(runtime_dir, static_dir)
    start_nginx.render_templates(runtime_dir, context)
    server_conf = runtime_dir / "conf.d/server.conf"
    rendered = server_conf.read_text()
    assert "proxy_pass https://grafana.example.test/;" in rendered
    assert 'proxy_set_header Authorization "Bearer secret-token";' in rendered
    assert "location /grafana/" in rendered
    # The CAI nginx build omits ngx_http_rewrite_module. Its core module
    # redirects /grafana to /grafana/ for this proxied slash-ending location.
    assert "--without-http_rewrite_module" in (
        start_nginx.TEMPLATE_DIR.parents[2] / "cai_integration/setup_environment.py"
    ).read_text()
    assert "return 308" not in rendered
    assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(server_conf.stat().st_mode) == 0o600


def test_nginx_rejects_untrusted_upstream_or_token(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setenv("GRAFANA_PROXY_BEARER_TOKEN", "secret-token")
    monkeypatch.setenv("GRAFANA_PROXY_UPSTREAM", "https://grafana.example.test/;bad")
    with pytest.raises(ValueError, match="HTTPS origin"):
        start_nginx.build_context(tmp_path, tmp_path)
    monkeypatch.setenv("GRAFANA_PROXY_UPSTREAM", "https://grafana.example.test")
    monkeypatch.setenv("GRAFANA_PROXY_BEARER_TOKEN", 'bad"\nheader')
    with pytest.raises(ValueError, match="valid bearer token"):
        start_nginx.build_context(tmp_path, tmp_path)


def test_readiness_checks_body_not_login_html(monkeypatch):
    import urllib.request
    url = "https://prom.example.test/-/ready"
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = 200
    response.geturl.return_value = url
    response.read.return_value = b"Prometheus Server is Ready."
    opener = Mock(return_value=response)
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    assert launch._wait_healthy(url, timeout=1, token="secret")
    assert opener.call_args.args[0].get_header("Authorization") == "Bearer secret"
    response.read.return_value = b"<html>Login</html>"
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(launch.time, "time", lambda: next(ticks))
    monkeypatch.setattr(launch.time, "sleep", lambda _: None)
    assert not launch._wait_healthy(url, timeout=1, token="secret")


def test_datasource_token_is_not_written_to_shared_file(monkeypatch, tmp_path):
    monkeypatch.setattr(grafana, "PROVISION_DIR", tmp_path)
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "expired-job-token")
    monkeypatch.setenv("CDSW_APIV2_KEY", "current-application-token")
    monkeypatch.setenv("PROMETHEUS_AUTH_HEADER", "")
    grafana.provision_datasource()
    text = (tmp_path / "datasources/prometheus.yml").read_text()
    ds = yaml.safe_load(text)["datasources"][0]
    assert ds["jsonData"]["httpHeaderName1"] == "Authorization"
    assert ds["secureJsonData"]["httpHeaderValue1"] == "$PROMETHEUS_AUTH_HEADER"
    assert "expired-job-token" not in text
    assert grafana.os.environ["PROMETHEUS_AUTH_HEADER"] == "Bearer current-application-token"


def test_provisioning_separates_ingress_and_grafana_credentials(monkeypatch):
    monkeypatch.setenv("CML_API_KEY", "ingress-token")
    monkeypatch.setattr(provision, "GRAFANA_HOST", "https://grafana.example.test")
    monkeypatch.setattr(provision, "_auth_header", lambda: "Basic grafana-credentials")
    response = io.BytesIO(json.dumps({"status": "success"}).encode())
    opener = Mock(return_value=response)
    monkeypatch.setattr(provision, "urlopen", opener)
    provision._post("/api/dashboards/import", {})
    request = opener.call_args.args[0]
    assert request.get_header("Authorization") == "Bearer ingress-token"
    assert request.get_header("X-grafana-authorization") == "Basic grafana-credentials"


def test_proxy_does_not_forward_cai_token(monkeypatch):
    monkeypatch.setenv("GRAFANA_ROOT_URL", "https://head.example.test/grafana/")
    response = Mock(status=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.getheaders.return_value = []
    response.read.return_value = b"{}"
    opener = Mock(return_value=response)
    monkeypatch.setattr(grafana, "_open_upstream", opener)
    handler = SimpleNamespace(path="/api/health", command="GET",
        headers={"Authorization": "Bearer ingress-token", "X-Grafana-Authorization": "Basic grafana-creds"},
        send_response=Mock(), send_header=Mock(), end_headers=Mock(), wfile=io.BytesIO())
    grafana._ProxyHandler._proxy(handler)
    assert opener.call_args.args[0].get_header("Authorization") == "Basic grafana-creds"
    assert not opener.call_args.args[0].has_header("X-grafana-authorization")


def test_grafana_upstream_redirect_is_not_followed(monkeypatch):
    redirect = HTTPError(
        "https://grafana.example.test/", 302, "Found",
        {"Location": "https://head.example.test/grafana/"}, io.BytesIO(b""),
    )
    opener = Mock()
    opener.open.side_effect = redirect
    builder = Mock(return_value=opener)
    monkeypatch.setattr(grafana, "build_opener", builder)
    with grafana._open_upstream(Request("https://grafana.example.test/")) as response:
        assert response.status == 302
        assert response.headers["Location"] == "https://head.example.test/grafana/"
    assert isinstance(builder.call_args.args[0], grafana._NoRedirect)


def test_direct_grafana_browser_navigation_uses_head_ui(monkeypatch):
    monkeypatch.setenv("GRAFANA_ROOT_URL", "https://head.example.test/grafana/")
    opener = Mock(side_effect=AssertionError("direct browser navigation must redirect"))
    monkeypatch.setattr(grafana, "_open_upstream", opener)
    handler = SimpleNamespace(
        path="/d/ray-default?orgId=1", command="GET",
        headers={"Accept": "text/html"},
        send_response=Mock(), send_header=Mock(), end_headers=Mock(),
    )
    grafana._ProxyHandler._proxy(handler)
    handler.send_response.assert_called_once_with(302)
    handler.send_header.assert_called_once_with(
        "Location", "https://head.example.test/grafana/d/ray-default?orgId=1"
    )
    opener.assert_not_called()
