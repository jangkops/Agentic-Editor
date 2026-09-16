"""stream_sse_realtime — 자격증명 만료 시 갱신 후 1회 재시도 (원장 #20).

converse 경로에는 예전부터 만료 재시도가 있었지만 주 채팅 경로인 SSE 스트림에는 없어, 토큰이 만료된
순간의 요청은 그대로 실패했다. HTTP 403 본문("security token … expired")과 in-band error 이벤트 두 형태를
모두 갱신 후 재시도하고, 재시도는 1회만, 데이터를 이미 방출한 뒤에는 재시도하지 않아야 한다.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine import gateway_module as gm  # noqa: E402
from ai_engine.gateway_module import GatewayClient  # noqa: E402

EXPIRED_BODY = "The security token included in the request is expired"
OK_LINES = ['data: {"type": "contentBlockDelta", "text": "hi"}', 'data: {"type": "messageStop"}']


def _client():
    c = GatewayClient(gateway_url="https://example.invalid/v1")
    c._sign = lambda method, url, body_bytes: {"Content-Type": "application/json"}
    c._get_creds = lambda: gm.Credentials("AKIDEXAMPLE", "SECRET", "TOKEN")
    return c


def _install(responses):
    """gm.httpx.AsyncClient 를 스크립트된 응답 목록으로 대체. 각 항목: (status, [lines])."""
    calls = []

    class _Resp:
        def __init__(self, status, lines):
            self.status_code = status; self._lines = lines
        async def aiter_text(self):
            for ln in self._lines:
                yield ln + "\n"
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, method, url, content=None, headers=None):
            calls.append(json.loads(content.decode()).get("modelId"))
            status, lines = responses[min(len(calls) - 1, len(responses) - 1)]
            return _Resp(status, lines)

    gm.httpx.AsyncClient = _Client
    return calls


def _run(client):
    async def _consume():
        out = []
        async for e in client.stream_sse_realtime("anthropic.claude-sonnet-4-20250514-v1:0", [{"role": "user", "content": [{"text": "hi"}]}]):
            out.append(e)
        return out
    return asyncio.run(_consume())


def test_http_403_expired_refreshes_and_retries_once(monkeypatch):
    orig = gm.httpx.AsyncClient
    try:
        calls = _install([(403, [EXPIRED_BODY]), (200, OK_LINES)])
        c = _client()
        refreshed = []
        monkeypatch.setattr(c, "force_refresh_creds", lambda: refreshed.append(1))
        events = _run(c)
        assert len(calls) == 2                      # 만료 → 갱신 → 재시도 1회
        assert refreshed == [1]
        assert [e.get("type") for e in events] == ["contentBlockDelta", "messageStop"]
        assert not any(e.get("type") == "error" for e in events)
    finally:
        gm.httpx.AsyncClient = orig


def test_in_band_expired_event_refreshes_and_retries(monkeypatch):
    orig = gm.httpx.AsyncClient
    try:
        inband = ['data: {"type": "error", "message": "' + EXPIRED_BODY + '"}']
        calls = _install([(200, inband), (200, OK_LINES)])
        c = _client()
        refreshed = []
        monkeypatch.setattr(c, "force_refresh_creds", lambda: refreshed.append(1))
        events = _run(c)
        assert len(calls) == 2 and refreshed == [1]
        assert [e.get("type") for e in events] == ["contentBlockDelta", "messageStop"]
    finally:
        gm.httpx.AsyncClient = orig


def test_expiry_retry_happens_only_once(monkeypatch):
    orig = gm.httpx.AsyncClient
    try:
        calls = _install([(403, [EXPIRED_BODY]), (403, [EXPIRED_BODY])])
        c = _client()
        refreshed = []
        monkeypatch.setattr(c, "force_refresh_creds", lambda: refreshed.append(1))
        events = _run(c)
        assert len(calls) == 2 and refreshed == [1]  # 두 번째 만료는 재시도하지 않고 오류로 끝난다
        assert len(events) == 1 and events[0]["type"] == "error" and "403" in events[0]["message"]
    finally:
        gm.httpx.AsyncClient = orig


def test_no_retry_after_output_was_emitted(monkeypatch):
    orig = gm.httpx.AsyncClient
    try:
        mixed = ['data: {"type": "contentBlockDelta", "text": "partial"}',
                 'data: {"type": "error", "message": "' + EXPIRED_BODY + '"}']
        calls = _install([(200, mixed), (200, OK_LINES)])
        c = _client()
        refreshed = []
        monkeypatch.setattr(c, "force_refresh_creds", lambda: refreshed.append(1))
        events = _run(c)
        assert len(calls) == 1 and refreshed == []   # 이미 출력이 나간 뒤라 재시도하지 않는다
        assert [e.get("type") for e in events] == ["contentBlockDelta", "error"]
    finally:
        gm.httpx.AsyncClient = orig


def test_refresh_failure_reports_error_instead_of_raising(monkeypatch):
    orig = gm.httpx.AsyncClient
    try:
        _install([(403, [EXPIRED_BODY]), (200, OK_LINES)])
        c = _client()
        state = {"refreshed": False}
        monkeypatch.setattr(c, "force_refresh_creds", lambda: state.update(refreshed=True))

        def _creds_after_refresh_fail():
            if state["refreshed"]:
                raise RuntimeError("no profile")          # 갱신 뒤에는 프로파일도 주입도 없는 상황
            return gm.Credentials("AKIDEXAMPLE", "SECRET", "TOKEN")
        monkeypatch.setattr(c, "_get_creds", _creds_after_refresh_fail)
        events = _run(c)
        assert len(events) == 1 and events[0]["type"] == "error" and "갱신 실패" in events[0]["message"]
    finally:
        gm.httpx.AsyncClient = orig


def test_non_expiry_error_is_not_retried(monkeypatch):
    orig = gm.httpx.AsyncClient
    try:
        calls = _install([(500, ["internal failure"]), (200, OK_LINES)])
        c = _client()
        refreshed = []
        monkeypatch.setattr(c, "force_refresh_creds", lambda: refreshed.append(1))
        events = _run(c)
        assert len(calls) == 1 and refreshed == []
        assert events and events[-1]["type"] == "error"
    finally:
        gm.httpx.AsyncClient = orig
