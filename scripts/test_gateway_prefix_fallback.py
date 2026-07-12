"""게이트웨이 양방향 modelId prefix 폴백 테스트 (수정 A 검증).

대상: ai_engine/gateway_module.py
- GatewayClient.converse            : /converse 재시도 루프의 양방향 prefix 폴백
- GatewayClient.converse_stream_live: Lambda 스트리밍 경로의 양방향 prefix 폴백
- 모듈 헬퍼 _is_prefix_form_error / _strip_region_prefix / _has_region_prefix

시나리오(요구사항 매핑):
- (a) bare 호출이 'model identifier is invalid'로 실패 → us. prefix로 재시도해 성공
- (b) us. 호출이 'not in allowed'로 실패 → bare로 재시도해 성공
- (c) 항상 실패시켜도 prefix 폴백은 정확히 1회만 (무한루프 방지)

제약 준수:
- 실제 게이트웨이/네트워크 호출 없음. urllib.request.urlopen(converse)와
  _converse_stream_live_once(stream)를 mock. _sign은 더미로 대체(자격증명 불필요).
- asyncio.run 으로 유한 시간에 실행(무한대기 없음).

실행: ai_engine/.venv/bin/python scripts/test_gateway_prefix_fallback.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine import gateway_module as gm
from ai_engine.gateway_module import (
    GatewayClient,
    _is_prefix_form_error,
    _strip_region_prefix,
    _has_region_prefix,
)

_PASS = 0
_FAIL = 0


def _check(name, cond):
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print(f"  PASS: {name}")
    else:
        _FAIL += 1
        print(f"  FAIL: {name}")


def _make_client():
    c = GatewayClient(gateway_url="https://example.invalid/v1")
    # 서명/자격증명 우회 — 네트워크·STS 호출 방지
    c._sign = lambda method, url, body_bytes: {"Content-Type": "application/json"}
    return c


# ── converse: urlopen mock 기반 ─────────────────────────────────────
def _install_converse_mock(responder):
    """urllib.request.urlopen을 mock. responder(model_id)->dict 를 JSON body로 반환.
    호출된 modelId 순서를 calls 리스트에 기록해 반환."""
    calls = []

    class _FakeResp:
        def __init__(self, payload):
            self._b = json.dumps(payload).encode()

        def read(self):
            return self._b

    def _fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode())
        mid = body.get("modelId", "")
        calls.append(mid)
        return _FakeResp(responder(mid))

    urllib.request.urlopen = _fake_urlopen
    return calls


def test_converse_bare_to_us():
    print("[converse] (a) bare 실패(invalid identifier) → us. 재시도 성공")
    _orig = urllib.request.urlopen
    try:
        def responder(mid):
            if _has_region_prefix(mid):
                return {"decision": "ALLOW", "output": {"message": {"content": [{"text": "ok"}]}}}
            return {"decision": "ERROR", "error": "ValidationException: model identifier is invalid"}

        calls = _install_converse_mock(responder)
        c = _make_client()
        result = asyncio.run(c.converse("anthropic.claude-x", [{"role": "user", "content": [{"text": "hi"}]}]))
        _check("최종 ALLOW", result.get("decision") == "ALLOW")
        _check("첫 호출 bare", calls[0] == "anthropic.claude-x")
        _check("재시도 us. prefix", calls[1] == "us.anthropic.claude-x")
        _check("호출 2회(폴백 1회)", len(calls) == 2)
    finally:
        urllib.request.urlopen = _orig


def test_converse_us_to_bare():
    print("[converse] (b) us. 실패(not in allowed) → bare 재시도 성공")
    _orig = urllib.request.urlopen
    try:
        def responder(mid):
            if _has_region_prefix(mid):
                return {"decision": "DENY", "denial_reason": "model us.nvidia.nemotron is not in allowed list"}
            return {"decision": "ALLOW", "output": {"message": {"content": [{"text": "ok"}]}}}

        calls = _install_converse_mock(responder)
        c = _make_client()
        result = asyncio.run(c.converse("us.nvidia.nemotron", [{"role": "user", "content": [{"text": "hi"}]}]))
        _check("최종 ALLOW", result.get("decision") == "ALLOW")
        _check("첫 호출 us.", calls[0] == "us.nvidia.nemotron")
        _check("재시도 bare", calls[1] == "nvidia.nemotron")
        _check("호출 2회(폴백 1회)", len(calls) == 2)
    finally:
        urllib.request.urlopen = _orig


def test_converse_fallback_once_only():
    print("[converse] (c) 항상 실패 → prefix 폴백 정확히 1회만")
    _orig = urllib.request.urlopen
    try:
        def responder(mid):
            # 어떤 형태든 항상 invalid → 폴백 유도, 무한루프 방지 확인
            return {"decision": "ERROR", "error": "model identifier is invalid"}

        calls = _install_converse_mock(responder)
        c = _make_client()
        result = asyncio.run(c.converse("qwen.qwen3", [{"role": "user", "content": [{"text": "hi"}]}]))
        _check("최종 ERROR 반환", result.get("decision") == "ERROR")
        # 서로 다른 modelId는 정확히 2종(원본 + 1회 flip)
        distinct = list(dict.fromkeys(calls))
        _check("distinct modelId 2종(폴백 1회)", distinct == ["qwen.qwen3", "us.qwen.qwen3"], )
        # flip은 정확히 1회 — bare→us 이후 다시 us→bare로 되돌아가지 않음
        _check("prefix flip 정확히 1회", calls.count("qwen.qwen3") == 1 and calls[1] == "us.qwen.qwen3")
        # 총 호출은 최대 attempt(3) — 무한루프 아님
        _check("총 호출 3회 이하(무한루프 아님)", len(calls) <= 3)
    finally:
        urllib.request.urlopen = _orig


# ── converse_stream_live: _converse_stream_live_once mock ───────────
def test_stream_bare_to_us():
    print("[stream] (a) bare 실패(invalid identifier) → us. 재시도 성공")
    calls = []

    async def fake_once(self, model_id, messages, system_prompt="", tool_config=None):
        calls.append(model_id)
        if _has_region_prefix(model_id):
            return {"decision": "ALLOW", "text": "ok"}
        return {"decision": "ERROR", "error": "model identifier is invalid"}

    _orig = GatewayClient._converse_stream_live_once
    try:
        GatewayClient._converse_stream_live_once = fake_once
        c = _make_client()
        result = asyncio.run(c.converse_stream_live("google.gemma-3", [{"role": "user", "content": [{"text": "hi"}]}]))
        _check("최종 ALLOW", result.get("decision") == "ALLOW")
        _check("첫 호출 bare", calls[0] == "google.gemma-3")
        _check("재시도 us.", calls[1] == "us.google.gemma-3")
        _check("호출 2회", len(calls) == 2)
    finally:
        GatewayClient._converse_stream_live_once = _orig


def test_stream_us_to_bare():
    print("[stream] (b) us. 실패(not in allowed) → bare 재시도 성공")
    calls = []

    async def fake_once(self, model_id, messages, system_prompt="", tool_config=None):
        calls.append(model_id)
        if _has_region_prefix(model_id):
            return {"decision": "ERROR", "error": "model is not in allowed list"}
        return {"decision": "ALLOW", "text": "ok"}

    _orig = GatewayClient._converse_stream_live_once
    try:
        GatewayClient._converse_stream_live_once = fake_once
        c = _make_client()
        result = asyncio.run(c.converse_stream_live("us.zai.glm", [{"role": "user", "content": [{"text": "hi"}]}]))
        _check("최종 ALLOW", result.get("decision") == "ALLOW")
        _check("첫 호출 us.", calls[0] == "us.zai.glm")
        _check("재시도 bare", calls[1] == "zai.glm")
        _check("호출 2회", len(calls) == 2)
    finally:
        GatewayClient._converse_stream_live_once = _orig


def test_stream_fallback_once_only():
    print("[stream] (c) 항상 실패 → prefix 폴백 정확히 1회만")
    calls = []

    async def fake_once(self, model_id, messages, system_prompt="", tool_config=None):
        calls.append(model_id)
        return {"decision": "ERROR", "error": "model identifier is invalid"}

    _orig = GatewayClient._converse_stream_live_once
    try:
        GatewayClient._converse_stream_live_once = fake_once
        c = _make_client()
        result = asyncio.run(c.converse_stream_live("minimax.abab", [{"role": "user", "content": [{"text": "hi"}]}]))
        _check("최종 ERROR", result.get("decision") == "ERROR")
        distinct = list(dict.fromkeys(calls))
        _check("distinct modelId 2종(폴백 1회)", distinct == ["minimax.abab", "us.minimax.abab"])
        _check("prefix flip 정확히 1회", calls.count("minimax.abab") == 1 and calls[1] == "us.minimax.abab")
        _check("총 호출 3회 이하(무한루프 아님)", len(calls) <= 3)
    finally:
        GatewayClient._converse_stream_live_once = _orig


# ── stream_sse_realtime: httpx.AsyncClient mock ────────────────────
def _run_sse(client, model_id):
    """async generator를 유한하게 소비해 이벤트 리스트 반환."""
    async def _consume():
        evts = []
        async for e in client.stream_sse_realtime(model_id, [{"role": "user", "content": [{"text": "hi"}]}]):
            evts.append(e)
        return evts
    return asyncio.run(_consume())


def _install_sse_mock(responder):
    """gm.httpx.AsyncClient를 mock. responder(model_id)->(status, [lines]).
    호출 modelId를 calls에 기록."""
    calls = []

    class _FakeStreamResp:
        def __init__(self, status, lines):
            self.status_code = status
            self._lines = lines

        async def aiter_text(self):
            for ln in self._lines:
                yield ln

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, content=None, headers=None):
            body = json.loads(content.decode())
            mid = body.get("modelId", "")
            calls.append(mid)
            status, lines = responder(mid)
            return _FakeStreamResp(status, lines)

    gm.httpx.AsyncClient = _FakeClient
    return calls


def _sse_client():
    c = _make_client()
    c._get_creds = lambda: gm.Credentials("AKIDEXAMPLE", "SECRET", "TOKEN")
    return c


def test_sse_bare_to_us():
    print("[sse] (a) bare 실패(invalid identifier) → us. 재시도 성공")
    _orig = gm.httpx.AsyncClient
    try:
        def responder(mid):
            if _has_region_prefix(mid):
                return 200, ['data: {"type":"content_block_delta","delta":{"text":"ok"}}\n']
            return 400, ['model identifier is invalid']

        calls = _install_sse_mock(responder)
        c = _sse_client()
        evts = _run_sse(c, "google.gemma-3")
        _check("데이터 이벤트 방출", any(e.get("type") == "content_block_delta" for e in evts))
        _check("에러 이벤트 없음(폴백 성공)", not any(e.get("type") == "error" for e in evts))
        _check("첫 호출 bare", calls[0] == "google.gemma-3")
        _check("재시도 us.", calls[1] == "us.google.gemma-3")
        _check("호출 2회", len(calls) == 2)
    finally:
        gm.httpx.AsyncClient = _orig


def test_sse_us_to_bare():
    print("[sse] (b) us. 실패(not in allowed) → bare 재시도 성공")
    _orig = gm.httpx.AsyncClient
    try:
        def responder(mid):
            if _has_region_prefix(mid):
                return 400, ['model is not in allowed list']
            return 200, ['data: {"type":"content_block_delta","delta":{"text":"ok"}}\n']

        calls = _install_sse_mock(responder)
        c = _sse_client()
        evts = _run_sse(c, "us.zai.glm")
        _check("데이터 이벤트 방출", any(e.get("type") == "content_block_delta" for e in evts))
        _check("에러 이벤트 없음", not any(e.get("type") == "error" for e in evts))
        _check("첫 호출 us.", calls[0] == "us.zai.glm")
        _check("재시도 bare", calls[1] == "zai.glm")
        _check("호출 2회", len(calls) == 2)
    finally:
        gm.httpx.AsyncClient = _orig


def test_sse_fallback_once_only():
    print("[sse] (c) 항상 실패 → prefix 폴백 정확히 1회만")
    _orig = gm.httpx.AsyncClient
    try:
        def responder(mid):
            return 400, ['model identifier is invalid']

        calls = _install_sse_mock(responder)
        c = _sse_client()
        evts = _run_sse(c, "minimax.abab")
        _check("최종 에러 이벤트 방출", any(e.get("type") == "error" for e in evts))
        distinct = list(dict.fromkeys(calls))
        _check("distinct modelId 2종(폴백 1회)", distinct == ["minimax.abab", "us.minimax.abab"])
        _check("prefix flip 정확히 1회", calls.count("minimax.abab") == 1 and calls[1] == "us.minimax.abab")
        _check("총 호출 유한(무한루프 아님)", len(calls) <= 4)
    finally:
        gm.httpx.AsyncClient = _orig


def test_prefix_helpers():
    print("[helpers] _is_prefix_form_error / strip / has")
    _check("not in allowed → True", _is_prefix_form_error("Model X is not in allowed list"))
    _check("invalid identifier → True", _is_prefix_form_error("ValidationException: model identifier is invalid"))
    _check("invalid model identifier → True", _is_prefix_form_error("invalid model identifier"))
    _check("unknown model → True", _is_prefix_form_error("unknown model foo"))
    _check("model not found → True", _is_prefix_form_error("model not found"))
    _check("resourcenotfound+model → True", _is_prefix_form_error("ResourceNotFoundException: model xyz"))
    _check("무관 에러 → False", not _is_prefix_form_error("throttling exception"))
    _check("빈 문자열 → False", not _is_prefix_form_error(""))
    _check("strip us.", _strip_region_prefix("us.foo.bar") == "foo.bar")
    _check("strip global.", _strip_region_prefix("global.foo.bar") == "foo.bar")
    _check("strip bare 유지", _strip_region_prefix("foo.bar") == "foo.bar")
    _check("has_region us. True", _has_region_prefix("us.foo"))
    _check("has_region bare False", not _has_region_prefix("foo"))


def main():
    test_prefix_helpers()
    test_converse_bare_to_us()
    test_converse_us_to_bare()
    test_converse_fallback_once_only()
    test_stream_bare_to_us()
    test_stream_us_to_bare()
    test_stream_fallback_once_only()
    test_sse_bare_to_us()
    test_sse_us_to_bare()
    test_sse_fallback_once_only()
    print(f"\n=== 결과: PASS={_PASS} FAIL={_FAIL} ===")
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
