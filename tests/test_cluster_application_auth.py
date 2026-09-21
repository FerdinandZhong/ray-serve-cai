"""Application creation must preserve site-required authentication."""

from unittest.mock import Mock

from ray_serve_cai.cai_cluster import CAIClusterManager, CMLAPIClient, WorkerGroupConfig


def test_client_defaults_to_authenticated_application():
    client = CMLAPIClient("https://example.test", "test-key")
    client.session = Mock()
    client.session.post.return_value.status_code = 201
    client.session.post.return_value.json.return_value = {"id": "app-1"}
    client.create_application(
        project_id="project", name="app", script="app.py", cpu=1,
        memory=2, runtime_identifier="runtime", subdomain="app",
    )
    assert client.session.post.call_args.kwargs["json"]["bypass_authentication"] is False


def test_head_and_worker_creation_require_authentication():
    manager = CAIClusterManager("https://example.test", "test-key", "project")
    manager.cml_client = Mock()
    manager.cml_client.create_application.return_value = Mock(id="app-1", status="running")
    manager.start_cluster(
        worker_groups=[], head_runtime_identifier="runtime",
        head_script_path="head.py", wait_ready=False,
    )
    assert manager.cml_client.create_application.call_args.kwargs["bypass_authentication"] is False
    manager.launch_worker(WorkerGroupConfig(
        name="cpu", node_type="cpu", count=0, cpu=1, memory=2, gpus=0,
        script_path="worker.py", runtime_identifier="runtime",
    ))
    assert manager.cml_client.create_application.call_args.kwargs["bypass_authentication"] is False
