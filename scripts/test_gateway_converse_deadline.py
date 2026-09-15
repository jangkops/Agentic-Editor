"""converse 연결 시도 예산("30분 벽" 제거) + S3 폴링 예외 분류 회귀 테스트.

배경(확정 진단):
  `GatewayClient.converse` 의 최악 소요가 정확히 1800초(30분)였다.
    - `for attempt in range(3)` — 3회 재시도
    - 각 시도: `urlopen(req, timeout=300)` = 300초
    - 비동기 모델(ACCEPTED)이면 추가로 `_poll_job_data(max_wait=300)` = 300초
    - 3 × (300 + 300) = 1800초
  재시도 사이에 누적 상한이 없어 예산이 매 시도마다 새로 시작한 것이 원인이다.

의도 정정(긴 컨텍스트 지원):
  예산의 목적은 **연결 시도 상한**으로 좁혀졌다. 잡 대기는 서버측에서 도는 작업을
  기다리는 시간이므로 예산과 무관하게 `_job_max_wait()`(기본 7200초)를 쓴다 —
  1시간 이상 걸리는 출력을 600초에서 자르지 않는다(§4).

  또한 `_poll_job_data` 의 `except Exception: continue` 가 모든 오류를 삼켜서
  권한 오류(403)가 "아직 결과 없음"과 구분되지 않고 5분을 전부 소모했다.

  마지막으로 `stream_sse_realtime` 의 ReadTimeout 문구가 "120초 무응답"이라
  실제 read 타임아웃(300초)과 어긋나 조사 시 오판을 유도했다.

제약 준수:
  - 네트워크·AWS 호출 없음. `urllib.request.urlopen` / boto3 S3 / httpx 를 mock.
  - `time.monotonic` 은 가짜 시계를 주입 — 실제로 기다리지 않는다.
  - `asyncio.sleep` 도 no-op 으로 대체해 폴링 루프가 실시간을 소모하지 않게 한다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_gateway_converse_deadline.py -q
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

import pytest
from botocore.exceptions import ClientError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ai_engine import gateway_module as gm  # noqa: E402
from ai_engine.gateway_module import GatewayClient, _is_transient_s3_miss  # noqa: E402


# ─────────────────────────────────────────────────────────────────
# 테스트 도구 — 가짜 시계 / 가짜 urlopen / 가짜 S3
# ─────────────────────────────────────────────────────────────────
class _FakeClock:
    """가짜 단조 시계 — 실제로 기다리지 않고 예산 소진을 재현한다.

    `gm.time` 자리에 주입한다(`converse` 는 모듈 전역 `time` 을 참조).
    `_get_creds` 는 함수 안에서 `import time` 을 하므로 영향받지 않는다.
    """

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
    # 서명/자격증명 우회 — 네트워크·STS 호출 방지
    c._sign = lambda method, url, body_bytes: {"Content-Type": "application/json"}
    c.force_refresh_creds = lambda: None
    return c


def _install_urlopen(monkeypatch, payload_for_call, clock=None, elapsed_per_call=0.0):
    """urlopen mock 설치. 넘겨받은 timeout 값을 순서대로 기록해 반환한다.

    payload_for_call(index) -> dict : 각 호출의 응답 본문
    elapsed_per_call        : 호출마다 가짜 시계를 이만큼 전진(실제 대기 없음)
    """
    timeouts: list = []

    def _fake_urlopen(req, timeout=None):
        idx = len(timeouts)
        timeouts.append(timeout)
        if clock is not None and elapsed_per_call:
            clock.advance(elapsed_per_call)
        return _FakeResp(payload_for_call(idx))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return timeouts


def _patch_sleep(monkeypatch, clock=None):
    """asyncio.sleep 을 계수용 no-op 으로 대체 — 호출 횟수를 리스트로 반환."""
    calls: list = []

    async def _fake_sleep(delay, *a, **k):
        calls.append(delay)
        if clock is not None:
            clock.advance(delay)

    monkeypatch.setattr(gm.asyncio, "sleep", _fake_sleep)
    return calls


#: 토큰 만료 에러 — converse 의 재시도(`continue`) 경로를 결정적으로 태운다.
_EXPIRED = {"decision": "ERROR", "error": "ExpiredTokenException: security token expired"}

_TEXT_JOB = {"output": {"message": {"role": "assistant",
                                    "content": [{"text": "ok"}]}}}


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "boom"},
         "ResponseMetadata": {"HTTPStatusCode": status}},
        "GetObject",
    )


# ─────────────────────────────────────────────────────────────────
# 1. 예산 소진 시 재시도 중단
# ─────────────────────────────────────────────────────────────────
def test_budget_exhaustion_stops_retries_before_third_attempt(monkeypatch):
    """예산이 소진되면 3회를 다 쓰지 않고 재시도가 중단된다(urlopen 호출 횟수로 확인)."""
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    _patch_sleep(monkeypatch)
    # 시도마다 400초 소모 → 기본 예산 600초는 2번째 시도 도중에 끝난다.
    timeouts = _install_urlopen(monkeypatch, lambda i: dict(_EXPIRED),
                                clock=clock, elapsed_per_call=400.0)

    c = _make_client()
    result = asyncio.run(c.converse("us.anthropic.claude-x",
                                    [{"role": "user", "content": [{"text": "hi"}]}]))

    assert len(timeouts) == 2, f"예산 소진에도 재시도가 계속됨: {len(timeouts)}회"
    # 반환 계약 불변 — 루프 뒤의 기존 return 으로 마지막 결과가 그대로 나온다.
    assert result.get("decision") == "ERROR"
    assert "expired" in result.get("error", "").lower()


# ─────────────────────────────────────────────────────────────────
# 2. 예산이 충분하면 기존과 동일하게 재시도(무회귀)
# ─────────────────────────────────────────────────────────────────
def test_sufficient_budget_preserves_three_attempts(monkeypatch):
    """예산이 충분하면 기존과 동일하게 3회 시도가 일어난다(무회귀)."""
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    _patch_sleep(monkeypatch)
    # 시도가 1초씩만 소모 → 예산(600초) 여유 충분
    timeouts = _install_urlopen(monkeypatch, lambda i: dict(_EXPIRED),
                                clock=clock, elapsed_per_call=1.0)

    c = _make_client()
    result = asyncio.run(c.converse("us.anthropic.claude-x",
                                    [{"role": "user", "content": [{"text": "hi"}]}]))

    assert len(timeouts) == 3, f"예산 여유에도 재시도가 줄었다: {len(timeouts)}회"
    assert result.get("decision") == "ERROR"


def test_fast_path_unchanged_single_call(monkeypatch):
    """정상·빠른 응답 경로는 그대로 — 1회 호출로 ALLOW 를 반환한다."""
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    _patch_sleep(monkeypatch)
    allow = {"decision": "ALLOW", "output": {"message": {"content": [{"text": "ok"}]}}}
    timeouts = _install_urlopen(monkeypatch, lambda i: dict(allow), clock=clock,
                                elapsed_per_call=0.1)

    c = _make_client()
    result = asyncio.run(c.converse("us.anthropic.claude-x",
                                    [{"role": "user", "content": [{"text": "hi"}]}]))
    assert len(timeouts) == 1
    assert result.get("decision") == "ALLOW"


# ─────────────────────────────────────────────────────────────────
# 3. urlopen timeout 이 남은 예산으로 clamp 된다
# ─────────────────────────────────────────────────────────────────
def test_urlopen_timeout_clamped_to_remaining_budget(monkeypatch):
    """urlopen 에 넘어간 timeout 이 남은 예산으로 clamp 된다(상한 300, 하한 5)."""
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    _patch_sleep(monkeypatch)
    # 1회당 450초 소모: 예산 600 → 2번째 시도 시작 시 남은 예산 150초
    timeouts = _install_urlopen(monkeypatch, lambda i: dict(_EXPIRED),
                                clock=clock, elapsed_per_call=450.0)

    c = _make_client()
    asyncio.run(c.converse("us.anthropic.claude-x",
                           [{"role": "user", "content": [{"text": "hi"}]}]))

    assert timeouts[0] == 300, f"첫 시도는 기존 상한 300이어야 함: {timeouts[0]}"
    assert timeouts[1] == 150, f"남은 예산(150초)으로 clamp 되지 않음: {timeouts[1]}"
    assert all(t <= 300 for t in timeouts), "300 상한 위반"


def test_urlopen_timeout_has_floor(monkeypatch):
    """예산이 거의 없어도 timeout 은 하한(5초) 아래로 내려가지 않는다."""
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    monkeypatch.setenv("AE_CONVERSE_TOTAL_BUDGET", "10")
    _patch_sleep(monkeypatch)
    # 예산 10초 — 첫 시도에서 9.5초 소모 → 남은 0.5초이나 시계가 deadline 전이라 재시도 1회
    timeouts = _install_urlopen(monkeypatch, lambda i: dict(_EXPIRED),
                                clock=clock, elapsed_per_call=9.5)

    c = _make_client()
    asyncio.run(c.converse("us.anthropic.claude-x",
                           [{"role": "user", "content": [{"text": "hi"}]}]))

    assert timeouts[0] == 10, f"예산 10초가 그대로 쓰여야 함: {timeouts[0]}"
    assert all(t >= 5 for t in timeouts), f"하한 5초 위반: {timeouts}"


# ─────────────────────────────────────────────────────────────────
# 4. 잡 대기는 연결 시도 예산과 무관하다 (의도 정정)
#    이전 판(clamp/300 상한)은 >1시간 잡을 600초에서 잘랐다. 예산의 목적은
#    "연결 시도 상한"으로 좁혀졌고, 잡 대기는 `_job_max_wait()` 가 관장한다.
# ─────────────────────────────────────────────────────────────────
def test_poll_job_max_wait_not_clamped_by_connection_budget(monkeypatch):
    """예산을 대부분 소모했어도 _poll_job_data 에는 잡 예산(_job_max_wait)이 넘어간다."""
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    monkeypatch.delenv("AE_JOB_MAX_WAIT", raising=False)
    _patch_sleep(monkeypatch)
    accepted = {"decision": "ACCEPTED", "job_id": "job-abc", "estimated_cost_krw": 1}
    # 첫 시도가 450초 소모 → 연결 시도 예산은 150초만 남았지만 잡 대기는 영향 없다.
    _install_urlopen(monkeypatch, lambda i: dict(accepted), clock=clock,
                     elapsed_per_call=450.0)

    seen: list = []

    async def _fake_poll(job_id, max_wait=300):
        seen.append(max_wait)
        return dict(_TEXT_JOB)

    c = _make_client()
    c._poll_job_data = _fake_poll
    result = asyncio.run(c.converse("us.anthropic.claude-x",
                                    [{"role": "user", "content": [{"text": "hi"}]}]))

    assert seen == [gm._job_max_wait()], f"잡 대기가 연결 예산으로 잘림: {seen}"
    assert seen == [7200], f"잡 대기 기본값(7200초)이 아님: {seen}"
    assert result.get("decision") == "ALLOW"


def test_poll_job_max_wait_uses_job_budget_not_300(monkeypatch):
    """잡 대기 상한은 300이 아니라 `_job_max_wait()` 이며 env 로 조정된다."""
    clock = _FakeClock()
    monkeypatch.setattr(gm, "time", clock)
    monkeypatch.setenv("AE_CONVERSE_TOTAL_BUDGET", "5000")
    monkeypatch.setenv("AE_JOB_MAX_WAIT", "5400")
    _patch_sleep(monkeypatch)
    accepted = {"decision": "ACCEPTED", "job_id": "job-abc"}
    _install_urlopen(monkeypatch, lambda i: dict(accepted), clock=clock,
                     elapsed_per_call=1.0)

    seen: list = []

    async def _fake_poll(job_id, max_wait=300):
        seen.append(max_wait)
        return dict(_TEXT_JOB)

    c = _make_client()
    c._poll_job_data = _fake_poll
    asyncio.run(c.converse("us.anthropic.claude-x",
                           [{"role": "user", "content": [{"text": "hi"}]}]))

    assert seen == [5400], f"AE_JOB_MAX_WAIT 이 반영되지 않음: {seen}"
    assert seen != [300], "잡 대기가 낡은 300초 상한으로 회귀"


# ─────────────────────────────────────────────────────────────────
# 5. _is_transient_s3_miss 분류
# ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("code,status", [
    ("NoSuchKey", 404),
    ("NotFound", 404),
    ("SomethingElse", 404),   # HTTP 404 → 정상적인 "아직 없음"
    ("SlowDown", 503),        # 판정 불가 → 기존 동작(폴링 계속) 유지
])
def test_transient_s3_miss_true(code, status):
    assert _is_transient_s3_miss(_client_error(code, status)) is True


@pytest.mark.parametrize("code,status", [
    ("AccessDenied", 403),
    ("InvalidAccessKeyId", 403),
    ("SignatureDoesNotMatch", 403),
    ("ExpiredToken", 400),
    ("NoSuchBucket", 404),      # 코드가 치명적이면 404 여도 종료
    ("AllAccessDisabled", 403),
    ("Whatever", 403),          # HTTP 403 → 권한 문제로 종료
])
def test_transient_s3_miss_false(code, status):
    assert _is_transient_s3_miss(_client_error(code, status)) is False


@pytest.mark.parametrize("exc", [
    Exception("정체불명"),
    ConnectionResetError("순간 네트워크 오류"),
    TimeoutError("read timeout"),
])
def test_unknown_exception_treated_as_transient(exc):
    """판정 불가 예외는 True — 기존 동작(폴링 계속)을 유지해 무회귀 보장."""
    assert _is_transient_s3_miss(exc) is True


# ─────────────────────────────────────────────────────────────────
# 6. AccessDenied 주입 시 _poll_job_data 가 즉시 None
# ─────────────────────────────────────────────────────────────────
class _FakeS3:
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def get_object(self, Bucket=None, Key=None):
        self.calls += 1
        raise self._exc


class _FakeBoto3:
    """gm.boto3 대체 — S3 는 주입한 가짜, Session 은 실패시켜 account 폴백 유도."""

    def __init__(self, s3):
        self._s3 = s3

    def client(self, name, **kwargs):
        return self._s3

    def Session(self, *a, **k):
        raise RuntimeError("네트워크/프로필 접근 금지")


def _poll_with_s3_error(monkeypatch, exc, max_wait=300):
    fake_s3 = _FakeS3(exc)
    monkeypatch.setattr(gm, "boto3", _FakeBoto3(fake_s3))
    sleeps = _patch_sleep(monkeypatch)
    c = _make_client()
    c._get_creds = lambda: gm.Credentials("AKIDEXAMPLE", "SECRET", "TOKEN")
    out = asyncio.run(c._poll_job_data("job-xyz", max_wait=max_wait))
    return out, fake_s3, sleeps


def test_poll_job_data_aborts_immediately_on_access_denied(monkeypatch):
    """AccessDenied → max_wait 을 다 소모하지 않고 즉시 None(asyncio.sleep 1회)."""
    out, fake_s3, sleeps = _poll_with_s3_error(
        monkeypatch, _client_error("AccessDenied", 403), max_wait=300)
    assert out is None
    assert len(sleeps) == 1, f"권한 오류인데 폴링을 계속함: sleep {len(sleeps)}회"
    assert fake_s3.calls == 1


def test_poll_job_data_keeps_polling_on_nosuchkey(monkeypatch):
    """NoSuchKey → 기존대로 max_wait 만큼 폴링 후 None(무회귀)."""
    out, fake_s3, sleeps = _poll_with_s3_error(
        monkeypatch, _client_error("NoSuchKey", 404), max_wait=4)
    assert out is None
    assert len(sleeps) == 4, f"정상 miss 인데 조기 종료함: sleep {len(sleeps)}회"
    assert fake_s3.calls == 4


def test_poll_job_data_keeps_polling_on_unknown_exception(monkeypatch):
    """판정 불가 예외 → 기존 동작(폴링 계속) 유지."""
    out, fake_s3, sleeps = _poll_with_s3_error(
        monkeypatch, RuntimeError("순간 오류"), max_wait=3)
    assert out is None
    assert len(sleeps) == 3
    assert fake_s3.calls == 3


# ─────────────────────────────────────────────────────────────────
# 7. SSE ReadTimeout 문구가 실제 read 타임아웃 상수를 반영
# ─────────────────────────────────────────────────────────────────
def test_sse_read_timeout_message_matches_constant(monkeypatch):
    """ReadTimeout 문구에 실제 read 타임아웃 상수 값이 들어 있고, httpx 도 같은 상수를 쓴다."""
    captured: dict = {}

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

        def stream(self, method, url, content=None, headers=None):
            return _FakeStream()

    monkeypatch.setattr(gm.httpx, "AsyncClient", _FakeClient)

    c = _make_client()
    c._get_creds = lambda: gm.Credentials("AKIDEXAMPLE", "SECRET", "TOKEN")

    async def _consume():
        evts = []
        async for e in c.stream_sse_realtime(
                "us.anthropic.claude-x", [{"role": "user", "content": [{"text": "hi"}]}]):
            evts.append(e)
        return evts

    evts = asyncio.run(_consume())
    msgs = [str(e.get("message") or e.get("error") or "") for e in evts]
    joined = " ".join(msgs)

    assert gm._SSE_READ_TIMEOUT == 300.0
    assert str(int(gm._SSE_READ_TIMEOUT)) in joined, f"실제 read 타임아웃 값 누락: {joined}"
    assert "120" not in joined, f"낡은 문구(120초)가 남아 있음: {joined}"

    # httpx.Timeout 도 같은 상수를 참조해야 한다(다시 어긋나지 않게).
    to = captured.get("timeout")
    assert to is not None
    assert to.read == gm._SSE_READ_TIMEOUT
    assert to.connect == gm._SSE_CONNECT_TIMEOUT


# ─────────────────────────────────────────────────────────────────
# 8. 예산 상수/환경변수 계약
# ─────────────────────────────────────────────────────────────────
def test_budget_default_and_env_override(monkeypatch):
    monkeypatch.delenv("AE_CONVERSE_TOTAL_BUDGET", raising=False)
    assert gm._converse_total_budget() == 600.0
    assert gm._CONVERSE_TOTAL_BUDGET_DEFAULT == 600
    monkeypatch.setenv("AE_CONVERSE_TOTAL_BUDGET", "90")
    assert gm._converse_total_budget() == 90.0
    # 비정상값·0 이하는 기본값으로 폴백
    monkeypatch.setenv("AE_CONVERSE_TOTAL_BUDGET", "abc")
    assert gm._converse_total_budget() == 600.0
    monkeypatch.setenv("AE_CONVERSE_TOTAL_BUDGET", "0")
    assert gm._converse_total_budget() == 600.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
