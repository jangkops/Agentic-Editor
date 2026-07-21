# Feature: reasoning-perf-reliability, Property 13: Baseline 비교 delta 는 성분별 차이이며 자기비교는 0이다
"""Property 13 — Baseline 비교 delta 성분별 차이·자기비교 0 property 테스트.

`compare_baselines(before, after)` 가 공통 집계 지표 `m` 마다
`after[m] - before[m]` 를 산출하고, `compare_baselines(x, x)` 의 모든 delta 가
정확히 0 임을 입력 전반에 걸쳐 검증한다.

비교 대상 스칼라 지표 키는 `eval_reasoning_perf._AGGREGATE_METRIC_KEYS`
(latency_ms_mean / latency_ms_median / grounding_mean / accuracy_mean /
recall_at_k_mean / mrr_mean)를 그대로 사용한다(드리프트 방지).

Validates: Requirements 3.2
"""
from __future__ import annotations

import os
import sys

from hypothesis import given, settings
from hypothesis import strategies as st

# 형제 테스트 파일과 동일하게 scripts 디렉터리를 sys.path 에 주입한 뒤 import 한다.
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from eval_reasoning_perf import (  # noqa: E402  (경로 주입 후 import)
    _AGGREGATE_METRIC_KEYS,
    compare_baselines,
)

# 집계 스칼라 값: NaN/Inf 는 delta 항등(x-x==0) 및 뺄셈 동등성을 깨므로 배제하고,
# 음수·0·경계·큰 값을 폭넓게 포함하도록 유한 실수를 생성한다(생성기 직접 구현 아님).
_metric_value = st.floats(
    allow_nan=False,
    allow_infinity=False,
    min_value=-1e9,
    max_value=1e9,
)


def _baseline_records() -> st.SearchStrategy:
    """Baseline_Record 형태({"aggregate": {지표키: float, ...}, ...}) dict 생성기.

    6개 집계 지표 키 전부를 유한 실수로 채운 aggregate 하위 dict 를 포함하며,
    compare_baselines 가 무시하는 부가 필드(timestamp 등)도 함께 실어 실제
    Baseline_Record 모양에 가깝게 만든다.
    """
    aggregate = st.fixed_dictionaries(
        {key: _metric_value for key in _AGGREGATE_METRIC_KEYS}
    )
    return st.builds(
        lambda agg: {
            "timestamp": "2026-07-01T00:00:00Z",
            "gateway_mode": "mock",
            "aggregate": dict(agg),
        },
        aggregate,
    )


@settings(max_examples=200)
@given(before=_baseline_records(), after=_baseline_records())
def test_compare_baselines_delta_is_componentwise_and_self_is_zero(
    before: dict,
    after: dict,
) -> None:
    delta = compare_baselines(before, after)

    # 반환 키 집합은 정확히 집계 지표 키 집합이다.
    assert set(delta.keys()) == set(_AGGREGATE_METRIC_KEYS)

    # 성분별 차이: delta[m] == after[m] - before[m] (동일 유한 실수 뺄셈이므로 정확 일치).
    for m in _AGGREGATE_METRIC_KEYS:
        expected = after["aggregate"][m] - before["aggregate"][m]
        assert delta[m] == expected, f"{m}: {delta[m]!r} != {expected!r}"

    # 자기비교: compare_baselines(x, x) 의 모든 delta 는 0 이다.
    self_delta = compare_baselines(after, after)
    assert set(self_delta.keys()) == set(_AGGREGATE_METRIC_KEYS)
    for m in _AGGREGATE_METRIC_KEYS:
        assert self_delta[m] == 0.0, f"self-compare {m} nonzero: {self_delta[m]!r}"
