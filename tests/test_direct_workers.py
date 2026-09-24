"""Offline direct-worker lifecycle checks; no CAI or GPU requests."""
import json
from unittest.mock import Mock

import pytest

from ray_serve_cai.management.models.requests import AddNodeRequest
from ray_serve_cai.management.models.responses import NodeInfo
from ray_serve_cai.management.services import cai_service as module
from ray_serve_cai.management.services.cai_service import CAIService
from ray_serve_cai.management.services.coordinator import CoordinatorService
from ray_serve_cai.recovery.recover import RecoveryOrchestrator
from ray_serve_cai.recovery.recovery_state import RecoveryState


@pytest.fixture
def svc(tmp_path, monkeypatch):
    path = tmp_path / "ray_cluster_info.json"
    path.write_text(json.dumps({"head_address": "10.0.0.1:6379",
                               "worker_runtime_identifier": "cuda-runtime",
                               "worker_groups": [], "workers": {}}))
    monkeypatch.setattr(module, "_CLUSTER_INFO_PATH", path)
    service = CAIService("project", "https://example.invalid", "unused")
    service.manager = Mock()
    service.manager.launch_worker.side_effect = [{"id": "app1"}, {"id": "app2"}, {"id": "app3"}]
    return service


def create(svc, **kwargs):
    return svc.create_worker_node(cpu=12, memory=64, gpus=1, accelerator_type="L40S", **kwargs)


def test_direct_specs_and_unique_identity(svc):
    a = create(svc, name="same", node_type="unregistered", labels={"purpose": "inference"})
    b = svc.create_worker_node(name="same", node_type="unregistered", cpu=8, memory=32)
    assert a["worker_id"] != b["worker_id"]
    assert a["app_name"] != b["app_name"]
    assert a["ray_node_id"] is None
    calls = svc.manager.launch_worker.call_args_list
    assert calls[0].kwargs["group"].script_path != calls[1].kwargs["group"].script_path
    for call in calls:
        group = call.kwargs["group"]
        compile(open(group.script_path).read(), group.script_path, "exec")
    resources = json.loads(calls[0].kwargs["environment"]["RAY_EXTRA_RESOURCES"])
    assert resources[f"worker_id:{a['worker_id']}"] == 1
    assert "purpose" not in resources
    spec = svc.worker_records()[a["worker_id"]]["spec"]
    assert (spec["cpu"], spec["memory"], spec["accelerator_type"]) == (12, 64, "L40S")
    assert spec["runtime_identifier"] == "cuda-runtime"
    assert svc.list_worker_groups() == []


def test_optional_template(svc):
    svc.define_node_type("small", cpu=4, memory=8, runtime_identifier="cpu-runtime")
    result = svc.create_worker_node(node_type="small")
    assert result["cpu"] == 4
    assert svc.worker_records()[result["worker_id"]]["spec"]["runtime_identifier"] == "cpu-runtime"


@pytest.mark.parametrize("payload", [{}, {"cpu": 12}, {"cpu": 12, "memory": 64, "node_type": 'bad"'},
    {"cpu": 12, "memory": 64, "ray_labels": {"worker_id:fake": 1}},
    {"cpu": 12, "memory": 64, "ray_labels": {"x": -1}}])
def test_invalid_requests(payload):
    with pytest.raises(ValueError):
        AddNodeRequest(**payload)


def test_missing_template_no_launch(svc):
    with pytest.raises(ValueError, match="cpu and memory"):
        svc.create_worker_node(node_type="missing")
    svc.manager.launch_worker.assert_not_called()


def test_uncertain_launch_preserves_record(svc):
    svc.manager.launch_worker.side_effect = TimeoutError("timeout")
    with pytest.raises(TimeoutError):
        create(svc)
    assert next(iter(svc.worker_records().values()))["status"] == "launch_unknown"


def coordinator(svc, tmp_path):
    coord = CoordinatorService(Mock(), svc)
    coord.state_file = tmp_path / "state.json"
    coord.resource_map = Mock()
    return coord


def test_identity_join_and_deletion(svc, tmp_path):
    coord = coordinator(svc, tmp_path)
    result = coord.add_worker_node(cpu=12, memory=64, name="worker", accelerator_type="L40S")
    coord.ray_service.get_nodes.return_value = [{"NodeID": "ray1", "Alive": True,
        "Resources": json.loads(svc.manager.launch_worker.call_args.kwargs["environment"]["RAY_EXTRA_RESOURCES"])}]
    svc.manager.list_applications.return_value = [{"id": "app1", "status": "running"}]
    node = NodeInfo(**coord.get_enriched_nodes()[0])
    assert node.app_id == "app1" and node.worker_id == result["worker_id"]
    assert svc.worker_records()[result["worker_id"]]["ray_node_id"] == "ray1"
    svc.manager.stop_application.return_value = False
    assert coord.remove_worker_node("app1")["status"] == "partial"
    assert svc.worker_records()
    coord.resource_map.unregister_worker.assert_not_called()
    svc.manager.stop_application.return_value = True
    assert coord.remove_worker_node("app1")["status"] == "success"
    assert not svc.worker_records()


def test_recovery_exact_specs_resume_and_foreign_app(svc, tmp_path):
    result = create(svc, labels={"purpose": "inference"})
    original = svc._load_cluster_info()
    cml = Mock()
    cml.list_applications.return_value = [{"id": "app1"}, {"id": "foreign", "name": "ray-other"}]
    cml.stop_application.return_value = True
    orch = RecoveryOrchestrator(cml=cml, cai_service=svc, deployment_store=Mock(),
        cluster_info_path=module._CLUSTER_INFO_PATH, http=Mock(),
        state=RecoveryState(tmp_path / "recovery.json", tmp_path / "lock"))
    assert orch.rebuild_workers(original) == {"deleted": 1, "created": 1}
    cml.stop_application.assert_called_once_with("app1")
    record = svc.worker_records()[result["worker_id"]]
    assert record["app_id"] == "app2"
    assert record["spec"] == original["workers"][result["worker_id"]]["spec"]
    cml.list_applications.return_value = [{"id": "app2"}, {"id": "foreign"}]
    assert orch.rebuild_workers(svc._load_cluster_info()) == {"deleted": 0, "created": 0}
    assert svc.manager.launch_worker.call_count == 2


def test_recovery_delete_failure_does_not_create(svc, tmp_path):
    create(svc)
    cml = Mock()
    cml.list_applications.return_value = [{"id": "app1"}]
    cml.stop_application.return_value = False
    orch = RecoveryOrchestrator(cml=cml, cai_service=svc, deployment_store=Mock(),
        cluster_info_path=module._CLUSTER_INFO_PATH, http=Mock(),
        state=RecoveryState(tmp_path / "recovery.json", tmp_path / "lock"))
    with pytest.raises(RuntimeError, match="replacement not launched"):
        orch.rebuild_workers(svc._load_cluster_info())
    assert svc.manager.launch_worker.call_count == 1


def test_old_ray_process_not_bound_to_replacement(svc, tmp_path):
    result = create(svc)
    old_resources = json.loads(svc.manager.launch_worker.call_args.kwargs["environment"]["RAY_EXTRA_RESOURCES"])
    # Simulate a replacement; the old raylet is still visible in Ray history.
    create(svc, _worker_id=result["worker_id"])
    new_resources = json.loads(svc.manager.launch_worker.call_args.kwargs["environment"]["RAY_EXTRA_RESOURCES"])
    coord = coordinator(svc, tmp_path)
    coord.add_node_mapping("old-ray", "app1", result["app_name"])
    coord.ray_service.get_nodes.return_value = [
        {"NodeID": "old-ray", "Alive": False, "Resources": old_resources},
        {"NodeID": "new-ray", "Alive": True, "Resources": new_resources}]
    svc.manager.list_applications.return_value = [{"id": "app2", "status": "running"}]
    nodes = coord.get_enriched_nodes()
    assert nodes[0]["app_id"] == "app1"
    assert nodes[1]["app_id"] == "app2"
    assert svc.worker_records()[result["worker_id"]]["ray_node_id"] == "new-ray"


def test_startup_save_preserves_api_records(svc):
    from cai_integration.launch_ray_cluster import save_cluster_info
    startup_snapshot = svc._load_cluster_info()
    result = create(svc)
    save_cluster_info(module._CLUSTER_INFO_PATH, startup_snapshot)
    assert result["worker_id"] in svc.worker_records()


def test_missing_runtime_does_not_launch(svc):
    info = svc._load_cluster_info()
    del info["worker_runtime_identifier"]
    svc._save_cluster_info(info)
    with pytest.raises(ValueError, match="runtime_identifier"):
        create(svc)
    svc.manager.launch_worker.assert_not_called()


def test_http_direct_creation_and_pending_listing(svc, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from ray_serve_cai.management.api.resources import router, get_coordinator
    from ray_serve_cai.management.auth import require_admin
    app = FastAPI()
    app.include_router(router)
    coord = coordinator(svc, tmp_path)
    app.dependency_overrides[get_coordinator] = lambda: coord
    app.dependency_overrides[require_admin] = lambda: True
    client = TestClient(app)
    response = client.post("/api/v1/resources/nodes", json={
        "name": "l40s-01", "cpu": 12, "memory": 64, "gpus": 1,
        "accelerator_type": "L40S", "labels": {"purpose": "inference"}})
    assert response.status_code == 201
    result = response.json()
    assert result["ray_node_id"] is None
    records = client.get("/api/v1/resources/worker-apps").json()["workers"]
    assert records[0]["worker_id"] == result["worker_id"]
    assert records[0]["spec"]["accelerator_type"] == "L40S"
    assert client.post("/api/v1/resources/nodes", json={"node_type": "missing"}).status_code == 422


def test_mixed_legacy_snapshot_flags_unsafe_recovery(svc, tmp_path):
    info = svc._load_cluster_info()
    del info["workers"]
    info["worker_groups"] = [{"count": 2}]
    svc._save_cluster_info(info)
    create(svc)
    assert svc._load_cluster_info()["legacy_workers_untracked"] is True
    cml = Mock()
    orch = RecoveryOrchestrator(cml=cml, cai_service=svc, deployment_store=Mock(),
        cluster_info_path=module._CLUSTER_INFO_PATH, http=Mock(),
        state=RecoveryState(tmp_path / "recovery.json", tmp_path / "lock"))
    with pytest.raises(RuntimeError, match="before restarting the head"):
        orch.run()
    cml.restart_application.assert_not_called()


def test_uncertain_recovery_never_duplicates(svc, tmp_path):
    svc.manager.launch_worker.side_effect = TimeoutError("uncertain")
    with pytest.raises(TimeoutError):
        create(svc)
    cml = Mock()
    cml.list_applications.return_value = []
    orch = RecoveryOrchestrator(cml=cml, cai_service=svc, deployment_store=Mock(),
        cluster_info_path=module._CLUSTER_INFO_PATH, http=Mock(),
        state=RecoveryState(tmp_path / "recovery.json", tmp_path / "lock"))
    with pytest.raises(RuntimeError, match="unresolved launch"):
        orch.rebuild_workers(svc._load_cluster_info())
    assert svc.manager.launch_worker.call_count == 1
