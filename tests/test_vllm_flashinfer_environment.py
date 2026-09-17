"""Unit tests for vLLM actor FlashInfer JIT environment setup."""

from pathlib import Path

import pytest

from ray_serve_cai.engines import vllm_engine


def _venv_python(venv_root: Path) -> str:
    return str(venv_root / "bin" / "python")


@pytest.fixture(autouse=True)
def _isolate_active_venv_roots(monkeypatch, tmp_path):
    """Prevent tests from discovering CUDA packages in the test runner's venv."""
    host_root = tmp_path / "isolated-host"
    monkeypatch.setattr(vllm_engine.sys, "prefix", str(host_root))
    monkeypatch.setattr(vllm_engine.sys, "executable", _venv_python(host_root))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)


def test_sets_cuda_home_to_venv_toolkit(monkeypatch, tmp_path):
    """A CUDA toolkit wheel is discoverable by FlashInfer JIT in the actor."""
    toolkit = tmp_path / "lib/python3.11/site-packages/nvidia/cu13"
    (toolkit / "bin").mkdir(parents=True)
    (toolkit / "bin/nvcc").touch()
    monkeypatch.setattr(vllm_engine.sys, "executable", _venv_python(tmp_path))
    monkeypatch.delenv("CUDA_HOME", raising=False)
    monkeypatch.delenv("VLLM_USE_FLASHINFER_SAMPLER", raising=False)

    vllm_engine._configure_flashinfer_jit_environment()

    assert vllm_engine.os.environ["CUDA_HOME"] == str(toolkit)
    assert "VLLM_USE_FLASHINFER_SAMPLER" not in vllm_engine.os.environ


def test_discovers_toolkit_when_venv_python_is_a_symlink(monkeypatch, tmp_path):
    """Interpreter symlinks must not redirect toolkit discovery to the base Python."""
    venv_root = tmp_path / "venv"
    base_python = tmp_path / "base/bin/python"
    base_python.parent.mkdir(parents=True)
    base_python.touch()
    (venv_root / "bin").mkdir(parents=True)
    (venv_root / "bin/python").symlink_to(base_python)
    toolkit = venv_root / "lib/python3.11/site-packages/nvidia/cu13"
    (toolkit / "bin").mkdir(parents=True)
    (toolkit / "bin/nvcc").touch()

    monkeypatch.setattr(vllm_engine.sys, "prefix", str(venv_root))
    monkeypatch.setattr(vllm_engine.sys, "executable", str(venv_root / "bin/python"))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("CUDA_HOME", raising=False)
    monkeypatch.delenv("VLLM_USE_FLASHINFER_SAMPLER", raising=False)

    vllm_engine._configure_flashinfer_jit_environment()

    assert vllm_engine.os.environ["CUDA_HOME"] == str(toolkit)
    assert "VLLM_USE_FLASHINFER_SAMPLER" not in vllm_engine.os.environ


def test_sys_prefix_wins_over_stale_virtual_env(monkeypatch, tmp_path):
    """Ray may retain the parent's VIRTUAL_ENV after switching py_executable."""
    active_root = tmp_path / "active"
    stale_root = tmp_path / "stale"
    active_toolkit = active_root / "lib/python3.11/site-packages/nvidia/cu13"
    stale_toolkit = stale_root / "lib/python3.11/site-packages/nvidia/cu12"
    for toolkit in (active_toolkit, stale_toolkit):
        (toolkit / "bin").mkdir(parents=True)
        (toolkit / "bin/nvcc").touch()

    monkeypatch.setattr(vllm_engine.sys, "prefix", str(active_root))
    monkeypatch.setattr(vllm_engine.sys, "executable", _venv_python(active_root))
    monkeypatch.setenv("VIRTUAL_ENV", str(stale_root))
    monkeypatch.delenv("CUDA_HOME", raising=False)

    vllm_engine._configure_flashinfer_jit_environment()

    assert vllm_engine.os.environ["CUDA_HOME"] == str(active_toolkit)


def test_preserves_explicit_cuda_home(monkeypatch, tmp_path):
    """A caller-supplied CUDA_HOME remains the deployment's source of truth."""
    toolkit = tmp_path / "lib/python3.11/site-packages/nvidia/cu13"
    (toolkit / "bin").mkdir(parents=True)
    (toolkit / "bin/nvcc").touch()
    monkeypatch.setattr(vllm_engine.sys, "executable", _venv_python(tmp_path))
    monkeypatch.setenv("CUDA_HOME", "/custom/cuda")

    vllm_engine._configure_flashinfer_jit_environment()

    assert vllm_engine.os.environ["CUDA_HOME"] == "/custom/cuda"


def test_blackwell_without_toolkit_disables_only_sampler(monkeypatch, tmp_path):
    """Blackwell gets a safe fallback when a CUDA JIT toolkit is unavailable."""
    monkeypatch.setattr(vllm_engine.sys, "executable", _venv_python(tmp_path))
    monkeypatch.setattr(vllm_engine, "_visible_cuda_compute_capability_major", lambda: 12)
    monkeypatch.delenv("CUDA_HOME", raising=False)
    monkeypatch.delenv("VLLM_USE_FLASHINFER_SAMPLER", raising=False)

    vllm_engine._configure_flashinfer_jit_environment()

    assert vllm_engine.os.environ["VLLM_USE_FLASHINFER_SAMPLER"] == "0"


def test_l40s_without_toolkit_keeps_flashinfer_default(monkeypatch, tmp_path):
    """L40S (sm_89) needs no Blackwell-specific sampler override."""
    monkeypatch.setattr(vllm_engine.sys, "executable", _venv_python(tmp_path))
    monkeypatch.setattr(vllm_engine, "_visible_cuda_compute_capability_major", lambda: 8)
    monkeypatch.delenv("CUDA_HOME", raising=False)
    monkeypatch.delenv("VLLM_USE_FLASHINFER_SAMPLER", raising=False)

    vllm_engine._configure_flashinfer_jit_environment()

    assert "VLLM_USE_FLASHINFER_SAMPLER" not in vllm_engine.os.environ


def test_preserves_explicit_flashinfer_sampler_setting(monkeypatch, tmp_path):
    """The live deployment's explicit sampler opt-out is never overridden."""
    monkeypatch.setattr(vllm_engine.sys, "executable", _venv_python(tmp_path))
    monkeypatch.setattr(vllm_engine, "_visible_cuda_compute_capability_major", lambda: 12)
    monkeypatch.setenv("VLLM_USE_FLASHINFER_SAMPLER", "0")

    vllm_engine._configure_flashinfer_jit_environment()

    assert vllm_engine.os.environ["VLLM_USE_FLASHINFER_SAMPLER"] == "0"


class _FakeDeployment:
    options_kwargs = None

    @classmethod
    def options(cls, **kwargs):
        cls.options_kwargs = kwargs
        return cls()

    def bind(self, engine_config):
        return engine_config


def _deployment_options(monkeypatch, **kwargs):
    _FakeDeployment.options_kwargs = None
    monkeypatch.setattr(vllm_engine, "VLLMEngine", _FakeDeployment)
    vllm_engine.create_vllm_deployment(
        {"model": "Qwen/Qwen3.8-27B-FP8"},
        tensor_parallel_size=2,
        venv_path=kwargs.pop("venv_path"),
        **kwargs,
    )
    return _FakeDeployment.options_kwargs


@pytest.mark.parametrize("explicit_bundles", [False, True])
def test_cuda_home_is_generated_in_tp_runtime_env(
    monkeypatch, tmp_path, explicit_bundles
):
    """Generated runtime config includes CUDA_HOME for both bundle paths."""
    toolkit = tmp_path / "lib/python3.11/site-packages/nvidia/cu13"
    (toolkit / "bin").mkdir(parents=True)
    (toolkit / "bin/nvcc").touch()
    supplied = [{"CPU": 4.0}, {"GPU": 1.0}, {"GPU": 1.0}]

    options = _deployment_options(
        monkeypatch,
        venv_path=str(tmp_path),
        placement_group_bundles=supplied if explicit_bundles else None,
    )
    runtime_env = options["ray_actor_options"]["runtime_env"]
    bundles = options["placement_group_bundles"]

    assert runtime_env["env_vars"]["CUDA_HOME"] == str(toolkit)
    assert runtime_env["env_vars"]["VLLM_USE_FLASHINFER_SAMPLER"] == "0"
    assert bundles[0].get("GPU", 0) == 0
    assert all(bundle.get("GPU", 0) <= 1 for bundle in bundles)


@pytest.mark.parametrize("explicit_bundles", [False, True])
def test_missing_toolkit_generates_sampler_fallback_for_tp_runtime_env(
    monkeypatch, tmp_path, explicit_bundles
):
    """Both bundle paths generate the conservative isolated-GPU fallback."""
    supplied = [{"CPU": 4.0}, {"GPU": 1.0}, {"GPU": 1.0}]

    options = _deployment_options(
        monkeypatch,
        venv_path=str(tmp_path),
        placement_group_bundles=supplied if explicit_bundles else None,
    )
    runtime_env = options["ray_actor_options"]["runtime_env"]
    bundles = options["placement_group_bundles"]

    assert runtime_env["env_vars"]["VLLM_USE_FLASHINFER_SAMPLER"] == "0"
    assert bundles[0].get("GPU", 0) == 0
    assert all(bundle.get("GPU", 0) <= 1 for bundle in bundles)


def test_explicit_sampler_opt_in_is_preserved_in_runtime_env(monkeypatch, tmp_path):
    """Generated runtime config preserves a validated explicit sampler opt-in."""
    options = _deployment_options(
        monkeypatch,
        venv_path=str(tmp_path),
        scheduling_env_vars={"VLLM_USE_FLASHINFER_SAMPLER": "1"},
    )
    runtime_env = options["ray_actor_options"]["runtime_env"]

    assert runtime_env["env_vars"]["VLLM_USE_FLASHINFER_SAMPLER"] == "1"


def test_explicit_cuda_home_is_preserved_in_runtime_env(monkeypatch, tmp_path):
    """Generated runtime config does not replace a caller's CUDA toolkit root."""
    toolkit = tmp_path / "lib/python3.11/site-packages/nvidia/cu13"
    (toolkit / "bin").mkdir(parents=True)
    (toolkit / "bin/nvcc").touch()

    options = _deployment_options(
        monkeypatch,
        venv_path=str(tmp_path),
        scheduling_env_vars={"CUDA_HOME": "/validated/cuda"},
    )
    runtime_env = options["ray_actor_options"]["runtime_env"]

    assert runtime_env["env_vars"]["CUDA_HOME"] == "/validated/cuda"
