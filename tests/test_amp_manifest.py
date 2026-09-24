"""Verify the AMP execution contract against its scripts and intended budgets.

Source: Cloudera AMP Project Specification (1.5.5): job timeouts are minutes;
current JupyterLab runtimes use jobs for automated AMP steps.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_amp_input_defaults_resolve_to_strings():
    manifest = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
    for name, definition in manifest["environment_variables"].items():
        assert isinstance(definition["default"], str), name
        assert set(definition) <= {"default", "description", "required"}, name


def test_amp_stages_are_bounded_sequential_jobs():
    manifest = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
    expected = [
        ("configure_project_resources", 5),
        ("setup_base_env", 15),
        ("setup_vllm_env", 30),
        ("setup_litellm_env", 10),
        ("launch_ray_cluster", 20),
        ("launch_monitoring", 15),
    ]
    tasks = manifest["tasks"]
    assert len(tasks) == 2 * len(expected) + 1
    for index, (name, minutes) in enumerate(expected):
        create, run = tasks[2 * index:2 * index + 2]
        assert create["type"] == "create_job"
        assert create["name"] == name
        assert create["cpu"] > 0 and create["memory"] > 0
        assert create["timeout"] == minutes
        assert (ROOT / create["script"]).is_file()
        assert run["type"] == "run_job"
        assert run["entity_label"] == create["entity_label"]
        assert run["wait_for"] is True
    manual_demo = tasks[-1]
    assert manual_demo["type"] == "create_job"
    assert manual_demo["name"] == "amp_llm_demo"
    assert manual_demo["timeout"] == 45
    assert (ROOT / manual_demo["script"]).is_file()
    assert manifest["specification_version"] == "1.0"
    assert isinstance(manifest["prototype_version"], str)
    assert manifest["runtimes"][0]["kernel"] == "Python 3.11"


def test_amp_initializes_head_only_without_worker_resource_inputs():
    manifest = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
    cluster = yaml.safe_load((ROOT / "configs/ray_cluster_config.yaml").read_text())
    env = manifest["environment_variables"]
    assert not {"RAY_LAUNCH_INITIAL_WORKERS", "RAY_WORKER_CPU", "RAY_WORKER_MEMORY",
                "RAY_WORKER_GPUS", "RAY_WORKER_ACCELERATOR_TYPE"} & env.keys()
    assert env["RAY_WORKER_NODE_TYPE"]["default"] == "gpu-worker"
    assert cluster["ray_cluster"]["launch_initial_workers"] is False
    assert cluster["ray_cluster"]["worker_groups"] == []
