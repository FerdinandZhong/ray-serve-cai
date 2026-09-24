import io
import json
from unittest.mock import Mock

import pytest

from cai_integration import project_environment
from cai_integration.project_environment import validate_project_environment


@pytest.mark.parametrize("value", [None, "", "{}", '{"CPU":"12","ENABLED":"false"}'])
def test_flat_environment_is_accepted(value):
    validate_project_environment(value)


@pytest.mark.parametrize("value", [{"default": "private-secret"}, [], True, 12, None])
def test_nested_or_non_string_values_rejected_without_leaking_values(value):
    with pytest.raises(ValueError) as error:
        validate_project_environment(json.dumps({"CONFIG": value}))
    assert "CONFIG" in str(error.value)
    assert "private-secret" not in str(error.value)


def test_malformed_hf_token_points_to_explicit_repair():
    with pytest.raises(ValueError, match="repair_project_hf_token"):
        validate_project_environment(json.dumps({"HUGGING_FACE_HUB_TOKEN": {"nativeEvent": {}}}))


@pytest.mark.parametrize("value", ["not json", "[]", "null"])
def test_invalid_top_level_rejected(value):
    with pytest.raises(ValueError):
        validate_project_environment(value)


def test_preflight_prefers_current_workbench_key(monkeypatch):
    monkeypatch.setenv("CML_HOST", "https://cai.example.test")
    monkeypatch.setenv("CML_PROJECT_ID", "project")
    monkeypatch.setenv("CML_API_KEY", "stale-key")
    monkeypatch.setenv("CDSW_APIV2_KEY", "current-key")
    response = io.BytesIO(json.dumps({"environment": "{}"}).encode())
    opener = Mock(return_value=response)
    monkeypatch.setattr(project_environment, "urlopen", opener)
    project_environment.preflight_project_environment()
    assert opener.call_args.args[0].get_header("Authorization") == "Bearer current-key"
