# Feature: reasoning-perf-reliability, Property 10: mock 모드 지표는 결정론적으로 재현된다
"""Property 10 — mock 모드 지표 결정론 재현 property 테스트.

Feature: reasoning-perf-reliability, Property 10: mock 모드 지표는 결정론적으로 재현된다
**Validates: Requirements 2.4**

For any Query_Set 에 대해, `run_eval(..., gateway_mode='mock')` 을 두 번 실행하면
산출된 질의별·집계 지표가 동일하다 — 단, 지연(latency, wall-clock)은 실제 실행 시간이라
정당하게 변동하므로 비교 대상에서 제외한다.

비교 대상:
- per-query: grounding / accuracy / recall_at_k / mrr / k / status (latency_ms 제외).
- aggregate: grounding_mean / accuracy_mean / recall_at_k_mean / mrr_mean /
  n_queries / n_failed (latency_ms_mean / latency_ms_median 제외).

대상 코드(실측):
- scripts/eval_reasoning_perf.run_eval(query_set, gateway_mode='mock', k):
    MockGateway(프롬프트+근거 컨텍스트 해시 시드 canned 텍스트)를 주입해 Full_Graph 를
    ainvoke 로 실행하므로 동일 Query_Set 은 항상 동일 텍스트·지표를 낳는다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_eval_mock_determinism_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import asyncio
import os
import sys

# repo 루트 + scripts 를 import 경로에 추가한다(test_fast_path_finite_pbt.py 패턴 미러).
# repo 루트: ai_engine 패키지 로드용. scripts: eval_reasoning_perf 로드용.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, _HERE)

# 안정적 baseline: 신규 적응형 깊이 라우팅·근거 게이트 플래그를 명시적으로 off 로 고정한다.
# (플래그 off 무회귀 경로에서 mock 결정론을 검증 — 요구사항 2.4/10.)
os.environ["AE_ENABLE_ADAPTIVE_DEPTH"] = "0"
os.environ["AE_ENABLE_GROUNDING_GATE"] = "0"

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from eval_reasoning_perf import run_eval  # noqa: E402

# ── 생성기(hand-roll 금지: hypothesis 조합만 사용) ──────────────────────────
# evidence 식별자: "path:start-end" 규약(recall_at_k/mrr relevant 식별자).
_evidence_ref = st.builds(
    lambda path, start, end: f"{path}:{start}-{end}",
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyz/_.",
        min_size=1,
        max_size=16,
    ),
    st.integers(min_value=0, max_value=500),
    st.integers(min_value=0, max_value=500),
)

# 단일 질의 본문(id 는 테스트에서 고유하게 부여). 빈/공백/유니코드 프롬프트 포섭.
_query_body = st.fixed_dictionaries(
    {
        "prompt": st.text(max_size=120),
        "expected_evidence_refs": st.lists(_evidence_ref, max_size=4),
        "expected_answer_refs": st.lists(st.text(max_size=24), max_size=4),
    }
)

# 작은 Query_Set(1~3 질의). 그래프를 실제로 실행하므로 크기를 작게 유지한다.
_query_set = st.lists(_query_body, min_size=1, max_size=3)

# 지연은 wall-clock 이라 정당하게 변동 → 비교 대상에서 제외한다.
_PER_QUERY_COMPARE_KEYS = ("grounding", "accuracy", "recall_at_k", "mrr", "k", "status")
_AGGREGATE_COMPARE_KEYS = (
    "grounding_mean",
    "accuracy_mean",
    "recall_at_k_mean",
    "mrr_mean",
    "n_queries",
    "n_failed",
)


def _assign_ids(bodies: list[dict]) -> list[dict]:
    """생성된 질의 본문에 고유 id 를 부여해 Query_Set 을 조립한다."""
    return [dict(body, id=f"q-{i}") for i, body in enumerate(bodies)]


@settings(max_examples=25, deadline=None)
@given(bodies=_query_set)
def test_run_eval_mock_is_deterministic(bodies):
    """mock 모드 run_eval 2회 실행 → 지연 제외 질의별·집계 지표가 동일하다."""
    query_set = _assign_ids(bodies)

    rec1 = asyncio.run(run_eval(query_set, gateway_mode="mock", k=5))
    rec2 = asyncio.run(run_eval(query_set, gateway_mode="mock", k=5))

    pq1 = rec1["per_query"]
    pq2 = rec2["per_query"]

    # (1) 질의 수가 동일하다.
    assert len(pq1) == len(pq2) == len(query_set)

    # (2) 질의별 지표(지연 제외)가 동일하다 — id 기준 매칭.
    by_id_2 = {q["id"]: q for q in pq2}
    for q1 in pq1:
        qid = q1["id"]
        assert qid in by_id_2, f"두 번째 실행에 질의 {qid!r} 누락"
        q2 = by_id_2[qid]
        for key in _PER_QUERY_COMPARE_KEYS:
            assert q1.get(key) == q2.get(key), (
                f"질의 {qid!r} 지표 '{key}' 비결정론: {q1.get(key)!r} != {q2.get(key)!r}"
            )

    # (3) 집계 지표(지연 평균/중앙값 제외)가 동일하다.
    agg1 = rec1["aggregate"]
    agg2 = rec2["aggregate"]
    for key in _AGGREGATE_COMPARE_KEYS:
        assert agg1.get(key) == agg2.get(key), (
            f"집계 지표 '{key}' 비결정론: {agg1.get(key)!r} != {agg2.get(key)!r}"
        )
