# Feature: deep-research-engine, Property 5: 커버리지 단조성(준동형)
"""Property 5 — 커버리지 단조성(준동형) property 테스트.

임의 소스 목록 ``A``, ``B`` 에 대해, 소스·제공자를 추가로 병합해도 고유 소스
커버리지(중복제거 후 개수)는 **감소하지 않는다**. 즉 ``A`` 는 ``A + B`` 의
접두(prefix)이므로 고유 키 집합이 단조 증가하여 다음이 항상 성립한다:

    len(dedup_sources(A + B)) >= len(dedup_sources(A))      (주 속성 — 단조성)

이 준동형 불변식을 3개 facet 으로 함께 검증한다(단일 속성):

    1. 단조(좌)  : ``len(dedup(A + B)) >= len(dedup(A))``
    2. 단조(우)  : ``len(dedup(A + B)) >= len(dedup(B))``
                   (병합은 교환적으로 어느 쪽 커버리지도 줄이지 않는다)
    3. 합집합 상한: ``len(dedup(A + B)) <= len(dedup(A)) + len(dedup(B))``
                   (병합 커버리지는 각 커버리지 합을 넘지 않는다 — 고유 키는
                    두 집합의 합집합이므로 창작이 없다)

대상 순수 함수는 ``ai_engine/research/dedup.py`` 의 ``dedup_sources`` /
``source_key`` / ``canonical_url`` / ``canonical_doi`` 이다(재구현 금지).

생성기는 ``A`` 와 ``B`` 를 **각각** ``SearchResult`` / ``PaperResult`` /
``dict`` 를 혼합한 목록으로 만들고, 정규화 후 충돌하는 중복 URL/DOI 를
공유 풀에서 표집해 (a) 리스트 내부 중복, (b) A↔B 교차 중복을 모두 유발한다.
빈/``None``/비정상 필드도 포함해 방어성을 함께 확인한다.

Stack: Python 3.11+, hypothesis 라이브러리(기존 ``scripts/test_*_pbt.py`` 관례).

**Validates: Requirements 5, 9**
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
# 생성기 — 정규화 후 충돌하는 중복 URL/DOI 를 공유 풀에서 표집한다.
# A 와 B 가 같은 풀을 공유하므로 리스트 내부 중복과 A↔B 교차 중복이 함께 발생한다.
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

# 작은 키 풀 + 최대 20개 원소 → 리스트 내부/교차 중복이 빈번하게 발생한다.
_source_list = st.lists(_element, max_size=20)


# ---------------------------------------------------------------------------
# Property 5 — 커버리지 단조성(준동형) (단일 속성, 3 facet)
# ---------------------------------------------------------------------------

@settings(max_examples=200)
@given(a=_source_list, b=_source_list)
def test_coverage_is_monotonic_under_merge(a: list, b: list) -> None:
    cov_a = len(dedup_sources(a))
    cov_b = len(dedup_sources(b))
    cov_ab = len(dedup_sources(a + b))

    # facet 1 — 단조(좌): B 를 추가 병합해도 A 커버리지는 감소하지 않는다.
    assert cov_ab >= cov_a, (
        f"coverage decreased after merging B: "
        f"len(dedup(A+B))={cov_ab} < len(dedup(A))={cov_a}"
    )

    # facet 2 — 단조(우): 병합은 교환적으로 B 커버리지도 줄이지 않는다.
    assert cov_ab >= cov_b, (
        f"coverage decreased after merging A: "
        f"len(dedup(A+B))={cov_ab} < len(dedup(B))={cov_b}"
    )

    # facet 3 — 합집합 상한: 병합 커버리지는 각 커버리지 합을 넘지 않는다
    # (고유 키는 두 집합의 합집합이므로 소스 창작이 없다).
    assert cov_ab <= cov_a + cov_b, (
        f"merged coverage exceeded sum of parts (source fabrication): "
        f"len(dedup(A+B))={cov_ab} > {cov_a}+{cov_b}={cov_a + cov_b}"
    )


# ---------------------------------------------------------------------------
# 스모크 — 생성기가 실제로 3가지 경계(부분겹침/완전겹침/서로소)를 만들고
# 단조성이 각 경계에서 성립함을 구체 예시로 고정(공허한 통과 방어).
# ---------------------------------------------------------------------------

def test_smoke_partial_overlap_increases_coverage() -> None:
    # A: 같은 키 2개(중복) → dedup 후 1개.
    a = [
        SearchResult(url="https://example.com/page"),
        SearchResult(url="https://EXAMPLE.com/page/?utm_source=x#frag"),
    ]
    # B: A 와 같은 키 1개(교차 중복) + 새로운 키 1개.
    b = [
        SearchResult(url="https://example.com/page"),   # A 와 충돌
        SearchResult(url="https://example.com/other"),  # 신규 키
    ]
    assert len(dedup_sources(a)) == 1
    assert len(dedup_sources(b)) == 2
    merged = dedup_sources(a + b)
    # 병합 고유 키 = {page, other} → 2개. 단조성: 2 >= 1.
    assert len(merged) == 2
    assert len(merged) >= len(dedup_sources(a))


def test_smoke_full_overlap_keeps_coverage_equal() -> None:
    # B 가 A 와 완전히 같은 키만 가지면 커버리지는 동일(비감소의 등호 경계).
    a = [SearchResult(url="https://example.com/page")]
    b = [SearchResult(url="https://example.com/page/#dup")]  # 같은 canonical_url
    assert source_key(b[0]) == "web:" + canonical_url(a[0].url)
    assert len(dedup_sources(a + b)) == len(dedup_sources(a)) == 1


def test_smoke_disjoint_reaches_union_upper_bound() -> None:
    # 서로소 키면 병합 커버리지 == 각 커버리지의 합(합집합 상한 등호 경계).
    a = [PaperResult(doi_or_url="10.1234/abcd")]
    b = [PaperResult(doi_or_url="10.5555/zzz")]
    assert len(dedup_sources(a + b)) == len(dedup_sources(a)) + len(dedup_sources(b)) == 2


# ---------------------------------------------------------------------------
# Driver — 직접 실행(`python scripts/test_research_coverage_monotonic_pbt.py`) 지원.
# pytest 로도 test_* 함수가 그대로 수집된다.
# ---------------------------------------------------------------------------

def main() -> int:
    print("Property test: deep-research-engine P5 — 커버리지 단조성(준동형)")
    print()

    checks = [
        ("smoke: 부분 겹침 → 커버리지 증가", test_smoke_partial_overlap_increases_coverage),
        ("smoke: 완전 겹침 → 커버리지 동일(등호 경계)", test_smoke_full_overlap_keeps_coverage_equal),
        ("smoke: 서로소 → 합집합 상한 도달", test_smoke_disjoint_reaches_union_upper_bound),
        ("prop: len(dedup(A+B)) >= len(dedup(A)) (+ 대칭/상한)", test_coverage_is_monotonic_under_merge),
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
