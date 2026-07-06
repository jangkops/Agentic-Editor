"""Regression — OpenAI 함수호출 실행 루프(route_openai_agent).

GPT가 스스로 도구를 호출하면 시스템이 실제 실행하고 결과를 누적해 다시 호출,
도구 호출이 없으면 최종 텍스트를 반환한다. 생성 파일은 verified_files로 추적.

대상: ai_engine.server.route_openai_agent
실행: pytest scripts/test_openai_agent_tool_loop.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

server = pytest.importorskip("ai_engine.server")


class _FakeGW:
    """openai_responses_call을 미리 준비한 응답 시퀀스로 흉내낸다."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def _to_openai_input(self, messages):
        # 실제 변환기와 동일한 형태의 최소 구현
        return [{"role": "user", "content": [{"type": "input_text", "text": "go"}]}]

    async def openai_responses_call(self, body, timeout=120):
        self.calls += 1
        return self._responses.pop(0) if self._responses else {"output": {"output": []}}


def _wrap(items):
    """게이트웨이 래퍼 형태로 감싼다."""
    return {"decision": "ALLOW", "output": {"object": "response", "output": items},
            "usage": {"input_tokens": 1, "output_tokens": 1}}


def _fc(name, args, call_id="c1"):
    return {"type": "function_call", "call_id": call_id, "name": name,
            "arguments": json.dumps(args)}


def _msg(text):
    return {"type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": text}]}


def test_tool_loop_executes_and_returns_text(monkeypatch, tmp_path):
    # 1회차: generate_pptx function_call → 2회차: 최종 텍스트(도구 없음)
    made = tmp_path / "out.pptx"
    made.write_bytes(b"PK\x03\x04fake")  # 0바이트 아님

    def _fake_exec(tool_name, args, project_path="", aws_profile="", bedrock_user="", template_id=""):
        assert tool_name == "generate_pptx"
        return json.dumps({"path": ".generated/out.pptx", "absPath": str(made), "model": "python-pptx"})

    monkeypatch.setattr(server, "_execute_tool", _fake_exec)

    gw = _FakeGW([
        _wrap([_fc("generate_pptx", {"title": "T", "slides": []})]),
        _wrap([_msg("완료했습니다.")]),
    ])
    res = asyncio.run(server.route_openai_agent(
        gw, "openai.gpt-5.5", [{"role": "user", "content": "pptx 만들어줘"}],
        project_path=str(tmp_path), max_iters=5))
    assert gw.calls == 2
    assert "완료" in res["text"]
    paths = [f["path"] for f in res["verified_files"]]
    assert ".generated/out.pptx" in paths


def test_no_tool_calls_returns_text_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_execute_tool", lambda *a, **k: "{}")
    gw = _FakeGW([_wrap([_msg("그냥 답변")])])
    res = asyncio.run(server.route_openai_agent(
        gw, "openai.gpt-5.5", [{"role": "user", "content": "안녕"}],
        project_path=str(tmp_path), max_iters=5))
    assert gw.calls == 1
    assert res["text"] == "그냥 답변"
    assert res["verified_files"] == []


def test_loop_stops_at_max_iters(monkeypatch, tmp_path):
    # 항상 function_call만 반환 → max_iters에서 종료(무한루프 방지)
    monkeypatch.setattr(server, "_execute_tool", lambda *a, **k: "{}")

    class _AlwaysCall(_FakeGW):
        async def openai_responses_call(self, body, timeout=120):
            self.calls += 1
            return _wrap([_fc("search_files", {"query": "x", "path": "."}, call_id=f"c{self.calls}")])

    gw = _AlwaysCall([])
    res = asyncio.run(server.route_openai_agent(
        gw, "openai.gpt-5.5", [{"role": "user", "content": "검색"}],
        project_path=str(tmp_path), max_iters=4))
    assert gw.calls == 4  # max_iters 초과 안 함


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
