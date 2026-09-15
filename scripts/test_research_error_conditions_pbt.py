# Feature: deep-research-engine, Property 8: 오류조건 비차단 폴백
"""Property-based test: 오류조건 비차단 폴백 (egress / normalize / providers).

Feature: deep-research-engine, Property 8: 오류조건 비차단 폴백
**Validates: Requirements 1.3, 1.8, 2.6, 3.5, 4.5, 8.3, 13.2, 13.3, 13.4**

For any (a) 불량 질의(빈/공백/과길이 2049+), (b) 제공자 호출 실패(httpx 예외/
타임아웃/HTTP 4xx·5xx/JSON·XML 파싱 실패), (c) 부분 필드 누락·형식 오류 원시 응답에
대해, 시스템은 **예외를 전파하거나 그래프 실행을 중단하지 않고** 구조화된 오류
dict / 빈 결과 / 추출 가능한 필드만 채운 정규 결과로 폴백한다.

이 테스트는 task 9.6의 범위(backend egress 폴백 · normalize 부분 파싱 · providers
어댑터 방어)를 **실제 네트워크 없이** 검증한다. 외부 HTTP는 전부
``httpx.AsyncClient`` 를 mock 으로 대체해(예외/타임아웃/오류상태/불량 바디 주입)
실제 소켓을 열지 않으며(real network 0), 옵트인/동의 플래그는 테스트에서만 켠 뒤
mock egress 로만 호출된다.

검증 축:

1. **egress 폴백(backend.py — 요구사항 1.3/1.8/2.6/3.5/13.2/13.3/13.4, P8):**
   - ``web_search_raw`` / ``academic_search_raw`` : httpx 예외·타임아웃·HTTP 오류상태·
     JSON/XML 파싱 실패를 주입해도 예외 없이 ``{"error": code, ...}`` 구조화 오류
     dict 를 반환한다.
   - ``fetch_url_raw`` : 동일 실패 주입 시 예외 없이 ``FetchResult(ok=False)`` 로
     폴백하고, 임의 바디(형식 오류 포함)에도 추출이 예외를 던지지 않는다.
   - 불량 질의(빈/공백/과길이)·미지원 제공자·비 http/https URL 은 **네트워크를
     호출하지 않고**(mock 호출 카운트 0) 즉시 구조화 오류로 폴백한다(요구사항 1.8).
   - 폴백 체인(``search_web_with_fallback`` / ``search_academic_with_fallback``):
     일부 제공자 실패 시 다음 제공자로 진행(13.2), 전 제공자 실패 시 예외 없이
     ``no_external_evidence`` 로 비차단 종료(13.3).
2. **부분 파싱 방어(normalize.py / providers.py — 요구사항 1.3/2.6/4.5, P8):**
   - ``parse_search_result`` / ``parse_paper_result`` 와 각 제공자 어댑터
     (``to_search_results`` / ``to_paper_results``)는 부분/비정상/비-dict 원시 입력에도
     예외 없이 기본값(빈 문자열/빈 리스트/정렬 가능한 0)으로 채운 정규 결과를 낸다.
   - egress 가 낸 구조화 오류 dict 를 어댑터에 넘겨도 예외 없이 빈 리스트가 나와
     파이프라인이 비차단으로 진행한다(요구사항 13.4).
   - ``serialize_result`` / ``deserialize_result`` 는 임의/비정상 입력에도 예외 없이
     안전하게 폴백한다.

Stack: Python 3.11+, hypothesis. 기존 scripts/test_*_pbt.py 관례를 따른다
(sys.path 삽입 후 ai_engine import, __main__ 에서 pytest 실행,
@settings(max_examples=200)). 비동기 egress 는 동기 테스트 내부에서 asyncio 로
1회 실행하며, httpx 는 항상 mock 이라 실제 네트워크 호출이 없다(밀폐/hermetic).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest import mock

# ai_engine 패키지를 직접 실행 시에도 import 할 수 있도록 저장소 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402
import pytest  # noqa: E402
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ai_engine.research import backend, normalize, providers  # noqa: E402
from ai_engine.research.models import PaperResult, SearchResult  # noqa: E402

# 최소 반복 200회(task 9.6 지정). 실제 네트워크가 없으므로 deadline 은 끄고(느린 CI/
# asyncio 오버헤드로 인한 deadline flake 방지) 오직 "예외 없음 + 구조화 폴백" 불변식만 본다.
_PBT = settings(max_examples=200, deadline=None)

# 제공자 name (backend/providers 정합).
_WEB_PROVIDERS = ["tavily", "exa", "brave"]
_ACADEMIC_PROVIDERS = ["semantic_scholar", "openalex", "arxiv", "pubmed"]
_KNOWN_PROVIDERS = set(_WEB_PROVIDERS) | set(_ACADEMIC_PROVIDERS)
# 단일 JSON GET/POST 로 성공을 흉내 낼 수 있는 제공자(성공 통과 테스트용 — arxiv/pubmed
# 는 XML/2단계라 제외).
_JSON_WEB_PROVIDERS = ["tavily", "exa", "brave"]
_JSON_ACADEMIC_PROVIDERS = ["semantic_scholar", "openalex"]

# 옵트인/동의를 켜고 제공자 키를 주입한 밀폐 env(task: "옵트인은 테스트에서 on"). 실제
# egress 는 httpx mock 이 가로채므로 네트워크는 발생하지 않는다. 웹 제공자
# (tavily/exa/brave)는 키 필수(_REQUIRES_KEY)라 mock 도달 전 missing_credential 로
# 빠지지 않도록 더미 키를 넣는다(값은 로그 마스킹 대상일 뿐 네트워크로 나가지 않음).
_OPTIN_ENV = {
    "AE_ENABLE_WEB_RESEARCH": "1",
    "AE_RESEARCH_CONSENT": "1",
    "AE_RESEARCH_WEB_PROVIDERS": "tavily,exa,brave",
    "AE_RESEARCH_ACADEMIC_PROVIDERS": "semantic_scholar,openalex,arxiv,pubmed",
    "TAVILY_API_KEY": "test-tavily-key-abcd1234",
    "EXA_API_KEY": "test-exa-key-abcd1234",
    "BRAVE_API_KEY": "test-brave-key-abcd1234",
    "SEMANTIC_SCHOLAR_API_KEY": "test-s2-key-abcd1234",
}

# 실패 유형 → backend 가 돌려줄 구조화 오류 코드(정확한 매핑 검증용).
_EXPECTED_ERROR = {
    "timeout": {"timeout"},
    "connect": {"request_error"},
    "unexpected": {"request_error"},
    "http_status": {"http_error"},
    "body_json_decode": {"parse_error"},
    "body_json_nondict": {"parse_error"},
    "body_bad_xml": {"parse_error"},
}
_EGRESS_FAIL_KINDS = sorted(_EXPECTED_ERROR)  # 검색 egress 는 전 실패 유형 적용

# fetch_url_raw 는 본문을 json/xml 파싱하지 않으므로 body_* 는 실패가 아니다(성공 폴백).
# 전송/상태 오류만 ok=False 로 이어진다.
_EXPECTED_FETCH_ERROR = {
    "timeout": "timeout",
    "connect": "request_error",
    "unexpected": "request_error",
    "http_status": "http_error",
}
_FETCH_FAIL_KINDS = sorted(_EXPECTED_FETCH_ERROR)


# =========================================================================== #
# httpx mock 인프라 (실제 네트워크 0) — AsyncClient 를 가짜로 대체
# =========================================================================== #
_UNSET = object()


class _FakeResponse:
    """httpx.Response 를 흉내 내는 최소 가짜 응답(주입된 실패/바디를 재현)."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        status_error=None,
        json_data=_UNSET,
        json_exc=None,
        content: bytes = b"",
        text: str = "",
        headers=None,
    ):
        self.status_code = status_code
        self._status_error = status_error
        self._json_data = json_data
        self._json_exc = json_exc
        self.content = content
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        """4xx/5xx 주입 시 실제 httpx.HTTPStatusError 를 던진다(그 외 no-op)."""
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        """JSON 파싱 실패/비-object 를 재현한다(그 외 주입된 dict/list 반환)."""
        if self._json_exc is not None:
            raise self._json_exc
        if self._json_data is _UNSET:
            raise json.JSONDecodeError("no json body", "", 0)
        return self._json_data


class _Behavior:
    """get/post 호출마다 순차 계획(plan)을 적용하는 스텁 동작기.

    plan 은 "0-인자 콜러블" 목록이며 각 콜러블은 ``_FakeResponse`` 를 반환하거나
    예외를 던진다. 호출 수가 plan 을 넘으면 마지막 항목을 반복한다. 폴백 체인처럼
    제공자별로 client 인스턴스가 새로 생겨도 동일 behavior 를 공유하므로 호출
    순서(=제공자 순서)를 그대로 흉내 낼 수 있다. ``calls`` 로 실제 네트워크 시도
    횟수를 관측한다(불량 질의/미지원 제공자/비 http URL 은 0 이어야 함).
    """

    def __init__(self, plan):
        self.plan = list(plan)
        self.calls = 0

    def act(self, method, *args, **kwargs):
        idx = min(self.calls, len(self.plan) - 1)
        self.calls += 1
        return self.plan[idx]()


def _make_fake_client_factory(behavior: _Behavior):
    """``httpx.AsyncClient`` 를 대체할 팩토리(비동기 컨텍스트 매니저)."""

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return behavior.act("get", *args, **kwargs)

        async def post(self, *args, **kwargs):
            return behavior.act("post", *args, **kwargs)

    return _FakeAsyncClient


# --- 실패/성공 콜러블 빌더 --------------------------------------------------- #
def _fail_callable(kind: str, status_code: int = 500):
    """실패 유형 → get/post 가 낼 결과(예외 또는 파싱 실패 응답)를 만드는 콜러블."""
    if kind == "timeout":
        return lambda: (_ for _ in ()).throw(httpx.ReadTimeout("simulated timeout"))
    if kind == "connect":
        return lambda: (_ for _ in ()).throw(httpx.ConnectError("simulated connect error"))
    if kind == "unexpected":
        return lambda: (_ for _ in ()).throw(RuntimeError("simulated unexpected error"))
    if kind == "http_status":
        req = httpx.Request("GET", "https://provider.invalid/endpoint")
        resp = httpx.Response(status_code, request=req)
        err = httpx.HTTPStatusError("simulated status", request=req, response=resp)
        return lambda: _FakeResponse(status_code=status_code, status_error=err,
                                     content=b"error", text="error")
    if kind == "body_json_decode":
        # JSON 경로: json() 이 JSONDecodeError. XML 경로: content 가 불량 XML → 둘 다 parse_error.
        return lambda: _FakeResponse(
            json_exc=json.JSONDecodeError("bad", "", 0),
            content=b"<<< not xml", text="not json",
            headers={"content-type": "application/json"},
        )
    if kind == "body_json_nondict":
        return lambda: _FakeResponse(
            json_data=["not", "an", "object"],
            content=b"<<< not xml", text="[]",
            headers={"content-type": "application/json"},
        )
    if kind == "body_bad_xml":
        return lambda: _FakeResponse(
            json_exc=json.JSONDecodeError("bad", "", 0),
            content=b"<broken><xml", text="<broken",
            headers={"content-type": "application/xml"},
        )
    raise AssertionError(f"unknown fail kind: {kind!r}")


def _ok_json_callable(data: dict):
    """성공 JSON 응답을 내는 콜러블(raise_for_status no-op, json()→data)."""
    return lambda: _FakeResponse(
        status_code=200, json_data=data, content=b"<r/>", text="{}",
        headers={"content-type": "application/json"},
    )


def _ok_fetch_callable(content: bytes, text: str, ctype: str):
    """성공 본문 응답을 내는 콜러블(fetch_url_raw 는 content-type 별로 추출)."""
    return lambda: _FakeResponse(
        status_code=200, content=content, text=text,
        headers={"content-type": ctype},
    )


# --- egress 실행 헬퍼(env on + httpx mock + asyncio 1회 실행) ---------------- #
def _aio(coro_factory):
    """코루틴을 이벤트 루프에서 1회 실행한다(실행 중 루프가 있어도 안전 폴백)."""
    coro = coro_factory()
    try:
        return asyncio.run(coro)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


def _run_egress(coro_factory, behavior: _Behavior):
    """옵트인 env 를 켜고 httpx.AsyncClient 를 mock 으로 대체한 채 egress 를 실행한다.

    실제 네트워크는 발생하지 않으며(httpx mock), 예외가 전파되면 P8 위반이므로
    AssertionError 로 승격해 반례를 명확히 보고한다.
    """
    fake_cls = _make_fake_client_factory(behavior)
    with mock.patch.dict(os.environ, _OPTIN_ENV), \
            mock.patch.object(backend.httpx, "AsyncClient", fake_cls):
        try:
            return _aio(coro_factory)
        except Exception as exc:  # noqa: BLE001 — P8: egress 는 예외를 전파하면 안 된다
            raise AssertionError(
                f"P8 위반: egress 가 예외를 전파했다 ({type(exc).__name__}: {exc})"
            ) from exc


# =========================================================================== #
# 입력 생성기(strategies)
# =========================================================================== #
# 검증을 통과하는 정상 질의(strip 후 비어있지 않고 ≤ 2048자).
_valid_query = st.text(min_size=1, max_size=120).map(
    lambda s: s if s.strip() else "valid fallback query"
)

# 불량 질의: 빈/공백만/과길이(2049+)/None. 모두 invalid_query 로 폴백해야 한다.
_overlong_query = st.integers(min_value=2049, max_value=5000).map(lambda n: "a" * n)
_whitespace_query = st.text(alphabet=" \t\n\r\f\v", min_size=1, max_size=12)
_bad_query = st.one_of(
    st.just(""), _whitespace_query, _overlong_query, st.none()
)

# http/https 정상 URL(스킴 통과 → mock 네트워크 시도).
_valid_http_url = st.builds(
    lambda scheme, host, path: f"{scheme}://{host}.test/{path}",
    st.sampled_from(["http", "https"]),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=10),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789/_-", max_size=15),
)

# 비 http/https URL: 네트워크를 호출하지 않고 unsupported_scheme/invalid_url 로 폴백.
_KNOWN_BAD_URLS = [
    "", "   ", "ftp://host/file", "file:///etc/passwd", "javascript:alert(1)",
    "data:text/html,hi", "mailto:a@b.com", "tel:12345", "http://", "https://",
    "://nohost", "hostonly", "/relative/path", "www.example.com",
]
_bad_url = st.one_of(
    st.sampled_from(_KNOWN_BAD_URLS),
    st.text(max_size=30).filter(
        lambda s: not s.strip().lower().startswith(("http://", "https://"))
    ),
    st.none(),
)

# 미지원 제공자(정규화 후 known 집합 밖).
_unknown_provider = st.one_of(
    st.sampled_from(["", "google", "duckduckgo", "bing", "tavily2", "scholar", "xyz"]),
    st.text(max_size=12),
).filter(
    lambda p: (p.strip().lower() if isinstance(p, str) else "") not in _KNOWN_PROVIDERS
)

# JSON-호환 임의 값(부분/비정상 원시 응답 재현 — NaN/inf/중첩 포함).
_json_leaf = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-100000, max_value=100000),
    st.floats(allow_nan=True, allow_infinity=True, width=32),
    st.text(max_size=15),
)
_json_values = st.recursive(
    _json_leaf,
    lambda ch: st.one_of(
        st.lists(ch, max_size=4),
        st.dictionaries(st.text(max_size=6), ch, max_size=4),
    ),
    max_leaves=12,
)

# 어댑터가 읽는 대표 키(존재하되 타입이 어긋난 값을 섞어 매핑 방어를 스트레스).
_ADAPTER_KEYS = [
    "title", "url", "content", "text", "highlights", "published_date",
    "publishedDate", "score", "description", "page_age", "age", "authors",
    "year", "venue", "abstract", "externalIds", "citationCount", "display_name",
    "authorships", "publication_year", "primary_location", "doi", "id",
    "cited_by_count", "relevance_score", "abstract_inverted_index", "summary",
    "published", "arxiv_doi", "ArticleTitle", "AuthorList", "Journal",
    "Abstract", "ELocationID", "MedlineCitation", "PMID", "name", "#text",
    "@EIdType", "author",
]
_partial_item = st.dictionaries(
    keys=st.one_of(st.sampled_from(_ADAPTER_KEYS), st.text(max_size=10)),
    values=_json_values,
    max_size=8,
)
# 파서/어댑터에 넣을 원시 항목: 부분 dict + 아예 dict 가 아닌 타입도 포함.
_raw_item = st.one_of(
    _partial_item, st.none(), st.lists(_json_values, max_size=3), st.text(max_size=12),
    st.integers(),
)

# 상위 top-level 에 "error" 가 없는 성공 응답 dict(패스스루/부분성공 폴백용).
_no_error_dict = st.dictionaries(
    keys=st.text(max_size=8).filter(lambda k: k != "error"),
    values=_json_values,
    max_size=6,
)

# egress 가 낼 법한 구조화 오류 dict(어댑터가 빈 결과로 흡수해야 함).
_error_dict = st.builds(
    lambda code, detail: {"error": code, "detail": detail},
    st.sampled_from([
        "timeout", "http_error", "parse_error", "request_error", "invalid_query",
        "unknown_provider", "missing_credential", "web_research_disabled",
        "no_external_evidence",
    ]),
    st.text(max_size=30),
)


@st.composite
def _raw_container(draw):
    """다양한 결과-컨테이너 형태(정상/비정상)를 생성해 어댑터 경로를 두루 자극한다."""
    items = draw(st.lists(st.one_of(_partial_item, _json_values), max_size=4))
    shape = draw(st.sampled_from([
        "results", "web.results", "data", "entries", "feed.entry",
        "PubmedArticleSet.PubmedArticle", "articles", "nondict", "garbage",
    ]))
    if shape == "results":
        return {"results": items}
    if shape == "web.results":
        return {"web": {"results": items}}
    if shape == "data":
        return {"data": items}
    if shape == "entries":
        return {"entries": items}
    if shape == "feed.entry":
        return {"feed": {"entry": items}}
    if shape == "PubmedArticleSet.PubmedArticle":
        return {"PubmedArticleSet": {"PubmedArticle": items}}
    if shape == "articles":
        return {"articles": items}
    if shape == "nondict":
        return draw(_json_values)  # dict 가 아닐 수도 있는 입력(방어 확인)
    return draw(st.dictionaries(st.text(max_size=6), _json_values, max_size=5))


# =========================================================================== #
# (1) egress 실패 주입 → 예외 없이 구조화 오류 dict (backend.py)
# =========================================================================== #
@_PBT
@given(
    provider=st.sampled_from(_WEB_PROVIDERS),
    kind=st.sampled_from(_EGRESS_FAIL_KINDS),
    status=st.integers(min_value=400, max_value=599),
    query=_valid_query,
)
def test_web_search_raw_failures_are_structured_errors(provider, kind, status, query):
    """web_search_raw: 모든 제공자 실패 유형 → 예외 없이 {"error": ...} (P8/13.4)."""
    behavior = _Behavior([_fail_callable(kind, status)])
    result = _run_egress(
        lambda: backend.web_search_raw(provider, query, top_k=5, timeout=1.0),
        behavior,
    )
    assert isinstance(result, dict)
    assert result.get("error") in _EXPECTED_ERROR[kind], (
        f"provider={provider} kind={kind}: 예상 오류 코드 밖 → {result!r}"
    )
    if kind == "http_status":
        assert result.get("status_code") == status
    assert behavior.calls == 1  # 네트워크는 정확히 1회 시도된 뒤 폴백


@_PBT
@given(
    provider=st.sampled_from(_ACADEMIC_PROVIDERS),
    kind=st.sampled_from(_EGRESS_FAIL_KINDS),
    status=st.integers(min_value=400, max_value=599),
    query=_valid_query,
)
def test_academic_search_raw_failures_are_structured_errors(provider, kind, status, query):
    """academic_search_raw: S2/OpenAlex/arXiv/PubMed 실패 → 예외 없이 구조화 오류 (P8)."""
    behavior = _Behavior([_fail_callable(kind, status)])
    result = _run_egress(
        lambda: backend.academic_search_raw(provider, query, top_k=5, timeout=1.0),
        behavior,
    )
    assert isinstance(result, dict)
    assert result.get("error") in _EXPECTED_ERROR[kind], (
        f"provider={provider} kind={kind}: 예상 오류 코드 밖 → {result!r}"
    )
    if kind == "http_status":
        assert result.get("status_code") == status
    assert behavior.calls == 1


@_PBT
@given(
    provider=st.sampled_from(_JSON_WEB_PROVIDERS + _JSON_ACADEMIC_PROVIDERS),
    data=_no_error_dict,
    query=_valid_query,
)
def test_egress_success_passthrough_has_no_error(provider, data, query):
    """정상 응답(부분 필드여도) → 예외 없이 원시 dict 그대로 반환(오류 키 없음)."""
    behavior = _Behavior([_ok_json_callable(data)])
    if provider in _JSON_ACADEMIC_PROVIDERS:
        result = _run_egress(
            lambda: backend.academic_search_raw(provider, query, top_k=5, timeout=1.0),
            behavior,
        )
    else:
        result = _run_egress(
            lambda: backend.web_search_raw(provider, query, top_k=5, timeout=1.0),
            behavior,
        )
    assert isinstance(result, dict)
    assert "error" not in result
    assert result == data
    assert behavior.calls == 1


# =========================================================================== #
# (2) fetch_url_raw — 실패 → ok=False, 성공(임의 바디) → 예외 없음 (요구사항 3.5)
# =========================================================================== #
@_PBT
@given(
    kind=st.sampled_from(_FETCH_FAIL_KINDS),
    status=st.integers(min_value=400, max_value=599),
    url=_valid_http_url,
)
def test_fetch_url_raw_failures_return_ok_false(kind, status, url):
    """fetch_url_raw: 전송/상태 오류 → 예외 없이 FetchResult(ok=False) (P8/3.5)."""
    behavior = _Behavior([_fail_callable(kind, status)])
    result = _run_egress(
        lambda: backend.fetch_url_raw(url, timeout=1.0, max_chars=1000),
        behavior,
    )
    assert isinstance(result, backend.FetchResult)
    assert result.ok is False
    assert result.error == _EXPECTED_FETCH_ERROR[kind]
    assert result.text == ""
    assert behavior.calls == 1


@_PBT
@given(
    content=st.binary(max_size=200),
    text=st.text(max_size=200),
    ctype=st.sampled_from([
        "text/html", "text/html; charset=utf-8", "text/plain", "application/json",
        "application/xml", "", "image/png", "application/octet-stream",
    ]),
    max_chars=st.integers(min_value=1, max_value=64),
    url=_valid_http_url,
)
def test_fetch_url_raw_success_is_robust(content, text, ctype, max_chars, url):
    """fetch_url_raw: 임의(형식 오류 포함) 바디에도 추출이 예외 없이 완료된다(P8)."""
    behavior = _Behavior([_ok_fetch_callable(content, text, ctype)])
    result = _run_egress(
        lambda: backend.fetch_url_raw(url, timeout=1.0, max_chars=max_chars),
        behavior,
    )
    assert isinstance(result, backend.FetchResult)
    assert result.ok is True
    assert isinstance(result.text, str)
    assert result.chars == len(result.text)
    assert len(result.text) <= max_chars      # 크기 상한 준수(요구사항 3.3)
    assert isinstance(result.truncated, bool)
    if result.truncated:
        assert result.chars == max_chars
    assert behavior.calls == 1


@_PBT
@given(url=_bad_url)
def test_fetch_url_raw_bad_scheme_skips_network(url):
    """비 http/https URL → 네트워크 미호출·즉시 ok=False (요구사항 3.1/P8)."""
    behavior = _Behavior([_fail_callable("timeout")])  # 호출되면 안 됨
    result = _run_egress(
        lambda: backend.fetch_url_raw(url, timeout=1.0, max_chars=100),
        behavior,
    )
    assert isinstance(result, backend.FetchResult)
    assert result.ok is False
    assert result.error in {"unsupported_scheme", "invalid_url"}
    assert behavior.calls == 0  # real network 0


# =========================================================================== #
# (3) 불량 질의 / 미지원 제공자 → 네트워크 미호출·구조화 오류 (요구사항 1.8/P8)
# =========================================================================== #
@_PBT
@given(provider=st.sampled_from(_WEB_PROVIDERS + _ACADEMIC_PROVIDERS), badq=_bad_query)
def test_egress_invalid_query_skips_network(provider, badq):
    """빈/공백/과길이 질의 → 제공자 미호출·invalid_query 구조화 오류(예외 없음)."""
    behavior = _Behavior([_fail_callable("timeout")])  # 호출되면 안 됨
    if provider in _WEB_PROVIDERS:
        result = _run_egress(
            lambda: backend.web_search_raw(provider, badq, top_k=5, timeout=1.0),
            behavior,
        )
    else:
        result = _run_egress(
            lambda: backend.academic_search_raw(provider, badq, top_k=5, timeout=1.0),
            behavior,
        )
    assert isinstance(result, dict)
    assert result.get("error") == "invalid_query"
    assert behavior.calls == 0  # real network 0 (요구사항 1.8)


@_PBT
@given(provider=_unknown_provider, query=_valid_query)
def test_egress_unknown_provider_skips_network(provider, query):
    """미지원 제공자 → 네트워크 미호출·unknown_provider 구조화 오류(예외 없음)."""
    behavior = _Behavior([_fail_callable("timeout")])  # 호출되면 안 됨
    result = _run_egress(
        lambda: backend.web_search_raw(provider, query, top_k=5, timeout=1.0),
        behavior,
    )
    assert isinstance(result, dict)
    assert result.get("error") == "unknown_provider"
    assert behavior.calls == 0


# =========================================================================== #
# (4) 폴백 체인 — 부분 실패 진행(13.2) / 전면 실패 비차단 종료(13.3)
# =========================================================================== #
@_PBT
@given(
    query=_valid_query,
    kind=st.sampled_from(_EGRESS_FAIL_KINDS),
    status=st.integers(min_value=400, max_value=599),
)
def test_web_fallback_chain_exhaustion_no_external_evidence(query, kind, status):
    """웹 폴백 체인 전 제공자 실패 → 예외 없이 no_external_evidence (요구사항 13.3)."""
    behavior = _Behavior([_fail_callable(kind, status)])  # 모든 호출 실패
    result = _run_egress(
        lambda: backend.search_web_with_fallback(
            query, ["tavily", "exa", "brave"], top_k=5, timeout=1.0
        ),
        behavior,
    )
    assert isinstance(result, dict)
    assert result.get("ok") is False
    assert result.get("error") == "no_external_evidence"
    assert len(result.get("attempts", [])) == 3
    assert behavior.calls == 3  # 제공자당 1회 시도 후 다음으로 폴백


@_PBT
@given(
    query=_valid_query,
    kind=st.sampled_from(_EGRESS_FAIL_KINDS),
    status=st.integers(min_value=400, max_value=599),
)
def test_academic_fallback_chain_exhaustion_no_external_evidence(query, kind, status):
    """학술 폴백 체인 전 제공자 실패 → 예외 없이 no_external_evidence (요구사항 13.3)."""
    providers_order = ["semantic_scholar", "openalex", "arxiv", "pubmed"]
    behavior = _Behavior([_fail_callable(kind, status)])
    result = _run_egress(
        lambda: backend.search_academic_with_fallback(
            query, providers_order, top_k=5, timeout=1.0
        ),
        behavior,
    )
    assert isinstance(result, dict)
    assert result.get("ok") is False
    assert result.get("error") == "no_external_evidence"
    assert len(result.get("attempts", [])) == len(providers_order)
    assert behavior.calls == len(providers_order)


@_PBT
@given(
    query=_valid_query,
    kind=st.sampled_from(_EGRESS_FAIL_KINDS),
    status=st.integers(min_value=400, max_value=599),
    data=_no_error_dict,
)
def test_web_fallback_partial_failure_then_success(query, kind, status, data):
    """1차 제공자 실패 → 2차 성공으로 비차단 진행(요구사항 13.2)."""
    # call 1(tavily) 실패 → call 2+(exa) 성공. behavior 는 provider 간 공유되어 순서를 흉내.
    behavior = _Behavior([_fail_callable(kind, status), _ok_json_callable(data)])
    result = _run_egress(
        lambda: backend.search_web_with_fallback(
            query, ["tavily", "exa", "brave"], top_k=5, timeout=1.0
        ),
        behavior,
    )
    assert isinstance(result, dict)
    assert result.get("ok") is True
    assert result.get("provider") == "exa"   # 2번째 제공자에서 회복
    assert result.get("raw") == data
    assert behavior.calls == 2                # tavily 실패 + exa 성공, brave 미도달


@_PBT
@given(kind=st.sampled_from(["web", "academic"]), badq=_bad_query)
def test_fallback_invalid_query_skips_network(kind, badq):
    """폴백 체인도 불량 질의 → 제공자 미호출·invalid_query (요구사항 1.8/P8)."""
    behavior = _Behavior([_fail_callable("timeout")])  # 호출되면 안 됨
    if kind == "web":
        result = _run_egress(
            lambda: backend.search_web_with_fallback(
                badq, ["tavily", "exa", "brave"], top_k=5, timeout=1.0
            ),
            behavior,
        )
    else:
        result = _run_egress(
            lambda: backend.search_academic_with_fallback(
                badq, ["semantic_scholar", "openalex"], top_k=5, timeout=1.0
            ),
            behavior,
        )
    assert isinstance(result, dict)
    assert result.get("ok") is False
    assert result.get("error") == "invalid_query"
    assert behavior.calls == 0


# =========================================================================== #
# (5) 부분 파싱 방어 — normalize.py (요구사항 1.3/2.6/4.5, P8)
# =========================================================================== #
@_PBT
@given(
    provider=st.sampled_from(_WEB_PROVIDERS + ["", "unknown", "TAVILY", "Exa "]),
    item=_raw_item,
)
def test_parse_search_result_never_raises(provider, item):
    """parse_search_result: 부분/비정상 원시 항목 → 예외 없이 기본값 SearchResult."""
    res = normalize.parse_search_result(provider, item)
    assert isinstance(res, SearchResult)
    assert isinstance(res.title, str)
    assert isinstance(res.url, str)
    assert isinstance(res.snippet, str)
    assert isinstance(res.published_date, str)
    assert isinstance(res.source_domain, str)
    assert isinstance(res.relevance_score, float)  # 정렬 가능한 수치(요구사항 1.3)
    assert isinstance(res.source_id, str)


@_PBT
@given(
    provider=st.sampled_from(_ACADEMIC_PROVIDERS + ["", "unknown", "OpenAlex", "PubMed "]),
    item=_raw_item,
)
def test_parse_paper_result_never_raises(provider, item):
    """parse_paper_result: 부분/비정상 원시 항목 → 예외 없이 기본값 PaperResult."""
    res = normalize.parse_paper_result(provider, item)
    assert isinstance(res, PaperResult)
    assert isinstance(res.title, str)
    assert isinstance(res.authors, list)
    assert all(isinstance(a, str) for a in res.authors)
    assert isinstance(res.year, int)            # 정렬 가능한 수치(요구사항 2.6)
    assert isinstance(res.citation_count, int)  # 정렬 가능한 수치
    assert isinstance(res.relevance_score, float)
    assert isinstance(res.source_id, str)


# =========================================================================== #
# (6) 어댑터 방어 — providers.py (요구사항 1.3/2.6, P8)
# =========================================================================== #
@_PBT
@given(name=st.sampled_from(providers.available_web_adapters()), raw=_raw_container())
def test_web_adapter_never_raises_on_partial_raw(name, raw):
    """웹 어댑터: 부분/비정상/비-dict 원시 → 예외 없이 SearchResult 목록."""
    out = providers.get_web_adapter(name).to_search_results(raw)
    assert isinstance(out, list)
    assert all(isinstance(r, SearchResult) for r in out)


@_PBT
@given(name=st.sampled_from(providers.available_academic_adapters()), raw=_raw_container())
def test_academic_adapter_never_raises_on_partial_raw(name, raw):
    """학술 어댑터: 부분/비정상/비-dict 원시 → 예외 없이 PaperResult 목록."""
    out = providers.get_academic_adapter(name).to_paper_results(raw)
    assert isinstance(out, list)
    assert all(isinstance(r, PaperResult) for r in out)


@_PBT
@given(
    web_name=st.sampled_from(providers.available_web_adapters()),
    acad_name=st.sampled_from(providers.available_academic_adapters()),
    err=_error_dict,
)
def test_adapters_on_structured_error_dict_yield_empty(web_name, acad_name, err):
    """egress 구조화 오류 dict → 어댑터가 예외 없이 빈 결과로 흡수(요구사항 13.4)."""
    web_out = providers.get_web_adapter(web_name).to_search_results(err)
    acad_out = providers.get_academic_adapter(acad_name).to_paper_results(err)
    assert web_out == []      # 결과 배열이 없어 창작 없이 빈 목록
    assert acad_out == []


# =========================================================================== #
# (7) 직렬화기/역직렬화기 방어 — normalize.py (요구사항 4.5, P8)
# =========================================================================== #
@_PBT
@given(d=st.one_of(
    st.dictionaries(st.text(max_size=8), _json_values, max_size=6),
    st.none(), st.lists(_json_values, max_size=3), st.text(max_size=12), st.integers(),
))
def test_deserialize_result_never_raises(d):
    """deserialize_result: 임의/비정상 입력 → 예외 없이 정규 결과로 복원/폴백."""
    res = normalize.deserialize_result(d)
    assert isinstance(res, (SearchResult, PaperResult))


@_PBT
@given(x=st.one_of(
    st.none(), st.integers(), st.text(max_size=12),
    st.lists(_json_values, max_size=3),
    st.dictionaries(st.text(max_size=6), _json_values, max_size=4),
))
def test_serialize_result_nonmodel_returns_empty(x):
    """serialize_result: 정규 결과가 아닌 입력 → 예외 없이 빈 dict."""
    assert normalize.serialize_result(x) == {}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
