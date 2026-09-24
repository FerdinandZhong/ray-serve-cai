"""AMP resource-input behavior without a live CML cluster."""

import pytest

import cai_integration.launch_ray_cluster as launcher


def test_explicit_empty_groups_never_fall_back_to_workers(monkeypatch):
    monkeypatch.setenv("RAY_LAUNCH_INITIAL_WORKERS", "true")
    monkeypatch.setenv("RAY_WORKER_CPU", "12")
    monkeypatch.setenv("RAY_WORKER_MEMORY", "64")
    config = launcher.load_config()
    assert config["worker_groups"] == []
    assert launcher.build_worker_groups(config) == []


def test_post_start_l40s_definition_preserves_user_resources():
    from ray_serve_cai.management.models.requests import DefineNodeTypeRequest, AddNodeRequest

    definition = DefineNodeTypeRequest(
        node_type="gpu-worker", cpu=12, memory=64, gpus=1,
        accelerator_type="L40S", count=0,
    )
    assert (definition.cpu, definition.memory, definition.accelerator_type) == (12, 64, "L40S")
    assert definition.count == 0
    assert AddNodeRequest(node_type=definition.node_type).node_type == "gpu-worker"


def test_amp_worker_inputs_override_only_the_zero_worker_template(monkeypatch):
    config = {
        "worker_groups": [
            {
                "name": "input-gpu", "node_type": "gpu-worker", "count": 0,
                "cpu": 20, "memory": 200, "gpus": 2, "input_template": True,
            },
            {
                "name": "fixed-cpu", "node_type": "cpu-worker", "count": 0,
                "cpu": 8, "memory": 16, "gpus": 0,
            },
        ],
        "worker_node_type": None,
        "worker_gpus": 0,
        "num_workers": 1,
        "worker_cpu": 1,
        "worker_memory": 4,
    }
    monkeypatch.setenv("RAY_WORKER_NODE_TYPE", "l40s-on-demand")
    monkeypatch.setenv("RAY_WORKER_CPU", "16")
    monkeypatch.setenv("RAY_WORKER_MEMORY", "64")
    monkeypatch.setenv("RAY_WORKER_GPUS", "1")
    monkeypatch.setenv("RAY_WORKER_ACCELERATOR_TYPE", "L40S")

    groups = launcher.build_worker_groups(config)

    assert [(g.node_type, g.count, g.cpu, g.memory, g.gpus) for g in groups] == [
        ("l40s-on-demand", 0, 16, 64, 1),
        ("cpu-worker", 0, 8, 16, 0),
    ]
    assert groups[0].accelerator_type == "L40S"


def test_head_resource_env_overrides_and_management_defaults(monkeypatch):
    monkeypatch.setenv("RAY_HEAD_CPU", "20")
    monkeypatch.setenv("RAY_HEAD_MEMORY", "80")
    monkeypatch.delenv("RAY_MANAGEMENT_API_CPU", raising=False)
    monkeypatch.delenv("RAY_MANAGEMENT_API_MEMORY", raising=False)

    config = launcher.load_config()

    assert config["head_cpu"] == 20
    assert config["head_memory"] == 80
    assert config["management_api_cpu"] is None
    assert config["management_api_memory"] is None


def test_blank_amp_inputs_do_not_override_or_crash(monkeypatch):
    """AMP exports optional fields as empty strings, not absent variables."""
    for name in (
        "RAY_HEAD_CPU", "RAY_HEAD_MEMORY", "RAY_MANAGEMENT_API_CPU",
        "RAY_MANAGEMENT_API_MEMORY", "RAY_WORKER_CPU", "RAY_WORKER_MEMORY",
        "RAY_WORKER_GPUS", "RAY_WORKER_NODE_TYPE",
        "RAY_LAUNCH_INITIAL_WORKERS",
        "RAY_SERVE_PROXY_HEALTH_CHECK_PERIOD_S", "MONITORING_GRAFANA_HOST",
    ):
        monkeypatch.setenv(name, "")

    config = launcher.load_config()

    assert config["head_cpu"] == 12
    assert config["head_memory"] == 32
    assert config["management_api_cpu"] is None
    assert config["worker_node_type"] is None


def test_worker_types_are_registered_without_launching_initial_workers(monkeypatch):
    config = {
        "launch_initial_workers": False,
        "worker_groups": [
            {"name": "gpu", "node_type": "gpu-worker", "count": 2,
             "cpu": 20, "memory": 200, "gpus": 2, "input_template": True},
        ],
    }

    groups = launcher.build_worker_groups(config)

    assert len(groups) == 1
    assert groups[0].node_type == "gpu-worker"
    assert groups[0].count == 0


def test_worker_launch_switch_can_reenable_configured_counts(monkeypatch):
    monkeypatch.setenv("RAY_LAUNCH_INITIAL_WORKERS", "true")
    config = launcher.load_config()

    assert config["launch_initial_workers"] is True


def test_amp_worker_type_rejects_unsafe_identifier(monkeypatch):
    config = {
        "worker_groups": [
            {"name": "input", "node_type": "gpu-worker", "count": 0,
             "cpu": 4, "memory": 16, "input_template": True},
        ],
    }
    monkeypatch.setenv("RAY_WORKER_NODE_TYPE", "gpu worker; bad")

    with pytest.raises(ValueError, match="RAY_WORKER_NODE_TYPE"):
        launcher.build_worker_groups(config)
