"""Regression tests for vLLM tool-calling / reasoning parser wiring.

Root cause these guard against (vLLM 0.26.0):
  OpenAIServingChat builds its OWN output parser from the tool_parser /
  reasoning_parser / enable_auto_tools kwargs passed *directly* to its
  constructor, and gates the response tool_calls on them
  (chat_completion/serving.py:152-159, 867-945). Forwarding these flags only to
  the OnlineRenderer (which validates input) leaves parser_cls at defaults, so
  tool calls leak into `content` with tool_calls=null.

These tests pin:
  1. build_vllm_engine_config carries the three user-facing flags through into
     engine_config (they'd otherwise be dropped by the whitelist).
  2. _extract_serving_kwargs maps FrontendArgs -> serving kwargs and pops them
     out of engine_config (so they never reach AsyncEngineArgs).
  3. _accepted_kwargs keeps those serving kwargs for a class whose signature
     matches vLLM 0.26.0 OpenAIServingChat — i.e. they really do land on the
     chat serving class, not just the renderer.

No Ray cluster or vLLM install needed. Run:
  python -m pytest tests/test_vllm_tool_parser_wiring.py -o addopts=""
"""

from ray_serve_cai.engines.vllm_config import build_vllm_engine_config
from ray_serve_cai.engines.vllm_engine import (
    _accepted_kwargs,
    _extract_serving_kwargs,
)


def test_build_config_passes_tool_and_reasoning_flags_through():
    user_config = {
        "model": "Qwen/Qwen3.8-27B-FP8",
        "enable_auto_tool_choice": True,
        "tool_call_parser": "qwen3_xml",
        "reasoning_parser": "qwen3",
    }
    cfg = build_vllm_engine_config(user_config)
    assert cfg["enable_auto_tool_choice"] is True
    assert cfg["tool_call_parser"] == "qwen3_xml"
    assert cfg["reasoning_parser"] == "qwen3"


def test_build_config_omits_flags_when_absent():
    cfg = build_vllm_engine_config({"model": "m"})
    assert "enable_auto_tool_choice" not in cfg
    assert "tool_call_parser" not in cfg
    assert "reasoning_parser" not in cfg


def test_extract_serving_kwargs_maps_and_pops():
    engine_config = {
        "model": "m",
        "enable_auto_tool_choice": True,
        "tool_call_parser": "qwen3_xml",
        "reasoning_parser": "qwen3",
    }
    sk = _extract_serving_kwargs(engine_config)

    # Mapped to vLLM's serving-layer names.
    assert sk["enable_auto_tools"] is True
    assert sk["tool_parser"] == "qwen3_xml"
    assert sk["reasoning_parser"] == "qwen3"
    assert sk["enable_reasoning"] is True

    # Popped out so they never reach AsyncEngineArgs.
    assert "enable_auto_tool_choice" not in engine_config
    assert "tool_call_parser" not in engine_config
    assert "reasoning_parser" not in engine_config
    assert engine_config["model"] == "m"


def test_extract_serving_kwargs_empty_when_absent():
    assert _extract_serving_kwargs({"model": "m"}) == {}


def test_disabled_auto_tool_choice_is_not_forwarded():
    # enable_auto_tool_choice=False must NOT set enable_auto_tools.
    sk = _extract_serving_kwargs({"enable_auto_tool_choice": False})
    assert "enable_auto_tools" not in sk


class _FakeOpenAIServingChat026:
    """Mirrors the keyword-only signature of vLLM 0.26.0 OpenAIServingChat for
    the subset that matters to _accepted_kwargs filtering."""

    def __init__(
        self,
        engine_client=None,
        models=None,
        response_role="assistant",
        *,
        online_renderer=None,
        request_logger=None,
        chat_template=None,
        chat_template_content_format="auto",
        reasoning_parser="",
        enable_auto_tools=False,
        tool_parser=None,
    ):
        pass


def test_accepted_kwargs_keeps_serving_flags_for_026_chat_signature():
    serving_kwargs = _extract_serving_kwargs({
        "enable_auto_tool_choice": True,
        "tool_call_parser": "qwen3_xml",
        "reasoning_parser": "qwen3",
    })
    kept = _accepted_kwargs(_FakeOpenAIServingChat026, {
        "engine_client": object(),
        "models": object(),
        "response_role": "assistant",
        "online_renderer": object(),
        "request_logger": None,
        **serving_kwargs,
    })
    # The three parser flags OpenAIServingChat needs for OUTPUT extraction.
    assert kept["enable_auto_tools"] is True
    assert kept["tool_parser"] == "qwen3_xml"
    assert kept["reasoning_parser"] == "qwen3"
    # enable_reasoning is not in the 0.26 signature and must be dropped.
    assert "enable_reasoning" not in kept
