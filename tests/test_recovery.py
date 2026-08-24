"""Unit tests for head-node recovery: state machine, lock, and orchestrator.

Fully mocked — no CML, Ray, or network. Time is driven by a fake sleep that
just counts, so the wait loop is deterministic.
Run: python -m pytest tests/test_recovery.py -o addopts=""
"""

import json

import pytest

from ray_serve_cai.recovery.recover import RecoveryOrchestrator
from ray_serve_cai.recovery.recovery_state import RecoveryState

# ── State machine ──────────────────────────────────────────────────────────

@pytest.fixture()
def state(tmp_path):
    return RecoveryState(path=tmp_path / "recovery_state.json", lock_path=tmp_path / ".lock")


def test_set_phase_persists_atomically(state):
    state.set_phase("RESTARTING_HEAD", head_app_id="app-1")
    data = json.loads(state._path.read_text())
    assert data["phase"] == "RESTARTING_HEAD"
    assert data["head_app_id"] == "app-1"
    assert data["started_at"] and data["updated_at"]


def test_unknown_phase_rejected(state):
    with pytest.raises(ValueError):
        state.set_phase("BOGUS")


def test_is_resumable(state):
    assert not state.is_resumable()
    state.set_phase("WAITING_HEAD")
    assert state.is_resumable()
    state.set_phase("COMPLETE")
    assert not state.is_resumable()


def test_clear(state):
    state.set_phase("DETECTED")
    state.clear()
    assert state.phase() is None


def test_lock_blocks_second_holder(state):
    assert state.acquire_lock("a")
    assert not state.acquire_lock("b")
    state.release_lock()
    assert state.acquire_lock("b")


def test_stale_lock_reclaimed(state):
    assert state.acquire_lock("a")
    # Rewrite lock with an old timestamp -> reclaimable.
    state._lock_path.write_text(json.dumps({"owner": "a", "at": "2000-01-01T00:00:00+00:00"}))
    assert state.acquire_lock("b", stale_after_s=60)


# ── Orchestrator ───────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeHttp:
    """health OK, gcs-address OK, redeploy POST OK; records calls."""

    def __init__(self):
        self.posts = []

    def get(self, url):
        if url.endswith("/api/health"):
            return _Resp(200, {"status": "healthy"})
        if url.endswith("/gcs-address"):
            return _Resp(200, {"gcs_address": "10.0.0.9:6379"})
        return _Resp(404)

    def post(self, url, json=None, headers=None):
        self.posts.append((url, json, headers))
        return _Resp(200, {"status": "deploying"})


class _FakeCml:
    def __init__(self):
        self.restarted = []
        self.stopped = []
        self.apps = [
            {"id": "h", "name": "ray-cluster-head"},
            {"id": "w1", "name": "ray-l40-workers-111"},
        ]

    def restart_application(self, app_id):
        self.restarted.append(app_id)
        return True

    def stop_application(self, app_id):
        self.stopped.append(app_id)
        return True

    def list_applications(self):
        return self.apps


class _FakeCai:
    def __init__(self):
        self.created = []

    def create_worker_node(self, node_type=None):
        self.created.append(node_type)
        return {"app_id": "new", "app_name": "ray-x", "node_type": node_type}


class _FakeStore:
    def all_records(self):
        return [{"name": "qwen3", "request": {"name": "qwen3", "engine_type": "vllm", "model": "m"}}]


@pytest.fixture()
def cluster_info(tmp_path):
    p = tmp_path / "ray_cluster_info.json"
    p.write_text(json.dumps({
        "head_app_id": "h",
        "head_app_name": "ray-cluster-head",
        "head_url": "https://head.example.com",
        "management_api_url": "https://head.example.com",
        "head_address": "10.0.0.1:6379",
        "worker_groups": [{"name": "l40", "node_type": "l40-gpu-worker", "count": 2}],
    }))
    return p


def _orch(cluster_info, state, **kw):
    return RecoveryOrchestrator(
        cml=_FakeCml(),
        cai_service=_FakeCai(),
        deployment_store=_FakeStore(),
        cluster_info_path=cluster_info,
        http=_FakeHttp(),
        service_token="svc-key",
        state=state,
        sleep=lambda s: None,
        poll_interval_s=1.0,
        head_timeout_s=30.0,
        **kw,
    )


def test_full_recovery_happy_path(cluster_info, state):
    orch = _orch(cluster_info, state)
    result = orch.run()
    assert result["status"] == "recovered"
    assert result["head_address"] == "10.0.0.9:6379"
    assert orch.cml.restarted == ["h"]                  # head restarted once
    assert orch.cml.stopped == ["w1"]                   # stale worker deleted
    assert orch.cai_service.created == ["l40-gpu-worker", "l40-gpu-worker"]  # 2 recreated
    assert result["deployments"] == {"ok": 1, "fail": 0}
    # cluster_info head_address rewritten
    assert json.loads(cluster_info.read_text())["head_address"] == "10.0.0.9:6379"
    # state cleared on completion
    assert state.phase() is None


def test_redeploy_sends_bearer_token(cluster_info, state):
    orch = _orch(cluster_info, state)
    orch.run()
    _, body, headers = orch.http.posts[0]
    assert headers["Authorization"] == "Bearer svc-key"
    assert body["name"] == "qwen3"


def test_dry_run_mutates_nothing(cluster_info, state):
    orch = _orch(cluster_info, state, dry_run=True)
    result = orch.run()
    assert result["status"] == "recovered"
    assert orch.cml.restarted == []      # no restart
    assert orch.cml.stopped == []        # no deletes
    assert orch.cai_service.created == []  # no creates
    assert orch.http.posts == []         # no redeploys
    # cluster info untouched
    assert json.loads(cluster_info.read_text())["head_address"] == "10.0.0.1:6379"


def test_resume_does_not_restart_head_twice(cluster_info, state):
    # Simulate a prior run that already restarted the head and died.
    state.set_phase("RESTARTING_HEAD", head_app_id="h")
    orch = _orch(cluster_info, state)
    orch.run()
    assert orch.cml.restarted == []  # NOT restarted again


def test_head_timeout_raises(cluster_info, state):
    orch = _orch(cluster_info, state)

    class _Down:
        def get(self, url):
            return _Resp(503)

        def post(self, *a, **k):
            return _Resp(200)

    orch.http = _Down()
    with pytest.raises(TimeoutError):
        orch.run()
    # lock released even on failure
    assert state.acquire_lock("next")
