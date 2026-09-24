import io
import json
from unittest.mock import Mock

import pytest

from cai_integration import repair_project_hf_token as repair


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _project(environment):
    return Response(json.dumps({"environment": json.dumps(environment)}).encode())


def test_replaces_only_hf_token_and_verifies_without_logging_it(monkeypatch, capsys):
    before = {"RAY_HEAD_CPU": "12", "HUGGING_FACE_HUB_TOKEN": {"nativeEvent": {}}}
    after = {"RAY_HEAD_CPU": "12", "HUGGING_FACE_HUB_TOKEN": "hf_secret"}
    opener = Mock(side_effect=[_project(before), Response(b"{}"), _project(after)])
    monkeypatch.setattr(repair, "urlopen", opener)

    repair.repair_project_hf_token("https://cai.example.test", "project", "cml_key", "hf_secret")

    patch = opener.call_args_list[1].args[0]
    assert patch.method == "PATCH"
    assert json.loads(json.loads(patch.data)["environment"]) == after
    assert "hf_secret" not in capsys.readouterr().out


def test_refuses_to_overwrite_other_malformed_variables(monkeypatch):
    before = {"HUGGING_FACE_HUB_TOKEN": {}, "RAY_HEAD_CPU": {"nativeEvent": {}}}
    opener = Mock(return_value=_project(before))
    monkeypatch.setattr(repair, "urlopen", opener)

    with pytest.raises(ValueError, match="RAY_HEAD_CPU"):
        repair.repair_project_hf_token("https://cai.example.test", "project", "cml_key", "hf_secret")
    assert opener.call_count == 1


def test_rejects_unverified_update(monkeypatch):
    before = {"HUGGING_FACE_HUB_TOKEN": {"nativeEvent": {}}}
    opener = Mock(side_effect=[_project(before), Response(b"{}"), _project(before)])
    monkeypatch.setattr(repair, "urlopen", opener)

    with pytest.raises(RuntimeError, match="did not match"):
        repair.repair_project_hf_token("https://cai.example.test", "project", "cml_key", "hf_secret")


def test_cli_prefers_current_workbench_key_over_stale_override(monkeypatch, tmp_path):
    hf_file = tmp_path / "hf-token"
    hf_file.write_text("hf_secret\n")
    monkeypatch.setenv("CML_API_KEY", "stale-key")
    monkeypatch.setenv("CDSW_APIV2_KEY", "current-key")
    operation = Mock()
    monkeypatch.setattr(repair, "repair_project_hf_token", operation)
    repair.main([
        "--host", "https://cai.example.test", "--project-id", "project",
        "--hf-token-file", str(hf_file),
    ])
    operation.assert_called_once_with(
        "https://cai.example.test", "project", "current-key", "hf_secret"
    )
