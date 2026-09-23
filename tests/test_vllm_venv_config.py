"""Environment selection must not leak into vLLM engine arguments."""

import pytest

from ray_serve_cai.engines import venv_utils, vllm_engine
from ray_serve_cai.engines.vllm_config import VLLMDeploymentFactory


@pytest.mark.parametrize("venv_name", [None, "vllm", "vllm-029"])
def test_environment_selection_is_consumed_before_engine_binding(monkeypatch, tmp_path, venv_name):
    monkeypatch.setattr(venv_utils, "VENV_BASE", str(tmp_path))
    env_root = tmp_path / f".venv-{venv_name or 'vllm'}"
    env_root.mkdir()
    captured = {}

    class Deployment:
        @classmethod
        def options(cls, **kwargs):
            captured.update(kwargs)
            return cls()

        def bind(self, config):
            return config

    monkeypatch.setattr(vllm_engine, "VLLMEngine", Deployment)
    config = {"model": "shb777/Llama-3.3-8B-Instruct-128K", "max_model_len": 32768}
    if venv_name is not None:
        config["venv_name"] = venv_name
    original = dict(config)

    bound = VLLMDeploymentFactory().create_deployment(config, num_replicas=2)

    assert "venv_name" not in bound
    assert bound["model"] == original["model"]
    assert bound["max_model_len"] == 32768
    assert config == original
    actor = captured["ray_actor_options"]
    assert actor["runtime_env"]["py_executable"] == str(env_root / "bin/python")
    assert actor["num_gpus"] == 1
    assert captured["num_replicas"] == 2


def test_missing_explicit_environment_still_fails_fast(monkeypatch, tmp_path):
    monkeypatch.setattr(venv_utils, "VENV_BASE", str(tmp_path))
    (tmp_path / ".venv-vllm").mkdir()
    with pytest.raises(ValueError, match="Requested venv_name='missing' not found"):
        VLLMDeploymentFactory().create_deployment({"model": "m", "venv_name": "missing"})
