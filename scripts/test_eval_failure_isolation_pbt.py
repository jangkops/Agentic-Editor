# Feature: reasoning-perf-reliability, Property 14: 개별 질의 실패는 격리되고 나머지는 완주한다
"""Property-based test: 개별 질의 실패 격리와 나머지 완주.

Feature: reasoning-perf-reliability, Property 14: 개별 질의 실패는 격리되고 나머지는 완주한다
**Validates: Requirements 1.6**

For any Query_Set where SOME queries raise during execution:
    - 결과 per-query 항목 수는 입력 질의 수와 같다(누락 없음).
    - 실패 질의는 `status == "failed"` 로 기록된다(예외가 하네스로 전파되지 않는다).
    - 나머지 질의는 정상 실행되어 `status == "ok"` 로 기록된다.
    - 집계의 `n_failed`(build_baseline_record → aggregate)가 실제 실패 수와 일치한다.

대상 코드(실측):
- scripts/eval_reasoning_perf.run_query: compiled_graph.ainvoke 를 개별 await 로 호출하고
  어떤 예외든 잡아 {id, status:"failed", error} 로 기록(요구사항 1.6, 전파 금지).
- scripts/eval_reasoning_perf.build_baseline_record → aggregate_metrics: status != "ok" 를
  n_failed 로 계수.

빠르고 결정론적인 접근(설계 Testing Strategy):
- 실제 그래프 실행 없이 FakeGraph 로 실패 격리 계약을 직접 검증한다. FakeGraph.ainvoke 는
  per-query fail 플래그(state["session_id"] 로 매핑)에 따라 RuntimeError 를 던지거나
  최소 유효 상태 dict 를 반환한다. 그런 뒤 build_baseline_record 로 n_failed 산출을 검증한다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_eval_failure_isolation_pbt.py -q
Stack: Python 3.11+, hypothesis library.
"""
from __future__ import annotations

import asyncio
import os
import sys

# repo 루트 + scripts 를 import 경로에 추가한다(test_fast_path_finite_pbt.py 패턴 미러).
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, _HERE)

from hypothesis import given, settings, strategies as st  # noqa: E402

from eval_reasoning_perf import (  # noqa: E402
    build_baseline_record,
    run_query,
)


def _minimal_valid_state(prompt: str) -> dict:
    """정상 질의에 대해 run_query 가 소비하는 최소 유효 GraphState 반환값.

    run_query 는 final_text / answer_quality / evidence / citations 를 읽어 지표를
    산출한다. 실제 그래프 왕복 없이 이 값들만 있으면 정상 경로(status=="ok")를 탄다.
    """
    return {
        "final_text": f"근거 범위 안에서 답을 작성했습니다: {prompt[:20]}",
        "answer_quality": {
            "faithfulness": {"score": 0.9, "degraded": False},
            "grounding": {"score": 0.85},
        },
        "evidence": {
            "chunks": [
                ({"file_path": "ai_engine/server.py", "start_line": 1, "end_line": 10}, 0.9),
            ]
        },
        "citations": {"verified": ["c1"], "unverified": []},
    }


class FakeGraph:
    """실패 격리 계약 검증용 결정론적 그래프 스텁.

    ainvoke 는 initial_state["session_id"](= run_query 가 query["id"] 로 설정)를 fail_map
    으로 조회해 fail 이면 RuntimeError 를 던지고, 아니면 최소 유효 상태 dict 를 반환한다.
    실제 그래프·게이트웨이·네트워크 없음.
    """

    def __init__(self, fail_map: dict[str, bool]) -> None:
        self._fail_map = fail_map

    async def ainvoke(self, state, config):  # noqa: ARG002 — config 미사용(계약 일치용)
        qid = str((state or {}).get("session_id", ""))
        if self._fail_map.get(qid, False):
            raise RuntimeError(f"injected failure for {qid}")
        prompt = (state or {}).get("prompt", "")
        return _minimal_valid_state(prompt)


# 생성기: 질의별 should_fail 플래그 리스트. 전부 성공/전부 실패/혼합을 모두 포섭한다.
_FAIL_FLAGS = st.lists(st.booleans(), min_size=0, max_size=12)


@settings(max_examples=100, deadline=None)
@given(fail_flags=_FAIL_FLAGS)
def test_failure_is_isolated_and_others_complete(fail_flags):
    """일부 질의가 예외를 던져도 나머지는 완주하고 n_failed 가 실제 실패 수와 일치한다."""
    # 질의 구성 및 fail_map(id → should_fail).
    queries = [
        {
            "id": f"q-{i:03d}",
            "prompt": f"질의 {i} 의 프롬프트",
            "expected_evidence_refs": ["ai_engine/server.py:1-10"],
            "expected_answer_refs": [],
        }
        for i in range(len(fail_flags))
    ]
    fail_map = {q["id"]: bool(flag) for q, flag in zip(queries, fail_flags)}
    fake = FakeGraph(fail_map)

    # 각 질의를 run_query 로 실행(예외는 run_query 내부에서 격리되어 전파되지 않아야 함).
    per_query = [
        asyncio.run(run_query(fake, q, {"configurable": {"thread_id": q["id"]}}))
        for q in queries
    ]

    expected_failures = sum(1 for flag in fail_flags if flag)

    # (1) per-query 항목 수 = 입력 질의 수(누락 없음).
    assert len(per_query) == len(queries), (
        f"per-query 항목 수 불일치: expected={len(queries)}, actual={len(per_query)}"
    )

    # (2) 실패는 status=="failed", 성공은 status=="ok" 로 기록된다.
    for q, flag, rec in zip(queries, fail_flags, per_query):
        assert rec["id"] == q["id"]
        if flag:
            assert rec["status"] == "failed", (
                f"실패 주입 질의가 failed 로 기록되지 않음: {rec!r}"
            )
        else:
            assert rec["status"] == "ok", (
                f"정상 질의가 ok 로 완주하지 않음(실패 격리 실패): {rec!r}"
            )

    # (3) 실제 실패 수 = per-query 상 failed 수.
    actual_failed = sum(1 for rec in per_query if rec["status"] == "failed")
    assert actual_failed == expected_failures

    # (4) 집계의 n_failed 가 실제 실패 수와 일치한다.
    record = build_baseline_record({}, per_query, "2026-07-01T00:00:00Z")
    assert record["aggregate"]["n_failed"] == expected_failures, (
        f"aggregate.n_failed 불일치: expected={expected_failures}, "
        f"actual={record['aggregate']['n_failed']}"
    )
    assert record["aggregate"]["n_queries"] == len(queries)
