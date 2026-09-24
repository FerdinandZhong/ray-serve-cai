"""Offline execution-context coverage: no packages installed or services called."""

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "cai_integration"


@pytest.mark.parametrize("name", [
    "amp_demo", "launch_ray_cluster", "launch_monitoring",
    "setup_vllm_env", "setup_litellm_env",
])
@pytest.mark.parametrize("mode", ["script", "notebook-root", "notebook-subdir", "notebook-env"])
def test_script_loading_and_root_resolution(name, mode, monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delenv("CDSW_PROJECT_DIR", raising=False)
    monkeypatch.chdir(SCRIPTS if mode == "notebook-subdir" else ROOT)
    namespace = {"__name__": "context_test", "__package__": None}
    if mode == "script":
        namespace["__file__"] = str(SCRIPTS / f"{name}.py")
        monkeypatch.chdir(tmp_path)
    if mode == "notebook-env":
        monkeypatch.setenv("CDSW_PROJECT_DIR", str(ROOT))
        monkeypatch.chdir(tmp_path)
    # Importing a utility must never replace the interpreter.
    monkeypatch.setattr(os, "execv", lambda *_: pytest.fail("unexpected re-exec"))
    exec(compile((SCRIPTS / f"{name}.py").read_text(), f"<{name}-cell>", "exec"), namespace)
    if name == "amp_demo":
        assert namespace["_demo_script_path"]() == SCRIPTS / "amp_demo.py"
    else:
        assert namespace["_project_root"]() == ROOT


@pytest.mark.parametrize("name", ["amp_demo", "launch_ray_cluster"])
def test_reexec_uses_explicit_script_not_kernel_arguments(name, tmp_path):
    tree = ast.parse((SCRIPTS / f"{name}.py").read_text())
    block = next(n for n in tree.body if isinstance(n, ast.If) and "os.execv" in ast.unparse(n))
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    calls = []
    ns = {
        "__name__": "__main__", "_VENV_PYTHON": python,
        "_running_in_venv": lambda _: False,
        "_demo_script_path": lambda: SCRIPTS / "amp_demo.py",
        "PROJECT_ROOT": ROOT, "Path": Path,
        "sys": SimpleNamespace(prefix="/different-env", argv=["ipykernel_launcher.py", "-f", "kernel.json"]),
        "os": SimpleNamespace(execv=lambda exe, argv: calls.append((exe, argv))),
    }
    exec(compile(ast.Module(body=[block], type_ignores=[]), "<cell>", "exec"), ns)
    assert calls == [(str(python), [str(python), "-u", str(SCRIPTS / f"{name}.py")])]
    calls.clear()
    ns["__name__"] = "imported_module"
    exec(compile(ast.Module(body=[block], type_ignores=[]), "<cell>", "exec"), ns)
    assert calls == []


@pytest.mark.parametrize("name", ["launch_ray_cluster_job", "launch_monitoring_job"])
@pytest.mark.parametrize("mode", ["root", "subdir", "env"])
def test_job_wrappers_resolve_root_and_strip_kernel_arguments(name, mode, tmp_path, monkeypatch):
    import subprocess
    import sys

    project = tmp_path / "project"
    scripts = project / "cai_integration"
    scripts.mkdir(parents=True)
    for filename in ("launch_ray_cluster.py", "launch_monitoring.py", "launch_ray_cluster.sh"):
        (scripts / filename).touch()
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.delenv("CDSW_PROJECT_DIR", raising=False)
    monkeypatch.chdir(scripts if mode == "subdir" else project)
    if mode == "env":
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CDSW_PROJECT_DIR", str(project))
    monkeypatch.setenv("GRAFANA_HOST", "https://example.test")
    monkeypatch.setattr(sys, "argv", ["ipykernel_launcher.py", "-f", "kernel.json"])
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs)) or SimpleNamespace(returncode=0))
    ns = {"__name__": "context_test"}
    exec(compile((SCRIPTS / f"{name}.py").read_text(), "<cell>", "exec"), ns)
    assert ns["main"]() == 0
    assert len(calls) == (2 if name == "launch_monitoring_job" else 1)
    for argv, kwargs in calls:
        assert argv[:2] == [str(python), "-u"]
        assert Path(argv[2]).parent == scripts
        assert len(argv) == 3
        assert kwargs["cwd"] == str(project)
