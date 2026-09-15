#!/usr/bin/env python3
"""Tavily 키리스 웹 검색 회귀 테스트 — 30명 배포에서 개인별 키 발급 불필요 보장.

── 배경 ──────────────────────────────────────────────────────────────────
웹 검색 제공자 3종(tavily/exa/brave)이 모두 `_REQUIRES_KEY` 였을 때, 키가 없으면
`web_search_raw` 가 네트워크 호출 전에 `missing_credential` 로 반환해 **웹 검색이
구조적으로 불가**했다. 30명에게 배포하면서 각자 계정을 만들어 키를 발급받게 하는 것은
사용자 공수가 과도하다.

Tavily 는 `X-Tavily-Access-Mode: keyless` 헤더로 계정·키 없이 동일한 응답 스키마를
제공한다(https://docs.tavily.com/documentation/keyless). 이 경로를 기본으로 쓰고,
키가 있으면 `Authorization: Bearer` 로 승격해 요청 한도만 올린다.

── 이 테스트가 고정하는 성질 ────────────────────────────────────────────
  1. tavily 는 `_REQUIRES_KEY` 에 없다 — 키 없이도 호출을 시도한다
  2. 키가 없으면 `X-Tavily-Access-Mode: keyless` 헤더로 보낸다 (Authorization 없음)
  3. 키가 있으면 `Authorization: Bearer` 로 보낸다 (keyless 헤더 없음)
  4. 어느 경로든 요청 본문과 응답 파싱은 동일하다 (스키마 불변)
  5. exa/brave 는 여전히 키 필수 — 키 없으면 missing_credential
  6. 어떤 경로에서도 키 원문이 로그·반환값에 노출되지 않는다

네트워크를 타지 않는다 — httpx.AsyncClient 를 가짜로 대체해 실제 전송 헤더를 관찰한다.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_engine.research import backend  # noqa: E402

# 옵트인·동의를 켠 밀폐 env(게이트 통과용). 자격증명은 각 테스트가 주입한다.
_GATE_ON = {"AE_ENABLE_WEB_RESEARCH": "1", "AE_RESEARCH_CONSENT": "1"}

_FAKE_TAVILY_RESPONSE = {
    "query": "q",
    "results": [
        {
            "title": "Sample result",
            "url": "https://example.org/a",
            "content": "body text",
            "score": 0.9,
        }
    ],
}


class _CapturedRequest:
    """전송 직전의 요청을 담는다(헤더·본문·URL)."""

    def __init__(self):
        self.url = None
        self.headers = None
        self.json_body = None


class _FakeClient:
    """httpx.AsyncClient 대역 — POST 를 가로채 고정 응답을 돌려준다."""

    def __init__(self, captured, payload):
        self._captured = captured
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None, params=None):
        self._captured.url = url
        self._captured.headers = dict(headers or {})
        self._captured.json_body = json
        return _FakeResponse(self._payload)

    async def get(self, url, params=None, headers=None):
        self._captured.url = url
        self._captured.headers = dict(headers or {})
        return _FakeResponse(self._payload)


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.content = b"{}"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _run_tavily(env_overrides, captured):
    """밀폐 env + 가짜 client 로 web_search_raw('tavily') 를 1회 실행한다."""
    import httpx

    saved_env = {}
    keys = set(_GATE_ON) | set(env_overrides) | {"TAVILY_API_KEY"}
    for k in keys:
        saved_env[k] = os.environ.get(k)
    saved_client = httpx.AsyncClient
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(_GATE_ON)
        os.environ.update(env_overrides)

        def _factory(*a, **kw):
            return _FakeClient(captured, _FAKE_TAVILY_RESPONSE)

        httpx.AsyncClient = _factory
        return asyncio.run(
            backend.web_search_raw("tavily", "sample query", top_k=3, timeout=5.0)
        )
    finally:
        httpx.AsyncClient = saved_client
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run_requires_key(provider):
    """키를 주입하지 않은 상태로 provider 를 호출한다(네트워크 미도달 기대)."""
    saved = {k: os.environ.get(k) for k in set(_GATE_ON) | {
        "EXA_API_KEY", "BRAVE_API_KEY"
    }}
    try:
        for k in saved:
            os.environ.pop(k, None)
        os.environ.update(_GATE_ON)
        return asyncio.run(
            backend.web_search_raw(provider, "sample query", top_k=3, timeout=5.0)
        )
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# =========================================================================== #
def test_tavily_not_in_requires_key() -> None:
    """tavily 가 _REQUIRES_KEY 에 없다 — 키 없이도 호출을 시도해야 한다."""
    assert "tavily" not in backend._REQUIRES_KEY, (
        "tavily 가 다시 필수 키 제공자가 되면 30명 배포에서 개인별 키 발급이 강제된다"
    )
    # 키리스 경로가 없는 제공자는 그대로 필수여야 한다.
    assert backend._REQUIRES_KEY == frozenset({"exa", "brave"})


def test_tavily_keyless_header_when_no_key() -> None:
    """키 미설정 → X-Tavily-Access-Mode: keyless, Authorization 헤더 부재."""
    cap = _CapturedRequest()
    raw = _run_tavily({}, cap)

    assert "error" not in raw, f"키리스 경로가 오류로 떨어짐: {raw}"
    assert cap.headers.get("X-Tavily-Access-Mode") == "keyless"
    assert "Authorization" not in cap.headers
    # 응답은 표준 스키마 그대로여야 한다(어댑터가 results[] 를 읽는다).
    assert raw.get("results") and raw["results"][0]["title"] == "Sample result"


def test_tavily_bearer_when_key_present() -> None:
    """키 설정 → Authorization: Bearer, keyless 헤더 부재(한도 상향 경로)."""
    cap = _CapturedRequest()
    raw = _run_tavily({"TAVILY_API_KEY": "tvly-SAMPLE-key-0123456789"}, cap)

    assert "error" not in raw
    assert cap.headers.get("Authorization") == "Bearer tvly-SAMPLE-key-0123456789"
    assert "X-Tavily-Access-Mode" not in cap.headers


def test_request_body_identical_across_both_paths() -> None:
    """키 유무와 무관하게 요청 본문(질의·상한·depth)이 동일하다 — 스키마 불변."""
    cap_keyless = _CapturedRequest()
    _run_tavily({}, cap_keyless)
    cap_keyed = _CapturedRequest()
    _run_tavily({"TAVILY_API_KEY": "tvly-SAMPLE-key-0123456789"}, cap_keyed)

    assert cap_keyless.json_body == cap_keyed.json_body
    assert cap_keyless.url == cap_keyed.url


def test_keyless_response_parses_through_adapter() -> None:
    """키리스 응답이 기존 Tavily 어댑터로 그대로 파싱된다(파서 변경 불필요)."""
    from ai_engine.research.providers import get_academic_adapter  # noqa: F401
    from ai_engine.research.providers import get_web_adapter

    cap = _CapturedRequest()
    raw = _run_tavily({}, cap)
    results = get_web_adapter("tavily").to_search_results(raw)
    assert len(results) == 1
    r = results[0]
    assert r.title == "Sample result"
    assert r.url == "https://example.org/a"
    assert r.provider == "tavily"


def test_exa_and_brave_still_require_key() -> None:
    """exa/brave 는 키리스 경로가 없어 키 없으면 네트워크 미호출로 막힌다."""
    for provider in ("exa", "brave"):
        raw = _run_requires_key(provider)
        assert raw.get("error") == "missing_credential", (
            f"{provider} 는 키 없이 호출되어서는 안 된다: {raw}"
        )
        assert raw.get("provider") == provider


def test_no_credential_leak_in_return_value() -> None:
    """어느 경로에서도 반환값에 키 원문이 담기지 않는다(P9)."""
    import json

    secret = "tvly-SAMPLE-key-0123456789"
    cap = _CapturedRequest()
    raw = _run_tavily({"TAVILY_API_KEY": secret}, cap)
    assert secret not in json.dumps(raw, ensure_ascii=False)


if __name__ == "__main__":
    import traceback

    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS {name}")
        except Exception:
            fails += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    print(f"\n{'실패 ' + str(fails) if fails else '전부 통과'}")
    raise SystemExit(1 if fails else 0)
