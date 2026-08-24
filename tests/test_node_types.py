"""Unit tests for runtime node_type definition (CAIService.define_node_type).

Uses a tmp cluster_info file and a stubbed launcher renderer — no /home/cdsw,
Ray, CML, or Jinja templates needed.
Run: python -m pytest tests/test_node_types.py -o addopts=""
"""

import json

import pytest

import cai_integration.launch_ray_cluster as launcher
from ray_serve_cai.management.services import cai_service as cai_service_mod
from ray_serve_cai.management.services.cai_service import CAIService


@pytest.fixture()
def info_path(tmp_path, monkeypatch):
    """Point the service at a tmp cluster_info.json seeded with built-in groups."""
    p = tmp_path / "ray_cluster_info.json"
    p.write_text(json.dumps({
        "head_address": "10.0.0.1:6379",
        "configuration": {"ray_port": 6379, "metrics_port": 9090},
        "worker_groups": [
            {"name": "l40-gpu-workers", "node_type": "l40-gpu-worker", "count": 1,
             "cpu": 16, "memory": 64, "gpus": 1, "accelerator_type": "L40S",
             "node_label": None, "script_path": "/home/cdsw/ray_worker_l40.py",
             "runtime_identifier": None},
        ],
    }))
    monkeypatch.setattr(cai_service_mod, "_CLUSTER_INFO_PATH", p)
    return p


@pytest.fixture()
def svc(info_path, monkeypatch):
    # Stub the launcher renderer: just record a script_path, no template/disk I/O.
    def fake_render(group, **kwargs):
        group.script_path = f"/home/cdsw/ray_worker_{group.node_type}.py"
        return group.script_path
    monkeypatch.setattr(launcher, "render_worker_launcher", fake_render)
    return CAIService(project_id="p", cml_host="http://cml.example", api_key="k")


def test_define_new_node_type(svc, info_path):
    g = svc.define_node_type("l40-gpu-worker-12cpu", cpu=12, memory=64, gpus=1,
                             accelerator_type="L40S")
    assert g["node_type"] == "l40-gpu-worker-12cpu"
    assert g["cpu"] == 12 and g["gpus"] == 1
    assert g["script_path"].endswith("l40-gpu-worker-12cpu.py")  # renderer ran

    # Persisted to disk and now visible to the add-node lookup path.
    on_disk = json.loads(info_path.read_text())["worker_groups"]
    assert any(x["node_type"] == "l40-gpu-worker-12cpu" for x in on_disk)
    assert svc._group_from_cluster_info("l40-gpu-worker-12cpu").cpu == 12


def test_duplicate_node_type_rejected(svc):
    with pytest.raises(ValueError, match="already exists"):
        svc.define_node_type("l40-gpu-worker", cpu=8, memory=32)  # built-in exists


def test_unknown_node_type_still_raises_until_defined(svc):
    with pytest.raises(RuntimeError, match="Available"):
        svc._group_from_cluster_info("nope")


def test_remove_node_type(svc, info_path):
    svc.define_node_type("temp-worker", cpu=4, memory=16)
    svc.remove_worker_group("temp-worker")
    on_disk = json.loads(info_path.read_text())["worker_groups"]
    assert not any(x["node_type"] == "temp-worker" for x in on_disk)


def test_remove_missing_raises(svc):
    with pytest.raises(ValueError, match="not found"):
        svc.remove_worker_group("ghost")


def test_atomic_write_leaves_no_tmp(svc, info_path):
    svc.define_node_type("t1", cpu=4, memory=16)
    assert not (info_path.parent / (info_path.name + ".tmp")).exists()
