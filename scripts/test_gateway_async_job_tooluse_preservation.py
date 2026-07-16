"""게이트웨이 비동기 잡(ACCEPTED→S3 폴링) 경로의 toolUse 보존 회귀 테스트.

배경(라이브 발견 결함 — 2026):
  `/converse` 는 일부 모델(Opus, toolConfig 동반 Sonnet 등)에서 `ACCEPTED` 를 반환하고
  비동기 S3 잡 폴링 경로를 탄다. 과거 폴링 헬퍼(_poll_job_result)는 **text 블록만** 뽑아
  반환해, 잡 결과에 담긴 toolUse 블록을 통째로 유실했다. 그 결과 planner(select_plan)/
  evaluator(submit_evaluation) 같은 toolChoice 강제 호출이 tool_calls 를 못 받아
  폴백(단일 subtask / achieved=True 기본값)으로 무력화됐다.

수정: GatewayClient._poll_job_data 가 구조화 dict 를 그대로 반환하고, converse 가
  output.message(toolUse 포함)를 손실 없이 전달한다.

이 테스트는 네트워크/AWS 없이 urlopen(ACCEPTED)과 _poll_job_data(잡 결과)를 mock 한다.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_gateway_async_job_tooluse_preservation.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine.gateway_module import GatewayClient


def _make_client():
    c = GatewayClient(gateway_url="https://example.invalid/v1")
    c._sign = lambda method, url, body_bytes: {"Content-Type": "application/json"}
    return c


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b


def _install_accepted_urlopen(job_id="job-123"):
    """초기 /converse 호출이 항상 ACCEPTED(비동기 잡)로 응답하도록 mock."""
    def _fake_urlopen(req, timeout=None):
        return _FakeResp({"decision": "ACCEPTED", "job_id": job_id,
                          "remaining_quota": {"x": 1}, "estimated_cost_krw": 42})
    urllib.request.urlopen = _fake_urlopen


_TOOLUSE_JOB = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "tu_1", "name": "select_plan",
                             "input": {"subtasks": [
                                 {"id": "t1", "domain": "coding", "subtask": "함수 작성", "depends_on": []},
                                 {"id": "t2", "domain": "research", "subtask": "조사", "depends_on": []},
                             ]}}}
            ],
        }
    },
    "usage": {"inputTokens": 10, "outputTokens": 20},
    "stopReason": "tool_use",
}

_TEXT_JOB = {
    "output": {"message": {"role": "assistant", "content": [
        {"text": "안녕하세요. "}, {"text": "결과입니다."}]}},
}


def _run(coro):
    return asyncio.run(coro)


def test_converse_async_preserves_tooluse():
    """ACCEPTED + toolUse 잡 결과 → converse 가 toolUse 블록을 그대로 보존."""
    _orig = urllib.request.urlopen
    try:
        _install_accepted_urlopen()
        c = _make_client()
        c._poll_job_data = lambda job_id, max_wait=300: _await_val(dict(_TOOLUSE_JOB))
        result = _run(c.converse("us.anthropic.claude-x",
                                 [{"role": "user", "content": [{"text": "hi"}]}],
                                 tool_config={"tools": []}))
        assert result.get("decision") == "ALLOW"
        content = result["output"]["message"]["content"]
        # toolUse 블록이 살아있어야 한다(text 로 뭉개지지 않음).
        tool_blocks = [b for b in content if isinstance(b, dict) and "toolUse" in b]
        assert len(tool_blocks) == 1, f"toolUse 블록 유실: {content}"
        assert tool_blocks[0]["toolUse"]["name"] == "select_plan"
        subtasks = tool_blocks[0]["toolUse"]["input"]["subtasks"]
        assert len(subtasks) == 2
        # 부가 필드(usage/stopReason)도 전달.
        assert result.get("stopReason") == "tool_use"
        assert result.get("usage", {}).get("outputTokens") == 20
        # quota/비용은 초기 ACCEPTED 응답 값 유지.
        assert result.get("estimated_cost_krw") == 42
    finally:
        urllib.request.urlopen = _orig


def test_converse_async_text_backward_compat():
    """ACCEPTED + text 전용 잡 결과 → 기존처럼 text 블록으로 정상 전달(하위 호환)."""
    _orig = urllib.request.urlopen
    try:
        _install_accepted_urlopen()
        c = _make_client()
        c._poll_job_data = lambda job_id, max_wait=300: _await_val(dict(_TEXT_JOB))
        result = _run(c.converse("us.anthropic.claude-x",
                                 [{"role": "user", "content": [{"text": "hi"}]}]))
        assert result.get("decision") == "ALLOW"
        content = result["output"]["message"]["content"]
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and "text" in b]
        assert "안녕하세요" in "".join(texts)
    finally:
        urllib.request.urlopen = _orig


def test_converse_async_timeout_returns_error():
    """폴링이 결과를 못 얻으면(None) 시간초과 ERROR 반환(비차단 상위 폴백 가능)."""
    _orig = urllib.request.urlopen
    try:
        _install_accepted_urlopen()
        c = _make_client()
        c._poll_job_data = lambda job_id, max_wait=300: _await_val(None)
        result = _run(c.converse("us.anthropic.claude-x",
                                 [{"role": "user", "content": [{"text": "hi"}]}]))
        assert result.get("decision") == "ERROR"
        assert "시간 초과" in result.get("error", "")
    finally:
        urllib.request.urlopen = _orig


def test_job_data_to_text_helper():
    """_job_data_to_text: text 블록만 이어붙이고, toolUse 전용이면 진단용 JSON 반환."""
    assert GatewayClient._job_data_to_text(_TEXT_JOB) == "안녕하세요. \n결과입니다."
    # toolUse 전용 → text 없음 → JSON 직렬화(진단)로 폴백, toolUse 문자열 흔적 존재.
    diag = GatewayClient._job_data_to_text(_TOOLUSE_JOB)
    assert "select_plan" in diag
    assert GatewayClient._job_data_to_text(None) == ""


def test_agenerate_end_to_end_tool_calls_from_async_job():
    """GatewayChatModel._agenerate(ainvoke)가 비동기 잡 toolUse → tool_calls 로 파싱."""
    from ai_engine.agent_system.chat_model_adapter import GatewayChatModel
    from langchain_core.messages import HumanMessage

    _orig = urllib.request.urlopen
    try:
        _install_accepted_urlopen()
        c = _make_client()
        c._poll_job_data = lambda job_id, max_wait=300: _await_val(dict(_TOOLUSE_JOB))
        llm = GatewayChatModel(gateway=c, model_id="us.anthropic.claude-x")
        ai = _run(llm.ainvoke([HumanMessage(content="hi")]))
        assert getattr(ai, "tool_calls", None), "tool_calls 가 비어있음 — toolUse 파싱 실패"
        tc = ai.tool_calls[0]
        assert tc["name"] == "select_plan"
        assert len(tc["args"]["subtasks"]) == 2
    finally:
        urllib.request.urlopen = _orig


async def _await_val(v):
    """coroutine 을 요구하는 자리(await _poll_job_data(...))에 상수를 주입하기 위한 래퍼."""
    return v


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
