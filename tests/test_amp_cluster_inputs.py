"""AMP resource-input behavior without a live CML cluster."""

import cai_integration.launch_ray_cluster as launcher
import pytest


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
