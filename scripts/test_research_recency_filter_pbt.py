# Feature: deep-research-engine, Property 12: 최신성 필터(준동형)
"""Property-based test: 최신성 필터의 준동형·결정성 (Property 12).

Feature: deep-research-engine, Property 12: 최신성 필터(준동형)
**Validates: Requirements 9.2, 9.3**

For any 검색 결과 목록과 최신성 창 ``window`` 에 대해, ``research/rank.py`` 의
``apply_recency`` 는 다음을 만족한다.

1. (창 이내 — 준동형) 창이 해석되면(cutoff 존재), 결과에서 발행일이 확정된 모든
   항목의 발행일은 ``>= cutoff`` 이다. 경계값(발행일 == cutoff 정각)은 포함된다.
2. (미상 일관 처리) 발행일 미상 항목은 구성된 단일 규칙으로 일관 처리된다.
   - ``exclude``: 결과에 미상 항목이 하나도 없다.
   - ``last``: 모든 미상 항목이 결과 말미에 입력 순서를 유지한 채 배치되고, 미상
     항목 뒤에 확정 발행일 항목이 오지 않는다.
3. (결정성) 동일 입력에 대해 항상 동일한 출력을 산출하며(반복 실행 동일), 결과를
   같은 인자로 다시 필터해도 동일하다(재정렬 안정 = 멱등).
4. (순수) 입력 리스트·항목을 변경하지 않는다.

또한 확정 항목의 정렬 순서(발행일 내림차순 → 관련성 내림차순 → 입력 순서 보존)를
함께 검증한다.

생성기는 발행일 미상, 경계값(cutoff 정각·±1초·±일), 다양한 window(양의 일수·숫자
문자열·ISO 기준일·datetime/date 객체·None·해석 불가 문자열), 동점 관련성을 포함해
필터·정렬·미상 처리 경로를 함께 자극한다. 테스트 주입용 고정 ``now`` 로 상대 일수
창의 cutoff 를 결정적으로 만든다(결정성).

Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime, timedelta, timezone

# 저장소 루트를 import 경로에 추가해 ``ai_engine.research`` 패키지를 로드한다.
# rank.py 가 상대 import(``.config``/``.models``)를 사용하므로 패키지 경로로
# import 해야 한다(부작용·자격증명 없는 순수 경로 삽입).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.research.config import DeepResearchConfig  # noqa: E402
from ai_engine.research.models import SearchResult  # noqa: E402
from ai_engine.research.rank import apply_recency  # noqa: E402


# 고정 기준 시각(now) — 상대 일수 창의 cutoff 를 결정적으로 만든다(테스트 주입용).
_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 명세 계약 미러 (구현 코드가 아닌 문서화된 계약을 반영한다)
# ---------------------------------------------------------------------------
def _norm(value) -> float:
    """관련성 정렬 키 정규화 계약(rank._as_sort_number 미러): None/비수치/NaN → 0.0."""
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(f) else f


def _expected_rule(rule_raw) -> str:
    """미상 처리 규칙 정규화 계약(rank._resolve_unknown_rule 미러).

    허용값(exclude|last) 외에는 config 기본값(recency_unknown="last")으로 폴백한다.
    """
    val = str(rule_raw or "").strip().lower()
    if val in ("exclude", "last"):
        return val
    return DeepResearchConfig().recency_unknown


# ---------------------------------------------------------------------------
# 생성기(strategies)
# ---------------------------------------------------------------------------
# 관련성 점수: 작은 표본으로 동점을 자주 만들어 tie-break 결정성을 자극한다.
_SCORE = st.sampled_from([0.0, 1.0, 1.0, 2.0, 5.0, -1.0])

# 발행일 미상 마커: 모두 확정 불가(파싱 실패 → "미상"). None 은 published_date=None 경로.
_UNKNOWN_MARKER = st.sampled_from(["", "  ", None, "unknown", "N/A", "tbd", "sometime"])

# window/cutoff 동시 생성용 date/datetime (마이크로초 0, 시간대 UTC).
_BOUNDED_DATE = st.dates(min_value=date(2010, 1, 1), max_value=date(2035, 12, 31))
_BOUNDED_DT = st.datetimes(
    min_value=datetime(2010, 1, 1),
    max_value=datetime(2035, 12, 31),
    timezones=st.just(timezone.utc),
).map(lambda d: d.replace(microsecond=0))


@st.composite
def _window_and_cutoff(draw):
    """window 값과 그에 대응하는 cutoff(명세 계약대로)를 함께 생성한다.

    window 형태별로 cutoff 를 동시 산출하므로, 검증 측이 구현을 재계산하지 않고도
    "창 이내(>= cutoff)"를 확인할 수 있다. cutoff 가 ``None`` 이면 "창 없음"(전체 통과).
    """
    kind = draw(
        st.sampled_from(
            ["none", "days_int", "days_str", "datetime", "date", "iso_str", "invalid"]
        )
    )
    if kind == "none":
        return None, None
    if kind == "days_int":
        n = draw(st.integers(min_value=1, max_value=3650))
        return n, _NOW - timedelta(days=n)
    if kind == "days_str":
        # 소수점 포함 문자열 → 날짜 파싱은 확실히 실패하고 일수(float)로 해석된다.
        n = draw(st.integers(min_value=1, max_value=3650))
        days = n + (0.5 if draw(st.booleans()) else 0.0)
        return f"{days}", _NOW - timedelta(days=days)
    if kind == "datetime":
        d = draw(_BOUNDED_DT)
        return d, d  # aware UTC datetime → cutoff 자체
    if kind == "date":
        d = draw(_BOUNDED_DATE)
        return d, datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if kind == "iso_str":
        d = draw(_BOUNDED_DATE)
        return d.isoformat(), datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    # invalid: 해석 불가 문자열 → 창 없음(cutoff None) 으로 폴백.
    return draw(st.sampled_from(["", "   ", "not-a-date", "0", "-5", "abc"])), None


def _known_dt(draw, cutoff):
    """확정 발행일 datetime 생성. cutoff 이 있으면 경계값(정각·±1초·±일)을 자극한다."""
    if cutoff is None:
        return draw(_BOUNDED_DT)
    kind = draw(
        st.sampled_from(
            ["exact", "just_after", "just_before", "after_days", "before_days"]
        )
    )
    if kind == "exact":
        return cutoff  # 경계값 정각 (>= 이므로 포함되어야 한다)
    if kind == "just_after":
        return cutoff + timedelta(seconds=1)
    if kind == "just_before":
        return cutoff - timedelta(seconds=1)
    if kind == "after_days":
        return cutoff + timedelta(days=draw(st.integers(min_value=1, max_value=120)))
    return cutoff - timedelta(days=draw(st.integers(min_value=1, max_value=120)))


@st.composite
def _recency_case(draw):
    """(window, cutoff, rule_raw, results, meta) 케이스를 생성한다.

    ``results`` 는 ``SearchResult`` 목록이고, ``meta[i] = (is_known, dt|None)`` 로 각
    항목의 확정 발행일 여부와 그 값을 보관해 검증에 사용한다(발행일 파싱을 테스트가
    재구현하지 않도록 생성 시점의 진실을 그대로 들고 다닌다).
    """
    window, cutoff = draw(_window_and_cutoff())
    rule_raw = draw(
        st.sampled_from(["exclude", "last", "", None, "LAST", "Exclude", "weird"])
    )
    n = draw(st.integers(min_value=0, max_value=12))

    results: list[SearchResult] = []
    meta: list[tuple[bool, datetime | None]] = []
    for i in range(n):
        score = draw(_SCORE)
        if draw(st.booleans()):  # known — 확정 발행일(ISO 문자열 round-trip)
            dt = _known_dt(draw, cutoff)
            results.append(
                SearchResult(
                    title=f"r{i}",
                    provider=f"p{i}",
                    published_date=dt.isoformat(),
                    relevance_score=score,
                )
            )
            meta.append((True, dt))
        else:  # unknown — 발행일 미상(파싱 불가 마커)
            marker = draw(_UNKNOWN_MARKER)
            results.append(
                SearchResult(
                    title=f"r{i}",
                    provider=f"p{i}",
                    published_date=marker,  # None/""/파싱불가 → 미상
                    relevance_score=score,
                )
            )
            meta.append((False, None))
    return window, cutoff, rule_raw, results, meta


# ---------------------------------------------------------------------------
# Property 12: apply_recency — 창 이내(준동형) + 미상 일관 처리 + 결정성 + 순수
# ---------------------------------------------------------------------------
@settings(max_examples=200)
@given(case=_recency_case())
def test_apply_recency_window_bound_unknown_rule_and_determinism(case):
    window, cutoff, rule_raw, results, meta = case
    rule = _expected_rule(rule_raw)

    before_ids = [id(r) for r in results]
    by_id = {id(r): meta[i] for i, r in enumerate(results)}
    in_index = {id(r): i for i, r in enumerate(results)}

    ordered = apply_recency(results, window, rule_raw, now=_NOW)

    # (4) 순수: 입력 리스트·항목 순서가 변경되지 않는다.
    assert [id(r) for r in results] == before_ids, "입력 리스트가 변경됨(비순수)"

    # (무창작) 결과의 모든 항목은 입력에 존재하고 중복이 없다.
    result_ids = [id(r) for r in ordered]
    assert set(result_ids) <= set(before_ids), "결과에 입력에 없는 항목이 존재(창작)"
    assert len(result_ids) == len(set(result_ids)), "결과에 중복 항목 존재"

    # 결과 항목을 입력 태그(meta) 기준으로 known/unknown 으로 분리한다.
    result_known = [r for r in ordered if by_id[id(r)][0]]
    result_unknown = [r for r in ordered if not by_id[id(r)][0]]
    input_unknown = [r for r in results if not by_id[id(r)][0]]

    # (1) 창 이내(준동형): cutoff 이 있으면 결과의 확정 항목은 모두 >= cutoff.
    #     경계값(발행일 == cutoff)도 포함되어야 한다(>=).
    if cutoff is not None:
        for r in result_known:
            dt = by_id[id(r)][1]
            assert dt >= cutoff, (
                f"창 밖 항목 잔존: {dt.isoformat()} < cutoff {cutoff.isoformat()}"
            )

    # (2) 미상 일관 처리.
    if rule == "exclude":
        assert result_unknown == [], "exclude 규칙인데 미상 항목이 결과에 잔존"
    else:  # last
        assert [id(r) for r in result_unknown] == [id(r) for r in input_unknown], (
            "last 규칙: 미상 항목이 입력 순서를 유지한 채 말미에 보존되지 않음"
        )
        # 확정 항목이 미상 항목 뒤에 오지 않는다(미상은 반드시 말미 블록).
        seen_unknown = False
        for r in ordered:
            if by_id[id(r)][0]:  # known
                assert not seen_unknown, "확정 항목이 미상 항목 뒤에 위치(말미 배치 위반)"
            else:
                seen_unknown = True

    # 확정 항목 정렬: 발행일 내림차순 → 관련성 내림차순 → (완전 동점) 입력 순서 보존.
    for a, b in zip(result_known, result_known[1:]):
        da, db = by_id[id(a)][1], by_id[id(b)][1]
        sa, sb = _norm(a.relevance_score), _norm(b.relevance_score)
        # 사전식 (발행일, 관련성) 내림차순 == (da, sa) >= (db, sb).
        assert (da, sa) >= (db, sb), (
            "정렬 위반(발행일 내림차순 → 관련성 내림차순)"
        )
        if da == db and sa == sb:  # 완전 동점 → 입력 순서 보존(안정 정렬)
            assert in_index[id(a)] < in_index[id(b)], (
                f"완전 동점 tie-break 입력순서 위반: {in_index[id(a)]} !< {in_index[id(b)]}"
            )

    # (3) 결정성: 동일 입력·동일 인자 재실행 결과가 동일하다.
    ordered2 = apply_recency(results, window, rule_raw, now=_NOW)
    assert [id(r) for r in ordered2] == result_ids, "동일 입력 재실행 결과가 비결정적"

    # (3) 재정렬 안정(멱등): 결과를 같은 인자로 다시 필터해도 동일하다.
    ordered_again = apply_recency(ordered, window, rule_raw, now=_NOW)
    assert [id(r) for r in ordered_again] == result_ids, "재필터 결과가 불안정(비멱등)"


if __name__ == "__main__":  # 편의 실행 경로
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
