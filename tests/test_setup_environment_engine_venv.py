"""Regression tests for isolated engine environment package contracts."""

from cai_integration import setup_environment


def test_clean_engine_setup_fails_when_required_bundle_install_fails(
    monkeypatch, tmp_path
):
    ready_checks = iter((False, False))
    monkeypatch.setattr(setup_environment, "is_venv_ready", lambda _: next(ready_checks))
    monkeypatch.setattr(setup_environment, "_ensure_uv", lambda: "uv")
    monkeypatch.setattr(setup_environment, "run_command", lambda command: " venv " in command)
    monkeypatch.setattr(setup_environment, "venv_ray_version", lambda _: "2.56.1")
    monkeypatch.setattr(setup_environment, "_base_pkg_version", lambda _: None)
    installs = []
    monkeypatch.setattr(
        setup_environment,
        "_pip_install_bundle_into_venv",
        lambda venv, specs: installs.append((venv, specs)) or False,
    )

    assert not setup_environment.setup_engine_venv(
        "vllm", setup_environment._ENGINE_PACKAGES["vllm"], venv_base=str(tmp_path)
    )
    assert len(installs) == 1
    assert "flashinfer-python>=0.6.16.post4,<0.6.17" in installs[0][1]
    assert "nvidia-cuda-nvcc==13.0.*" in installs[0][1]


def test_existing_vllm_venv_repairs_full_bundle_in_one_transaction(
    monkeypatch, tmp_path
):
    versions = {
        "ray": "2.56.1",
        "fastapi": "0.136.3",
        "vllm": "0.13.0",
        "flashinfer-python": "0.6.18",
        "nvidia-cuda-nvcc": "12.9.0",
        "nvidia-cuda-runtime": "13.0.1",
        "nvidia-cuda-cccl": "13.0.2",
        "ninja": "1.13.0",
        "protobuf": "6.0.0",
    }
    monkeypatch.setattr(setup_environment, "venv_ray_version", lambda _: "2.56.1")
    monkeypatch.setattr(
        setup_environment,
        "_base_pkg_version",
        lambda name: {"fastapi": "0.136.3"}.get(name),
    )
    monkeypatch.setattr(
        setup_environment, "_venv_pkg_version", lambda _venv, name: versions.get(name)
    )
    installed = []

    def install_bundle(_venv, specs):
        installed.append(specs)
        versions.update(
            {
                "flashinfer-python": "0.6.16.post4",
                "nvidia-cuda-nvcc": "13.0.0",
            }
        )
        return True

    monkeypatch.setattr(
        setup_environment, "_pip_install_bundle_into_venv", install_bundle
    )
    monkeypatch.setattr(setup_environment, "_venv_dependencies_consistent", lambda _: True)

    assert setup_environment._reconcile_engine_venv(
        "vllm",
        str(tmp_path / ".venv-vllm"),
        str(tmp_path / ".venv-vllm.lock"),
        setup_environment._ENGINE_PACKAGES["vllm"],
    )
    assert len(installed) == 1
    assert installed[0] == [
        "ray[serve]==2.56.1",
        "protobuf>=5.29.6,<7.0",
        "fastapi==0.136.3",
        "vllm>=0.13.0",
        "flashinfer-python>=0.6.16.post4,<0.6.17",
        "nvidia-cuda-nvcc==13.0.*",
        "nvidia-cuda-runtime==13.0.*",
        "nvidia-cuda-cccl==13.0.*",
        "ninja",
    ]


def test_reconcile_fails_if_install_does_not_satisfy_requirement(monkeypatch, tmp_path):
    monkeypatch.setattr(setup_environment, "_resolved_engine_packages", lambda specs: specs)
    monkeypatch.setattr(setup_environment, "_requirement_satisfied", lambda *_: False)
    monkeypatch.setattr(setup_environment, "_pip_install_bundle_into_venv", lambda *_: True)

    assert not setup_environment._reconcile_engine_venv(
        "vllm", str(tmp_path / "venv"), str(tmp_path / "venv.lock"), ["vllm>=0.13.0"]
    )


def test_compatible_existing_venv_checks_dependencies_without_install(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(setup_environment, "_resolved_engine_packages", lambda specs: specs)
    monkeypatch.setattr(setup_environment, "_requirement_satisfied", lambda *_: True)
    installs = []
    monkeypatch.setattr(
        setup_environment,
        "_pip_install_bundle_into_venv",
        lambda *_: installs.append(True) or True,
    )
    monkeypatch.setattr(setup_environment, "_venv_dependencies_consistent", lambda _: True)

    assert setup_environment._reconcile_engine_venv(
        "vllm", str(tmp_path / "venv"), str(tmp_path / "venv.lock"), ["vllm>=0.13.0"]
    )
    assert installs == []


def test_compatible_top_level_versions_fail_on_inconsistent_dependencies(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(setup_environment, "_resolved_engine_packages", lambda specs: specs)
    monkeypatch.setattr(setup_environment, "_requirement_satisfied", lambda *_: True)
    monkeypatch.setattr(setup_environment, "_venv_dependencies_consistent", lambda _: False)

    assert not setup_environment._reconcile_engine_venv(
        "vllm", str(tmp_path / "venv"), str(tmp_path / "venv.lock"), ["vllm>=0.13.0"]
    )


def test_concurrent_ready_path_reconciles_without_reacquiring_lock(monkeypatch, tmp_path):
    ready_checks = iter((False, True))
    monkeypatch.setattr(setup_environment, "is_venv_ready", lambda _: next(ready_checks))
    monkeypatch.setattr(setup_environment, "_ensure_uv", lambda: "uv")
    calls = []
    monkeypatch.setattr(
        setup_environment,
        "_reconcile_engine_venv",
        lambda *args, **kwargs: calls.append(kwargs) or True,
    )

    assert setup_environment.setup_engine_venv(
        "vllm", ["vllm>=0.13.0"], venv_base=str(tmp_path)
    )
    assert calls == [{"lock_held": True}]


def test_bundle_install_quotes_and_uses_one_resolver_call(monkeypatch):
    commands = []
    monkeypatch.setattr(setup_environment, "_resolve_uv", lambda: "uv")
    monkeypatch.setattr(
        setup_environment, "run_command", lambda command: commands.append(command) or True
    )

    assert setup_environment._pip_install_bundle_into_venv(
        "/tmp/engine", ["vllm>=0.13.0", "flashinfer-python>=0.6.16.post4,<0.6.17"]
    )
    assert len(commands) == 1
    assert "'vllm>=0.13.0'" in commands[0]
    assert "'flashinfer-python>=0.6.16.post4,<0.6.17'" in commands[0]


def test_dependency_check_without_uv_is_read_only(monkeypatch):
    commands = []
    monkeypatch.setattr(setup_environment, "_resolve_uv", lambda: None)
    monkeypatch.setattr(
        setup_environment, "run_command", lambda command: commands.append(command) or False
    )

    assert not setup_environment._venv_dependencies_consistent("/tmp/engine")
    assert commands == ["PIP_USER=0 /tmp/engine/bin/python -m pip check"]
