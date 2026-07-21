# Feature: reasoning-perf-reliability, Property 11: recall@k 는 k 에 대해 단조 비감소한다
"""Property-based test: recall@k 의 k 에 대한 단조 비감소성.

Feature: reasoning-perf-reliability, Property 11: recall@k 는 k 에 대해 단조 비감소한다
**Validates: Requirements 1.4**

For any relevant identifier set, retrieved sequence, and k1 <= k2:
    recall_at_k(relevant, retrieved, k1) <= recall_at_k(relevant, retrieved, k2)

`recall_at_k` 는 `scripts/eval_reasoning_perf.py` 가 재수출하는 순수 함수
(`ai_engine/rag/eval_metrics.recall_at_k`)를 그대로 사용한다.

생성기(hypothesis strategies)는 다음 edge case 를 포함한다:
    - k 가 0, 음수, retrieved 길이 초과
    - 빈 relevant 집합(recall_at_k 규약상 항상 1.0)
    - retrieved 시퀀스의 중복 식별자

Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import sys
from pathlib import Path

# scripts/ 를 import 경로에 추가해 eval_reasoning_perf(재수출) 를 로드한다.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from hypothesis import given, settings, strategies as st  # noqa: E402

from eval_reasoning_perf import recall_at_k  # noqa: E402

# 식별자 풀은 작게 유지해 relevant 와 retrieved 가 겹칠 확률을 높인다
# (겹침이 없으면 recall 은 항상 0.0 이라 단조성 검증이 무의미해진다).
_IDENTIFIERS = st.text(alphabet="abcde", min_size=1, max_size=3)

# relevant: 중복은 recall_at_k 내부에서 set 정규화되므로 리스트로 생성해도 무방하다.
_relevant = st.lists(_IDENTIFIERS, min_size=0, max_size=8)

# retrieved: 중복 식별자를 명시적으로 허용(중복 edge case 포섭).
_retrieved = st.lists(_IDENTIFIERS, min_size=0, max_size=12)

# k: 음수·0·retrieved 길이 초과를 모두 포함하도록 넓은 정수 범위를 사용한다.
_k = st.integers(min_value=-3, max_value=20)


@settings(max_examples=300)
@given(relevant=_relevant, retrieved=_retrieved, k_a=_k, k_b=_k)
def test_recall_at_k_monotonic_non_decreasing(relevant, retrieved, k_a, k_b):
    """k1 <= k2 이면 recall_at_k(k1) <= recall_at_k(k2)."""
    k1, k2 = sorted((k_a, k_b))  # k1 <= k2 보장

    r1 = recall_at_k(relevant, retrieved, k1)
    r2 = recall_at_k(relevant, retrieved, k2)

    assert r1 <= r2 + 1e-12, (
        f"단조성 위반: recall@{k1}={r1} > recall@{k2}={r2} "
        f"(relevant={relevant!r}, retrieved={retrieved!r})"
    )
