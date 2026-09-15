# Feature: deep-research-engine, Property 7: 관련성 정렬(결정적 내림차순)
"""Property-based test: 관련성 정렬의 결정적 내림차순 (Property 7).

Feature: deep-research-engine, Property 7: 관련성 정렬(결정적 내림차순)
**Validates: Requirements 1.4, 1.5, 2.3, 2.4**

For any 검색 결과 목록에 대해, ``research/rank.py`` 의 관련성 정렬은 다음을 만족한다.

- 웹(``sort_by_relevance_web``): 관련성 점수 내림차순. 동점은 제공자 반환(입력)
  순서 보존(요구사항 1.4/1.5).
- 논문(``sort_by_relevance_papers``): 관련성 내림차순 → 동점 시 피인용수 내림차순
  → 그래도 동점이면 제공자 반환(입력) 순서 보존(요구사항 2.3/2.4).

세 가지 불변식을 검증한다.
    1. 인접쌍 내림차순: 정렬 결과의 모든 인접쌍 ``(a, b)`` 에 대해 정렬 키가
       ``key(a) >= key(b)`` 이다(전체 내림차순과 동치).
    2. 동점 tie-break 결정성: 같은 입력을 2회 정렬하면 결과가 동일하고, 동점(정렬
       키 동일) 그룹 내에서는 입력 상대 순서가 보존된다(안정 정렬).
    3. 순열(순수·항목 보존): 결과는 입력의 순열이며(항목 창작·누락 없음), 정렬은
       입력 리스트를 변경하지 않는다(순수 함수).

정렬 키 정규화는 rank.py 의 문서화된 계약(결측 ``None``·비수치·``NaN`` → 정렬
가능한 기본값 ``0.0``)을 그대로 미러링한다(``_norm``). 생성기는 동점 다수, 결측
(``None``/``0.0``), 음수, ``NaN``/``inf``, 수치/비수치 문자열을 포함해 tie-break 와
결측 처리 경로를 함께 자극한다.

Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import math
import os
import sys
from collections import Counter

# 저장소 루트를 import 경로에 추가해 ``ai_engine.research`` 패키지를 로드한다.
# rank.py 가 상대 import(``.config``/``.models``)를 사용하므로 패키지 경로로
# import 해야 한다(부작용·자격증명 없는 순수 경로 삽입).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.research.models import PaperResult, SearchResult  # noqa: E402
from ai_engine.research.rank import (  # noqa: E402
    sort_by_relevance_papers,
    sort_by_relevance_web,
)


# ---------------------------------------------------------------------------
# 정렬 키 계약(rank._as_sort_number 미러) — 구현이 아닌 명세를 반영한다.
# 결측(None)·비수치·NaN 은 정렬 가능한 기본값 0.0 으로 취급한다(P7/P8).
# ---------------------------------------------------------------------------
def _norm(value) -> float:
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(f) else f


# ---------------------------------------------------------------------------
# 생성기(strategies)
# ---------------------------------------------------------------------------
# 점수 유사 값: 작은 표본으로 동점을 자주 만들고, 결측/음수/NaN/inf/문자열을 섞어
# 결측 처리 경로(P8)와 tie-break 결정성을 함께 자극한다.
_SCORE_LIKE = st.one_of(
    st.sampled_from([0.0, 1.0, 2.0, 2.0, 5.0, -1.0, -3.0]),  # 잦은 동점 + 음수
    st.floats(allow_nan=True, allow_infinity=True),           # NaN/inf/임의 실수
    st.integers(min_value=-5, max_value=5),                    # 정수 점수
    st.none(),                                                 # 결측(None → 0.0)
    st.sampled_from(["1.5", "2", "0", "", "abc", "-1"]),      # 수치/비수치 문자열
)

# 피인용수 유사 값: 논문 2차 정렬 키. 동점을 자주 만들고 결측/음수/문자열 포함.
_CITE_LIKE = st.one_of(
    st.sampled_from([0, 1, 3, 3, 10, -2]),
    st.integers(min_value=-5, max_value=5),
    st.none(),
    st.sampled_from(["5", "0", "", "x"]),
)

_web_scores = st.lists(_SCORE_LIKE, min_size=0, max_size=14)
_paper_specs = st.lists(st.tuples(_SCORE_LIKE, _CITE_LIKE), min_size=0, max_size=14)


def _build_web(scores):
    """점수 리스트 → 고유 식별 가능한 SearchResult 리스트(입력 순서 = index)."""
    return [
        SearchResult(title=f"r{i}", provider=f"p{i}", relevance_score=s)
        for i, s in enumerate(scores)
    ]


def _build_papers(specs):
    """(점수, 피인용수) 리스트 → 고유 식별 가능한 PaperResult 리스트."""
    return [
        PaperResult(
            title=f"r{i}", provider=f"p{i}", relevance_score=s, citation_count=c
        )
        for i, (s, c) in enumerate(specs)
    ]


def _assert_permutation_and_purity(original, ordered):
    """결과가 입력의 순열이고(항목 창작·누락 없음), 입력이 불변임을 검증한다."""
    # 순열: 같은 객체 집합(중복 다중집합까지 동일)을 id 기준으로 대조.
    assert len(ordered) == len(original), (
        f"길이 불일치: in={len(original)} out={len(ordered)}"
    )
    assert Counter(id(x) for x in ordered) == Counter(id(x) for x in original), (
        "결과가 입력의 순열이 아님(항목 창작·누락·중복 변화)"
    )


# ---------------------------------------------------------------------------
# 웹: sort_by_relevance_web — 요구사항 1.4/1.5
# ---------------------------------------------------------------------------
@settings(max_examples=200)
@given(scores=_web_scores)
def test_web_relevance_sort_descending_stable_permutation(scores):
    results = _build_web(scores)
    before_ids = [id(r) for r in results]

    ordered = sort_by_relevance_web(results)

    # (순수) 입력 리스트가 변경되지 않음.
    assert [id(r) for r in results] == before_ids, "입력 리스트가 변경됨(비순수)"
    # (3) 순열·항목 보존.
    _assert_permutation_and_purity(results, ordered)

    in_index = {id(r): i for i, r in enumerate(results)}
    for a, b in zip(ordered, ordered[1:]):
        ka, kb = _norm(a.relevance_score), _norm(b.relevance_score)
        # (1) 인접쌍 내림차순.
        assert ka >= kb, f"내림차순 위반: {ka} < {kb}"
        # (2) 동점 그룹 내 입력 상대 순서 보존(안정 정렬).
        if ka == kb:
            assert in_index[id(a)] < in_index[id(b)], (
                f"동점 tie-break 불안정: 입력순서 {in_index[id(a)]} !< {in_index[id(b)]}"
            )

    # (2) 결정성: 같은 입력 2회 정렬 결과 동일.
    assert [r.title for r in sort_by_relevance_web(results)] == [
        r.title for r in ordered
    ], "동일 입력 재정렬 결과가 비결정적"


# ---------------------------------------------------------------------------
# 논문: sort_by_relevance_papers — 요구사항 2.3/2.4
# ---------------------------------------------------------------------------
@settings(max_examples=200)
@given(specs=_paper_specs)
def test_paper_relevance_sort_descending_stable_permutation(specs):
    results = _build_papers(specs)
    before_ids = [id(r) for r in results]

    ordered = sort_by_relevance_papers(results)

    # (순수) 입력 리스트가 변경되지 않음.
    assert [id(r) for r in results] == before_ids, "입력 리스트가 변경됨(비순수)"
    # (3) 순열·항목 보존.
    _assert_permutation_and_purity(results, ordered)

    in_index = {id(r): i for i, r in enumerate(results)}
    for a, b in zip(ordered, ordered[1:]):
        # 1차 관련성 내림차순 → 2차 피인용수 내림차순(튜플 사전식 비교).
        ka = (_norm(a.relevance_score), _norm(a.citation_count))
        kb = (_norm(b.relevance_score), _norm(b.citation_count))
        # (1) 인접쌍 내림차순(관련성 → 피인용수).
        assert ka >= kb, f"내림차순 위반(관련성→피인용수): {ka} < {kb}"
        # (2) 완전 동점(관련성·피인용수 모두 동일) 그룹 내 입력 순서 보존.
        if ka == kb:
            assert in_index[id(a)] < in_index[id(b)], (
                f"동점 tie-break 불안정: 입력순서 {in_index[id(a)]} !< {in_index[id(b)]}"
            )

    # (2) 결정성: 같은 입력 2회 정렬 결과 동일.
    assert [r.title for r in sort_by_relevance_papers(results)] == [
        r.title for r in ordered
    ], "동일 입력 재정렬 결과가 비결정적"


if __name__ == "__main__":  # 편의 실행 경로
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
