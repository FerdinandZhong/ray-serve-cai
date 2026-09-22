"""Focused regressions for AMP completion/readiness exit semantics."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_script(name):
    path = Path(__file__).parents[1] / "cai_integration" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


amp_demo = _load_script("amp_demo")
monitoring_job = _load_script("launch_monitoring_job")
launch_monitoring = _load_script("launch_monitoring")


class _Response:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _demo_env(monkeypatch):
    monkeypatch.setenv("CDSW_DOMAIN", "example.test")
    monkeypatch.setenv("CDSW_APIV2_KEY", "secret")
    monkeypatch.setattr(amp_demo.time, "sleep", lambda _seconds: None)


def test_demo_fails_immediately_on_terminal_deploy_failure(monkeypatch):
    _demo_env(monkeypatch)
    responses = iter([
        _Response(200),
        _Response(200),
        _Response(503),
        _Response(200, {"status": "ApplicationStatus.DEPLOY_FAILED"}),
    ])
    monkeypatch.setattr(amp_demo.requests, "get", lambda *a, **k: next(responses))

    assert amp_demo.main() == 1


def test_demo_requires_nonempty_standard_completion(monkeypatch):
    _demo_env(monkeypatch)
    get_responses = iter([_Response(200), _Response(200), _Response(200)])
    monkeypatch.setattr(
        amp_demo.requests, "get", lambda *a, **k: next(get_responses)
    )
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        return _Response(200, {"choices": [{"text": "   "}]})

    monkeypatch.setattr(amp_demo.requests, "post", post)

    assert amp_demo.main() == 1
    assert calls[0][0].endswith("/v1/completions")
    assert "prompt" in calls[0][1]


def test_demo_succeeds_with_nonempty_standard_completion(monkeypatch):
    _demo_env(monkeypatch)
    get_responses = iter([_Response(200), _Response(200), _Response(200)])
    monkeypatch.setattr(
        amp_demo.requests, "get", lambda *a, **k: next(get_responses)
    )
    monkeypatch.setattr(
        amp_demo.requests,
        "post",
        lambda *a, **k: _Response(200, {"choices": [{"text": "It splits work."}]}),
    )

    assert amp_demo.main() == 0


def test_demo_deploy_timeout_fails_with_malformed_status_tolerated(monkeypatch):
    _demo_env(monkeypatch)

    class Clock:
        now = 0

        def time(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(amp_demo.time, "time", clock.time)
    monkeypatch.setattr(amp_demo.time, "sleep", clock.sleep)
    monkeypatch.setattr(amp_demo, "DEPLOY_TIMEOUT_S", 1)
    responses = iter([
        _Response(200),
        _Response(200),
        _Response(503),
        _Response(200, ["not", "a", "mapping"]),
    ])
    monkeypatch.setattr(amp_demo.requests, "get", lambda *a, **k: next(responses))

    assert amp_demo.main() == 1


def test_demo_rejects_non_200_health_then_recovers(monkeypatch):
    _demo_env(monkeypatch)
    responses = iter([
        amp_demo.requests.ConnectionError("transient"),
        _Response(401),
        _Response(200),
        _Response(200),
        _Response(200),
    ])

    def get(*args, **kwargs):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(amp_demo.requests, "get", get)
    monkeypatch.setattr(
        amp_demo.requests,
        "post",
        lambda *a, **k: _Response(200, {"choices": [{"text": "ready"}]}),
    )

    assert amp_demo.main() == 0


def test_venv_detection_uses_active_prefix(monkeypatch, tmp_path):
    venv_python = tmp_path / "venv" / "bin" / "python"
    monkeypatch.setattr(amp_demo.sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(amp_demo.sys, "executable", "/shared/base/python")

    assert amp_demo._running_in_venv(venv_python)


def test_monitoring_job_fails_when_required_provisioning_fails(monkeypatch, tmp_path):
    script = tmp_path / "cai_integration" / "launch_monitoring_job.py"
    script.parent.mkdir()
    script.touch()
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    monkeypatch.setattr(monitoring_job, "__file__", str(script))
    monkeypatch.setenv("GRAFANA_HOST", "https://grafana.example.test")
    results = iter([SimpleNamespace(returncode=0), SimpleNamespace(returncode=7)])
    monkeypatch.setattr(monitoring_job.subprocess, "run", lambda *a, **k: next(results))

    assert monitoring_job.main() == 7


def _monitoring_env(monkeypatch):
    monkeypatch.setenv("CML_HOST", "https://cml.example.test")
    monkeypatch.setenv("CML_API_KEY", "secret")
    monkeypatch.setenv("CDSW_PROJECT_ID", "project")
    monkeypatch.setenv("CDSW_DOMAIN", "example.test")
    client = SimpleNamespace(create_application=lambda **kwargs: None)
    manager = SimpleNamespace(cml_client=client)
    monkeypatch.setattr(
        launch_monitoring, "CAIClusterManager", lambda **kwargs: manager
    )
    monkeypatch.setattr(launch_monitoring, "_load_config", lambda: {})


def test_monitoring_fails_on_prometheus_readiness_timeout(monkeypatch):
    _monitoring_env(monkeypatch)
    checked = []
    monkeypatch.setattr(
        launch_monitoring,
        "_wait_healthy",
        lambda url, timeout, token: checked.append(url) or False,
    )

    assert launch_monitoring.main() == 1
    assert checked == ["https://prometheus-server.example.test/-/ready"]


def test_monitoring_fails_on_grafana_health_timeout(monkeypatch):
    _monitoring_env(monkeypatch)
    checked = []

    def wait(url, timeout, token):
        checked.append(url)
        return len(checked) == 1

    monkeypatch.setattr(launch_monitoring, "_wait_healthy", wait)

    assert launch_monitoring.main() == 1
    assert checked == [
        "https://prometheus-server.example.test/-/ready",
        "https://grafana-server.example.test/api/health",
    ]
