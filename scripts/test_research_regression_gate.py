# Feature: deep-research-engine
"""회귀 판정 단위 테스트 (Task 16.3) — `ai_engine/research/eval_harness.py::check_regression`.

요구사항 9.7의 품질 게이트를 단위 수준에서 검증한다: 관련성·최신성·출처 신뢰도·
인용 정확도·커버리지·중복제거 지표 중 **어느 하나라도** baseline 대비 구성된 허용
하락폭(`AE_RESEARCH_REGRESSION_TOLERANCE`, 기본 0.05)을 **초과하여** 낮아지면 회귀로
판정한다. `check_regression`은 감시 지표(`HIGHER_IS_BETTER_METRICS`)별 상대 하락폭
`relative_drop = (baseline - current) / baseline` 이 `tolerance` 를 **엄격히 초과**(`>`)
할 때만 회귀로 표기한다.

검증 축:

  (1) 허용폭 초과 하락 → 회귀 True (그리고 해당 지표가 `regressed_metrics` 에 포함).
  (2) 허용폭 이내 하락 → 회귀 False.
  (3) 개선/동일(current >= baseline) → 무회귀, 자기 자신 대비(self-vs-self) 무회귀.
  (4) 경계: 정확히 tolerance 만큼의 하락은 회귀 아님(엄격한 `>` 의미론).
  (5) baseline <= 0 인 지표는 회귀 불가(가드) — 하락폭이 정의되지 않음.
  (6) tolerance 인자 오버라이드 vs env 기본값이 모두 반영됨(음수는 0.0 으로 클램프).
  (7) 순수 집계 dict 와 `evaluate_golden_set` 형태(`{"aggregate": ...}`) 입력을 모두 수용.

`check_regression`은 순수/결정적이며 네트워크·파일 I/O가 없다. env 기본값 경로만
`os.environ`을 읽으므로, 해당 하위 테스트는 `_env` 컨텍스트로 변수를 결정적으로
설정/복원해 hermetic 하게 실행한다.

Stack: Python 3.11+ 표준 라이브러리 + pytest 수집. 단발 실행(워치 모드 금지):
    ai_engine/.venv/bin/python -m pytest scripts/test_research_regression_gate.py -q
    ai_engine/.venv/bin/python scripts/test_research_regression_gate.py

_Requirements: 9.7_
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

# 스크립트를 직접 실행할 때도 ai_engine 패키지를 import 할 수 있게 repo 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_engine.research.eval_harness import (  # noqa: E402
    HIGHER_IS_BETTER_METRICS,
    check_regression,
    evaluate_golden_set,
)
from ai_engine.research.models import SearchResult  # noqa: E402

# check_regression 이 감시하는 지표 키(모두 higher-is-better). 상수와 정합해야 한다.
_METRIC_KEYS = (
    "precision_at_k",
    "mrr",
    "recall_at_k",
    "recency_score",
    "authority_score",
    "citation_accuracy",
    "coverage_sources",
    "coverage_providers",
    "dedup_ratio",
)


# --------------------------------------------------------------------------- #
# 헬퍼: 감시 지표 전체를 채운 집계 dict + target 지표만 다른 baseline/current 쌍
# --------------------------------------------------------------------------- #
def _agg(**overrides) -> dict:
    """모든 감시 지표 키를 양수 기본값으로 채운 집계 dict(+선택 오버라이드).

    비율 지표는 [0,1] 대표값, 커버리지(개수형)는 질의당 평균 소스/제공자 수를
    모사한 float 값이다. check_regression 은 범위를 강제하지 않으므로 경계
    테스트에서는 오버라이드로 임의 값을 주입한다.
    """
    base = {
        "precision_at_k": 0.80,
        "mrr": 0.70,
        "recall_at_k": 0.60,
        "recency_score": 0.50,
        "authority_score": 0.65,
        "citation_accuracy": 0.90,
        "coverage_sources": 8.0,
        "coverage_providers": 3.0,
        "dedup_ratio": 0.40,
    }
    base.update(overrides)
    return base


def _pair(metric: str, base_v: float, cur_v: float) -> tuple[dict, dict]:
    """target 지표만 baseline/current 가 다르고 나머지 감시 지표는 동일한 집계쌍.

    나머지 지표는 baseline==current 이므로 상대 하락폭 0(무회귀)이라, 판정이
    오직 `metric` 하나에 의해 결정되도록 격리한다.

    Returns:
        ``(current_agg, baseline_agg)`` 순서(check_regression 인자 순서와 정합).
    """
    base = _agg()
    cur = _agg()
    base[metric] = base_v
    cur[metric] = cur_v
    return cur, base


# =========================================================================== #
# (0) 헬퍼/상수 정합 — _agg 가 감시 지표를 정확히 커버하는지 확인
# =========================================================================== #
def test_agg_helper_covers_all_monitored_metrics() -> None:
    """`_agg` 키 집합이 `HIGHER_IS_BETTER_METRICS` 와 정확히 일치한다(커버리지 보장)."""
    assert set(_agg().keys()) == set(HIGHER_IS_BETTER_METRICS)
    assert set(_METRIC_KEYS) == set(HIGHER_IS_BETTER_METRICS)


# =========================================================================== #
# (1) 허용폭 초과 하락 → 회귀 True, 해당 지표가 regressed_metrics 에 포함
# =========================================================================== #
def test_drop_beyond_tolerance_flags_regression() -> None:
    """baseline 대비 tolerance 초과 하락한 지표는 회귀로 판정되고 목록에 포함된다."""
    # precision: 0.80 → 0.60, 상대 하락폭 0.25 > 0.05 → 회귀.
    cur, base = _pair("precision_at_k", 0.80, 0.60)
    res = check_regression(cur, base, tolerance=0.05)

    assert res["regressed"] is True
    assert "precision_at_k" in res["regressed_metrics"]
    # 다른 지표는 동일값이라 회귀 아님 → 목록은 target 하나뿐.
    assert res["regressed_metrics"] == ["precision_at_k"]

    # 반환 구조/상세 필드 검증.
    assert set(res.keys()) == {"regressed", "tolerance", "regressed_metrics", "details"}
    assert res["tolerance"] == 0.05
    det = res["details"]["precision_at_k"]
    assert det["baseline"] == 0.80
    assert det["current"] == 0.60
    assert det["regressed"] is True
    assert abs(det["relative_drop"] - 0.25) < 1e-9
    # 회귀하지 않은 지표는 상세에서도 regressed=False.
    assert res["details"]["mrr"]["regressed"] is False


def test_coverage_metric_drop_is_detected() -> None:
    """개수형 커버리지 지표(질의당 평균 소스 수)도 상대 하락폭으로 회귀 판정된다."""
    # coverage_sources: 8 → 4, 상대 하락폭 0.5 > 0.05 → 회귀.
    cur, base = _pair("coverage_sources", 8.0, 4.0)
    res = check_regression(cur, base, tolerance=0.05)
    assert res["regressed"] is True
    assert res["regressed_metrics"] == ["coverage_sources"]


def test_multiple_regressed_metrics_sorted() -> None:
    """여러 지표가 동시에 회귀하면 regressed_metrics 가 정렬되어 반환된다."""
    base = _agg()
    cur = _agg(precision_at_k=0.10, mrr=0.10, dedup_ratio=0.10)  # 셋 다 큰 하락
    res = check_regression(cur, base, tolerance=0.05)
    assert res["regressed"] is True
    # 정렬된 키 목록(사전순).
    assert res["regressed_metrics"] == sorted(["dedup_ratio", "mrr", "precision_at_k"])


# =========================================================================== #
# (2) 허용폭 이내 하락 → 회귀 False
# =========================================================================== #
def test_drop_within_tolerance_no_regression() -> None:
    """허용 하락폭 이내(초과 아님)의 하락은 회귀로 판정하지 않는다."""
    # precision: 0.80 → 0.78, 상대 하락폭 0.025 <= 0.05 → 무회귀.
    cur, base = _pair("precision_at_k", 0.80, 0.78)
    res = check_regression(cur, base, tolerance=0.05)
    assert res["regressed"] is False
    assert res["regressed_metrics"] == []
    assert res["details"]["precision_at_k"]["regressed"] is False


# =========================================================================== #
# (3) 개선/동일 → 무회귀, 자기 자신 대비(self-vs-self) 무회귀
# =========================================================================== #
def test_improvement_is_not_regression() -> None:
    """모든 지표가 개선(current > baseline)이면 회귀가 아니다."""
    base = _agg()
    cur = _agg(
        precision_at_k=0.95,
        mrr=0.85,
        recall_at_k=0.75,
        recency_score=0.60,
        authority_score=0.80,
        citation_accuracy=0.99,
        coverage_sources=12.0,
        coverage_providers=4.0,
        dedup_ratio=0.55,
    )
    res = check_regression(cur, base, tolerance=0.05)
    assert res["regressed"] is False
    assert res["regressed_metrics"] == []
    # 개선 지표의 상대 하락폭은 음수(하락 아님).
    assert res["details"]["precision_at_k"]["relative_drop"] < 0.0


def test_self_vs_self_is_not_regression() -> None:
    """동일 집계를 baseline/current 로 넘기면(자기 자신 대비) 회귀가 아니다."""
    agg = _agg()
    res = check_regression(agg, agg, tolerance=0.05)
    assert res["regressed"] is False
    assert res["regressed_metrics"] == []
    # 모든 지표 하락폭 정확히 0.
    for key in HIGHER_IS_BETTER_METRICS:
        assert res["details"][key]["drop"] == 0.0
        assert res["details"][key]["relative_drop"] == 0.0


def test_equality_per_metric_is_not_regression() -> None:
    """일부 개선·일부 동일이 섞여도 하락이 없으면 무회귀."""
    base = _agg()
    cur = _agg(mrr=0.90)  # mrr 개선, 나머지 동일
    res = check_regression(cur, base, tolerance=0.05)
    assert res["regressed"] is False


# =========================================================================== #
# (4) 경계: 정확히 tolerance 만큼 하락은 회귀 아님(엄격한 > 의미론)
# =========================================================================== #
def test_exactly_at_tolerance_is_not_regression() -> None:
    """상대 하락폭이 tolerance 와 정확히 같으면 회귀가 아니다(`>` 는 엄격).

    이진 부동소수점에서 정확히 표현되는 값(2.0, 1.0, 0.5)을 사용해 경계를
    오차 없이 만든다: (2.0 - 1.0) / 2.0 == 0.5 == tolerance.
    """
    cur, base = _pair("precision_at_k", 2.0, 1.0)  # 상대 하락폭 = 0.5
    res = check_regression(cur, base, tolerance=0.5)
    assert res["details"]["precision_at_k"]["relative_drop"] == 0.5
    assert res["details"]["precision_at_k"]["regressed"] is False
    assert res["regressed"] is False


def test_just_beyond_tolerance_is_regression() -> None:
    """경계를 조금이라도 초과하면(> tolerance) 회귀로 판정된다(엄격성 대조군)."""
    # (2.0 - 0.9) / 2.0 = 0.55 > 0.5 → 회귀.
    cur, base = _pair("precision_at_k", 2.0, 0.9)
    res = check_regression(cur, base, tolerance=0.5)
    assert res["details"]["precision_at_k"]["relative_drop"] > 0.5
    assert res["regressed"] is True
    assert res["regressed_metrics"] == ["precision_at_k"]


# =========================================================================== #
# (5) baseline <= 0 → 회귀 불가(가드)
# =========================================================================== #
def test_zero_baseline_is_never_regression() -> None:
    """baseline == 0 인 지표는 상대 하락폭이 정의되지 않아 회귀로 보지 않는다."""
    # current 도 0 (동일).
    cur, base = _pair("recency_score", 0.0, 0.0)
    res = check_regression(cur, base, tolerance=0.05)
    assert res["details"]["recency_score"]["relative_drop"] == 0.0
    assert res["details"]["recency_score"]["regressed"] is False
    assert res["regressed"] is False

    # baseline 0, current 개선(양수) — 여전히 회귀 아님.
    cur2, base2 = _pair("recency_score", 0.0, 0.5)
    res2 = check_regression(cur2, base2, tolerance=0.05)
    assert res2["details"]["recency_score"]["regressed"] is False
    assert res2["regressed"] is False


def test_negative_baseline_is_never_regression() -> None:
    """baseline <= 0(음수 포함) 지표는 current 가 더 낮아도 회귀 불가(가드)."""
    # baseline=-1.0, current=-2.0 → 절대 하락(drop=1.0)이지만 base_v<=0 → rel_drop=0.
    cur, base = _pair("authority_score", -1.0, -2.0)
    res = check_regression(cur, base, tolerance=0.05)
    assert res["details"]["authority_score"]["relative_drop"] == 0.0
    assert res["details"]["authority_score"]["regressed"] is False
    assert res["regressed"] is False


def test_all_zero_aggregates_no_regression() -> None:
    """모든 지표가 0 인 집계쌍(예: 빈 golden 세트)은 회귀가 아니다."""
    zeros = {k: 0.0 for k in HIGHER_IS_BETTER_METRICS}
    res = check_regression(zeros, zeros, tolerance=0.05)
    assert res["regressed"] is False
    assert res["regressed_metrics"] == []


# =========================================================================== #
# (6) tolerance 오버라이드 vs env 기본값 (음수 클램프 포함)
# =========================================================================== #
@contextmanager
def _env(**overrides):
    """os.environ 키를 임시 설정/삭제하고 종료 시 원복(값이 None 이면 삭제)."""
    missing = object()
    saved = {k: os.environ.get(k, missing) for k in overrides}
    try:
        for k, v in overrides.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is missing:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def test_explicit_tolerance_override_honored() -> None:
    """tolerance 인자가 주어지면 그 값이 판정에 사용된다(env 무관)."""
    # mrr: 1.0 → 0.9, 상대 하락폭 ≈ 0.10.
    cur, base = _pair("mrr", 1.0, 0.9)

    # 엄격한 tolerance(0.05) → 회귀.
    strict = check_regression(cur, base, tolerance=0.05)
    assert strict["tolerance"] == 0.05
    assert strict["regressed"] is True
    assert strict["regressed_metrics"] == ["mrr"]

    # 관대한 tolerance(0.20) → 무회귀.
    lax = check_regression(cur, base, tolerance=0.20)
    assert lax["tolerance"] == 0.20
    assert lax["regressed"] is False


def test_explicit_arg_overrides_env() -> None:
    """env 가 다른 값으로 설정돼 있어도 명시적 tolerance 인자가 우선한다."""
    cur, base = _pair("mrr", 1.0, 0.9)  # 상대 하락폭 ≈ 0.10
    with _env(AE_RESEARCH_REGRESSION_TOLERANCE="0.20"):
        # env=0.20 이면 무회귀지만, 명시 인자 0.05 가 우선 → 회귀.
        res = check_regression(cur, base, tolerance=0.05)
        assert res["tolerance"] == 0.05
        assert res["regressed"] is True


def test_env_default_tolerance_used_when_arg_none() -> None:
    """tolerance 인자가 None 이면 AE_RESEARCH_REGRESSION_TOLERANCE(env)에서 로딩한다."""
    cur, base = _pair("mrr", 1.0, 0.9)  # 상대 하락폭 ≈ 0.10

    # (a) env 미설정 → config 기본값 0.05 사용 → 0.10 > 0.05 → 회귀.
    with _env(AE_RESEARCH_REGRESSION_TOLERANCE=None):
        res = check_regression(cur, base)
        assert res["tolerance"] == 0.05
        assert res["regressed"] is True

    # (b) env=0.20 → 그 값 사용 → 0.10 > 0.20 아님 → 무회귀.
    with _env(AE_RESEARCH_REGRESSION_TOLERANCE="0.20"):
        res = check_regression(cur, base)
        assert res["tolerance"] == 0.20
        assert res["regressed"] is False


def test_negative_tolerance_clamped_to_zero() -> None:
    """음수 tolerance 는 0.0 으로 클램프된다(모든 하락=회귀, 동일=무회귀)."""
    # 동일 지표(하락 0) → tol=0.0 에서도 무회귀(자기 자신 통과 보장).
    eq_cur, eq_base = _pair("mrr", 1.0, 1.0)
    res_eq = check_regression(eq_cur, eq_base, tolerance=-1.0)
    assert res_eq["tolerance"] == 0.0
    assert res_eq["regressed"] is False

    # 아주 작은 하락도 tol=0.0 초과 → 회귀.
    drop_cur, drop_base = _pair("mrr", 1.0, 0.9)
    res_drop = check_regression(drop_cur, drop_base, tolerance=-1.0)
    assert res_drop["tolerance"] == 0.0
    assert res_drop["regressed"] is True


# =========================================================================== #
# (7) 입력 형태 수용 — 순수 집계 dict + evaluate_golden_set 형태({"aggregate":...})
# =========================================================================== #
def test_accepts_bare_and_wrapped_aggregate_inputs() -> None:
    """순수 집계 dict 와 {"aggregate": ...} 형태 입력이 동일하게 처리된다."""
    cur, base = _pair("precision_at_k", 0.80, 0.50)  # 회귀 케이스

    bare = check_regression(cur, base, tolerance=0.05)
    wrapped = check_regression(
        {"k": 10, "n_queries": 1, "per_query": [], "aggregate": cur},
        {"k": 10, "n_queries": 1, "per_query": [], "aggregate": base},
        tolerance=0.05,
    )
    mixed = check_regression(cur, {"aggregate": base}, tolerance=0.05)

    # 세 경로 모두 동일 판정.
    assert bare["regressed"] is True
    assert wrapped["regressed"] == bare["regressed"]
    assert wrapped["regressed_metrics"] == bare["regressed_metrics"]
    assert mixed["regressed_metrics"] == bare["regressed_metrics"]


def test_accepts_evaluate_golden_set_output_self_baseline() -> None:
    """실제 evaluate_golden_set 산출물을 그대로 넘겨도 자기 자신 대비 무회귀.

    실행기가 자신의 baseline 을 통과하는 근거(회귀 게이트의 정상 통과 경로).
    """
    golden = [
        {
            "query": "transformer attention",
            "results": [
                SearchResult(
                    title="Attention Is All You Need",
                    url="https://arxiv.org/abs/1706.03762",
                    relevance_score=0.98,
                    provider="tavily",
                    source_id="web:https://arxiv.org/abs/1706.03762",
                ),
                SearchResult(
                    title="BERT",
                    url="https://example.com/bert",
                    relevance_score=0.55,
                    provider="exa",
                    source_id="web:https://example.com/bert",
                ),
            ],
            "relevant_ids": ["web:https://arxiv.org/abs/1706.03762"],
        }
    ]
    result = evaluate_golden_set(golden, k=10)
    assert "aggregate" in result  # evaluate_golden_set 형태 확인

    reg = check_regression(result, result, tolerance=0.05)
    assert reg["regressed"] is False
    assert reg["regressed_metrics"] == []


def test_golden_set_regression_against_degraded_current() -> None:
    """golden baseline 대비 지표가 하락한 현재 집계는 회귀로 판정된다(형태 혼합)."""
    golden = [
        {
            "query": "q",
            "results": [
                SearchResult(
                    title="A",
                    url="https://a.example.com",
                    relevance_score=0.9,
                    provider="tavily",
                    source_id="web:https://a.example.com",
                ),
            ],
            "relevant_ids": ["web:https://a.example.com"],
        }
    ]
    baseline = evaluate_golden_set(golden, k=10)  # precision@k=1.0 등 상위값
    # 현재는 관련 소스를 전혀 못 맞춘 상태 → precision/mrr/recall = 0 → 큰 하락.
    degraded_golden = [
        {
            "query": "q",
            "results": [
                SearchResult(
                    title="Z",
                    url="https://z.example.com",
                    relevance_score=0.9,
                    provider="tavily",
                    source_id="web:https://z.example.com",
                ),
            ],
            "relevant_ids": ["web:https://a.example.com"],  # 결과에 없음
        }
    ]
    current = evaluate_golden_set(degraded_golden, k=10)

    res = check_regression(current, baseline, tolerance=0.05)
    assert res["regressed"] is True
    # precision@k/mrr/recall@k 중 최소 하나 이상이 회귀 목록에 있어야 한다.
    assert any(
        m in res["regressed_metrics"]
        for m in ("precision_at_k", "mrr", "recall_at_k")
    )


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지). 모든 테스트 함수를 순차 호출한다.
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in _tests:
        fn()
    print(f"PASSED: {len(_tests)} regression-gate unit tests (Task 16.3)")
