# Feature: deep-research-engine, Property 2: 중복제거 크기·부분집합 불변식
"""Property 2 — 중복제거 크기·부분집합 불변식 property 테스트.

임의 소스 목록 ``xs`` 에 대해 ``dedup_sources`` 가 다음 4개 불변식을 항상
만족하는지 검증한다(단일 속성, 4개 facet):

    1. 크기       : ``len(dedup_sources(xs)) <= len(xs)``
    2. 부분집합   : 결과의 모든 항목이 입력 ``xs`` 에 존재한다(창작 없음,
                    **객체 정체성**(``is``) 기준).
    3. 키 유일성  : 결과에 동일한 ``source_key`` 를 가진 서로 다른 두 소스가
                    존재하지 않는다.
    4. first-wins : 각 ``source_key`` 에 대해 ``xs`` 에서 **최초로 등장한**
                    소스가 보존되며, 결과 순서는 최초 등장 순서를 따른다.

대상 순수 함수는 ``ai_engine/research/dedup.py`` 의 ``dedup_sources`` /
``source_key`` / ``canonical_url`` / ``canonical_doi`` 이다(재구현 금지).

생성기는 ``SearchResult`` / ``PaperResult`` / ``dict`` 를 혼합하고, 정규화 후
충돌하는 중복 URL/DOI 를 다수 포함하며(작은 풀에서 표집), 빈/``None``/비정상
필드를 포함한다. ``None`` 원소도 포함해 방어성을 함께 확인한다.

Stack: Python 3.11+, hypothesis 라이브러리(기존 ``scripts/test_*_pbt.py`` 관례).

**Validates: Requirements 7.1, 7.2, 7.3**
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
# Property 2 — 크기·부분집합·키 유일성·first-wins (단일 속성, 4 facet)
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(xs=_source_list)
def test_dedup_size_subset_uniqueness_firstwins(xs: list) -> None:
    result = dedup_sources(xs)

    # facet 1 — 크기 불변식: 결과 크기는 입력 크기 이하.
    assert len(result) <= len(xs), (
        f"dedup grew the list: len(result)={len(result)} > len(xs)={len(xs)}"
    )

    # facet 2 — 부분집합(창작 없음): 결과의 모든 항목은 입력에 '동일 객체'로 존재.
    for r in result:
        assert any(r is x for x in xs), (
            "dedup produced a source not present in input (identity check failed)"
        )

    # facet 3 — 키 유일성: 결과에 동일 source_key 가 둘 이상 없다.
    keys = [source_key(r) for r in result]
    assert len(keys) == len(set(keys)), (
        f"duplicate source_key remained in dedup result: {keys}"
    )

    # facet 4 — first-wins + 순서: 각 키의 최초 등장 소스가 보존되고 순서 유지.
    seen: set = set()
    expected: list = []
    for x in xs:
        k = source_key(x)
        if k not in seen:
            seen.add(k)
            expected.append(x)
    assert len(result) == len(expected), (
        f"dedup size mismatch vs first-wins oracle: "
        f"{len(result)} != {len(expected)}"
    )
    for i, (r, e) in enumerate(zip(result, expected)):
        assert r is e, (
            f"dedup did not preserve first occurrence / order at index {i}"
        )


# ---------------------------------------------------------------------------
# 스모크 — 생성기가 실제로 충돌을 만들며 first-wins 가 성립함을 구체 예시로 고정
# (속성 테스트가 공허하게 통과하지 않도록 방어).
# ---------------------------------------------------------------------------

def test_smoke_known_duplicates_collapse_first_wins() -> None:
    # a, b 는 canonical_url 이 동일("https://example.com/page")하므로 하나로 합쳐진다.
    a = SearchResult(url="https://example.com/page")
    b = SearchResult(url="https://EXAMPLE.com/page/?utm_source=x#frag")
    c = SearchResult(url="https://example.com/other")

    # 사전 조건: a 와 b 는 같은 키, c 는 다른 키.
    assert source_key(a) == source_key(b) == "web:" + canonical_url(a.url)
    assert source_key(c) != source_key(a)

    out = dedup_sources([a, b, c])
    assert len(out) == 2                # 3개 입력 → 2개 (b 제거)
    assert out[0] is a and out[1] is c  # first-wins: 최초 등장한 a 보존, 순서 유지

    # DOI 교차 정규화도 하나로 합쳐지는지 확인(SearchResult ↔ PaperResult).
    p1 = PaperResult(doi_or_url="10.1234/abcd")
    p2 = PaperResult(doi_or_url="https://doi.org/10.1234/abcd")
    out2 = dedup_sources([p1, p2])
    assert len(out2) == 1 and out2[0] is p1


# ---------------------------------------------------------------------------
# Driver — 직접 실행(`python scripts/test_research_dedup_invariants_pbt.py`) 지원.
# pytest 로도 test_* 함수가 그대로 수집된다.
# ---------------------------------------------------------------------------

def main() -> int:
    print("Property test: deep-research-engine P2 — dedup 크기·부분집합 불변식")
    print()

    checks = [
        ("smoke: 알려진 중복 축약 + first-wins", test_smoke_known_duplicates_collapse_first_wins),
        ("prop: 크기·부분집합·키 유일성·first-wins", test_dedup_size_subset_uniqueness_firstwins),
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
