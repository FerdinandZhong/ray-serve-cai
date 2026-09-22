"""Offline coverage for authenticated monitoring application ingress."""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import yaml

from cai_integration import launch_monitoring as launch
from cai_integration import provision_monitoring as provision
from cai_integration.monitoring import grafana_launcher as grafana


def test_monitoring_creation_and_readiness_use_auth(monkeypatch):
    for key, value in {"CML_HOST": "https://example.test", "CML_API_KEY": "secret",
                       "CDSW_PROJECT_ID": "p", "CDSW_DOMAIN": "example.test"}.items():
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
    assert client.create_application.call_args_list[1].kwargs["environment"]["PROMETHEUS_BEARER_TOKEN"] == "secret"
    assert all(c.kwargs["token"] == "secret" for c in health.call_args_list)


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
    monkeypatch.setenv("PROMETHEUS_BEARER_TOKEN", "sensitive-token")
    monkeypatch.setenv("PROMETHEUS_AUTH_HEADER", "")
    grafana.provision_datasource()
    text = (tmp_path / "datasources/prometheus.yml").read_text()
    ds = yaml.safe_load(text)["datasources"][0]
    assert ds["jsonData"]["httpHeaderName1"] == "Authorization"
    assert ds["secureJsonData"]["httpHeaderValue1"] == "$PROMETHEUS_AUTH_HEADER"
    assert "sensitive-token" not in text


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
    response = Mock(status=200)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.getheaders.return_value = []
    response.read.return_value = b"{}"
    opener = Mock(return_value=response)
    monkeypatch.setattr(grafana, "urlopen", opener)
    handler = SimpleNamespace(path="/api/health", command="GET",
        headers={"Authorization": "Bearer ingress-token", "X-Grafana-Authorization": "Basic grafana-creds"},
        send_response=Mock(), send_header=Mock(), end_headers=Mock(), wfile=io.BytesIO())
    grafana._ProxyHandler._proxy(handler)
    assert opener.call_args.args[0].get_header("Authorization") == "Basic grafana-creds"
    assert not opener.call_args.args[0].has_header("X-grafana-authorization")
