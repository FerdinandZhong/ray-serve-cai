import json

import pytest

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


@pytest.mark.parametrize("value", ["not json", "[]", "null"])
def test_invalid_top_level_rejected(value):
    with pytest.raises(ValueError):
        validate_project_environment(value)
