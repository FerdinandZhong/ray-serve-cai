"""Exercise bootstrap helper resolution without installing or contacting CAI."""

import ast
from pathlib import Path

import pytest

from cai_integration import setup_environment as setup


@pytest.mark.parametrize("mode", ["script", "module", "notebook-root", "notebook-script-dir"])
def test_preflight_resolves_without_sibling_on_python_path(tmp_path, monkeypatch, mode):
    directory = tmp_path / "cai_integration"
    directory.mkdir()
    (directory / "project_environment.py").write_text(
        "def preflight_project_environment():\n"
        "    raise ValueError('preflight-executed')\n"
    )
    if mode.startswith("notebook"):
        monkeypatch.delattr(setup, "__file__")
    else:
        monkeypatch.setattr(setup, "__file__", str(directory / "setup_environment.py"))
    monkeypatch.setattr(setup, "__package__", "cai_integration" if mode == "module" else None)
    monkeypatch.chdir(directory if mode == "notebook-script-dir" else tmp_path)
    # Reaching the helper (and propagating its error) proves resolution works.
    with pytest.raises(ValueError, match="preflight-executed"):
        setup.run_project_preflight()


def test_missing_helper_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.delattr(setup, "__file__")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="helper is missing"):
        setup.run_project_preflight()


def test_entrypoint_checks_project_before_installation():
    tree = ast.parse(Path(setup.__file__).read_text())
    entrypoint = ast.Module(body=[tree.body[-1]], type_ignores=[])
    calls = []
    namespace = {
        "__name__": "__main__",
        "run_project_preflight": lambda: calls.append("preflight"),
        "main": lambda: calls.append("install"),
    }
    exec(compile(entrypoint, "<notebook-cell>", "exec"), namespace)
    assert calls == ["preflight", "install"]

    def fail():
        raise RuntimeError("invalid environment")

    calls.clear()
    namespace["run_project_preflight"] = fail
    with pytest.raises(RuntimeError, match="invalid environment"):
        exec(compile(entrypoint, "<notebook-cell>", "exec"), namespace)
    assert calls == []
