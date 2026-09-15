# Feature: deep-research-engine, Property 4: 재랭킹 순열 불변식
"""Property-based test: 재랭킹 순열 불변식 (Property 4).

Feature: deep-research-engine, Property 4: 재랭킹 순열 불변식
**Validates: Requirements 7.5, 7.6**

For any 질의·다중 제공자 순위 리스트·(중복 포함) 소스 집합에 대해,
``research/rank.py`` 의 ``merge_and_rerank(query, per_provider_ranklists, sources,
gw=...)`` 결과는 항상 입력 소스 풀(``dedup_sources(sources)``)의 **순열**이다:
소스 창작·누락·중복이 없다. 이 불변식은 재사용 자산
``rag.reranker.parse_rerank_order`` 의 "항상 [0, n) 유효 인덱스 순열" 규약을
그대로 계승한다(재구현 금지 — 요구사항 7.5/7.6).

네 가지 불변식을 검증한다.
    1. 순열(id 다중집합): 결과의 **객체 정체성**(``id``) 다중집합이
       ``dedup_sources(sources)`` 와 정확히 일치한다(항목 창작·누락 없음).
    2. 중복 없음: 결과에 동일한 ``source_key`` 를 가진 서로 다른 두 항목이 없다
       (순열이므로 자연히 성립하나 명시적으로 확인).
    3. 규약 계승·비차단 폴백: **범위밖/중복/누락/빈 응답**을 내는 악의적 리랭커
       (``gw``) mock 을 주입하거나, ``gw`` 가 ``None``·예외·비동기(코루틴)·``None``
       반환이어도 예외 없이 결정적 순열로 폴백한다.
    4. 순수성: ``merge_and_rerank`` 는 입력 소스 리스트를 변경하지 않는다.

대상 순수 조합 함수는 ``ai_engine/research/rank.py`` 의 ``merge_and_rerank`` 이며,
재사용 자산 ``ai_engine/research/dedup.py``(``dedup_sources``/``source_key``),
``ai_engine/rag/hybrid_search.py``(``rrf_fuse``),
``ai_engine/rag/reranker.py``(``parse_rerank_order``)를 조립한다.

생성기는 (a) 정규화 후 충돌하는 중복 URL/DOI 를 다수 포함하는
``SearchResult``/``PaperResult``/``dict``/``None`` 혼합 소스 목록, (b) 소스 객체·
``sources`` 인덱스(범위밖 포함)·URL/DOI 문자열이 섞인 다중 제공자 순위 리스트,
(c) 범위밖·중복·누락·빈 응답·비동기·예외를 포괄하는 악의적 리랭커 mock 을 함께
자극한다.

Stack: Python 3.11+, hypothesis 라이브러리(기존 ``scripts/test_*_pbt.py`` 관례).
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# 저장소 루트를 import 경로에 추가해 ``ai_engine.research`` 패키지를 로드한다.
# rank.py 가 상대 import(``.config``/``.models``/``.dedup``)를 사용하므로 패키지
# 경로로 import 해야 한다(부작용·자격증명 없는 순수 경로 삽입).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ai_engine.research.dedup import dedup_sources, source_key  # noqa: E402
from ai_engine.research.models import PaperResult, SearchResult  # noqa: E402
from ai_engine.research.rank import merge_and_rerank  # noqa: E402


# ---------------------------------------------------------------------------
# 소스 생성기 — 정규화 후 충돌하는 중복 URL/DOI 를 강제하기 위해 작은 풀에서
# 표집한다(dedup 후 풀 크기가 입력보다 작아지는 경로를 자극).
# ---------------------------------------------------------------------------

# 아래 6개는 모두 canonical_url == "https://example.com/page" 로 수렴한다.
_URL_POOL = [
    "https://example.com/page",
    "https://example.com/page/",              # 말미 슬래시
    "https://EXAMPLE.com/page",               # 호스트 대문자
    "https://example.com/page#section",       # fragment
    "https://example.com/page?utm_source=nl",  # 추적 쿼리
    "https://example.com:443/page",           # 기본 포트
    # --- 위와 다른 정규 키(비충돌) ---
    "https://example.com/other",
    "http://example.com/page",                # 스킴이 달라 별도 키
    "https://example.org/page",               # 호스트가 달라 별도 키
    "example.net/bare-host",                  # 스킴 없는 bare 호스트
    "",                                       # 빈 URL
    "not a valid url",                        # 비정상 입력
]

# 아래 4개는 모두 canonical_doi == "10.1234/abcd" 로 수렴한다.
_DOI_POOL = [
    "10.1234/abcd",
    "https://doi.org/10.1234/abcd",
    "DOI:10.1234/ABCD",
    "doi:10.1234/abcd",
    "10.5555/zzz",                            # 별도 키
    "",
]

# source_id 는 "web:"/"doi:" 프리픽스일 때만 키 산정에 우선 사용된다.
_SID_POOL = [
    "", "", "", "",
    "web:https://example.com/page",
    "doi:10.1234/abcd",
    "garbage-not-a-prefix",
]


def _weird_text() -> st.SearchStrategy:
    """빈/None/제어문자/일반 텍스트를 포함하는 '비정상 필드' 텍스트 생성기."""
    return st.one_of(
        st.none(),
        st.just(""),
        st.text(
            alphabet=st.characters(min_codepoint=0, max_codepoint=0x2FFF),
            max_size=12,
        ),
    )


def _score() -> st.SearchStrategy:
    return st.one_of(
        st.none(),
        st.floats(allow_nan=False, allow_infinity=False, width=32),
        st.integers(min_value=-5, max_value=100),
    )


@st.composite
def _gen_search(draw) -> SearchResult:
    return SearchResult(
        title=draw(_weird_text()),
        url=draw(st.sampled_from(_URL_POOL)),
        snippet=draw(_weird_text()),
        published_date=draw(_weird_text()),
        source_domain=draw(_weird_text()),
        relevance_score=draw(_score()),
        provider=draw(st.sampled_from(["tavily", "exa", "brave", ""])),
        source_id=draw(st.sampled_from(_SID_POOL)),
    )


@st.composite
def _gen_paper(draw) -> PaperResult:
    return PaperResult(
        title=draw(_weird_text()),
        authors=draw(st.one_of(st.none(), st.lists(st.text(max_size=8), max_size=3))),
        year=draw(st.one_of(st.none(), st.integers(min_value=0, max_value=2100))),
        venue=draw(_weird_text()),
        abstract=draw(_weird_text()),
        # DOI 또는 URL 혼합 → 논문↔웹 교차 충돌도 발생시킨다.
        doi_or_url=draw(st.sampled_from(_DOI_POOL + _URL_POOL)),
        citation_count=draw(st.integers(min_value=0, max_value=1000)),
        relevance_score=draw(_score()),
        provider=draw(st.sampled_from(["semantic_scholar", "openalex", ""])),
        source_id=draw(st.sampled_from(_SID_POOL)),
    )


@st.composite
def _gen_dict(draw) -> dict:
    """정규 필드 일부만 담은 dict 소스(부분/비정상 dict)."""
    d: dict = {}
    if draw(st.booleans()):
        d["url"] = draw(st.sampled_from(_URL_POOL))
    if draw(st.booleans()):
        d["doi"] = draw(st.sampled_from(_DOI_POOL))
    if draw(st.booleans()):
        d["doi_or_url"] = draw(st.sampled_from(_DOI_POOL + _URL_POOL))
    if draw(st.booleans()):
        d["source_id"] = draw(st.sampled_from(_SID_POOL))
    if draw(st.booleans()):
        d["title"] = draw(_weird_text())
    return d


# 소스 원소: SearchResult / PaperResult / dict 혼합 + None(방어성).
_element = st.one_of(_gen_search(), _gen_paper(), _gen_dict(), st.none())

# 작은 키 풀 + 최대 20개 원소 → 중복이 빈번하게 발생한다.
_source_list = st.lists(_element, max_size=20)


# ---------------------------------------------------------------------------
# 다중 제공자 순위 리스트 + 소스 목록 동시 생성.
# 순위 리스트 항목은 (a) sources 인덱스(범위밖 포함) int, (b) URL/DOI 문자열,
# (c) 실제 소스 객체 참조가 섞인다 — merge_and_rerank 의 위치 해석 경로를 모두
# 자극한다(pool 밖 항목은 무시되어야 한다).
# ---------------------------------------------------------------------------
@st.composite
def _sources_and_ranklists(draw):
    sources = draw(_source_list)
    n = len(sources)
    item_opts = [
        st.integers(min_value=-3, max_value=n + 5),   # 인덱스(범위밖 포함)
        st.sampled_from(_URL_POOL + _DOI_POOL),        # URL/DOI 문자열
    ]
    if n > 0:
        item_opts.append(st.sampled_from(sources))     # 실제 소스 객체 참조
    ranklists = draw(
        st.lists(st.lists(st.one_of(*item_opts), max_size=8), max_size=4)
    )
    return sources, ranklists


# 질의: 빈/공백/유니코드 포함 임의 문자열(재랭킹 컨텍스트로만 사용).
_query = st.text(
    alphabet=st.characters(min_codepoint=0, max_codepoint=0x2FFF), max_size=24
)


# ---------------------------------------------------------------------------
# 악의적 리랭커 mock — 범위밖/중복/누락/빈 응답을 내는 재랭킹 신호.
# parse_rerank_order 규약이 이를 흡수해 결과가 여전히 순열이어야 한다.
# ---------------------------------------------------------------------------

# 인덱스 토큰(범위밖 포함): parse_rerank_order 가 [0,n) 밖/중복을 무시하는 경로.
_INDEX_TOKEN = st.integers(min_value=-4, max_value=25)

# gw mock 이 반환할 원(raw) 응답. rank._rerank_raw_to_text 가 문자열/리스트/기타를
# 정규화하고 parse_rerank_order 로 파싱한다.
_rerank_raw = st.one_of(
    # 숫자·구두점이 섞인 자유 텍스트(범위밖/중복/누락 자연 발생).
    st.text(alphabet="0123456789[], -xyz\n", max_size=24),
    st.lists(_INDEX_TOKEN, max_size=12),          # 인덱스 리스트(범위밖/중복/누락)
    st.tuples(_INDEX_TOKEN, _INDEX_TOKEN, _INDEX_TOKEN),
    st.just("[]"),                                # 빈 배열 표기
    st.just(""),                                  # 완전 빈 응답
    st.none(),                                    # None 반환 → 폴백
    _INDEX_TOKEN,                                 # 정수 하나(str 경로)
    st.dictionaries(st.integers(-2, 6), st.integers(0, 6), max_size=3),  # dict → str()
)

# gw 형태: 없음/호출가능/메서드 어댑터(order|text|rerank)/예외/비동기(코루틴).
_gw_kind = st.sampled_from(
    ["none", "callable", "method_order", "method_text", "method_rerank",
     "raises", "coroutine"]
)


def _make_gw(kind: str, raw):
    """지정된 형태의 악의적 리랭커 mock 을 만든다.

    - ``none``: gw 미제공(융합·신뢰도 기반 결정적 순서 그대로).
    - ``callable``: ``gw(query, summaries)`` 가 raw 를 반환.
    - ``method_*``: ``rerank_order``/``rerank_text``/``rerank`` 메서드로 raw 반환.
    - ``raises``: 호출 시 예외 → 비차단 폴백.
    - ``coroutine``: 코루틴 반환 → 동기 맥락에서 대기 불가 → 폴백.
    """
    if kind == "none":
        return None
    if kind == "raises":
        def _raise(query, summaries):
            raise RuntimeError("adversarial reranker failure")
        return _raise
    if kind == "coroutine":
        async def _coro(query, summaries, _r=raw):
            return _r
        return _coro
    if kind == "callable":
        return lambda query, summaries, _r=raw: _r
    # 메서드 기반 어댑터(호출 불가 객체 → rank._invoke_reranker 가 메서드 탐색).
    method_name = {
        "method_order": "rerank_order",
        "method_text": "rerank_text",
        "method_rerank": "rerank",
    }[kind]

    class _Adapter:
        pass

    obj = _Adapter()
    setattr(obj, method_name, lambda query, summaries, _r=raw: _r)
    return obj


# ---------------------------------------------------------------------------
# 공통 검증 — 결과가 dedup 풀의 순열이고 중복 키가 없음을 확인.
# ---------------------------------------------------------------------------
def _assert_permutation(pool: list, result: list, ctx: str = "") -> None:
    # facet 1 — 순열(id 다중집합): 창작·누락·중복 없음.
    assert len(result) == len(pool), (
        f"길이 불일치{ctx}: len(result)={len(result)} != len(pool)={len(pool)}"
    )
    assert Counter(id(x) for x in result) == Counter(id(x) for x in pool), (
        f"결과가 dedup_sources(sources) 의 순열이 아님(창작·누락·중복){ctx}"
    )
    # facet 2 — 중복 키 없음(순열이므로 자연 성립하나 명시 확인).
    keys = [source_key(x) for x in result]
    assert len(keys) == len(set(keys)), f"중복 source_key 잔존{ctx}: {keys}"


# ---------------------------------------------------------------------------
# Property 4 — 악의적 gw mock 을 포함한 순열 불변식(단일 속성, 4 facet).
# ---------------------------------------------------------------------------
@settings(max_examples=200)
@given(
    data=_sources_and_ranklists(),
    raw=_rerank_raw,
    kind=_gw_kind,
    query=_query,
)
def test_merge_and_rerank_is_permutation_of_dedup_pool(data, raw, kind, query):
    sources, ranklists = data
    gw = _make_gw(kind, raw)

    before_ids = [id(x) for x in sources]
    pool = dedup_sources(sources)  # 순열 대상 풀(first-wins)

    result = merge_and_rerank(query, ranklists, sources, gw=gw)

    # facet 4 — 순수성: 입력 소스 리스트가 변경되지 않음.
    assert [id(x) for x in sources] == before_ids, "입력 소스 리스트가 변경됨(비순수)"

    # facet 1~3 — 순열·중복없음(악의적 gw 규약 계승/폴백 포함).
    _assert_permutation(pool, result, ctx=f" (gw_kind={kind!r})")


# ---------------------------------------------------------------------------
# 스모크 — 악의적 재랭커가 실제로 재정렬을 수행하며 순열임을 구체 예시로 고정
# (속성 테스트가 공허하게 통과하지 않도록 방어).
# ---------------------------------------------------------------------------
def test_smoke_adversarial_reranker_reorders_yet_permutation() -> None:
    a = SearchResult(title="A", url="https://example.com/a")
    b = SearchResult(title="B", url="https://example.com/b")
    c = SearchResult(title="C", url="https://example.com/c")
    srcs = [a, b, c]
    pool = dedup_sources(srcs)
    assert len(pool) == 3  # 사전조건: 세 소스는 서로 다른 정규 키.

    # (1) 누락 인덱스만 내는 mock: parse_rerank_order("[1]", 3) == [1, 0, 2]
    #     → 실제 재정렬 발생(첫 항목이 b 로 이동), 결과는 여전히 순열.
    out = merge_and_rerank("q", [[0, 1, 2]], srcs, gw=lambda q, s: "[1]")
    assert Counter(id(x) for x in out) == Counter(id(x) for x in pool)
    assert out[0] is b, "누락 인덱스 mock 이 실제 재정렬을 수행하지 않음(공허 통과)"

    # (2) 범위밖 + 중복 인덱스 mock: 모두 무시·보강되어 전 위치 순열로 복원.
    out2 = merge_and_rerank("q", [[a, b, c]], srcs, gw=lambda q, s: "[99, 0, 0, -5]")
    assert Counter(id(x) for x in out2) == Counter(id(x) for x in pool)

    # (3) 인덱스 리스트를 그대로 반환하는 mock(문자열 아님) — str 정규화 경로.
    out3 = merge_and_rerank("q", [[2, 1, 0]], srcs, gw=lambda q, s: [2, 2, 7, -1])
    assert Counter(id(x) for x in out3) == Counter(id(x) for x in pool)


def test_smoke_duplicate_sources_collapse_before_rerank() -> None:
    # a, b 는 canonical_url 이 동일 → dedup 후 풀은 2개. 재랭킹 결과도 2개 순열.
    a = SearchResult(title="A", url="https://example.com/page")
    b = SearchResult(title="B", url="https://EXAMPLE.com/page/?utm_source=x#f")
    c = SearchResult(title="C", url="https://example.com/other")
    srcs = [a, b, c]
    pool = dedup_sources(srcs)
    assert len(pool) == 2 and pool[0] is a and pool[1] is c  # first-wins

    for gw in (None, lambda q, s: "[1, 0]", lambda q, s: "", lambda q, s: None):
        out = merge_and_rerank("q", [[a, b, c], [1, 0]], srcs, gw=gw)
        assert len(out) == 2
        assert Counter(id(x) for x in out) == Counter(id(x) for x in pool)


# ---------------------------------------------------------------------------
# Driver — 직접 실행(`python scripts/test_research_rerank_permutation_pbt.py`).
# pytest 로도 test_* 함수가 그대로 수집된다.
# ---------------------------------------------------------------------------
def main() -> int:
    print("Property test: deep-research-engine P4 — 재랭킹 순열 불변식")
    print()

    checks = [
        ("smoke: 악의적 재랭커 재정렬 + 순열",
         test_smoke_adversarial_reranker_reorders_yet_permutation),
        ("smoke: 중복 소스 축약 후 재랭킹 순열",
         test_smoke_duplicate_sources_collapse_before_rerank),
        ("prop: 순열·중복없음·폴백·순수성(악의적 gw)",
         test_merge_and_rerank_is_permutation_of_dedup_pool),
    ]
    failures = []
    for label, fn in checks:
        print(f"[run] {label} ...", end=" ", flush=True)
        try:
            fn()
            print("OK")
        except Exception as e:  # noqa: BLE001
            print("FAIL")
            failures.append((label, e))

    print()
    if failures:
        print(f"FAILED: {len(failures)} of {len(checks)} checks")
        for label, e in failures:
            print(f"  - {label}: {e}")
        return 1
    print(f"PASSED: all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
