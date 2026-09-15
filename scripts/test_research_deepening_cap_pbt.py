# Feature: deep-research-engine, Property 13: 심화 반복 유한 종료
"""Property-based test: 딥리서치 심화 반복 유한 종료 (`should_deepen` / 심화 루프 카운터).

Feature: deep-research-engine, Property 13: 심화 반복 유한 종료
**Validates: Requirements 5.7, 5.8, 9.6**

*For any* 초기 상태(커버리지 지표 · deepening_count · 임계값)에 대해, 딥리서치 심화 반복
횟수는 `Deepening_Cap`(`AE_MAX_DEEPENING`) 이하로 유지되어 **유한 시간에 종료**한다. 심화
결정(`should_deepen`)은 `deepening_count < Deepening_Cap`일 때만 참이 될 수 있다.

검증 대상은 `ai_engine.research.deep_research.should_deepen(metrics, deepening_count,
config)` 하나다 — LLM·네트워크·파일 I/O 가 전혀 없는 순수·결정적 함수(속성 테스트에 이상적).
`run_deep_research` 의 심화 while-루프는 이 함수의 반환값으로만 재진입하므로, 이 함수의
"유한 종료 게이트" 불변식이 곧 파이프라인 전체의 유한 종료(P13)를 결정한다.

판정 규칙(요구사항 5.7 — 결정적 커버리지 지표):
    심화 후보 = (고유 소스 수 < Min_Sources) OR (고유 제공자 수 < Min_Providers)
                OR (미검증 인용 비율 > Unverified_Threshold)
    should_deepen = 심화 후보 AND (deepening_count < Deepening_Cap)

유한 종료(요구사항 5.8 / 9.6 — P13):
    deepening_count 는 심화 1회마다 monotonic 하게 +1 하는 카운터다. deepening_count 가
    Deepening_Cap 에 도달하면 should_deepen 은 **커버리지 지표와 무관하게 항상 False** 를
    반환한다(조기 반환 게이트). 따라서 카운터가 0 에서 시작해 매 반복 +1 하면 최대
    Deepening_Cap 회 후 반드시 게이트가 닫혀 루프가 종료한다.

검증 축(본 태스크 지정):
    - Property A: 임의 metrics + deepening_count >= cap → should_deepen 은 **항상 False**
      (유한 종료 게이트 — 커버리지가 아무리 부족해도 cap 도달이면 심화 없음).
    - Property B: should_deepen 을 반복 호출하며 deepening_count 를 +1 하는 mock 심화 루프
      (run_deep_research 의 while-루프 모델)는 **임의의 metrics 시퀀스**에 대해 <= cap 회
      안에 종료한다(P13 핵심 불변식).
    - 보강: cap 미만에서는 심화 판정이 커버리지 기준과 정확히 일치(결정성, 요구사항 5.7),
      항상-심화 최악 metrics 에서는 루프가 정확히 cap 회 실행, 음수 cap 은 0 으로 정규화(하한),
      `AE_MAX_DEEPENING` env → from_env → cap 게이트 배선(요구사항 5.8/9.6).

`ResearchMetrics`(research/models.py)는 `coverage_sources`/`coverage_providers`/
`citation_accuracy`(= 1 − 미검증비율)를 보유하므로, should_deepen 은 미검증 비율을
`1 − citation_accuracy` 로 유도한다(`_synthesize_report` 산출 규약과 정합). 테스트는 이
규약대로 metrics 를 구성하고, 오라클은 **구현이 유도하는 것과 동일한 부동소수 식**으로
미검증 비율을 계산해 경계값 flake 를 배제한다(동어반복이 아니라 문서화된 5.7 규칙의 독립
재기술).

Stack: Python 3.11+, hypothesis. 기존 scripts/test_research_*_pbt.py 관례를 따른다
(sys.path 삽입 후 ai_engine import, @settings(max_examples>=100), __main__ 에서 pytest 실행).
config 는 항상 명시적으로 주입해 os.environ 에 의존하지 않는다(밀폐/hermetic).
"""
from __future__ import annotations

import sys
from pathlib import Path

# ai_engine 패키지를 직접 실행 시에도 import 할 수 있도록 저장소 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402
from hypothesis import example, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ai_engine.research.config import DeepResearchConfig  # noqa: E402
from ai_engine.research.deep_research import should_deepen  # noqa: E402
from ai_engine.research.models import ResearchMetrics  # noqa: E402

# 최소 반복 200회(기존 research PBT 관례). 순수 함수라 실제 네트워크·부작용이 없다.
# deadline 은 끈다(루프 모델 테스트의 CI 슬로우/스케줄링 flake 방지).
_PBT = settings(max_examples=200, deadline=None)


# --------------------------------------------------------------------------- #
# 입력 생성기(strategies) — cap 경계(below/at/above)와 커버리지 부족/충분을 두루 겨냥
# --------------------------------------------------------------------------- #
_cap = st.integers(min_value=0, max_value=8)               # Deepening_Cap 후보
_cov_sources = st.integers(min_value=0, max_value=30)      # 고유 소스 수
_cov_providers = st.integers(min_value=0, max_value=10)    # 고유 제공자 수
_unverified = st.floats(                                   # 미검증 인용 비율 [0,1]
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)
_min_sources = st.integers(min_value=0, max_value=15)      # Min_Sources 임계
_min_providers = st.integers(min_value=0, max_value=6)     # Min_Providers 임계
_threshold = st.floats(                                    # Unverified_Threshold 임계
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)
# deepening_count: 음수도 포함(구현은 음수를 0 으로 정규화 — P8/P13 하한 확인).
_count = st.integers(min_value=-3, max_value=25)

# 한 라운드의 커버리지 지표 묶음(임의 metrics 시퀀스 생성용).
_metric_tuple = st.tuples(_cov_sources, _cov_providers, _unverified)


# --------------------------------------------------------------------------- #
# 헬퍼 — 독립 오라클(구현의 private 를 import 하지 않고 스펙을 재기술)
# --------------------------------------------------------------------------- #
def _clamp01(x: float) -> float:
    """[0,1] 클램프(독립 재구현)."""
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _config(
    cap: int,
    min_sources: int = 5,
    min_providers: int = 2,
    threshold: float = 0.2,
) -> DeepResearchConfig:
    """지정 임계값으로 DeepResearchConfig 를 직접 구성한다(다른 필드는 표 기본값).

    dataclass 라 일부 kwargs 만 주면 나머지는 기본값을 사용한다. should_deepen 은
    max_deepening/min_sources/min_providers/unverified_threshold 만 참조한다.
    """
    return DeepResearchConfig(
        max_deepening=cap,
        min_sources=min_sources,
        min_providers=min_providers,
        unverified_threshold=threshold,
    )


def _metrics(cov_sources: int, cov_providers: int, unverified_ratio: float) -> ResearchMetrics:
    """커버리지 지표를 ResearchMetrics 로 구성한다(citation_accuracy = 1 − 미검증비율 규약).

    should_deepen 은 미검증 비율을 `1 − citation_accuracy` 로 유도하므로, 미검증 비율을
    직접 통제하려면 citation_accuracy 를 `1 − unverified_ratio` 로 설정한다(_synthesize_report
    산출 규약과 동일).
    """
    return ResearchMetrics(
        coverage_sources=cov_sources,
        coverage_providers=cov_providers,
        citation_accuracy=1.0 - unverified_ratio,
    )


def _effective_unverified(unverified_ratio: float) -> float:
    """구현이 metrics 에서 유도하는 미검증 비율과 **동일한 부동소수 식**으로 계산한다.

    구현: `clamp01(1 − citation_accuracy)`, 여기서 citation_accuracy = `1 − unverified_ratio`.
    동일 입력·동일 식이므로 저장/복원 과정의 부동소수 차이가 없어 임계값 경계에서 오라클이
    구현과 정확히 일치한다(경계 flake 배제).
    """
    citation_accuracy = 1.0 - unverified_ratio
    return _clamp01(1.0 - citation_accuracy)


def _coverage_deficient(
    cov_sources: int,
    cov_providers: int,
    unverified_ratio: float,
    min_sources: int,
    min_providers: int,
    threshold: float,
) -> bool:
    """요구사항 5.7 심화 후보 규칙의 독립 오라클(소스<min OR 제공자<min OR 미검증>임계)."""
    return (
        cov_sources < min_sources
        or cov_providers < min_providers
        or _effective_unverified(unverified_ratio) > threshold
    )


@st.composite
def _below_cap_case(draw):
    """cap >= 1 이고 0 <= deepening_count < cap 인 (cap, count) 쌍(cap 미만 판정 검증용)."""
    cap = draw(st.integers(min_value=1, max_value=8))
    count = draw(st.integers(min_value=0, max_value=cap - 1))
    return cap, count


# =========================================================================== #
# Property A — 유한 종료 게이트: deepening_count >= cap → 항상 False
# =========================================================================== #
@_PBT
@given(
    cap=_cap,
    extra=st.integers(min_value=0, max_value=25),
    cov_s=_cov_sources,
    cov_p=_cov_providers,
    uv=_unverified,
    min_s=_min_sources,
    min_p=_min_providers,
    thr=_threshold,
)
# 결정적 경계 케이스를 매 실행 포함.
@example(cap=0, extra=0, cov_s=0, cov_p=0, uv=1.0, min_s=5, min_p=2, thr=0.2)   # count=0 >= cap=0
@example(cap=3, extra=0, cov_s=0, cov_p=0, uv=1.0, min_s=5, min_p=2, thr=0.0)   # count == cap(경계)
@example(cap=3, extra=100, cov_s=0, cov_p=0, uv=1.0, min_s=10**6, min_p=10**6, thr=0.0)  # count >> cap
def test_cap_gate_forces_false_regardless_of_metrics(
    cap, extra, cov_s, cov_p, uv, min_s, min_p, thr
):
    """deepening_count >= Deepening_Cap 이면 커버리지가 아무리 부족해도 should_deepen 은 항상 False (P13)."""
    config = _config(cap, min_s, min_p, thr)
    metrics = _metrics(cov_s, cov_p, uv)
    deepening_count = cap + extra  # 항상 >= cap (extra >= 0)
    result = should_deepen(metrics, deepening_count, config)
    assert result is False, (
        "P13 위반: deepening_count>=Deepening_Cap 인데 should_deepen 이 True 를 반환했다.\n"
        f"  cap={cap} count={deepening_count} cov_s={cov_s} cov_p={cov_p} uv={uv} "
        f"(min_s={min_s} min_p={min_p} thr={thr})"
    )


# =========================================================================== #
# Property B — mock 심화 루프는 임의 metrics 시퀀스에 대해 <= cap 회 안에 종료 (P13 핵심)
# =========================================================================== #
@_PBT
@given(
    cap=_cap,
    seq=st.lists(_metric_tuple, min_size=0, max_size=15),
    min_s=_min_sources,
    min_p=_min_providers,
    thr=_threshold,
)
# 빈 시퀀스(→ 항상-심화 metrics)로 cap 경계(0/8)를 결정적으로 포함.
@example(cap=0, seq=[], min_s=5, min_p=2, thr=0.2)
@example(cap=8, seq=[], min_s=5, min_p=2, thr=0.2)
def test_deepening_loop_terminates_within_cap(cap, seq, min_s, min_p, thr):
    """run_deep_research 의 while-루프 모델: 임의 metrics 시퀀스에도 심화 횟수 <= cap 로 유한 종료(P13)."""
    config = _config(cap, min_s, min_p, thr)

    def next_metrics(i: int) -> ResearchMetrics:
        # 매 반복 새 리포트(새 metrics)를 산출하는 실제 루프를 모델링한다. 시퀀스가 비면
        # 항상-심화(0 소스/0 제공자/100% 미검증) metrics 로 최악 상황을 강제한다.
        if seq:
            cov_s, cov_p, uv = seq[i % len(seq)]
            return _metrics(cov_s, cov_p, uv)
        return _metrics(0, 0, 1.0)

    deepening_count = 0
    guard = 0
    # 유계 안전망: 게이트가 정상이면 루프는 <= cap 회다. 초과하면 종료하지 않은 것(P13 위반).
    max_guard = cap + 50
    while should_deepen(next_metrics(guard), deepening_count, config):
        deepening_count += 1
        guard += 1
        assert guard <= max_guard, (
            f"P13 위반: 심화 루프가 cap={cap} 내에 종료하지 않았다 (guard={guard})."
        )
    assert deepening_count <= cap, (
        f"P13 위반: 최종 deepening_count={deepening_count} 가 Deepening_Cap={cap} 를 초과했다."
    )


# =========================================================================== #
# 보강 1 — 항상-심화 최악 metrics: 루프는 정확히 cap 회 실행 후 종료
# =========================================================================== #
@_PBT
@given(cap=_cap)
@example(cap=0)
@example(cap=1)
@example(cap=8)
def test_always_deficient_loop_runs_exactly_cap_times(cap):
    """metrics 가 항상 심화를 원해도(0 소스/0 제공자/100% 미검증) 루프는 정확히 cap 회 후 종료(P13)."""
    # 임계값을 극단으로: 어떤 coverage 도 부족, 미검증 비율은 임계 초과 → 오직 cap 게이트만 종료 가능.
    config = _config(cap, min_sources=10**6, min_providers=10**6, threshold=0.0)
    metrics = _metrics(0, 0, 1.0)  # 항상 심화 후보

    deepening_count = 0
    guard = 0
    max_guard = cap + 100
    while should_deepen(metrics, deepening_count, config):
        deepening_count += 1
        guard += 1
        assert guard <= max_guard, (
            f"P13 위반: 항상-심화 metrics 에서 루프가 종료하지 않음 (cap={cap})."
        )
    assert deepening_count == cap, (
        "항상-심화 최악 케이스에서 심화 횟수는 정확히 Deepening_Cap 여야 한다: "
        f"got {deepening_count}, cap={cap}"
    )


# =========================================================================== #
# 보강 2 — cap 미만에서는 심화 판정 ⇔ 커버리지 부족 (요구사항 5.7, 결정성)
# =========================================================================== #
@_PBT
@given(
    cc=_below_cap_case(),
    cov_s=_cov_sources,
    cov_p=_cov_providers,
    uv=_unverified,
    min_s=_min_sources,
    min_p=_min_providers,
    thr=_threshold,
)
def test_below_cap_matches_coverage_criteria(cc, cov_s, cov_p, uv, min_s, min_p, thr):
    """deepening_count < cap 이면 should_deepen ⇔ (소스<min OR 제공자<min OR 미검증>임계) (요구사항 5.7)."""
    cap, count = cc
    config = _config(cap, min_s, min_p, thr)
    metrics = _metrics(cov_s, cov_p, uv)
    result = should_deepen(metrics, count, config)
    expected = _coverage_deficient(cov_s, cov_p, uv, min_s, min_p, thr)
    assert result is expected, (
        "cap 미만 심화 판정이 커버리지 기준과 불일치(요구사항 5.7).\n"
        f"  cap={cap} count={count} cov_s={cov_s}(min {min_s}) cov_p={cov_p}(min {min_p}) "
        f"uv={uv}(thr {thr}) → expected={expected} result={result}"
    )


# =========================================================================== #
# 보강 3 — 순수·결정적: 같은 입력 → 같은 bool (외부 상태 비의존)
# =========================================================================== #
@_PBT
@given(
    cap=_cap,
    count=_count,
    cov_s=_cov_sources,
    cov_p=_cov_providers,
    uv=_unverified,
    min_s=_min_sources,
    min_p=_min_providers,
    thr=_threshold,
)
def test_should_deepen_is_deterministic_and_bool(cap, count, cov_s, cov_p, uv, min_s, min_p, thr):
    """should_deepen 은 순수·결정적이며 항상 bool 을 반환한다(동일 입력 → 동일 결과)."""
    config = _config(cap, min_s, min_p, thr)
    metrics = _metrics(cov_s, cov_p, uv)
    r1 = should_deepen(metrics, count, config)
    r2 = should_deepen(metrics, count, config)
    assert isinstance(r1, bool)
    assert r1 is r2


# =========================================================================== #
# 보강 4 — 음수 Deepening_Cap 은 0 으로 정규화되어 어떤 커버리지에서도 심화하지 않음(하한)
# =========================================================================== #
@_PBT
@given(
    neg_cap=st.integers(min_value=-8, max_value=-1),
    count=st.integers(min_value=0, max_value=15),
    cov_s=_cov_sources,
    cov_p=_cov_providers,
    uv=_unverified,
)
def test_negative_cap_never_deepens(neg_cap, count, cov_s, cov_p, uv):
    """음수 Deepening_Cap → 0 정규화 → deepening_count(>=0) >= 0 이므로 항상 False (유한 종료 하한 P13)."""
    config = _config(neg_cap, min_sources=10**6, min_providers=10**6, threshold=0.0)
    metrics = _metrics(cov_s, cov_p, uv)
    assert should_deepen(metrics, count, config) is False


# =========================================================================== #
# 보강 5 — AE_MAX_DEEPENING env → from_env → cap 게이트 배선 (요구사항 5.8/9.6)
# =========================================================================== #
@_PBT
@given(
    cap=st.integers(min_value=0, max_value=8),
    extra=st.integers(min_value=0, max_value=20),
    cov_s=_cov_sources,
    cov_p=_cov_providers,
    uv=_unverified,
)
def test_cap_gate_via_from_env_max_deepening(cap, extra, cov_s, cov_p, uv):
    """AE_MAX_DEEPENING(Deepening_Cap) → from_env → should_deepen: count>=cap 이면 항상 False (5.8/9.6)."""
    # 밀폐: os.environ 이 아니라 명시적 env 매핑을 from_env 에 주입한다.
    config = DeepResearchConfig.from_env(
        {
            "AE_MAX_DEEPENING": str(cap),
            "AE_RESEARCH_MIN_SOURCES": "1000000",
            "AE_RESEARCH_MIN_PROVIDERS": "1000000",
            "AE_RESEARCH_UNVERIFIED_THRESHOLD": "0.0",
        }
    )
    assert config.max_deepening == cap  # env → config 배선 확인
    metrics = _metrics(cov_s, cov_p, uv)
    # 임계값이 극단이라 metrics 는 항상 심화 후보이나, count>=cap 게이트가 그를 눌러 False.
    assert should_deepen(metrics, cap + extra, config) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
