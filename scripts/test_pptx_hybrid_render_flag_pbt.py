"""Feature: pptx-ultra-quality-hybrid-render, Property 20: Feature Flag 파서의 결정성

`ai_engine.server._hybrid_render_enabled(env)` 순수 함수에 대한 property-based + 단위 테스트.

검증 대상 (A안: 기본 ON + 킬스위치 — 제품 결정으로 default-OFF 계약을 의도적으로 반전):
  - Property 20 (Task 1.2): 임의 문자열 입력에 대해 `"0"`(공백 제거 기준)에서만 False,
    그 외 전부(미설정/""/"1"/인식 불가 값) True, 어떤 입력에도 예외 미발생,
    동일 입력 → 동일 출력(결정론).
  - Task 1.3: 대표 입력("1"/"0"/""/"true"/"on"/"2")의 개별 반환값 단언 및
    인식 불가 값에서 경고 로그가 정확히 1줄 기록되되 반환은 True(기본 ON)임을
    stdout(print) 캡처로 관측.

HERMETIC: 네트워크/ Vertex / 게이트웨이 호출 0. 순수 함수만 호출한다.
실행: ./venv/bin/python -m pytest scripts/test_pptx_hybrid_render_flag_pbt.py -p no:cacheprovider -q
"""

from __future__ import annotations

import os
import sys

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.server import _hybrid_render_enabled  # noqa: E402


# 구현은 입력을 str(env).strip() 후 판정한다 (기본 ON + 킬스위치):
#   strip 결과 == "0"  → False (명시적 킬스위치)
#   그 외 전부          → True  (미설정/""/"1"/인식 불가 값 모두 기본 ON;
#                                인식 불가 값은 경고 1줄 후 True)
def _expected(env: str) -> bool:
    return env.strip() != "0"


# ---------------------------------------------------------------------------
# Task 1.2 — Property 20: Feature Flag 파서의 결정성 (PBT, 100+ iterations)
# Validates: Requirements 1.7, 6.1, 6.2, 6.5
# ---------------------------------------------------------------------------

@settings(max_examples=300)
@given(env=st.text())
def test_property20_false_only_for_zero_never_raises(env: str) -> None:
    """임의 문자열 입력: "0"(공백 제거 기준)에서만 False, 그 외 True, 예외 미발생."""
    result = _hybrid_render_enabled(env)  # 어떤 입력에도 raise하지 않아야 함
    assert isinstance(result, bool)
    assert result == _expected(env)


@settings(max_examples=300)
@given(env=st.text())
def test_property20_deterministic_same_input_same_output(env: str) -> None:
    """결정론: 동일 입력을 반복 호출해도 동일 출력."""
    first = _hybrid_render_enabled(env)
    for _ in range(3):
        assert _hybrid_render_enabled(env) == first


@settings(max_examples=200)
@given(
    prefix=st.sampled_from(["", " ", "\t", "\n", "  ", "\u00a0", " \t "]),
    suffix=st.sampled_from(["", " ", "\t", "\n", "  ", "\u3000"]),
)
def test_property20_whitespace_padded_zero_is_false(prefix: str, suffix: str) -> None:
    """ASCII 공백으로만 둘러싼 "0"은 False (strip 규칙). 그 외는 _expected와 일치."""
    env = f"{prefix}0{suffix}"
    assert _hybrid_render_enabled(env) == _expected(env)


@settings(max_examples=200)
@given(
    env=st.text(
        alphabet=st.characters(min_codepoint=0x80, max_codepoint=0x2FFF),
        min_size=1,
        max_size=12,
    )
)
def test_property20_unicode_inputs_are_true_and_safe(env: str) -> None:
    """유니코드(비-ASCII) 문자열은 "0"이 될 수 없으므로 항상 True(기본 ON), 예외 미발생."""
    result = _hybrid_render_enabled(env)
    assert result is True


# ---------------------------------------------------------------------------
# Task 1.3 — 인식 불가 값 경고 로그 단위 테스트
# Validates: Requirements 6.5
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "env, expected",
    [
        ("1", True),
        ("0", False),
        ("", True),
        ("true", True),
        ("on", True),
        ("2", True),
    ],
)
def test_individual_return_values(env: str, expected: bool) -> None:
    """대표 입력의 개별 반환값 단언 (기본 ON + 킬스위치: "0"만 False)."""
    assert _hybrid_render_enabled(env) is expected


@pytest.mark.parametrize("env", ["true", "on", "2", "yes", "enable", "TRUE"])
def test_unrecognized_value_emits_exactly_one_warning_line(env, capsys) -> None:
    """인식 불가 값 → 경고 로그가 정확히 1줄(≤200자) 기록되되 반환은 True(기본 ON)."""
    result = _hybrid_render_enabled(env)
    assert result is True
    captured = capsys.readouterr()
    warning_lines = [
        ln for ln in captured.out.splitlines() if "[HybridRender]" in ln
    ]
    assert len(warning_lines) == 1
    assert len(warning_lines[0]) <= 200


@pytest.mark.parametrize("env", ["1", "0", ""])
def test_recognized_values_emit_no_warning(env, capsys) -> None:
    """인식 가능한 값("1"/"0"/"")은 경고 로그를 남기지 않는다."""
    _hybrid_render_enabled(env)
    captured = capsys.readouterr()
    warning_lines = [
        ln for ln in captured.out.splitlines() if "[HybridRender]" in ln
    ]
    assert warning_lines == []
