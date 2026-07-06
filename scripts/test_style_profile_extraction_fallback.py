"""Task 3.3 — Style_Profile per-token 폴백 단위 테스트.

설계 §구성요소 2(per-token 폴백 표)와 요구사항 3.1/3.3 검증.

`ai_engine/template_manager.py`의 이미 구현된 함수를 *그대로 사용*한다:
  - extract_style_profile(prs) : 테마 XML에서 7키(raw/None) 추출
  - build_style_profile(prs)   : 추출값에 per-token 폴백 적용 → 완성 dict
  - _first_real_family(stack)  : 폰트 스택 → 1–64자 단일 패밀리
  - _theme_element(prs)        : 테마 lxml 루트
구현 코드는 수정하지 않는다.

검증 케이스:
  1. 정상 테마   : python-pptx 기본 Presentation()의 내장 테마에서 추출 시 7토큰이 모두
                   추출값(정규화)으로 채워지고 SLIDE_DESIGN 폴백이 아님.
  2. 일부 누락/무효: 일부 토큰만 누락/무효일 때 그 토큰만 SLIDE_DESIGN 기본값으로 대체되고
                   정상 토큰은 추출값을 유지함.
  3. 전부 누락   : 모든 토큰이 부재여도 7토큰이 모두 비어 있지 않음(색=#RRGGBB, 폰트=1–64자).

build_style_profile(prs)는 내부에서 extract_style_profile(prs)를 호출하므로, 누락/무효
케이스는 모듈 전역 extract_style_profile를 monkeypatch해 raw 토큰을 직접 주입하는 최소
단위로 검증한다(이 경로는 python-pptx 없이도 동작). 정상 케이스만 python-pptx 필요.

실행:
  ai_engine/.venv/bin/python scripts/test_style_profile_extraction_fallback.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

try:
    from pptx import Presentation

    _HAS_PPTX = True
except ImportError:
    _HAS_PPTX = False

import template_manager as tm
from slide_templates import SLIDE_DESIGN
from style_profile import normalize_color

_HEX6 = re.compile(r"^#[0-9A-F]{6}$")

_COLOR_FIELDS = (
    "primaryColor",
    "secondaryColor",
    "accentColor",
    "textColor",
    "backgroundColor",
)
_FONT_FIELDS = ("headingFont", "bodyFont")
_ALL_FIELDS = _COLOR_FIELDS + _FONT_FIELDS

# 설계 §구성요소 2 per-token 폴백 표 — Style_Profile 필드 → SLIDE_DESIGN 키.
_COLOR_FALLBACK = {
    "primaryColor": "primary",
    "secondaryColor": "secondary",
    "accentColor": "accent",
    "textColor": "text_dark",
    "backgroundColor": "bg_light",
}
_FONT_FALLBACK = {
    "headingFont": "font_heading",
    "bodyFont": "font_body",
}


def _expected_color_fallback(field):
    """누락/무효 색상 토큰의 기대 폴백값(SLIDE_DESIGN 대응값을 #RRGGBB로 정규화)."""
    return normalize_color(SLIDE_DESIGN[_COLOR_FALLBACK[field]])


def _expected_font_fallback(field):
    """누락/무효 폰트 토큰의 기대 폴백값(SLIDE_DESIGN 폰트 스택의 첫 실패밀리)."""
    return tm._first_real_family(SLIDE_DESIGN[_FONT_FALLBACK[field]])


def _check_all_nonempty(profile, where):
    """결과 Style_Profile의 7토큰이 모두 비어 있지 않은지 검증(요구사항 3.1).

    색상 5종은 대문자 #RRGGBB, 폰트 2종은 1–64자 문자열이어야 한다.
    """
    assert isinstance(profile, dict), f"{where}: profile is not a dict: {profile!r}"
    for f in _COLOR_FIELDS:
        v = profile.get(f)
        assert isinstance(v, str) and _HEX6.match(v), (
            f"{where}: color {f}={v!r} is not a valid #RRGGBB"
        )
    for f in _FONT_FIELDS:
        v = profile.get(f)
        assert isinstance(v, str) and 1 <= len(v) <= 64, (
            f"{where}: font {f}={v!r} is not a 1-64 char string"
        )


def _patched_build(fake_raw):
    """extract_style_profile를 주어진 raw dict로 대체한 뒤 build_style_profile 결과 반환.

    build_style_profile는 내부에서 모듈 전역 extract_style_profile를 호출하므로,
    전역을 임시 교체하면 raw 토큰을 제어할 수 있다(prs 인자는 사용되지 않음).
    """
    orig = tm.extract_style_profile
    tm.extract_style_profile = lambda prs: dict(fake_raw)
    try:
        return tm.build_style_profile(None)
    finally:
        tm.extract_style_profile = orig


def case_normal_theme():
    """1. 정상 테마: 기본 내장 테마에서 7토큰이 모두 추출값으로 채워짐."""
    if not _HAS_PPTX:
        print("SKIP  case_normal_theme — python-pptx 미설치")
        return None

    prs = Presentation()
    raw = tm.extract_style_profile(prs)
    profile = tm.build_style_profile(prs)

    _check_all_nonempty(profile, "normal-theme")

    # 정상 추출된 토큰은 추출값(정규화)을 유지하며 SLIDE_DESIGN 폴백이 아니어야 한다.
    for f in _COLOR_FIELDS:
        rawv = raw.get(f)
        norm = normalize_color(rawv) if isinstance(rawv, str) else None
        assert norm is not None, (
            f"normal-theme: 기본 테마에서 {f} 추출 실패 (raw={rawv!r})"
        )
        assert profile[f] == norm, (
            f"normal-theme: {f} 추출값 미유지 — got {profile[f]!r}, expected {norm!r}"
        )

    for f in _FONT_FIELDS:
        rawv = raw.get(f)
        fam = tm._first_real_family(rawv) if isinstance(rawv, str) else None
        assert fam is not None, (
            f"normal-theme: 기본 테마에서 {f} 추출 실패 (raw={rawv!r})"
        )
        assert profile[f] == fam, (
            f"normal-theme: {f} 추출값 미유지 — got {profile[f]!r}, expected {fam!r}"
        )

    print("PASS  case_normal_theme — 7토큰 모두 추출값 유지")
    print(f"        raw     = {raw}")
    print(f"        profile = {profile}")
    return profile


def case_partial_fallback():
    """2. 일부 누락/무효: 무효 토큰만 SLIDE_DESIGN 폴백, 정상 토큰은 추출값 유지."""
    fake_raw = {
        "primaryColor": "4472C4",          # 유효(# 없는 6자리) → #4472C4 유지
        "secondaryColor": None,            # 누락 → 폴백
        "accentColor": "ZZZ",              # 무효(16진수 아님) → 폴백
        "textColor": "1a1a2e",             # 유효(소문자) → #1A1A2E 유지
        "backgroundColor": "#fff",         # 무효(3자리 축약) → 폴백
        "headingFont": "Times New Roman",  # 유효 → 유지
        "bodyFont": "",                    # 무효(빈 문자열) → 폴백
    }
    profile = _patched_build(fake_raw)

    _check_all_nonempty(profile, "partial")

    # --- 정상 토큰은 추출값(정규화) 유지 ---
    assert profile["primaryColor"] == "#4472C4", profile["primaryColor"]
    assert profile["textColor"] == "#1A1A2E", profile["textColor"]
    assert profile["headingFont"] == "Times New Roman", profile["headingFont"]

    # --- 누락/무효 토큰만 SLIDE_DESIGN 대응 기본값으로 대체 ---
    assert profile["secondaryColor"] == _expected_color_fallback("secondaryColor"), (
        profile["secondaryColor"]
    )
    assert profile["accentColor"] == _expected_color_fallback("accentColor"), (
        profile["accentColor"]
    )
    assert profile["backgroundColor"] == _expected_color_fallback("backgroundColor"), (
        profile["backgroundColor"]
    )
    assert profile["bodyFont"] == _expected_font_fallback("bodyFont"), (
        profile["bodyFont"]
    )

    # 정상 토큰이 우연히 폴백값과 같지 않은지(=폴백이 일어나지 않았음을) 명시적으로 확인.
    assert profile["primaryColor"] != _expected_color_fallback("primaryColor"), (
        "primaryColor 추출값이 폴백값과 같아 케이스 변별 불가"
    )

    print("PASS  case_partial_fallback — 무효 토큰만 폴백, 정상 토큰 유지")
    print(f"        profile = {profile}")
    return profile


def case_all_missing():
    """3. 전부 누락: 모든 토큰 부재여도 7토큰이 모두 비어 있지 않고 모두 폴백값."""
    empty = {k: None for k in _ALL_FIELDS}
    profile = _patched_build(empty)

    _check_all_nonempty(profile, "all-missing")

    for f in _COLOR_FIELDS:
        assert profile[f] == _expected_color_fallback(f), (
            f"all-missing: {f} 폴백 불일치 — got {profile[f]!r}, "
            f"expected {_expected_color_fallback(f)!r}"
        )
    for f in _FONT_FIELDS:
        assert profile[f] == _expected_font_fallback(f), (
            f"all-missing: {f} 폴백 불일치 — got {profile[f]!r}, "
            f"expected {_expected_font_fallback(f)!r}"
        )

    print("PASS  case_all_missing — 전 토큰 SLIDE_DESIGN 폴백, 7토큰 비어있지 않음")
    print(f"        profile = {profile}")
    return profile


def main():
    print("=" * 72)
    print("Task 3.3 — Style_Profile per-token 폴백 단위 테스트")
    print(f"python-pptx 설치 여부: {_HAS_PPTX}")
    print("=" * 72)

    cases = (case_normal_theme, case_partial_fallback, case_all_missing)
    failures = []
    for case in cases:
        try:
            case()
        except AssertionError as exc:
            failures.append((case.__name__, str(exc)))
            print(f"FAIL  {case.__name__} — {exc}")
        except Exception as exc:  # noqa: BLE001 - 진단용 전체 포착
            failures.append((case.__name__, repr(exc)))
            print(f"ERROR {case.__name__} — {exc!r}")

    print("-" * 72)
    if failures:
        print(f"RESULT: FAILED ({len(failures)}/{len(cases)} 케이스 실패)")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print(f"RESULT: ALL PASSED ({len(cases)}/{len(cases)} 케이스)")
    sys.exit(0)


if __name__ == "__main__":
    main()
