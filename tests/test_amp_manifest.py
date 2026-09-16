"""Verify the AMP execution contract against its scripts and intended budgets.

Source: Cloudera AMP Project Specification (1.5.5): job timeouts are minutes;
current JupyterLab runtimes use jobs for automated AMP steps.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_amp_stages_are_bounded_sequential_jobs():
    manifest = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
    expected = [
        ("setup_base_env", 15),
        ("setup_vllm_env", 30),
        ("setup_litellm_env", 10),
        ("launch_ray_cluster", 20),
        ("launch_monitoring", 15),
        ("amp_llm_demo", 45),
    ]
    tasks = manifest["tasks"]
    assert len(tasks) == 2 * len(expected)
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
    assert manifest["specification_version"] == "1.0"
    assert isinstance(manifest["prototype_version"], str)
    assert manifest["runtimes"][0]["kernel"] == "Python 3.11"


def test_amp_default_placement_matches_launched_worker_capacity():
    manifest = yaml.safe_load((ROOT / ".project-metadata.yaml").read_text())
    cluster = yaml.safe_load((ROOT / "configs/ray_cluster_config.yaml").read_text())
    env = manifest["environment_variables"]
    label = env["GPU_NODE_TYPE"]["default"]
    tp = int(env["TENSOR_PARALLEL_SIZE"]["default"])
    assert any(
        group["node_type"] == label and group["count"] > 0 and group["gpus"] >= tp
        for group in cluster["ray_cluster"]["worker_groups"]
    )
