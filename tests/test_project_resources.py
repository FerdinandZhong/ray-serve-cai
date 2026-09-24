import io
import json
import runpy
import urllib.request
from unittest.mock import Mock

import pytest

from cai_integration import configure_project_resources as resources
from cai_integration.setup_project import ProjectSetup


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _env(monkeypatch):
    monkeypatch.setenv("CML_HOST", "https://example.test")
    monkeypatch.setenv("CML_API_KEY", "secret")
    monkeypatch.setenv("CDSW_PROJECT_ID", "project")


def test_configures_and_verifies_shared_memory(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("RAY_SHARED_MEMORY_LIMIT_MB", "40000")
    opener = Mock(side_effect=[Response(b"{}"), Response(json.dumps({"shared_memory_limit": 40000}).encode())])
    monkeypatch.setattr(resources, "urlopen", opener)

    resources.configure_project_resources()

    patch = opener.call_args_list[0].args[0]
    assert patch.method == "PATCH"
    assert json.loads(patch.data) == {"shared_memory_limit": 40000}
    assert patch.get_header("Authorization") == "Bearer secret"
    assert opener.call_args_list[1].args[0].method == "GET"


def test_successful_job_script_finishes_without_system_exit(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("RAY_SHARED_MEMORY_LIMIT_MB", '{"nativeEvent":{}}')
    opener = Mock(side_effect=[Response(b"{}"), Response(b'{"shared_memory_limit":40000}')])
    monkeypatch.setattr(urllib.request, "urlopen", opener)

    # CAI executes the job in IPython, where even sys.exit(0) is a failed job.
    runpy.run_path(resources.__file__, run_name="__main__")
    assert opener.call_count == 2


def test_fails_when_server_does_not_apply_value(monkeypatch):
    _env(monkeypatch)
    opener = Mock(side_effect=[Response(b"{}"), Response(b'{"shared_memory_limit":64}')])
    monkeypatch.setattr(resources, "urlopen", opener)
    with pytest.raises(RuntimeError, match="requested 40000 MB, got 64 MB"):
        resources.configure_project_resources()


@pytest.mark.parametrize("value", ["", "abc", '{"nativeEvent":{}}'])
def test_fixed_shared_memory_ignores_malformed_project_input(monkeypatch, value):
    _env(monkeypatch)
    monkeypatch.setenv("RAY_SHARED_MEMORY_LIMIT_MB", value)
    opener = Mock(side_effect=[Response(b"{}"), Response(b'{"shared_memory_limit":40000}')])
    monkeypatch.setattr(resources, "urlopen", opener)

    resources.configure_project_resources()
    assert json.loads(opener.call_args_list[0].args[0].data) == {"shared_memory_limit": 40000}


def test_project_creation_verifies_shared_memory_before_jobs(monkeypatch):
    monkeypatch.setenv("RAY_SHARED_MEMORY_LIMIT_MB", '{"nativeEvent":{}}')
    setup = ProjectSetup.__new__(ProjectSetup)
    setup.make_request = Mock(side_effect=[{}, {"shared_memory_limit": 40000}])

    assert setup.configure_project_resources("project") is True
    assert setup.make_request.call_args_list[0].args == ("PATCH", "projects/project")
    assert setup.make_request.call_args_list[0].kwargs["data"]["shared_memory_limit"] == 40000
    assert setup.make_request.call_args_list[1].args == ("GET", "projects/project")


def test_project_creation_stops_if_shared_memory_is_not_applied(monkeypatch):
    monkeypatch.delenv("RAY_SHARED_MEMORY_LIMIT_MB", raising=False)
    setup = ProjectSetup.__new__(ProjectSetup)
    setup.make_request = Mock(side_effect=[{}, {"shared_memory_limit": 64}])
    setup.get_or_create_project = Mock(return_value="project")
    setup.wait_for_git_clone = Mock()
    setup.github_repo = "example/repo"

    assert setup.run() is False
    setup.wait_for_git_clone.assert_not_called()
