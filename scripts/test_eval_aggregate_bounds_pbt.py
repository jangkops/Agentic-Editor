# Feature: reasoning-perf-reliability, Property 12: 근거성 집계 지표는 유효 범위와 경계 규약을 지킨다
"""Property 12 — 근거성 집계 지표 범위·경계 규약 property 테스트.

`groundedness` / `context_precision` / `unsupported_claim_rate` 의 반환값이 항상
`[0.0, 1.0]` 범위이며, 분모가 0인 경계에서 각 함수의 명시된 관례를 따르는지 검증한다:
    - groundedness(n_claims<=0)          → 1.0
    - unsupported_claim_rate(n_claims<=0) → 0.0
    - context_precision(k<=0)            → 0.0

지표 함수는 `scripts/eval_reasoning_perf.py` 가 `ai_engine/rag/eval_metrics.py` 에서
재-export 한 순수 함수를 그대로 사용한다(재구현 금지).

Validates: Requirements 1.3
"""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from eval_reasoning_perf import (
    context_precision,
    groundedness,
    unsupported_claim_rate,
)

# 음이 아닌 정수 주장 수: 분모 0 경계, 지원>전체 방어 케이스, 큰 값을 모두 포함하도록
# 0 부터 넉넉한 상한까지 균등 생성한다(생성기 직접 구현 아님 — hypothesis 조합).
_counts = st.integers(min_value=0, max_value=10_000)

# context_precision 대조용 식별자 시퀀스/집합(순위 보존 검증에 사용).
_ids = st.lists(st.text(min_size=1, max_size=8), min_size=0, max_size=20)
# k: 음수·0(경계) 및 시퀀스 길이 초과를 포함하도록 넓게 생성.
_k = st.integers(min_value=-5, max_value=25)


@settings(max_examples=200)
@given(
    n_supported=_counts,
    n_unsupported=_counts,
    n_claims=_counts,
    relevant=_ids,
    retrieved=_ids,
    k=_k,
)
def test_aggregate_metrics_bounds_and_boundaries(
    n_supported: int,
    n_unsupported: int,
    n_claims: int,
    relevant: list[str],
    retrieved: list[str],
    k: int,
) -> None:
    g = groundedness(n_supported, n_claims)
    u = unsupported_claim_rate(n_unsupported, n_claims)
    p = context_precision(relevant, retrieved, k)

    # 유효 범위: 세 지표 모두 [0.0, 1.0].
    for name, val in (("groundedness", g), ("unsupported_claim_rate", u), ("context_precision", p)):
        assert 0.0 <= val <= 1.0, f"{name} out of range: {val!r}"

    # 분모 0 경계 규약.
    if n_claims <= 0:
        assert g == 1.0
        assert u == 0.0
    if k <= 0:
        assert p == 0.0
