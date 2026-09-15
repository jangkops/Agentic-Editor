# Feature: deep-research-engine, Property 3: 중복제거 멱등성
"""Property 3 — 중복제거 멱등성 property 테스트.

임의 소스 목록 ``xs`` 에 대해 ``dedup_sources`` 가 **멱등(idempotent)** 임을
검증한다:

    dedup_sources(dedup_sources(xs)) == dedup_sources(xs)

여기서 "동등"은 리스트 길이뿐 아니라 **객체 정체성(``is``)과 순서까지** 동일함을
의미한다. 즉 한 번 중복 제거한 결과를 다시 중복 제거해도 아무 항목이 제거되거나
재배치되지 않는다. 이는 first-wins 규약(각 ``source_key`` 의 최초 등장 소스만
보존, 등장 순서 유지)의 직접적 귀결이다 — 첫 적용 결과는 이미 키가 유일하므로
두 번째 적용에서 제거할 대상이 없다.

대상 순수 함수는 ``ai_engine/research/dedup.py`` 의 ``dedup_sources`` /
``source_key`` / ``canonical_url`` / ``canonical_doi`` 이다(재구현 금지).

생성기는 ``SearchResult`` / ``PaperResult`` / ``dict`` 를 혼합하고, 정규화 후
충돌하는 중복 URL/DOI 를 다수 포함하며(작은 풀에서 표집 → 첫 dedup 이 실제로
항목을 제거하게 만들어 멱등성 검증이 공허하지 않게 한다), 빈/``None``/제어문자
필드와 ``None`` 원소(방어성)를 포함한다.

Stack: Python 3.11+, hypothesis 라이브러리(기존 ``scripts/test_*_pbt.py`` 관례).

**Validates: Requirements 7.4**
"""
from __future__ import annotations

import sys
from pathlib import Path

# 스크립트를 직접 실행할 때 ai_engine 패키지를 import 가능하게 한다.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ai_engine.research.dedup import (  # noqa: E402
    canonical_url,
    dedup_sources,
    source_key,
)
from ai_engine.research.models import PaperResult, SearchResult  # noqa: E402


# ---------------------------------------------------------------------------
# 생성기 — 정규화 후 충돌하는 중복 URL/DOI 를 강제하기 위해 작은 풀에서 표집한다.
# 작은 풀 + 다수 원소 → 첫 dedup 이 실제로 항목을 제거하므로 멱등성 검증이
# 공허하게 통과하지 않는다.
# ---------------------------------------------------------------------------

# 아래 6개는 모두 canonical_url == "https://example.com/page" 로 수렴한다
# (말미 슬래시·호스트 대문자·fragment·utm 추적쿼리·기본 포트 443 제거).
_URL_POOL = [
    "https://example.com/page",
    "https://example.com/page/",              # 말미 슬래시
    "https://EXAMPLE.com/page",               # 호스트 대문자
    "https://example.com/page#section",       # fragment
    "https://example.com/page?utm_source=nl",  # 추적 쿼리
    "https://example.com:443/page",           # 기본 포트
    # --- 위와 다른 정규 키를 갖는 항목들(비충돌) ---
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
    "https://doi.org/10.1234/abcd",           # doi.org 프리픽스
    "DOI:10.1234/ABCD",                       # 대소문자 + doi: 프리픽스
    "doi:10.1234/abcd",
    # --- 별도 키 / 비정상 ---
    "10.5555/zzz",
    "",
]

# source_id 는 "web:"/"doi:" 프리픽스일 때만 키 산정에 우선 사용된다.
# 대부분 빈 문자열로 두어 url/doi 경로를 타게 하고, 일부만 override 한다.
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
        # DOI 또는 URL 을 혼합 → 논문↔웹 교차 충돌도 발생시킨다.
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

# 작은 키 풀 + 최대 25개 원소 → 중복이 빈번하게 발생한다.
_source_list = st.lists(_element, max_size=25)


# ---------------------------------------------------------------------------
# Property 3 — 멱등성: dedup_sources(dedup_sources(xs)) == dedup_sources(xs)
#              (객체 정체성·순서까지 동일)
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(xs=_source_list)
def test_dedup_is_idempotent(xs: list) -> None:
    once = dedup_sources(xs)
    twice = dedup_sources(once)

    # 멱등성: 두 번 적용한 결과는 한 번 적용한 결과와 길이가 같아야 한다.
    assert len(twice) == len(once), (
        f"dedup not idempotent (size changed on 2nd apply): "
        f"len(twice)={len(twice)} != len(once)={len(once)}"
    )

    # 멱등성(강): 원소별 객체 정체성(is)과 순서까지 완전히 동일해야 한다.
    for i, (t, o) in enumerate(zip(twice, once)):
        assert t is o, (
            f"dedup not idempotent at index {i}: 2nd apply changed identity/order"
        )

    # 안정성 근거: 첫 적용 결과는 이미 키가 유일하므로 두 번째 적용이 아무것도
    # 제거하지 않는다(멱등성의 구조적 원인을 함께 고정).
    keys_once = [source_key(r) for r in once]
    assert len(keys_once) == len(set(keys_once)), (
        f"dedup(once) still contained duplicate keys: {keys_once}"
    )


# ---------------------------------------------------------------------------
# 스모크 — 생성기가 실제로 충돌을 만들며(첫 dedup 이 항목을 제거) 그 뒤 멱등이
# 성립함을 구체 예시로 고정(속성 테스트가 공허하게 통과하지 않도록 방어).
# ---------------------------------------------------------------------------

def test_smoke_idempotent_after_real_collapse() -> None:
    # a, b 는 canonical_url 이 동일하므로 첫 dedup 에서 b 가 제거된다.
    a = SearchResult(url="https://example.com/page")
    b = SearchResult(url="https://EXAMPLE.com/page/?utm_source=x#frag")
    c = SearchResult(url="https://example.com/other")
    # p1, p2 는 DOI 정규화가 동일 → 하나로 합쳐진다.
    p1 = PaperResult(doi_or_url="10.1234/abcd")
    p2 = PaperResult(doi_or_url="https://doi.org/10.1234/abcd")

    xs = [a, b, c, p1, p2, None, None]

    once = dedup_sources(xs)
    # 사전 조건: 첫 적용이 실제로 항목을 제거해야 멱등성 검증이 의미를 갖는다.
    assert len(once) < len(xs), "smoke fixture failed to trigger any dedup"
    # a, c, p1, None → 4개 (b·p2 제거, None 은 'web:' 단일 키로 1개만 보존).
    assert [r for r in once] == [a, c, p1, None]

    twice = dedup_sources(once)
    # 멱등: 두 번째 적용은 아무것도 바꾸지 않는다(정체성·순서 동일).
    assert len(twice) == len(once)
    assert all(t is o for t, o in zip(twice, once))

    # 삼중 적용까지도 안정함을 확인(고정점).
    thrice = dedup_sources(twice)
    assert all(x is y for x, y in zip(thrice, once))

    # source_key 가 기대대로 수렴하는지(생성기 전제) 확인.
    assert source_key(a) == source_key(b) == "web:" + canonical_url(a.url)
    assert source_key(p1) == source_key(p2) == "doi:10.1234/abcd"


def test_smoke_empty_and_none_idempotent() -> None:
    # 엣지: 빈 입력·None 입력·비이터러블은 [] 로 폴백되고 멱등하다.
    assert dedup_sources([]) == []
    assert dedup_sources(dedup_sources([])) == []
    assert dedup_sources(None) == []
    assert dedup_sources(dedup_sources(None)) == []


# ---------------------------------------------------------------------------
# Driver — 직접 실행(`python scripts/test_research_dedup_idempotent_pbt.py`) 지원.
# pytest 로도 test_* 함수가 그대로 수집된다.
# ---------------------------------------------------------------------------

def main() -> int:
    print("Property test: deep-research-engine P3 — dedup 멱등성")
    print()

    checks = [
        ("smoke: 실제 축약 후 멱등(정체성·순서)", test_smoke_idempotent_after_real_collapse),
        ("smoke: 빈/None 입력 멱등", test_smoke_empty_and_none_idempotent),
        ("prop: dedup(dedup(xs)) == dedup(xs)", test_dedup_is_idempotent),
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
