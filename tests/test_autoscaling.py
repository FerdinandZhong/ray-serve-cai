"""Offline autoscaling contract tests; no CAI calls or physical GPUs."""

import copy
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from ray.autoscaler.tags import TAG_RAY_LAUNCH_REQUEST, TAG_RAY_USER_NODE_TYPE

from cai_integration.autoscaling.provider import CAIWorkerBackend
from ray_serve_cai.autoscaling.policy import ClusterScalingPolicy, worker_resources
from ray_serve_cai.autoscaling.store import ScalingStore
from ray_serve_cai.engines import venv_utils, vllm_engine
from ray_serve_cai.engines.vllm_config import VLLMDeploymentFactory
from ray_serve_cai.management.models.requests import DeployApplicationRequest


def policy(mode="observe"):
    return ClusterScalingPolicy(
        enabled=mode != "disabled",
        mode=mode,
        max_workers=2,
        max_gpus=2,
        pools=[
            {
                "id": "l40s",
                "max_workers": 2,
                "worker_spec": {
                    "cpu": 16,
                    "memory": 64,
                    "gpus": 1,
                    "node_type": "gpu-worker",
                    "accelerator_type": "L40S",
                    "runtime_identifier": "cuda-runtime",
                },
            }
        ],
    )


@pytest.fixture
def provider(tmp_path):
    store = ScalingStore(tmp_path / "state.json")
    store.set_policy(policy("full"))
    service = Mock()
    service.create_worker_node.return_value = {"app_id": "app1"}
    service.list_applications.return_value = []
    service.worker_records.return_value = {}
    snapshot = Mock(return_value=[])
    result = CAIWorkerBackend(
        {"state_path": str(store.path)},
        store.read()["cluster_id"],
        service=service,
        snapshot=snapshot,
    )
    return result


def launch(provider, count=1, request="request1"):
    spec = policy().pools[0].worker_spec.model_dump()
    tags = {TAG_RAY_LAUNCH_REQUEST: request, TAG_RAY_USER_NODE_TYPE: "l40s"}
    provider.launch_batch({"worker_spec": spec}, tags, count)
    return provider.non_terminated_nodes({})


def test_create_is_durable_and_idempotent(provider):
    def create(**kwargs):
        record = provider.store.read()["workers"][kwargs["_worker_id"]]
        assert record["state"] == "creating"
        assert kwargs["_autoscaling"]["instance_id"] == kwargs["_worker_id"]
        return {"app_id": "app1"}

    provider.service.create_worker_node.side_effect = create
    first = launch(provider)
    assert launch(provider) == first
    provider.service.create_worker_node.assert_called_once()


def test_unknown_create_blocks_retries_and_new_demand(provider):
    provider.service.create_worker_node.side_effect = TimeoutError("unknown outcome")
    with pytest.raises(TimeoutError):
        launch(provider)
    with pytest.raises(RuntimeError, match="Unresolved"):
        launch(provider)
    with pytest.raises(RuntimeError, match="unresolved"):
        launch(provider, request="different")
    provider.service.create_worker_node.assert_called_once()


@pytest.mark.parametrize("mode", ["disabled", "observe"])
def test_read_only_modes_never_create(provider, mode):
    provider.store.set_policy(policy(mode))
    with pytest.raises(RuntimeError, match="paused"):
        launch(provider)
    provider.service.create_worker_node.assert_not_called()


def test_caps_and_concurrent_commitments(provider):
    launch(provider, count=2)
    with pytest.raises(RuntimeError, match="limit"):
        launch(provider, request="another")
    assert provider.service.create_worker_node.call_count == 2


def test_lowered_pool_limits_still_count_existing_resource_commitments(provider):
    launch(provider, count=2)
    changed = policy("full").model_dump()
    changed["max_workers"] = 3
    changed["pools"][0]["max_workers"] = 1
    second = copy.deepcopy(changed["pools"][0])
    second["id"] = "second-pool"
    changed["pools"].append(second)
    provider.store.set_policy(ClusterScalingPolicy(**changed))
    with pytest.raises(RuntimeError, match="gpus budget"):
        provider.launch_batch(
            {"worker_spec": second["worker_spec"]},
            {TAG_RAY_LAUNCH_REQUEST: "second", TAG_RAY_USER_NODE_TYPE: second["id"]},
            1,
        )
    assert provider.service.create_worker_node.call_count == 2


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [{"status": "RUNNING"}],
        [{"status": "IDLE"}],
        [{"status": "DEAD"}, {"status": "RUNNING"}],
    ],
)
def test_never_delete_live_unknown_or_rejoined_worker(provider, nodes):
    node_id = launch(provider)[0]
    provider.snapshot.return_value = [{**n, "instance_id": node_id} for n in nodes]
    with pytest.raises(RuntimeError, match="confirmed stopped"):
        provider.terminate_node(node_id)
    provider.service.delete_application.assert_not_called()


def stopped(provider):
    node_id = launch(provider)[0]
    provider.snapshot.return_value = [{"instance_id": node_id, "status": "DEAD"}]
    provider.service.worker_records.return_value = {
        node_id: {"app_id": "app1", "autoscaling": {"cluster_id": provider.cluster_name}}
    }
    return node_id


def test_delete_requires_confirmed_cai_disappearance(provider):
    node_id = stopped(provider)
    provider.service.list_applications.return_value = [{"id": "app1"}]
    with pytest.raises(RuntimeError, match="pending confirmation"):
        provider.terminate_node(node_id)
    assert not provider.is_terminated(node_id)
    provider.service.forget_worker.assert_not_called()
    provider.service.list_applications.return_value = []
    provider.terminate_node(node_id)
    assert provider.is_terminated(node_id)
    provider.service.forget_worker.assert_called_once_with("app1")


def test_changed_identity_and_scale_up_only_block_deletion(provider):
    node_id = stopped(provider)
    provider.service.worker_records.return_value[node_id]["app_id"] = "replacement"
    with pytest.raises(RuntimeError, match="identity changed"):
        provider.terminate_node(node_id)
    provider.store.set_policy(policy("scale_up_only"))
    with pytest.raises(RuntimeError, match="disabled"):
        provider.terminate_node(node_id)
    provider.service.delete_application.assert_not_called()


def test_pool_update_does_not_replace_existing_worker_spec(provider):
    launch(provider)
    changed = policy()
    changed.pools[0].worker_spec.gpus = 2
    with pytest.raises(ValueError, match="launch spec"):
        provider.store.set_policy(changed)


def test_policy_budget_and_direct_spec_validation():
    value = policy().model_dump()
    value["max_gpus"] = 1
    # A possible pool maximum need not be pre-reserved against global budgets.
    ClusterScalingPolicy(**value)
    value["pools"][0]["initial_workers"] = 2
    with pytest.raises(ValueError, match="max_gpus"):
        ClusterScalingPolicy(**value)
    value = policy().model_dump()
    value["pools"][0]["worker_spec"]["memory"] = None
    with pytest.raises(ValueError, match="explicit"):
        ClusterScalingPolicy(**value)
    resources = worker_resources(policy().pools[0].worker_spec)
    assert resources == {"CPU": 16, "GPU": 1, "node_type:gpu-worker": 1, "accelerator_type:L40S": 1}


@pytest.mark.parametrize(
    "extra",
    [
        {"autoscaling_config": {"max_replicas": 0}},
        {"autoscaling_config": {"typo": 4}},
        {"autoscaling_config": {"max_replicas": 4}, "num_replicas": 2},
        {
            "autoscaling_config": {"max_replicas": 4, "target_ongoing_requests": 10},
            "max_ongoing_requests": 10,
        },
        {"engine_config": {"autoscaling_config": {"max_replicas": 4}}},
    ],
)
def test_replica_policy_validation(extra):
    with pytest.raises(ValueError):
        DeployApplicationRequest(name="model", engine_type="vllm", model="m", **extra)


@pytest.mark.parametrize("explicit", [False, True])
def test_autoscaling_does_not_leak_into_vllm_and_preserves_tp_bundles(
    monkeypatch, tmp_path, explicit
):
    monkeypatch.setattr(venv_utils, "VENV_BASE", str(tmp_path))
    captured = {}

    class Deployment:
        @classmethod
        def options(cls, **kwargs):
            captured.update(kwargs)
            return cls()

        def bind(self, config):
            return config

    monkeypatch.setattr(vllm_engine, "VLLMEngine", Deployment)
    config = {
        "model": "m",
        "autoscaling_config": {"min_replicas": 1, "max_replicas": 3},
        "scheduling_resources": {"node_type:gpu-worker": 0.001},
    }
    original = copy.deepcopy(config)
    bundles = [{"CPU": 4}, {"GPU": 1}, {"GPU": 1}] if explicit else None
    result = VLLMDeploymentFactory().create_deployment(
        config,
        tensor_parallel_size=2,
        multi_node=True,
        placement_group_bundles=bundles,
        max_ongoing_requests=16,
    )
    assert "autoscaling_config" not in result
    assert config == original
    assert captured["autoscaling_config"] == original["autoscaling_config"]
    assert "num_replicas" not in captured
    assert captured["max_ongoing_requests"] == 16
    assert captured["placement_group_bundles"] == [
        {"CPU": 4},
        {"GPU": 1, "node_type:gpu-worker": 0.001},
        {"GPU": 1, "node_type:gpu-worker": 0.001},
    ]


def test_schema_cleanup_and_policy_authorization(tmp_path):
    from ray_serve_cai.management.api.autoscaling import get_store
    from ray_serve_cai.management.app import app
    from ray_serve_cai.management.auth import require_admin, require_user

    paths = app.openapi()["paths"]
    assert "/api/v1/resources/nodes" in paths
    assert not any("node-types" in p for p in paths)
    assert "/api/v1/resources/workers" not in paths
    assert "/api/v1/resources/worker-apps" in paths
    # Compatibility handlers exist even though absent from OpenAPI.
    client = TestClient(app)
    assert client.get("/api/v1/resources/node-types").status_code == 401
    assert client.get("/api/v1/cluster/autoscaling").status_code == 401
    store = ScalingStore(tmp_path / "api-state.json")
    try:
        app.dependency_overrides[get_store] = lambda: store
        app.dependency_overrides[require_user] = lambda: Mock(is_admin=False, roles=[])
        assert client.get("/api/v1/cluster/autoscaling").status_code == 200
        assert client.put(
            "/api/v1/cluster/autoscaling", json=policy().model_dump()
        ).status_code in {401, 403}
        app.dependency_overrides[require_admin] = lambda: Mock(is_admin=True)
        response = client.put("/api/v1/cluster/autoscaling", json=policy().model_dump())
        assert response.status_code == 200
        assert response.json()["revision"] == 1
        status = client.get("/api/v1/cluster/autoscaling/status").json()
        assert status["supervisor_fresh"] is False
        assert status["consolidation"] == "not_implemented"
        assert (
            client.get("/api/v1/cluster/autoscaling/events").json()["events"][-1]["action"]
            == "policy_updated"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("explicit", [False, True])
def test_launch_payload_reaches_real_serve_binding(monkeypatch, tmp_path, explicit):
    """Real HTTP/schema/service/factory/binding; no Ray cluster or vLLM startup."""
    from ray import serve

    from ray_serve_cai.management.api.applications import get_coordinator
    from ray_serve_cai.management.app import app
    from ray_serve_cai.management.auth import require_admin, require_user
    from ray_serve_cai.management.services.ray_service import RayService

    payload = {
        "name": "qwen3-6-autoscaling",
        "engine_type": "vllm",
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "route_prefix": "/qwen3-6-auto",
        "tensor_parallel_size": 2,
        "multi_node": True,
        "autoscaling_config": {
            "min_replicas": 1,
            "max_replicas": 3,
            "target_ongoing_requests": 4,
            "upscale_delay_s": 30,
            "downscale_delay_s": 300,
        },
        "max_ongoing_requests": 16,
        "engine_config": {
            "dtype": "auto",
            "gpu_memory_utilization": 0.9,
            "max_model_len": 32768,
            "enforce_eager": True,
            "enable_prefix_caching": True,
            "enable_auto_tool_choice": True,
            "tool_call_parser": "qwen3_coder",
            "reasoning_parser": "qwen3",
        },
        "scheduling": {
            "resources": {"node_type:gpu-worker": 0.001, "accelerator_type:L40S": 0.001},
            "env_vars": {
                "VLLM_USE_V2_MODEL_RUNNER": "0",
                "VLLM_USE_FLASHINFER_SAMPLER": "0",
                "NCCL_SOCKET_IFNAME": "eth0",
                "GLOO_SOCKET_IFNAME": "eth0",
                "NCCL_IB_DISABLE": "1",
            },
        },
    }
    if explicit:
        payload["scheduling"].update(
            placement_group_bundles=[{"CPU": 4}, {"GPU": 1}, {"GPU": 1}],
            placement_group_strategy="PACK",
        )
    service = RayService()
    monkeypatch.setattr(service, "connect", lambda: None)
    monkeypatch.setattr(venv_utils, "VENV_BASE", str(tmp_path))
    submit = Mock()
    monkeypatch.setattr(serve, "run", submit)
    coordinator = Mock(ray_service=service)
    previous = dict(app.dependency_overrides)
    try:
        identity = Mock(username="test-admin", is_admin=True)
        app.dependency_overrides[get_coordinator] = lambda: coordinator
        app.dependency_overrides[require_admin] = lambda: identity
        app.dependency_overrides[require_user] = lambda: identity
        response = TestClient(app).post("/api/v1/applications", json=payload)
        assert response.status_code == 200, response.text
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)

    submit.assert_called_once()
    bound = submit.call_args.args[0]._bound_deployment
    config = bound._deployment_config
    assert config.autoscaling_config.min_replicas == 1
    assert config.autoscaling_config.max_replicas == 3
    assert config.autoscaling_config.target_ongoing_requests == 4
    assert config.max_ongoing_requests == 16
    engine_args = bound.init_args[0]
    assert "autoscaling_config" not in engine_args
    assert "max_ongoing_requests" not in engine_args
    for key, value in payload["engine_config"].items():
        assert engine_args[key] == value
    assert engine_args["distributed_executor_backend"] == "ray"
    assert bound._replica_config.placement_group_bundles == [
        {"CPU": 4},
        {"GPU": 1, **payload["scheduling"]["resources"]},
        {"GPU": 1, **payload["scheduling"]["resources"]},
    ]
    env = bound.ray_actor_options["runtime_env"]["env_vars"]
    assert all(env[k] == v for k, v in payload["scheduling"]["env_vars"].items())
    saved = coordinator.deployment_store.record.call_args.kwargs["request"]
    assert saved["autoscaling_config"] == payload["autoscaling_config"]
    assert saved["max_ongoing_requests"] == 16


def test_corrupt_state_fails_closed(tmp_path):
    store = ScalingStore(tmp_path / "broken.json")
    store.path.write_text("{")
    with pytest.raises(json.JSONDecodeError):
        store.read()


def test_reconciliation_observe_launch_drain_rejection_and_confirmed_delete(provider, monkeypatch):
    from ray.autoscaler.v2.utils import ResourceRequestUtil
    from ray.core.generated.autoscaler_pb2 import (
        ClusterResourceState,
        NodeState,
        NodeStatus,
        ResourceRequestByCount,
    )

    from cai_integration.autoscaling import supervisor

    cluster = ClusterResourceState(
        pending_resource_requests=[
            ResourceRequestByCount(request=ResourceRequestUtil.make({"GPU": 1}), count=1)
        ]
    )
    monkeypatch.setattr(supervisor, "get_cluster_resource_state", lambda client: cluster)
    client = Mock()
    provider.store.set_policy(policy("observe"))
    observed = supervisor.reconcile(provider.store, client, provider)
    assert observed["proposed_launches"] == [{"pool_id": "l40s", "count": 1}]
    provider.service.create_worker_node.assert_not_called()
    provider.store.set_policy(policy("full"))
    blocked = supervisor.reconcile(provider.store, client, provider)
    assert blocked["state"] == "blocked"
    supervisor.reconcile(provider.store, client, provider, network_ready=True)
    node_id = provider.non_terminated_nodes({})[0]
    provider.service.worker_records.return_value = {
        node_id: {"app_id": "app1", "autoscaling": {"cluster_id": provider.cluster_name}}
    }
    # A pending worker covers demand, including after constructing a fresh backend.
    supervisor.reconcile(provider.store, client, provider, network_ready=True)
    provider.service.create_worker_node.assert_called_once()
    cluster.ClearField("pending_resource_requests")
    live = NodeState(
        node_id=b"rank-worker",
        instance_id=node_id,
        ray_node_type_name="l40s",
        status=NodeStatus.IDLE,
        idle_duration_ms=1000000,
        total_resources={"CPU": 16, "GPU": 1},
        available_resources={"CPU": 16, "GPU": 1},
    )
    cluster.node_states.append(live)
    client.drain_node.return_value = (False, "became busy")
    supervisor.reconcile(provider.store, client, provider, network_ready=True)
    assert provider.store.read()["workers"][node_id]["state"] == "joined"
    provider.service.delete_application.assert_not_called()
    client.drain_node.return_value = (True, "")
    supervisor.reconcile(provider.store, client, provider, network_ready=True)
    assert provider.store.read()["workers"][node_id]["state"] == "draining"
    provider.service.delete_application.assert_not_called()
    cluster.node_states[0].status = NodeStatus.DEAD
    provider.snapshot.return_value = [{"instance_id": node_id, "status": "DEAD"}]
    provider.service.worker_records.return_value = {
        node_id: {"app_id": "app1", "autoscaling": {"cluster_id": provider.cluster_name}}
    }
    supervisor.reconcile(provider.store, client, provider, network_ready=True)
    assert provider.is_terminated(node_id)


def test_recovery_and_scaling_share_exclusive_lock(provider):
    from ray_serve_cai.autoscaling.lifecycle import lifecycle_lock

    with lifecycle_lock(provider.store.path.parent):
        with pytest.raises(RuntimeError, match="lifecycle operation"):
            launch(provider)
    provider.service.create_worker_node.assert_not_called()


def test_initial_pool_is_once_only_even_after_retirement(provider, monkeypatch):
    from ray.core.generated.autoscaler_pb2 import ClusterResourceState

    from cai_integration.autoscaling import supervisor
    from ray_serve_cai.autoscaling.policy import initial_remaining

    # Configure a newly named pool; bootstrap counts cannot be changed later.
    value = policy("full").model_dump()
    value["pools"][0]["id"] = "initial-pool"
    value["pools"][0]["initial_workers"] = 2
    provider.store.set_policy(ClusterScalingPolicy(**value))
    monkeypatch.setattr(supervisor, "get_cluster_resource_state", lambda _: ClusterResourceState())
    # User-selected initial capacity does not require automatic GPU growth approval.
    supervisor.reconcile(provider.store, Mock(), provider, network_ready=False)
    assert provider.service.create_worker_node.call_count == 2
    supervisor.reconcile(provider.store, Mock(), provider, network_ready=False)
    assert provider.service.create_worker_node.call_count == 2
    with provider.store.transaction() as data:
        for worker in data["workers"].values():
            worker["state"] = "terminated"
    data = provider.store.read()
    assert initial_remaining(data, data["policy"]["pools"][0]) == 0
    supervisor.reconcile(provider.store, Mock(), provider, network_ready=False)
    assert provider.service.create_worker_node.call_count == 2


@pytest.mark.parametrize("status", [400, 403, 422, 429])
def test_confirmed_admission_rejection_retries_after_durable_backoff(provider, status):
    import requests

    response = requests.Response()
    response.status_code = status
    provider.service.create_worker_node.side_effect = requests.HTTPError(response=response)
    with pytest.raises(requests.HTTPError):
        launch(provider)
    data = provider.store.read()
    assert next(iter(data["workers"].values()))["state"] == "rejected"
    assert provider.non_terminated_nodes({}) == []
    with pytest.raises(RuntimeError, match="backoff"):
        launch(provider, request="retry")
    with provider.store.transaction() as data:
        data["admission"]["l40s"]["retry_at"] = 0
    provider.service.create_worker_node.side_effect = None
    launch(provider, request="retry")
    assert provider.service.create_worker_node.call_count == 2
    assert provider.store.read()["admission"] == {}


@pytest.mark.parametrize("status", [408, 409, 500, 503])
def test_ambiguous_http_failures_do_not_free_launch_commitments(provider, status):
    import requests

    response = requests.Response()
    response.status_code = status
    provider.service.create_worker_node.side_effect = requests.HTTPError(response=response)
    with pytest.raises(requests.HTTPError):
        launch(provider)
    with pytest.raises(RuntimeError, match="unresolved"):
        launch(provider, request="retry")
    provider.service.create_worker_node.assert_called_once()


def test_optional_limits_are_enforced_against_actual_commitments(provider):
    value = policy("full").model_dump()
    value.update(max_workers=None, max_gpus=None, max_cpus=None, max_memory_gb=None)
    value["pools"][0]["max_workers"] = None
    provider.store.set_policy(ClusterScalingPolicy(**value))
    launch(provider, count=3)
    assert provider.service.create_worker_node.call_count == 3
    value["max_gpus"] = 3
    provider.store.set_policy(ClusterScalingPolicy(**value))
    with pytest.raises(RuntimeError, match="gpus budget"):
        launch(provider, request="over-budget")
