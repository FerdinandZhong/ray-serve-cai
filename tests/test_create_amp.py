"""Prove selected AMP values reach worker templates without UI serialization."""

from unittest.mock import Mock

import pytest
import yaml

from cai_integration import create_amp


def manifest():
    return yaml.safe_load((create_amp.ROOT / ".project-metadata.yaml").read_text())


def test_selected_head_resources_survive_payload_without_worker_configuration(monkeypatch):
    from cai_integration import launch_ray_cluster as launcher

    payload = create_amp.build_payload(
        manifest(), {"RAY_HEAD_CPU": "12", "RAY_HEAD_MEMORY": "64"},
        name="example", runtime="runtime", git_url="https://example.test/repo.git",
        git_ref="feature/blueprint_fix",
    )
    env = payload["create_project_request"]["environment"]
    assert all(isinstance(v, str) for v in env.values())
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = launcher.load_config()
    groups = launcher.build_worker_groups(config)
    assert (config["head_cpu"], config["head_memory"]) == (12, 64)
    assert groups == []


@pytest.mark.parametrize("bad", [12, None, [], {"nativeEvent": {}, "target": None}])
def test_reject_non_string_inputs_instead_of_defaulting(bad):
    with pytest.raises(ValueError, match="RAY_HEAD_CPU"):
        create_amp.resolve_inputs(manifest()["environment_variables"], {"RAY_HEAD_CPU": bad})


@pytest.mark.parametrize("bad", ["-1", "0", "twelve", "12.5"])
def test_reject_invalid_resource_strings(bad):
    with pytest.raises(ValueError):
        create_amp.resolve_inputs(manifest()["environment_variables"], {"RAY_HEAD_CPU": bad})


def test_shared_memory_is_not_an_amp_form_input():
    assert "RAY_SHARED_MEMORY_LIMIT_MB" not in manifest()["environment_variables"]


def test_unknown_inputs_are_not_silently_ignored():
    with pytest.raises(ValueError, match="Unknown AMP inputs"):
        create_amp.resolve_inputs(manifest()["environment_variables"], {"TYPO": "12"})


def test_dry_run_never_calls_api_or_reads_token(monkeypatch, capsys):
    post = Mock(side_effect=AssertionError("unexpected network request"))
    monkeypatch.setattr(create_amp.requests, "post", post)
    assert create_amp.main([
        "--name", "example", "--runtime", "runtime", "--git-ref", "branch",
        "--token-file", "/nonexistent",
    ]) == 0
    post.assert_not_called()
    assert "Dry run" in capsys.readouterr().out


def test_explicit_apply_posts_flat_values_to_amp_not_existing_project(monkeypatch):
    post = Mock(return_value=Mock(status_code=200))
    monkeypatch.setattr(create_amp.requests, "post", post)
    monkeypatch.setenv("CML_API_KEY", "test-secret")
    create_amp.main([
        "--name", "example", "--runtime", "runtime", "--git-ref", "branch",
        "--host", "https://example.test", "--apply",
    ])
    assert post.call_args.args == ("https://example.test/api/v2/amps",)
    env = post.call_args.kwargs["json"]["create_project_request"]["environment"]
    assert "RAY_WORKER_CPU" not in env
    assert "RAY_WORKER_MEMORY" not in env
    assert "RAY_WORKER_ACCELERATOR_TYPE" not in env


def test_worker_resources_must_be_defined_after_initialization():
    with pytest.raises(ValueError, match="Unknown AMP inputs"):
        create_amp.resolve_inputs(manifest()["environment_variables"], {"RAY_WORKER_ACCELERATOR_TYPE": "L40S"})
