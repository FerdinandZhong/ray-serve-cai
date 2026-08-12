"""Unit tests for DeploymentStore (deploy-intent persistence).

Uses a tmp file path — no /home/cdsw, Ray, or CML needed.
Run: python -m pytest tests/test_deployment_store.py -o addopts=""
"""

import json

import pytest

from ray_serve_cai.management.services.deployment_store import DeploymentStore


@pytest.fixture()
def store(tmp_path):
    return DeploymentStore(path=tmp_path / "ray_serve_deployments.json")


def test_record_and_get(store):
    store.record(
        "qwen3",
        route_prefix="/qwen3",
        engine_type="vllm",
        model="Qwen/Qwen3",
        venv_name="vllm-013",
        num_replicas=2,
        tensor_parallel_size=4,
        request={"name": "qwen3", "engine_type": "vllm"},
        deployer="alice",
    )
    rec = store.get("qwen3")
    assert rec["engine_type"] == "vllm"
    assert rec["deployer"] == "alice"
    assert rec["num_replicas"] == 2
    assert rec["created_at"] and rec["updated_at"]


def test_record_persists_to_disk(store):
    store.record("a", engine_type="vllm")
    on_disk = json.loads(store._path.read_text())
    assert "a" in on_disk["deployments"]
    assert on_disk["last_updated"]


def test_remove(store):
    store.record("a", engine_type="vllm")
    store.remove("a")
    assert store.get("a") is None


def test_remove_missing_is_noop(store):
    store.remove("ghost")  # must not raise
    assert store.all_records() == []


def test_redeploy_preserves_created_at(store):
    store.record("a", num_replicas=1)
    created = store.get("a")["created_at"]
    store.record("a", num_replicas=5)
    rec = store.get("a")
    assert rec["created_at"] == created  # preserved
    assert rec["num_replicas"] == 5      # updated


def test_reconcile_flags_live_and_drift(store):
    store.record("live-app", engine_type="vllm")
    store.record("gone-app", engine_type="vllm")
    records = store.reconcile(live_names={"live-app"})
    by_name = {r["name"]: r["live"] for r in records}
    assert by_name == {"live-app": True, "gone-app": False}


def test_reconcile_does_not_mutate_store(store):
    store.record("a", engine_type="vllm")
    store.reconcile(live_names=set())
    # Drift is surfaced, not deleted.
    assert store.get("a") is not None


def test_all_records(store):
    store.record("a", engine_type="vllm")
    store.record("b", engine_type="sglang")
    names = {r["name"] for r in store.all_records()}
    assert names == {"a", "b"}


def test_corrupt_file_recovers_gracefully(store):
    store._path.write_text("{ not json")
    # Load should fall back to empty rather than raise.
    assert store.all_records() == []
    store.record("a", engine_type="vllm")
    assert store.get("a") is not None
