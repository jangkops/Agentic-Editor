"""Task 9.3 검증 — design_tokens_for_profile per-token 오버라이드 단위 테스트
(요구사항 7.5 / 7.6, 7.1 보강).

직접 실행형: ai_engine/.venv/bin/python scripts/test_design_tokens_override.py

대상: ai_engine/slide_templates.py 의 design_tokens_for_profile(profile) 헬퍼와
SLIDE_DESIGN 상수. 구현 코드는 수정하지 않는다.

design_tokens_for_profile는 Style_Profile 매핑(dict 또는 None)을 받아 SLIDE_DESIGN
모양의 *새* 토큰 dict를 반환한다(원본 SLIDE_DESIGN은 절대 변형하지 않음).

매핑(설계 §구성요소 5):
  primaryColor → primary, textColor → text_dark, backgroundColor → bg_light,
  accentColor → accent, secondaryColor → secondary,
  headingFont → font_heading, bodyFont → font_body.

검증 항목:
  1. (요구사항 7.5) profile=None → SLIDE_DESIGN과 동일한 토큰을 가진 사본 반환.
     원본 SLIDE_DESIGN이 변형되지 않음(별개 객체, 반환값 변형이 원본에 전파 안 됨).
  2. (요구사항 7.1 보강) 모든 토큰이 유효한 profile → 7개 매핑 키가 모두 SP 값으로
     교체됨(색상은 #RRGGBB 정규화). 매핑되지 않은 키는 SLIDE_DESIGN 기본값 유지.
  3. (요구사항 7.6) 일부 토큰만 무효(색상이 #RRGGBB 아님 / 폰트가 빈 문자열·64자
     초과) → 그 무효 토큰만 SLIDE_DESIGN 기본값 유지, 나머지 유효 토큰은 SP 값 적용,
     호출은 예외 없이 완료.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

from slide_templates import SLIDE_DESIGN, design_tokens_for_profile  # noqa: E402
from style_profile import normalize_color  # noqa: E402


# Style_Profile key → SLIDE_DESIGN key (설계 §구성요소 5). 테스트가 구현의
# _PROFILE_*_MAP과 독립적으로 동일 매핑을 명시해 회귀를 잡는다.
_COLOR_MAP = {
    "primaryColor": "primary",
    "textColor": "text_dark",
    "backgroundColor": "bg_light",
    "accentColor": "accent",
    "secondaryColor": "secondary",
}
_FONT_MAP = {
    "headingFont": "font_heading",
    "bodyFont": "font_body",
}
_ALL_MAPPED_DESIGN_KEYS = set(_COLOR_MAP.values()) | set(_FONT_MAP.values())

# 모든 토큰이 유효한 기준 Style_Profile (dict — design_tokens_for_profile은 dict를
# 받는다). 색상은 의도적으로 소문자/혼합을 섞어 normalize_color 정규화도 함께 검증하며,
# 모든 값을 SLIDE_DESIGN 기본값과 *다르게* 골라 요구사항 7.1의 "기본값 아님"을 검증한다.
_VALID_PROFILE = {
    "primaryColor": "#1e90ff",     # ≠ 기본 #0066FF
    "secondaryColor": "#112233",   # ≠ 기본 #00C896
    "accentColor": "#445566",      # ≠ 기본 #FF6B35
    "textColor": "#202020",        # ≠ 기본 #1A1A1A
    "backgroundColor": "#eeeeee",  # ≠ 기본 #FAFAFA
    "headingFont": "Pretendard",   # ≠ 기본 폰트 스택
    "bodyFont": "Noto Sans KR",    # ≠ 기본 폰트 스택
}


def test_profile_none_equals_slide_design_copy():
    """요구사항 7.5: profile=None → SLIDE_DESIGN과 동일 토큰, 단 별개의 사본."""
    tokens = design_tokens_for_profile(None)
    # 내용은 SLIDE_DESIGN과 완전히 동일해야 한다(원본 기본값).
    assert tokens == SLIDE_DESIGN, (
        "profile=None 반환은 SLIDE_DESIGN과 키/값이 동일해야 함"
    )
    # 그러나 원본과 같은 객체여서는 안 된다(사본 반환).
    assert tokens is not SLIDE_DESIGN, "반환 dict는 SLIDE_DESIGN 원본과 별개 객체여야 함"

    # 반환 dict를 변형해도 원본 SLIDE_DESIGN이 오염되지 않아야 한다.
    before = dict(SLIDE_DESIGN)
    tokens["primary"] = "#000000"
    tokens["__probe__"] = "x"
    assert SLIDE_DESIGN == before, "반환 dict 변형이 SLIDE_DESIGN 원본에 전파되면 안 됨"
    print("  [OK] profile=None → SLIDE_DESIGN 동일 + 별개 사본(원본 불변) (요구사항 7.5)")


def test_non_dict_profile_returns_defaults_copy():
    """비-dict 입력(None 외)도 안전하게 SLIDE_DESIGN 기본값 사본을 반환한다."""
    for bad in ("not-a-dict", 123, [], ("a",), object()):
        tokens = design_tokens_for_profile(bad)
        assert tokens == SLIDE_DESIGN, f"비-dict 입력 {bad!r} → 기본값 사본이어야 함"
        assert tokens is not SLIDE_DESIGN, "비-dict 입력도 별개 사본 반환"
    print("  [OK] 비-dict 입력 → SLIDE_DESIGN 기본값 사본(예외 없음)")


def test_all_valid_profile_overrides_all_mapped_tokens():
    """요구사항 7.1: 모든 토큰 유효 → 7개 매핑 키가 SP 값으로 교체(색상 정규화)."""
    tokens = design_tokens_for_profile(_VALID_PROFILE)

    # 색상 5종: normalize_color로 정규화된 대문자 #RRGGBB와 일치해야 한다.
    for profile_key, design_key in _COLOR_MAP.items():
        expected = normalize_color(_VALID_PROFILE[profile_key])
        assert expected is not None, f"테스트 전제: {profile_key} 는 유효 색상이어야 함"
        assert tokens[design_key] == expected, (
            f"{design_key} 는 SP {profile_key}={_VALID_PROFILE[profile_key]!r} "
            f"정규화값 {expected!r} 이어야 함, got {tokens[design_key]!r}"
        )
        # 기본값과 달라야(=실제 교체됨) 한다 (요구사항 7.1 "기본값 아님").
        assert tokens[design_key] != SLIDE_DESIGN[design_key], (
            f"{design_key} 는 SLIDE_DESIGN 기본값에서 교체되어야 함"
        )

    # 폰트 2종: 그대로 적용.
    for profile_key, design_key in _FONT_MAP.items():
        assert tokens[design_key] == _VALID_PROFILE[profile_key], (
            f"{design_key} 는 SP {profile_key} 값으로 교체되어야 함"
        )
        assert tokens[design_key] != SLIDE_DESIGN[design_key], (
            f"{design_key} 는 기본 폰트 스택에서 교체되어야 함"
        )

    # 매핑되지 않은 키(gradient/shadow/muted 등)는 기본값을 그대로 유지.
    for key in SLIDE_DESIGN:
        if key not in _ALL_MAPPED_DESIGN_KEYS:
            assert tokens[key] == SLIDE_DESIGN[key], (
                f"매핑 외 토큰 {key} 는 SLIDE_DESIGN 기본값을 유지해야 함"
            )

    # 원본 SLIDE_DESIGN 불변 확인.
    assert SLIDE_DESIGN["primary"] == "#0066FF", "SLIDE_DESIGN 원본이 변형됨"
    print("  [OK] 모든 토큰 유효 → 7개 매핑 키 SP 값 교체 + 매핑 외 토큰 유지 (요구사항 7.1)")


def test_partial_invalid_tokens_fall_back_per_token():
    """요구사항 7.6: 일부 토큰만 무효 → 그 토큰만 기본값 유지, 나머지는 SP 값 적용."""
    # primaryColor: 유효 / secondaryColor: 무효(#RRGGBB 아님) /
    # accentColor: 유효 / textColor: 무효(3자리 축약) /
    # backgroundColor: 무효(빈 문자열) /
    # headingFont: 유효 / bodyFont: 무효(빈 문자열)
    profile = {
        "primaryColor": "#123ABC",   # 유효
        "secondaryColor": "red",     # 무효 색상
        "accentColor": "#abcdef",    # 유효
        "textColor": "#FFF",         # 무효(6자리 아님)
        "backgroundColor": "",       # 무효(빈)
        "headingFont": "Pretendard",  # 유효
        "bodyFont": "",              # 무효(빈)
    }
    tokens = design_tokens_for_profile(profile)

    # 유효 토큰은 SP 값(색상 정규화)으로 적용.
    assert tokens["primary"] == normalize_color("#123ABC") == "#123ABC"
    assert tokens["accent"] == normalize_color("#abcdef") == "#ABCDEF"
    assert tokens["font_heading"] == "Pretendard"

    # 무효 토큰은 그 토큰만 SLIDE_DESIGN 기본값 유지.
    assert tokens["secondary"] == SLIDE_DESIGN["secondary"], "무효 secondaryColor → 기본값 유지"
    assert tokens["text_dark"] == SLIDE_DESIGN["text_dark"], "무효 textColor → 기본값 유지"
    assert tokens["bg_light"] == SLIDE_DESIGN["bg_light"], "무효 backgroundColor → 기본값 유지"
    assert tokens["font_body"] == SLIDE_DESIGN["font_body"], "무효 bodyFont → 기본값 유지"
    print("  [OK] 일부 무효 색상/폰트 → 해당 토큰만 기본값, 유효 토큰은 SP 값 적용 (요구사항 7.6)")


def test_font_length_boundaries():
    """요구사항 7.6: 폰트 길이 경계 — 64자는 유효, 65자/공백-only는 무효(기본값 유지)."""
    font_64 = "F" * 64
    font_65 = "F" * 65
    profile = {
        "primaryColor": "#0066FF",
        "textColor": "#1A1A1A",
        "headingFont": font_64,      # 경계 유효(정확히 64자)
        "bodyFont": "   ",           # 무효(공백만 → strip 후 빈)
    }
    tokens = design_tokens_for_profile(profile)
    assert tokens["font_heading"] == font_64, "정확히 64자 폰트는 적용되어야 함"
    assert tokens["font_body"] == SLIDE_DESIGN["font_body"], "공백-only 폰트 → 기본값 유지"

    # 65자 폰트는 무효 → 기본값 유지.
    profile_over = {
        "primaryColor": "#0066FF",
        "textColor": "#1A1A1A",
        "headingFont": font_65,
        "bodyFont": "Roboto",
    }
    tokens_over = design_tokens_for_profile(profile_over)
    assert tokens_over["font_heading"] == SLIDE_DESIGN["font_heading"], (
        "65자 폰트는 무효 → 기본값 유지"
    )
    assert tokens_over["font_body"] == "Roboto", "유효 bodyFont는 적용"
    print("  [OK] 폰트 길이 경계(64 유효 / 65·공백 무효) per-token 폴백 (요구사항 7.6)")


def test_empty_dict_profile_keeps_all_defaults():
    """빈 dict profile → 모든 토큰 부재 → 7개 매핑 키 모두 기본값 유지(사본)."""
    tokens = design_tokens_for_profile({})
    assert tokens == SLIDE_DESIGN, "빈 dict → 매핑 토큰 부재로 전부 기본값"
    assert tokens is not SLIDE_DESIGN, "빈 dict도 별개 사본 반환"
    print("  [OK] 빈 dict profile → 전체 기본값 사본(부재 토큰 폴백) (요구사항 7.6)")


if __name__ == "__main__":
    print("== Task 9.3: design_tokens_for_profile per-token 오버라이드 단위 테스트 ==")
    test_profile_none_equals_slide_design_copy()
    test_non_dict_profile_returns_defaults_copy()
    test_all_valid_profile_overrides_all_mapped_tokens()
    test_partial_invalid_tokens_fall_back_per_token()
    test_font_length_boundaries()
    test_empty_dict_profile_keeps_all_defaults()
    print("ALL PASSED")
