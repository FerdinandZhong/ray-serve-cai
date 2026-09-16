"""Regression tests for CML/CAI Workbench environment resolution."""

import subprocess
import sys
from pathlib import Path

import pytest

from cai_integration.cml_env import resolve_cml_connection


@pytest.mark.parametrize(
    ("environment", "host", "api_key", "project_id"),
    [
        (
            {
                "CML_HOST": "https://legacy.example/",
                "CML_API_KEY": "legacy-key",
                "CML_PROJECT_ID": "legacy-project",
            },
            "https://legacy.example/",
            "legacy-key",
            "legacy-project",
        ),
        (
            {
                "CDSW_DOMAIN": "ml.example.cloudera.site",
                "CDSW_APIV2_KEY": "workbench-key",
                "CDSW_PROJECT_ID": "workbench-project",
            },
            "https://ml.example.cloudera.site",
            "workbench-key",
            "workbench-project",
        ),
    ],
)
def test_resolve_cml_connection_supports_legacy_and_workbench_variables(
    environment, host, api_key, project_id
):
    connection = resolve_cml_connection(environment)

    assert connection.host == host
    assert connection.api_key == api_key
    assert connection.project_id == project_id


def test_resolve_cml_connection_prefers_explicit_cml_values():
    connection = resolve_cml_connection(
        {
            "CML_HOST": "https://override.example",
            "CML_API_KEY": "override-key",
            "CML_PROJECT_ID": "override-project",
            "CDSW_DOMAIN": "ignored.example",
            "CDSW_APIV2_KEY": "ignored-key",
            "CDSW_PROJECT_ID": "ignored-project",
        }
    )

    assert connection.host == "https://override.example"
    assert connection.api_key == "override-key"
    assert connection.project_id == "override-project"


def test_resolve_cml_connection_ignores_blank_values():
    connection = resolve_cml_connection(
        {"CML_HOST": "  ", "CML_API_KEY": "", "CDSW_DOMAIN": "  ", "CDSW_APIV2_KEY": ""}
    )

    assert connection.host is None
    assert connection.api_key is None
    assert connection.project_id is None


@pytest.mark.parametrize(
    ("invocation", "from_repo_root"),
    [
        (["cai_integration/create_jobs.py", "--project-id", "test-project"], False),
        (["cai_integration/setup_project.py"], False),
        (["-m", "cai_integration.create_jobs", "--project-id", "test-project"], True),
        (["-m", "cai_integration.setup_project"], True),
    ],
)
def test_workflow_scripts_support_path_and_module_execution_without_pythonpath(
    invocation, from_repo_root
):
    repo_root = Path(__file__).resolve().parents[1]
    cwd = repo_root if from_repo_root else repo_root.parent
    command = [sys.executable]
    if invocation[0].startswith("cai_integration/"):
        command.append(str(repo_root / invocation[0]))
        command.extend(invocation[1:])
    else:
        command.extend(invocation)
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={},
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "Missing required environment variables" in output
    assert "ModuleNotFoundError" not in output
