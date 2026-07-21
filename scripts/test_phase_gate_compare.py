"""Phase-gate verification harness — reasoning-perf-reliability Requirement 12.

각 Phase 는 Eval_Harness 의 Baseline_Record 를 `compare_baselines` 로 비교해 개선을
수치로 실증한 뒤에만 다음 Phase 로 진행한다(요구사항 12). 이 하네스는 서로 다른 플래그
구성으로 만든 두 Baseline_Record 를 로드/구성해 성분별 delta 를 자동 단언한다.

**Validates: Requirements 12.2, 12.3, 12.4**
- 12.2: Phase 2a(적응형 깊이) — 단순 질의 지연 감소를 baseline 대비 수치로 실증
        (`compare_baselines(before, after)["latency_ms_mean"] < 0`).
- 12.3: Phase 2b(근거 강제 게이트) — 근거성 지표 개선을 baseline 대비 수치로 실증
        (`compare_baselines(before, after)["grounding_mean"] > 0`).
- 12.4: 개선 실증에 회귀가 확인되면 게이트가 이를 실패로 플래그해 다음 Phase 진행을 막는다
        (`phase_gate_ok(...) is False` on regression).

결정론(요구사항 2.4): 지표 값을 직접 담은 합성(synthetic) Baseline_Record 를 사용하므로
네트워크·게이트웨이·비용 없이 완전히 재현 가능하다. Property 13(자기비교 delta = 0,
요구사항 3.2)도 함께 sanity 검증한다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_phase_gate_compare.py -q
Stack: Python 3.11+, pytest (example-based).
"""
from __future__ import annotations

import os
import sys

# repo 루트 + scripts 를 import 경로에 추가한다(test_fast_path_finite_pbt.py 패턴 미러).
# repo 루트: ai_engine 패키지 로드용. scripts: eval_reasoning_perf 로드용.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, _HERE)

from eval_reasoning_perf import (  # noqa: E402  (경로 주입 후 import)
    build_baseline_record,
    compare_baselines,
)

_TS = "2026-01-01T00:00:00Z"


# ─────────────────────────────────────────────────────────────────────────
# 합성 Baseline_Record 빌더 — 지표 값을 직접 주입해 결정론 보장
# ─────────────────────────────────────────────────────────────────────────
def _synthetic_baseline(
    flags: dict,
    *,
    latency_ms: float,
    grounding: float,
    accuracy: float = 1.0,
    recall_at_k: float = 1.0,
    mrr: float = 1.0,
    k: int = 5,
    n_queries: int = 3,
) -> dict:
    """지정 지표를 갖는 결정론적 Baseline_Record 를 구성한다.

    build_baseline_record 를 실제로 통과시켜(자격증명 부재 불변식 포함) per-query 지표를
    집계한 Baseline_Record 를 얻는다. 모든 질의에 동일 값을 넣어 평균이 정확히 그 값이 되게 한다.
    """
    per_query = [
        {
            "id": f"q{i}",
            "latency_ms": latency_ms,
            "grounding": grounding,
            "accuracy": accuracy,
            "recall_at_k": recall_at_k,
            "mrr": mrr,
            "k": k,
            "status": "ok",
        }
        for i in range(n_queries)
    ]
    return build_baseline_record(flags, per_query, _TS)


# ─────────────────────────────────────────────────────────────────────────
# 재사용 하네스 — 페이즈 게이트 통과/실패 판정 (요구사항 12.4)
# ─────────────────────────────────────────────────────────────────────────
def phase_gate_ok(
    before: dict,
    after: dict,
    *,
    expect_latency_down: bool = False,
    expect_grounding_up: bool = False,
) -> bool:
    """compare_baselines delta 로 페이즈 게이트 통과 여부를 판정한다(요구사항 12.4).

    - expect_latency_down: Phase 2a 기대 — 지연 평균 delta < 0 이어야 통과(요구사항 12.2).
    - expect_grounding_up: Phase 2b 기대 — 근거성 평균 delta > 0 이어야 통과(요구사항 12.3).
    회귀(기대와 반대 방향, 또는 개선 없음)면 False 를 반환해 다음 Phase 진행을 막는다.
    """
    delta = compare_baselines(before, after)
    if expect_latency_down and not (delta["latency_ms_mean"] < 0):
        return False
    if expect_grounding_up and not (delta["grounding_mean"] > 0):
        return False
    return True


# ═════════════════════════════════════════════════════════════════════════
# Phase 2a — 단순 질의 지연 감소 실증 (요구사항 12.2)
# ═════════════════════════════════════════════════════════════════════════
def test_phase2a_latency_reduction_asserted():
    """적응형 깊이 on 후 지연 평균이 baseline 대비 감소함을 delta 로 단언한다."""
    before = _synthetic_baseline(
        {"AE_ENABLE_ADAPTIVE_DEPTH": False}, latency_ms=1200.0, grounding=0.6
    )
    after = _synthetic_baseline(
        {"AE_ENABLE_ADAPTIVE_DEPTH": True}, latency_ms=450.0, grounding=0.6
    )

    delta = compare_baselines(before, after)
    # 지연 감소 → after - before < 0.
    assert delta["latency_ms_mean"] < 0
    assert delta["latency_ms_median"] < 0
    # 하네스도 통과로 판정.
    assert phase_gate_ok(before, after, expect_latency_down=True) is True


def test_phase2a_latency_regression_flagged():
    """지연이 오히려 증가하면 게이트가 회귀로 플래그(실패)함을 확인한다(요구사항 12.4)."""
    before = _synthetic_baseline(
        {"AE_ENABLE_ADAPTIVE_DEPTH": False}, latency_ms=450.0, grounding=0.6
    )
    after = _synthetic_baseline(
        {"AE_ENABLE_ADAPTIVE_DEPTH": True}, latency_ms=1200.0, grounding=0.6
    )

    delta = compare_baselines(before, after)
    # 회귀: 지연 증가 → delta > 0.
    assert delta["latency_ms_mean"] > 0
    # 하네스는 반드시 실패로 판정해 다음 Phase 진행을 막는다.
    assert phase_gate_ok(before, after, expect_latency_down=True) is False


# ═════════════════════════════════════════════════════════════════════════
# Phase 2b — 근거성 지표 개선 실증 (요구사항 12.3)
# ═════════════════════════════════════════════════════════════════════════
def test_phase2b_grounding_improvement_asserted():
    """근거 강제 게이트 on 후 근거성 평균이 baseline 대비 개선됨을 delta 로 단언한다."""
    before = _synthetic_baseline(
        {"AE_ENABLE_GROUNDING_GATE": False}, latency_ms=800.0, grounding=0.55
    )
    after = _synthetic_baseline(
        {"AE_ENABLE_GROUNDING_GATE": True}, latency_ms=800.0, grounding=0.82
    )

    delta = compare_baselines(before, after)
    # 근거성 개선 → after - before > 0.
    assert delta["grounding_mean"] > 0
    assert phase_gate_ok(before, after, expect_grounding_up=True) is True


def test_phase2b_grounding_regression_flagged():
    """근거성이 오히려 하락하면 게이트가 회귀로 플래그(실패)함을 확인한다(요구사항 12.4)."""
    before = _synthetic_baseline(
        {"AE_ENABLE_GROUNDING_GATE": False}, latency_ms=800.0, grounding=0.82
    )
    after = _synthetic_baseline(
        {"AE_ENABLE_GROUNDING_GATE": True}, latency_ms=800.0, grounding=0.55
    )

    delta = compare_baselines(before, after)
    assert delta["grounding_mean"] < 0
    assert phase_gate_ok(before, after, expect_grounding_up=True) is False


def test_phase2b_grounding_no_change_flagged():
    """개선이 없으면(delta == 0) 게이트를 통과하지 못한다(요구사항 12.4)."""
    before = _synthetic_baseline(
        {"AE_ENABLE_GROUNDING_GATE": False}, latency_ms=800.0, grounding=0.7
    )
    after = _synthetic_baseline(
        {"AE_ENABLE_GROUNDING_GATE": True}, latency_ms=800.0, grounding=0.7
    )

    delta = compare_baselines(before, after)
    assert delta["grounding_mean"] == 0.0
    # 개선 없음도 진행 불가로 판정.
    assert phase_gate_ok(before, after, expect_grounding_up=True) is False


# ═════════════════════════════════════════════════════════════════════════
# 결정론 sanity — 자기비교 delta = 0 (Property 13 / 요구사항 3.2)
# ═════════════════════════════════════════════════════════════════════════
def test_self_comparison_all_zero_deltas():
    """compare_baselines(x, x) 는 모든 성분 delta 가 0 이다(결정론 보장)."""
    x = _synthetic_baseline(
        {"AE_ENABLE_ADAPTIVE_DEPTH": True, "AE_ENABLE_GROUNDING_GATE": True},
        latency_ms=640.0,
        grounding=0.73,
        accuracy=0.9,
        recall_at_k=0.8,
        mrr=0.6,
    )
    delta = compare_baselines(x, x)
    assert set(delta.keys()) == {
        "latency_ms_mean",
        "latency_ms_median",
        "grounding_mean",
        "accuracy_mean",
        "recall_at_k_mean",
        "mrr_mean",
    }
    assert all(v == 0.0 for v in delta.values())


def test_harness_deterministic_across_runs():
    """동일 입력으로 하네스를 반복 실행해도 판정이 재현된다(요구사항 2.4)."""
    before = _synthetic_baseline(
        {"AE_ENABLE_ADAPTIVE_DEPTH": False}, latency_ms=1000.0, grounding=0.5
    )
    after = _synthetic_baseline(
        {"AE_ENABLE_ADAPTIVE_DEPTH": True}, latency_ms=400.0, grounding=0.75
    )
    first = phase_gate_ok(
        before, after, expect_latency_down=True, expect_grounding_up=True
    )
    second = phase_gate_ok(
        before, after, expect_latency_down=True, expect_grounding_up=True
    )
    assert first is True
    assert first == second
