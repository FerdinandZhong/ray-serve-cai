"""Unit tests for the vLLM model-endpoint API-key middleware.

Drives the ASGI middleware directly with a fake app/send — no Ray or vLLM
needed. Run: python -m pytest tests/test_vllm_api_key_middleware.py -o addopts=""
"""

import asyncio

import pytest

from ray_serve_cai.engines.vllm_engine import _ApiKeyMiddleware


class _Recorder:
    """Captures ASGI send() events and whether the inner app was called."""

    def __init__(self):
        self.status = None
        self.inner_called = False

    async def inner_app(self, scope, receive, send):
        self.inner_called = True

    async def send(self, event):
        if event["type"] == "http.response.start":
            self.status = event["status"]


def _scope(path="/v1/chat/completions", token=None):
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {"type": "http", "path": path, "headers": headers}


def _run(mw, scope, rec):
    asyncio.run(mw(scope, None, rec.send))


@pytest.fixture()
def _no_key(monkeypatch):
    monkeypatch.delenv("VLLM_API_KEY", raising=False)


@pytest.fixture()
def _with_key(monkeypatch):
    monkeypatch.setenv("VLLM_API_KEY", "secret-123")


def test_fail_open_when_no_key(_no_key):
    rec = _Recorder()
    mw = _ApiKeyMiddleware(rec.inner_app)
    _run(mw, _scope(token=None), rec)
    assert rec.inner_called
    assert rec.status is None  # not rejected


def test_rejects_missing_token_when_key_set(_with_key):
    rec = _Recorder()
    mw = _ApiKeyMiddleware(rec.inner_app)
    _run(mw, _scope(token=None), rec)
    assert not rec.inner_called
    assert rec.status == 401


def test_rejects_wrong_token(_with_key):
    rec = _Recorder()
    mw = _ApiKeyMiddleware(rec.inner_app)
    _run(mw, _scope(token="wrong"), rec)
    assert not rec.inner_called
    assert rec.status == 401


def test_allows_correct_token(_with_key):
    rec = _Recorder()
    mw = _ApiKeyMiddleware(rec.inner_app)
    _run(mw, _scope(token="secret-123"), rec)
    assert rec.inner_called
    assert rec.status is None


def test_health_is_exempt_even_with_key(_with_key):
    rec = _Recorder()
    mw = _ApiKeyMiddleware(rec.inner_app)
    _run(mw, _scope(path="/some-prefix/health", token=None), rec)
    assert rec.inner_called
    assert rec.status is None
