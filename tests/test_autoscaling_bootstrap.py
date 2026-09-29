"""New-cluster defaults and one-time initial resource specifications."""

import json

import pytest

from cai_integration import launch_ray_cluster as launcher
from cai_integration.autoscaling.bootstrap import bootstrap_policy, initialize_scaling
from ray_serve_cai.autoscaling.store import ScalingStore


def test_amp_initial_pools_preserve_multiple_shapes_and_have_no_implicit_cap(monkeypatch, tmp_path):
    pools = [
        {"id": "gpu", "initial_workers": 2, "worker_spec": {"cpu": 16, "memory": 64, "gpus": 1}},
        {
            "id": "cpu",
            "initial_workers": 3,
            "min_workers": 1,
            "worker_spec": {"cpu": 4, "memory": 8, "gpus": 0, "runtime_identifier": "cpu-runtime"},
        },
    ]
    monkeypatch.setenv("RAY_INITIAL_WORKER_POOLS", json.dumps(pools))
    config = launcher.load_config()
    policy = bootstrap_policy(config)
    assert policy.enabled and policy.mode == "full"
    assert policy.max_workers is None and policy.max_gpus is None
    assert [p.initial_workers for p in policy.pools] == [2, 3]
    assert [p.min_workers for p in policy.pools] == [0, 1]
    assert policy.pools[1].worker_spec.runtime_identifier == "cpu-runtime"
    path = tmp_path / "state.json"
    initialize_scaling(config, path)
    store = ScalingStore(path)
    changed = policy.model_copy(deep=True)
    changed.mode = "observe"
    store.set_policy(changed)
    initialize_scaling(config, path)
    assert store.read()["policy"]["mode"] == "observe"
    assert store.read()["revision"] == 2  # Bootstrap retry does not replace API edits.


def test_no_initial_resources_still_starts_with_scaling_capability(monkeypatch):
    monkeypatch.delenv("RAY_INITIAL_WORKER_POOLS", raising=False)
    policy = bootstrap_policy(launcher.load_config())
    assert policy.enabled and policy.mode == "full"
    assert policy.pools == []


def test_blank_amp_default_preserves_yaml_pool_choices(monkeypatch, tmp_path):
    import yaml

    (tmp_path / "configs").mkdir()
    pools = [{"id": "cpu", "initial_workers": 2, "worker_spec": {"cpu": 4, "memory": 8}}]
    (tmp_path / "configs" / "ray_cluster_config.yaml").write_text(
        yaml.safe_dump({"ray_cluster": {"worker_pools": pools}})
    )
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("RAY_INITIAL_WORKER_POOLS", "")
    assert launcher.load_config()["worker_pools"] == pools
    monkeypatch.setenv("RAY_INITIAL_WORKER_POOLS", "[]")
    assert launcher.load_config()["worker_pools"] == []


@pytest.mark.parametrize("raw", ['{"cpu": 4}', "not-json"])
def test_bad_initial_pool_input_fails_before_cloud_launch(monkeypatch, raw):
    monkeypatch.setenv("RAY_INITIAL_WORKER_POOLS", raw)
    with pytest.raises(ValueError):
        launcher.load_config()


def test_optional_budget_and_no_duplicate_legacy_launch_path():
    config = {
        "worker_runtime_identifier": "runtime",
        "worker_pools": [
            {"id": "gpu", "initial_workers": 2, "worker_spec": {"cpu": 4, "memory": 8, "gpus": 1}}
        ],
        "autoscaling": {"limits": {"max_gpus": 1}},
    }
    with pytest.raises(ValueError, match="max_gpus"):
        bootstrap_policy(config)
    config["autoscaling"]["limits"] = None
    config["worker_groups"] = [{"count": 1}]
    with pytest.raises(ValueError, match="not both"):
        bootstrap_policy(config)


@pytest.mark.parametrize("override,enabled", [(None, True), ("0", False)])
def test_rendered_head_starts_single_controller_by_default(monkeypatch, override, enabled):
    import ast
    import os
    from unittest.mock import Mock

    from jinja2 import Environment, FileSystemLoader

    if override is None:
        monkeypatch.delenv("RAY_CAI_AUTOSCALING", raising=False)
    else:
        monkeypatch.setenv("RAY_CAI_AUTOSCALING", override)
    # Isolate the rendered start command, including its real default/override
    # logic. Do not launch Ray/NGINX or read project credentials in this test.
    template = Environment(loader=FileSystemLoader(str(launcher.TEMPLATE_DIR)))
    script = template.get_template("ray_head_launcher.py.j2").render(
        ray_port=6379,
        dashboard_port=8265,
        metrics_port=9090,
        proxy_health_check_period_s=None,
        proxy_health_check_timeout_s=None,
        proxy_ready_check_timeout_s=None,
        proxy_min_draining_period_s=None,
    )
    tree = ast.parse(script)
    start = next(
        i
        for i, stmt in enumerate(tree.body)
        if isinstance(stmt, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_autoscaling_enabled" for t in stmt.targets)
    )
    subprocess = Mock()
    fake_os = Mock(environ=dict(os.environ))
    ns = {"os": fake_os, "subprocess": subprocess, "RAY_BIN": "/test/ray"}
    exec(
        compile(
            ast.Module(body=tree.body[start : start + 3], type_ignores=[]), "<head-start>", "exec"
        ),
        ns,
    )
    assert ns["_autoscaling_enabled"] is enabled
    assert ("--no-monitor" in subprocess.run.call_args.args[0]) is enabled
    if enabled:
        assert fake_os.environ["RAY_CAI_AUTOSCALING"] == "1"
        assert fake_os.environ["RAY_enable_autoscaler_v2"] == "1"
