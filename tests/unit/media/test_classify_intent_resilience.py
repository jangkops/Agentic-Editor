"""Regression — classify_intent는 게이트웨이 실패에도 안정적으로 fallback한다.

증상: "main.js [Intent] 분류 실패/타임아웃: The user aborted a request" —
프론트 3초 타임아웃이 백엔드 게이트웨이 지연(특히 SSO 만료 시)보다 짧아 abort.

수정:
  - 프론트(main.js): 타임아웃 3초 → 12초(백엔드 10초 정합), abort/네트워크 구분 메시지,
    needsReauth 시 재로그인 안내. (JS — 본 테스트는 백엔드 신호 계약만 검증)
  - 백엔드(classify_intent): 어떤 예외에도 200 + 유효 JSON 반환. degraded=True,
    SSO/토큰 만료 감지 시 needsReauth=True.

Correctness property:
  P1. 게이트웨이 예외 발생 시에도 classify_intent는 유효한 fallback JSON을 반환한다
      (intent='simple_qa', 모든 필수 키 존재). 예외를 호출자로 던지지 않는다.
  P2. SSO/security-token 만료 메시지면 needsReauth=True.
  P3. 그 외 일반 실패(타임아웃 등)면 needsReauth=False, degraded=True.
  P4. 정상 분류 응답에는 degraded/needsReauth가 없거나 falsy.
"""
import os
import sys
import json
import asyncio
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai_engine"))

import server  # noqa: E402


class _FakeReq:
    def __init__(self, payload):
        self._p = payload

    async def json(self):
        return self._p


class _RaisingGW:
    def __init__(self, exc):
        self._exc = exc

    async def converse(self, **kwargs):
        raise self._exc


def _body(resp):
    return json.loads(resp.body.decode("utf-8"))


_REQUIRED_KEYS = ("intent", "needs_tools", "complexity", "parallel_useful", "file_types", "reasoning")


@pytest.fixture
def fake_gw(monkeypatch):
    def _install(exc):
        monkeypatch.setattr(server, "_get_gw", lambda *a, **k: _RaisingGW(exc))
    return _install


def _classify(prompt="PPTX 발표자료 만들어줘"):
    return _body(asyncio.run(server.classify_intent(_FakeReq({
        "prompt": prompt, "awsProfile": "bedrock-gw", "bedrockUser": "",
    }))))


@pytest.mark.unit
def test_gateway_exception_returns_valid_fallback(fake_gw):
    """P1 — 게이트웨이 예외에도 유효 fallback JSON, 예외 미전파."""
    fake_gw(RuntimeError("boom: gateway 500"))
    body = _classify()
    for k in _REQUIRED_KEYS:
        assert k in body, f"필수 키 누락: {k}"
    assert body["intent"] == "simple_qa"
    assert body.get("degraded") is True


@pytest.mark.unit
@pytest.mark.parametrize("msg", [
    "classifier failed: The SSO session associated with this profile has expired",
    "The security token included in the request is expired",
    "ExpiredTokenException: token expired",
])
def test_sso_expiry_sets_needs_reauth(fake_gw, msg):
    """P2 — SSO/토큰 만료 메시지 → needsReauth=True."""
    fake_gw(RuntimeError(msg))
    body = _classify()
    assert body.get("needsReauth") is True, f"needsReauth 미설정: {msg}"
    assert body.get("degraded") is True


@pytest.mark.unit
@pytest.mark.parametrize("exc", [
    asyncio.TimeoutError("gateway timeout"),
    RuntimeError("connection reset"),
    ValueError("no JSON in classifier response"),
])
def test_generic_failure_no_reauth(fake_gw, exc):
    """P3 — 일반 실패는 degraded만, needsReauth=False."""
    fake_gw(exc)
    body = _classify()
    assert body.get("degraded") is True
    assert bool(body.get("needsReauth")) is False


@pytest.mark.unit
def test_empty_prompt_not_degraded():
    """P4 — 빈 프롬프트의 trivial fallback은 degraded 아님(게이트웨이 미호출)."""
    body = _classify(prompt="")
    assert body["intent"] == "simple_qa"
    assert not body.get("degraded")
    assert not body.get("needsReauth")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
