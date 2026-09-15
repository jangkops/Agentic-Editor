"""매우 긴 컨텍스트 입출력(1시간 이상) 지원 회귀 테스트.

확정 진단(수정 대상):
  1. `_resolve_model_max_tokens` 가 맵 매칭 실패 시 4096 을 반환해
     `anthropic.claude-opus-5` / `claude-sonnet-5` / gpt 계열이 4096 으로 눌렸다.
     → 미지 모델은 `_UNKNOWN_MODEL_MAX_TOKENS`(= AE_MAX_TOKENS 기본값 64000).
  2. 낙관적 폴백의 안전망 — 비스트리밍 `converse` 에도 max_tokens step-down
     재시도를 추가(SSE 의 `_is_max_tokens_error` / `_extract_model_token_limit` 재사용).
     step-down 은 만료·prefix 재시도 예산(3회)과 별도 카운터로 최대 2회.
  3. 잡 폴링 상한을 `AE_JOB_MAX_WAIT`(기본 7200초)로 올리고 간격을 적응형으로.
  4. 연결 시도 예산(`AE_CONVERSE_TOTAL_BUDGET`)이 잡 대기를 600초에서 자르지 않는다.
  5. 스트리밍 total 타임아웃 상향(`_converse_stream_live_once` / `stream_sse_realtime`).

제약: 네트워크·AWS 호출 없음(urlopen / boto3 S3 / httpx mock). 가짜 시계·no-op
`asyncio.sleep` 으로 실제 대기 없음.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_gateway_long_context.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

import pytest
from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine import gateway_module as gm  # noqa: E402
from ai_engine.gateway_module import GatewayClient  # noqa: E402


# ─────────────────────────────────────────────────────────────────
# 공용 테스트 도구
# ─────────────────────────────────────────────────────────────────
class _FakeClock:
    """가짜 단조 시계 — `gm.time` 자리에 주입한다(실제 대기 없음)."""

    def __init__(self, start: float = 1000.0):
        self.now = float(start)

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b


def _make_client() -> GatewayClient:
    c = GatewayClient(gateway_url="https://example.invalid/v1")
    c._sign = lambda method, url, body_bytes: {"Content-Type": "application/json"}
    c.force_refresh_creds = lambda: None
    return c


def _install_urlopen(monkeypatch, payload_for_call, clock=None, elapsed_per_call=0.0):
    """urlopen mock — 각 호출의 요청 본문(dict)을 순서대로 기록해 반환한다."""
    bodies: list = []

    def _fake_urlopen(req, timeout=None):
        idx = len(bodies)
        try:
            bodies.append(json.loads(req.data.decode()))
        except Exception:
            bodies.append({})
        if clock is not None and elapsed_per_call:
            clock.advance(elapsed_per_call)
        return _FakeResp(payload_for_call(idx))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return bodies


def _patch_sleep(monkeypatch, clock=None):
    calls: list = []

    async def _fake_sleep(delay, *a, **k):
        calls.append(delay)
        if clock is not None:
            clock.advance(delay)

    monkeypatch.setattr(gm.asyncio, "sleep", _fake_sleep)
    return calls


_MSG = [{"role": "user", "content": [{"text": "hi"}]}]
_TEXT_JOB = {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}}
_EXPIRED = {"decision": "ERROR", "error": "ExpiredTokenException: security token expired"}


def _max_tokens_error(limit: int | None = 32768) -> dict:
    """Bedrock max_tokens 검증 실패 응답(실측 문구 형태)."""
    if limit is None:
        msg = ("ValidationException: The maximum tokens you requested exceeds "
               "the model limit for this model.")
    else:
        msg = ("ValidationException: The maximum tokens you requested exceeds "
               f"the model limit of {limit}.")
    return {"decision": "ERROR", "error": msg}


# ═════════════════════════════════════════════════════════════════
# 1. 맵에 있는 모델의 반환값은 변경 전과 동일 (무회귀 스냅샷)
# ═════════════════════════════════════════════════════════════════
#: 변경 전 값 그대로 — 한 개라도 바뀌면 실패한다.
_FROZEN_MAP_HITS = {
    "us.anthropic.claude-opus-4-20250514-v1:0": 64000,
    "anthropic.claude-sonnet-4-20250514-v1:0": 64000,
    "anthropic.claude-haiku-4-5-20251001-v1:0": 32000,
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0": 64000,
    "anthropic.claude-3-5-sonnet-20241022-v2:0": 8192,
    "anthropic.claude-3-opus-20240229-v1:0": 4096,
    "anthropic.claude-3-haiku-20240307-v1:0": 4096,
    "amazon.nova-pro-v1:0": 5120,
    "amazon.nova-lite-v1:0": 5120,
    "amazon.nova-micro-v1:0": 5120,
    "us.deepseek.r1-v1:0": 32767,
    "deepseek.v3-v1:0": 32767,
    "mistral.mistral-large-2407-v1:0": 8192,
    "mistral.pixtral-large-2502-v1:0": 8192,
    "meta.llama3-3-70b-instruct-v1:0": 8192,
    "cohere.command-r-plus-v1:0": 4000,
    "nvidia.nemotron-4-v1:0": 16384,
    "writer.palmyra-x5-v1:0": 8192,
    "ai21.jamba-1-5-large-v1:0": 4096,
    "stability.sd3-5-large-v1:0": 1024,
    "amazon.titan-image-generator-v2:0": 1024,
    "amazon.nova-canvas-v1:0": 1024,
}


@pytest.mark.parametrize("model_id,expected", sorted(_FROZEN_MAP_HITS.items()))
def test_map_hit_models_unchanged(model_id, expected):
    """맵에 매칭되는 모델의 max_tokens 는 변경 전과 한 개도 다르지 않다."""
    assert gm._resolve_model_max_tokens(model_id) == expected


def test_map_contains_no_speculative_gen5_keys():
    """실제 한계를 모르는 5세대 키를 맵에 추가하지 않았다(근거 없는 추측값 금지)."""
    for key in gm._MODEL_MAX_TOKENS_MAP:
        assert "opus-5" not in key and "sonnet-5" not in key and "gpt" not in key


# ═════════════════════════════════════════════════════════════════
# 2. 맵에 없는 모델 → _UNKNOWN_MODEL_MAX_TOKENS (4096 아님)
# ═════════════════════════════════════════════════════════════════
_UNKNOWN_IDS = [
    "anthropic.claude-opus-5-20260101-v1:0",
    "us.anthropic.claude-sonnet-5-20260101-v1:0",
    "global.anthropic.claude-opus-5",
    "openai.gpt-5-5-2026",
    "openai.gpt-4o-2024-11-20",
    "some.brand-new-model-v9:0",
]


@pytest.mark.parametrize("model_id", _UNKNOWN_IDS)
def test_unknown_models_get_optimistic_ceiling(model_id):
    """미지 모델은 낙관적 상한을 받고 4096 으로 눌리지 않는다."""
    got = gm._resolve_model_max_tokens(model_id)
    assert got == gm._UNKNOWN_MODEL_MAX_TOKENS
    assert got != gm._DEFAULT_MAX_TOKENS
    assert got == 64000


def test_unknown_ceiling_matches_env_default():
    """`_UNKNOWN_MODEL_MAX_TOKENS` 는 AE_MAX_TOKENS 기본값과 같은 낙관적 상한이다."""
    assert gm._UNKNOWN_MODEL_MAX_TOKENS == 64000
    # _DEFAULT_MAX_TOKENS 상수는 지우지 않았다(다른 참조·빈 model_id 폴백용).
    assert gm._DEFAULT_MAX_TOKENS == 4096
    assert gm._resolve_model_max_tokens("") == gm._DEFAULT_MAX_TOKENS


#: 맵 키와 절대 겹치지 않는 무작위 심볼 생성기 — 미지 모델 입력 공간을 좁혀서 만든다.
_SAFE_ALPHABET = "wxyzWXYZ0123456789-._:"


@settings(max_examples=200, deadline=None)
@given(st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=40))
def test_property_unmatched_symbols_never_return_4096(symbol):
    """맵 키를 포함하지 않는 임의 모델 ID 는 항상 낙관적 상한을 받는다.

    **Validates: 수정 1 — 미지 모델 max_tokens 낙관적 폴백**
    """
    low = symbol.lower()
    # 생성기가 우연히 맵 키를 만들 수는 없지만(알파벳 제한), 방어적으로 걸러낸다.
    if any(key in low for key in gm._MODEL_MAX_TOKENS_MAP):
        return
    assert gm._resolve_model_max_tokens(symbol) == gm._UNKNOWN_MODEL_MAX_TOKENS


# ═════════════════════════════════════════════════════════════════
# 3. _build_payload 가 미지 모델에 min(env_cap, 낙관적 상한) 을 싣는다
# ═════════════════════════════════════════════════════════════════
def test_build_payload_unknown_model_uses_optimistic_ceiling(monkeypatch):
    monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    c = _make_client()
    body = c._build_payload("anthropic.claude-opus-5-20260101-v1:0", _MSG)
    assert body["inferenceConfig"]["maxTokens"] == min(64000, gm._UNKNOWN_MODEL_MAX_TOKENS)
    assert body["inferenceConfig"]["maxTokens"] != 4096


def test_build_payload_env_cap_still_caps_unknown_model(monkeypatch):
    """env_cap 이 더 작으면 그 값이 이긴다(min 계약 유지)."""
    monkeypatch.setenv("AE_MAX_TOKENS", "12000")
    c = _make_client()
    body = c._build_payload("openai.gpt-5-5-2026", _MSG)
    assert body["inferenceConfig"]["maxTokens"] == 12000


def test_build_payload_map_hit_unchanged(monkeypatch):
    """맵 매칭 모델의 payload maxTokens 는 변경 전과 동일하다."""
    monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    c = _make_client()
    body = c._build_payload("anthropic.claude-3-5-sonnet-20241022-v2:0", _MSG)
    assert body["inferenceConfig"]["maxTokens"] == 8192


# ═════════════════════════════════════════════════════════════════
# 4. converse 의 max_tokens step-down 재시도
# ═════════════════════════════════════════════════════════════════
def _run_converse_with(monkeypatch, payload_for_call, clock=None, elapsed=0.0,
                       model_id="anthropic.claude-opus-5-20260101-v1:0"):
    clock = clock or _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    _patch_sleep(monkeypatch, clock=None)
    bodies = _install_urlopen(monkeypatch, payload_for_call, clock=clock,
                              elapsed_per_call=elapsed)
    c = _make_client()
    result = asyncio.run(c.converse(model_id, _MSG))
    return result, bodies


def test_converse_steps_down_to_parsed_limit_minus_one(monkeypatch):
    """max_tokens 초과 오류에 하향 재시도하고, 에러가 알려준 한계−1 을 쓴다."""
    monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    allow = {"decision": "ALLOW", "output": {"message": {"content": [{"text": "ok"}]}}}

    def _payload(idx):
        return _max_tokens_error(32768) if idx == 0 else dict(allow)

    result, bodies = _run_converse_with(monkeypatch, _payload)

    assert len(bodies) == 2, f"step-down 재시도가 일어나지 않음: {len(bodies)}회"
    # 시작값은 _build_payload 가 계산한 값(미지 모델 → 낙관적 상한)
    assert bodies[0]["inferenceConfig"]["maxTokens"] == 64000
    assert bodies[1]["inferenceConfig"]["maxTokens"] == 32767, "limit-1 을 쓰지 않음"
    assert result.get("decision") == "ALLOW"


def test_converse_step_down_halves_when_limit_unparsable(monkeypatch):
    """한계를 파싱할 수 없으면 현재값의 50%로 줄인다(하한 1024)."""
    monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    allow = {"decision": "ALLOW", "output": {"message": {"content": [{"text": "ok"}]}}}

    def _payload(idx):
        return _max_tokens_error(None) if idx == 0 else dict(allow)

    result, bodies = _run_converse_with(monkeypatch, _payload)

    assert len(bodies) == 2
    assert bodies[1]["inferenceConfig"]["maxTokens"] == 32000, "50% 하향이 아님"
    assert result.get("decision") == "ALLOW"


def test_converse_step_down_preserves_rest_of_body(monkeypatch):
    """재시도 시 maxTokens 만 바뀌고 나머지 body 는 그대로다."""
    monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    allow = {"decision": "ALLOW", "output": {"message": {"content": [{"text": "ok"}]}}}
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    _patch_sleep(monkeypatch)
    bodies = _install_urlopen(
        monkeypatch, lambda i: _max_tokens_error(8192) if i == 0 else dict(allow))
    tool_config = {"tools": [{"toolSpec": {"name": "t"}}]}

    c = _make_client()
    asyncio.run(c.converse("anthropic.claude-opus-5", _MSG, "sys", tool_config))

    first, second = bodies[0], bodies[1]
    assert first["modelId"] == second["modelId"]
    assert first["messages"] == second["messages"]
    assert first["system"] == second["system"]
    assert first["toolConfig"] == second["toolConfig"] == tool_config
    assert second["inferenceConfig"]["maxTokens"] == 8191


def test_converse_step_down_capped_at_two(monkeypatch):
    """step-down 은 최대 2회 — 계속 초과 오류가 오면 3번째 하향은 없다."""
    monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    # 매번 파싱 불가 오류 → 50% 하향(64000 → 32000 → 16000)
    result, bodies = _run_converse_with(monkeypatch, lambda i: _max_tokens_error(None))

    assert gm._MAX_TOKENS_STEPDOWN_RETRIES == 2
    assert len(bodies) == 3, f"step-down 상한 위반: {len(bodies)}회 호출"
    sizes = [b["inferenceConfig"]["maxTokens"] for b in bodies]
    assert sizes == [64000, 32000, 16000]
    assert result.get("decision") == "ERROR"


def test_step_down_does_not_consume_expiry_retry_budget(monkeypatch):
    """step-down 이 만료 재시도 예산(3회)을 잠식하지 않는다.

    step-down 2회 후 만료 오류가 이어져도 만료 재시도가 그대로 3회분 남아 있어야 한다
    → 총 호출 5회(step-down 2 + 만료 시도 3).
    """
    monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)

    def _payload(idx):
        return _max_tokens_error(None) if idx < 2 else dict(_EXPIRED)

    result, bodies = _run_converse_with(monkeypatch, _payload)

    assert len(bodies) == 5, f"만료 재시도 예산이 잠식됨: {len(bodies)}회"
    sizes = [b["inferenceConfig"]["maxTokens"] for b in bodies]
    # 하향된 값은 만료 후 payload 재구성에서도 유지된다.
    assert sizes == [64000, 32000, 16000, 16000, 16000]
    assert result.get("decision") == "ERROR"


def test_step_down_does_not_consume_prefix_fallback_budget(monkeypatch):
    """step-down 후에도 prefix 폴백 1회가 그대로 남아 있다."""
    monkeypatch.delenv("AE_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    prefix_deny = {"decision": "DENY",
                   "denial_reason": "model xyz is not in allowed models"}
    allow = {"decision": "ALLOW", "output": {"message": {"content": [{"text": "ok"}]}}}

    def _payload(idx):
        if idx == 0:
            return _max_tokens_error(4096)
        if idx == 1:
            return dict(prefix_deny)
        return dict(allow)

    result, bodies = _run_converse_with(monkeypatch, _payload,
                                        model_id="anthropic.claude-opus-5")

    assert len(bodies) == 3
    assert bodies[1]["inferenceConfig"]["maxTokens"] == 4095      # step-down 유지
    assert bodies[1]["modelId"] == "anthropic.claude-opus-5"
    assert bodies[2]["modelId"] == "us.anthropic.claude-opus-5"   # prefix 폴백 사용
    assert bodies[2]["inferenceConfig"]["maxTokens"] == 4095      # maxTokens 는 그대로
    assert result.get("decision") == "ALLOW"


def test_converse_no_step_down_on_ordinary_error(monkeypatch):
    """max_tokens 무관 오류에는 하향 재시도를 하지 않는다(무회귀)."""
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    result, bodies = _run_converse_with(
        monkeypatch, lambda i: {"decision": "ERROR", "error": "HTTP 500: boom"})
    assert len(bodies) == 1
    assert result.get("decision") == "ERROR"


# ═════════════════════════════════════════════════════════════════
# 5. converse → _poll_job_data 에 _job_max_wait() 를 넘긴다
# ═════════════════════════════════════════════════════════════════
def test_converse_passes_job_max_wait_to_poll(monkeypatch):
    """ACCEPTED 경로가 600초 예산이 아니라 _job_max_wait()(기본 7200) 를 넘긴다."""
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    monkeypatch.delenv("AE_JOB_MAX_WAIT", raising=False)
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    _patch_sleep(monkeypatch)
    _install_urlopen(monkeypatch, lambda i: {"decision": "ACCEPTED", "job_id": "job-1"},
                     clock=clock, elapsed_per_call=1.0)

    seen: list = []

    async def _fake_poll(job_id, max_wait=300):
        seen.append(max_wait)
        return dict(_TEXT_JOB)

    c = _make_client()
    c._poll_job_data = _fake_poll
    result = asyncio.run(c.converse("anthropic.claude-opus-5", _MSG))

    assert seen == [7200], f"잡 대기가 잘림: {seen}"
    assert seen[0] > gm._CONVERSE_TOTAL_BUDGET_DEFAULT, "연결 예산(600초)에 갇힘"
    assert result.get("decision") == "ALLOW"


def test_poll_job_data_signature_unchanged():
    """`_poll_job_data` 시그니처·기본값 불변 — 기존 테스트가 mock 으로 못박는다."""
    import inspect
    sig = inspect.signature(GatewayClient._poll_job_data)
    assert list(sig.parameters.keys()) == ["self", "job_id", "max_wait"]
    assert sig.parameters["max_wait"].default == 300
    sig2 = inspect.signature(GatewayClient._openai_poll_job)
    assert list(sig2.parameters.keys()) == ["self", "job_id", "poll_interval", "max_wait"]
    assert sig2.parameters["poll_interval"].default == 5
    assert sig2.parameters["max_wait"].default == 300


# ═════════════════════════════════════════════════════════════════
# 6. _job_max_wait() 계약 — 기본값 / env override / 비정상값 폴백
# ═════════════════════════════════════════════════════════════════
def test_job_max_wait_default(monkeypatch):
    monkeypatch.delenv("AE_JOB_MAX_WAIT", raising=False)
    assert gm._JOB_MAX_WAIT_DEFAULT == 7200
    assert gm._job_max_wait() == 7200


@pytest.mark.parametrize("raw,expected", [
    ("3600", 3600),
    ("10800", 10800),
    ("1", 1),
])
def test_job_max_wait_env_override(monkeypatch, raw, expected):
    monkeypatch.setenv("AE_JOB_MAX_WAIT", raw)
    assert gm._job_max_wait() == expected


@pytest.mark.parametrize("raw", ["abc", "", "0", "-5", "None"])
def test_job_max_wait_bad_value_falls_back(monkeypatch, raw):
    monkeypatch.setenv("AE_JOB_MAX_WAIT", raw)
    assert gm._job_max_wait() == gm._JOB_MAX_WAIT_DEFAULT


# ═════════════════════════════════════════════════════════════════
# 7. 적응형 폴링 간격
# ═════════════════════════════════════════════════════════════════
class _NoSuchKeyS3:
    """항상 NoSuchKey — 폴링을 max_wait 까지 계속하게 만든다(정상 miss)."""

    def __init__(self):
        self.calls = 0

    def get_object(self, Bucket=None, Key=None):
        from botocore.exceptions import ClientError
        self.calls += 1
        raise ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "not yet"},
             "ResponseMetadata": {"HTTPStatusCode": 404}},
            "GetObject",
        )


class _FakeBoto3:
    def __init__(self, s3):
        self._s3 = s3

    def client(self, name, **kwargs):
        return self._s3

    def Session(self, *a, **k):
        raise RuntimeError("네트워크/프로필 접근 금지")


def _poll_and_count(monkeypatch, max_wait):
    s3 = _NoSuchKeyS3()
    monkeypatch.setattr(gm, "boto3", _FakeBoto3(s3))
    sleeps = _patch_sleep(monkeypatch)
    c = _make_client()
    c._get_creds = lambda: gm.Credentials("AKIDEXAMPLE", "SECRET", "TOKEN")
    out = asyncio.run(c._poll_job_data("job-xyz", max_wait=max_wait))
    return out, s3, sleeps


def test_short_max_wait_keeps_one_second_interval(monkeypatch):
    """max_wait=4 → 1초 간격 4회 폴링(기존 동작 유지)."""
    out, s3, sleeps = _poll_and_count(monkeypatch, 4)
    assert out is None
    assert sleeps == [1, 1, 1, 1], f"1초 간격 4회가 아님: {sleeps}"
    assert s3.calls == 4


def test_long_max_wait_uses_far_fewer_polls(monkeypatch):
    """긴 max_wait 은 간격이 커져 총 폴링 횟수가 max_wait 보다 훨씬 적다."""
    out, s3, sleeps = _poll_and_count(monkeypatch, 7200)
    assert out is None
    # 총 대기 시간은 정확히 max_wait 를 채운다(의미 유지).
    assert sum(sleeps) == 7200
    # 고정 1초 간격이면 7200회다 — 1/5 미만이어야 한다.
    assert len(sleeps) < 7200 / 5, f"폴링 횟수가 줄지 않음: {len(sleeps)}회"
    assert s3.calls == len(sleeps)
    # 단계 경계에 맞춰 간격이 커진다.
    assert sleeps[0] == gm._JOB_POLL_INTERVAL_FAST
    assert sleeps[-1] == gm._JOB_POLL_INTERVAL_SLOWEST


@pytest.mark.parametrize("elapsed,expected", [
    (0, 1), (29.9, 1),
    (30, 2), (299, 2),
    (300, 5), (1199, 5),
    (1200, 10), (7000, 10),
])
def test_job_poll_interval_stages(elapsed, expected):
    """단계 경계: 처음 30초 1초, 그 다음 2초, 5분 이후 5초, 20분 이후 10초."""
    assert gm._job_poll_interval(elapsed) == expected


def test_job_poll_stage_boundaries_are_module_constants():
    assert (gm._JOB_POLL_FAST_UNTIL, gm._JOB_POLL_MEDIUM_UNTIL,
            gm._JOB_POLL_SLOW_UNTIL) == (30, 300, 1200)


def test_poll_total_wait_never_exceeds_max_wait(monkeypatch):
    """적응형 간격이 max_wait("총 대기 초")을 넘기지 않는다."""
    for mw in (1, 3, 7, 31, 45, 305, 1205):
        out, s3, sleeps = _poll_and_count(monkeypatch, mw)
        assert out is None
        assert sum(sleeps) == pytest.approx(mw), f"max_wait={mw}, 합계={sum(sleeps)}"
        assert all(s > 0 for s in sleeps)


# ═════════════════════════════════════════════════════════════════
# 8. 스트리밍 total 타임아웃 상향
# ═════════════════════════════════════════════════════════════════
def _capture_httpx_timeout(monkeypatch, raw_text=""):
    """httpx.AsyncClient 를 가로채 timeout 을 기록하는 mock 설치."""
    captured: dict = {}

    class _Resp:
        text = raw_text

    class _FakeStream:
        async def __aenter__(self):
            raise gm.httpx.ReadTimeout("read timed out")

        async def __aexit__(self, *a):
            return False

    class _FakeClient:
        def __init__(self, *a, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, content=None, headers=None):
            return _Resp()

        def stream(self, method, url, content=None, headers=None):
            return _FakeStream()

    monkeypatch.setattr(gm.httpx, "AsyncClient", _FakeClient)
    return captured


def test_stream_live_once_total_timeout_raised(monkeypatch):
    """`_converse_stream_live_once` 의 total 타임아웃이 300초 벽을 벗어났다."""
    monkeypatch.delenv("AE_SSE_TOTAL_TIMEOUT", raising=False)
    monkeypatch.delenv("AE_STREAM_LIVE_TOTAL_TIMEOUT", raising=False)
    captured = _capture_httpx_timeout(monkeypatch, raw_text="data: {}\n")
    c = _make_client()
    c._get_creds = lambda: gm.Credentials("AKIDEXAMPLE", "SECRET", "TOKEN")
    asyncio.run(c._converse_stream_live_once("anthropic.claude-opus-5", _MSG))

    to = captured.get("timeout")
    assert to is not None
    assert to.read == gm._stream_live_total_timeout()
    assert to.read == gm._SSE_TOTAL_TIMEOUT == 3600.0, "SSE total 과 같은 값이어야 함"
    assert to.read > 300.0, "300초 벽이 그대로 남아 있음"
    assert to.connect == gm._STREAM_LIVE_CONNECT_TIMEOUT == 30.0


def test_stream_live_total_timeout_env_override(monkeypatch):
    monkeypatch.delenv("AE_SSE_TOTAL_TIMEOUT", raising=False)
    monkeypatch.setenv("AE_STREAM_LIVE_TOTAL_TIMEOUT", "5400")
    assert gm._stream_live_total_timeout() == 5400.0
    # SSE env 만 있어도 따라 올라간다(기본값 = SSE total).
    monkeypatch.delenv("AE_STREAM_LIVE_TOTAL_TIMEOUT", raising=False)
    monkeypatch.setenv("AE_SSE_TOTAL_TIMEOUT", "7200")
    assert gm._stream_live_total_timeout() == 7200.0
    # 비정상값은 기본값 폴백
    monkeypatch.setenv("AE_STREAM_LIVE_TOTAL_TIMEOUT", "abc")
    assert gm._stream_live_total_timeout() == 7200.0


def test_sse_total_timeout_env_adjustable(monkeypatch):
    monkeypatch.delenv("AE_SSE_TOTAL_TIMEOUT", raising=False)
    assert gm._sse_total_timeout() == gm._SSE_TOTAL_TIMEOUT == 3600.0
    monkeypatch.setenv("AE_SSE_TOTAL_TIMEOUT", "10800")
    assert gm._sse_total_timeout() == 10800.0
    monkeypatch.setenv("AE_SSE_TOTAL_TIMEOUT", "0")
    assert gm._sse_total_timeout() == 3600.0
    monkeypatch.setenv("AE_SSE_TOTAL_TIMEOUT", "bogus")
    assert gm._sse_total_timeout() == 3600.0


def test_sse_realtime_uses_env_total_timeout(monkeypatch):
    """`stream_sse_realtime` 의 httpx total 이 env 조정 가능한 상수를 쓴다."""
    monkeypatch.setenv("AE_SSE_TOTAL_TIMEOUT", "7200")
    captured = _capture_httpx_timeout(monkeypatch)
    c = _make_client()
    c._get_creds = lambda: gm.Credentials("AKIDEXAMPLE", "SECRET", "TOKEN")

    async def _consume():
        out = []
        async for e in c.stream_sse_realtime("anthropic.claude-opus-5", _MSG):
            out.append(e)
        return out

    asyncio.run(_consume())
    to = captured.get("timeout")
    assert to is not None
    # httpx.Timeout(total, connect=..., read=...) → total 은 write/pool 에 반영된다.
    assert to.write == 7200.0
    assert to.pool == 7200.0
    # read/connect 는 그대로.
    assert to.read == gm._SSE_READ_TIMEOUT == 300.0
    assert to.connect == gm._SSE_CONNECT_TIMEOUT == 30.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
