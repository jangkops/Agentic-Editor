"""Unit tests — GatewayClient OpenAI 메서드 (httpx/urllib 모킹).

Feature: gateway-openai-models
대상: ai_engine.gateway_module.GatewayClient.openai_responses_sync /
      openai_responses_job_submit / _openai_poll_job

검증(Req 5.4, 5.5, 5.7, 7.1, 7.2, 7.3, 8.3):
  - 403 → QuotaExceededError
  - 422 → OpenAISurfaceError
  - 500 → 지수 백오프 후 OpenAISurfaceError
  - 동기 타임아웃 → SyncTimeout
  - job 폴링 완료/실패/타임아웃
  - 기존 메서드 시그니처 불변(introspection)

실행: pytest scripts/test_gateway_openai_methods.py -q
"""
from __future__ import annotations

import asyncio
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine import gateway_module as gm  # noqa: E402
from ai_engine.gateway_module import (  # noqa: E402
    GatewayClient,
    JobFailed,
    JobTimeout,
    OpenAIModelUnsupported,
    OpenAISurfaceError,
    QuotaExceededError,
    SyncTimeout,
)


def _client(monkeypatch):
    gw = GatewayClient(gateway_url="https://test.local")
    # 토큰 만료 오판 방지 + 자격증명 갱신 무력화
    monkeypatch.setattr(gw, "_is_expired_error", lambda *_a, **_k: False, raising=False)
    monkeypatch.setattr(gw, "force_refresh_creds", lambda *_a, **_k: None, raising=False)
    return gw


def _no_sleep(monkeypatch):
    async def _instant(_s):
        return None
    monkeypatch.setattr(gm.asyncio, "sleep", _instant)


def _stub_blocking(monkeypatch, gw, responses):
    """_openai_request_blocking을 순차 응답 리스트로 스텁."""
    seq = list(responses)

    def _fake(method, url, body_bytes, timeout):
        return seq.pop(0) if seq else seq_last

    seq_last = responses[-1] if responses else {"status": 200, "json": {}, "body": ""}
    monkeypatch.setattr(gw, "_openai_request_blocking", _fake, raising=False)


def test_sync_success(monkeypatch):
    gw = _client(monkeypatch)
    _stub_blocking(monkeypatch, gw, [{"status": 200, "json": {"output_text": "hi"}, "body": ""}])
    out = asyncio.run(gw.openai_responses_sync("openai.gpt-5.5", [{"role": "user", "content": "x"}]))
    assert out == {"output_text": "hi"}


def test_sync_403_quota(monkeypatch):
    gw = _client(monkeypatch)
    _no_sleep(monkeypatch)
    _stub_blocking(monkeypatch, gw, [{"status": 403, "json": None, "body": "denied"}])
    with pytest.raises(QuotaExceededError):
        asyncio.run(gw.openai_responses_sync("openai.gpt-5.5", [{"role": "user", "content": "x"}]))


def test_sync_422_surface(monkeypatch):
    gw = _client(monkeypatch)
    _no_sleep(monkeypatch)
    _stub_blocking(monkeypatch, gw, [{"status": 422, "json": None, "body": "bad request"}])
    with pytest.raises(OpenAISurfaceError):
        asyncio.run(gw.openai_responses_sync("openai.gpt-5.5", [{"role": "user", "content": "x"}]))


def test_sync_500_backoff_then_fail(monkeypatch):
    gw = _client(monkeypatch)
    _no_sleep(monkeypatch)
    # 3회 연속 500 → 백오프 소진 후 OpenAISurfaceError
    _stub_blocking(monkeypatch, gw, [{"status": 500, "json": None, "body": "boom"}] * 3)
    with pytest.raises(OpenAISurfaceError):
        asyncio.run(gw.openai_responses_sync("openai.gpt-5.5", [{"role": "user", "content": "x"}]))


def test_sync_500_then_success(monkeypatch):
    gw = _client(monkeypatch)
    _no_sleep(monkeypatch)
    _stub_blocking(
        monkeypatch,
        gw,
        [
            {"status": 500, "json": None, "body": "boom"},
            {"status": 200, "json": {"output_text": "ok"}, "body": ""},
        ],
    )
    out = asyncio.run(gw.openai_responses_sync("openai.gpt-5.5", [{"role": "user", "content": "x"}]))
    assert out["output_text"] == "ok"


def test_sync_timeout(monkeypatch):
    gw = _client(monkeypatch)
    _stub_blocking(monkeypatch, gw, [{"status": -1, "error": "timeout: t", "timeout": True}])
    with pytest.raises(SyncTimeout):
        asyncio.run(gw.openai_responses_sync("openai.gpt-5.5", [{"role": "user", "content": "x"}]))


def test_unsupported_model(monkeypatch):
    gw = _client(monkeypatch)
    _no_sleep(monkeypatch)
    _stub_blocking(
        monkeypatch, gw, [{"status": 422, "json": None, "body": "unsupported model foo"}]
    )
    with pytest.raises(OpenAIModelUnsupported):
        asyncio.run(gw.openai_responses_sync("openai.gpt-9.9", [{"role": "user", "content": "x"}]))


def test_job_submit_extracts_id(monkeypatch):
    gw = _client(monkeypatch)
    _stub_blocking(monkeypatch, gw, [{"status": 200, "json": {"job_id": "job-abc"}, "body": ""}])
    jid = asyncio.run(gw.openai_responses_job_submit("openai.gpt-5.5", [{"role": "user", "content": "x"}]))
    assert jid == "job-abc"


def test_job_submit_missing_id_raises(monkeypatch):
    gw = _client(monkeypatch)
    _stub_blocking(monkeypatch, gw, [{"status": 200, "json": {"nope": 1}, "body": ""}])
    with pytest.raises(OpenAISurfaceError):
        asyncio.run(gw.openai_responses_job_submit("openai.gpt-5.5", [{"role": "user", "content": "x"}]))


def test_poll_completed(monkeypatch):
    gw = _client(monkeypatch)
    _no_sleep(monkeypatch)
    _stub_blocking(
        monkeypatch,
        gw,
        [
            {"status": 200, "json": {"status": "in_progress"}, "body": ""},
            {"status": 200, "json": {"status": "completed", "output_text": "done"}, "body": ""},
        ],
    )
    out = asyncio.run(gw._openai_poll_job("job-1", poll_interval=1, max_wait=60))
    assert out["output_text"] == "done"


def test_poll_failed(monkeypatch):
    gw = _client(monkeypatch)
    _no_sleep(monkeypatch)
    _stub_blocking(monkeypatch, gw, [{"status": 200, "json": {"status": "failed"}, "body": ""}])
    with pytest.raises(JobFailed):
        asyncio.run(gw._openai_poll_job("job-1", poll_interval=1, max_wait=60))


def test_poll_timeout(monkeypatch):
    gw = _client(monkeypatch)
    _no_sleep(monkeypatch)
    # 항상 in_progress → max_wait 초과 시 JobTimeout
    _stub_blocking(monkeypatch, gw, [{"status": 200, "json": {"status": "queued"}, "body": ""}] * 5)
    with pytest.raises(JobTimeout):
        asyncio.run(gw._openai_poll_job("job-1", poll_interval=5, max_wait=5))


def test_sync_payload_has_no_modelId(monkeypatch):
    # 동기 라우트(/openai/responses)는 본문을 백엔드로 그대로 전달하므로
    # 'modelId' 같은 게이트웨이 전용 필드가 있으면 502로 거부된다.
    # _build_openai_payload는 OpenAI 표준 필드만 가져야 한다(회귀 가드).
    gw = GatewayClient(gateway_url="https://test.local")
    body = gw._build_openai_payload("openai.gpt-5.5", [{"role": "user", "content": "x"}])
    assert "modelId" not in body, "동기 페이로드에 modelId가 들어가면 502 발생"
    assert body["model"] == "openai.gpt-5.5"
    assert "input" in body


def test_existing_methods_signature_unchanged():
    # 순수 add 원칙 — 기존 메서드 시그니처 불변(회귀 가드)
    conv = inspect.signature(GatewayClient.converse)
    assert list(conv.parameters.keys()) == ["self", "model_id", "messages", "system_prompt", "tool_config"]
    inv = inspect.signature(GatewayClient.invoke_model) if hasattr(GatewayClient, "invoke_model") else None
    # invoke 계열 존재 시 self/model_id/body 형태 유지(이름은 구현에 따름) — 존재만 확인
    assert hasattr(GatewayClient, "converse")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
