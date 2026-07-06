"""Task 1.5 검증 — deserialize 검증 에러 단위 테스트 (요구사항 4.5 / 4.6 / 4.7).

직접 실행형: ai_engine/.venv/bin/python scripts/test_style_profile_validation.py

검증 항목:
  1. (요구사항 4.5) 구문상 잘못된 JSON → StyleProfileError(code='invalid-json').
  2. (요구사항 4.6) 필수 필드(primaryColor/textColor/headingFont/bodyFont) 중 하나
     이상 누락 → StyleProfileError(code='invalid-style-profile')이며 missing에
     누락된 모든 필드명이 포함됨. 부분 객체가 반환되지 않음(예외 raise) 확인.
  3. (요구사항 4.7) 색상 필드가 '#' + 6자리 hex 형식이 아님 → 해당 field를 포함한
     StyleProfileError(code='invalid-color')이며 첫 실패에서 즉시 검증 중단됨 확인.

구현 코드(ai_engine/style_profile.py)는 수정하지 않는다. deserialize는 실패 시
StyleProfileError(ValueError 서브타입)를 raise하며, .code/.missing/.field 속성으로
원인을 표현한다.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

from style_profile import StyleProfile, StyleProfileError, deserialize, serialize  # noqa: E402


def _expect_error(text, *, expected_code):
    """deserialize(text)가 StyleProfileError(code=expected_code)를 raise하는지 검사하고
    잡은 예외를 반환한다. 객체를 반환하면(예외 미발생) 즉시 실패시킨다."""
    try:
        result = deserialize(text)
    except StyleProfileError as exc:
        assert exc.code == expected_code, \
            f"expected code={expected_code!r}, got {exc.code!r} (input={text!r})"
        # StyleProfileError는 ValueError 서브타입이어야 함 (설계 §구성요소 3).
        assert isinstance(exc, ValueError), "StyleProfileError must subclass ValueError"
        return exc
    raise AssertionError(
        f"expected StyleProfileError(code={expected_code!r}) but got object: {result!r}"
    )


# 7개 필드를 모두 채운 유효 기준 페이로드 (개별 테스트에서 변형해 사용).
_VALID = {
    "primaryColor": "#1E1E1E",
    "secondaryColor": "#00C896",
    "accentColor": "#FF6B35",
    "textColor": "#202020",
    "backgroundColor": "#FAFAFA",
    "headingFont": "Pretendard",
    "bodyFont": "Noto Sans KR",
}


def _json_without(*drop_fields):
    """_VALID에서 drop_fields를 제외한 JSON 문자열을 생성."""
    import json
    data = {k: v for k, v in _VALID.items() if k not in drop_fields}
    return json.dumps(data)


def _json_with(**overrides):
    """_VALID에 overrides를 적용한 JSON 문자열을 생성."""
    import json
    data = dict(_VALID)
    data.update(overrides)
    return json.dumps(data)


def test_sanity_valid_roundtrips():
    """전제 확인: 유효 페이로드는 정상 역직렬화되며 왕복 보존을 만족한다."""
    import json
    p = deserialize(json.dumps(_VALID))
    assert isinstance(p, StyleProfile), p
    # serialize ∘ deserialize ∘ serialize 안정성 (참고: 요구사항 4.4)
    assert serialize(p) == serialize(deserialize(serialize(p)))
    print("  [OK] 유효 페이로드 정상 역직렬화 + 왕복 안정 (전제 확인)")


def test_invalid_json():
    """요구사항 4.5: 구문상 잘못된 JSON → invalid-json."""
    # 대표 케이스: 미완성 객체.
    _expect_error("{not json", expected_code="invalid-json")
    # 추가 손상 입력들도 동일하게 invalid-json 이어야 한다.
    for bad in ["", "   ", "{", "[1,2,", '{"primaryColor": }', "}{", "null xyz"]:
        _expect_error(bad, expected_code="invalid-json")
    # 최상위가 객체가 아닌 유효 JSON(배열/스칼라) → 필수 필드 존재 불가 → invalid-json.
    for non_object in ["[]", '["#1E1E1E"]', '"#1E1E1E"', "123", "true", "null"]:
        _expect_error(non_object, expected_code="invalid-json")
    print("  [OK] 구문상 잘못된 JSON / 비객체 입력 → invalid-json (요구사항 4.5)")


def test_missing_required_single():
    """요구사항 4.6: 필수 필드 하나 누락 → invalid-style-profile, missing에 그 필드 포함."""
    for field in ("primaryColor", "textColor", "headingFont", "bodyFont"):
        exc = _expect_error(_json_without(field), expected_code="invalid-style-profile")
        assert exc.missing == [field], \
            f"missing should be [{field!r}], got {exc.missing!r}"
        # 부분 객체가 만들어지지 않음을 보장 — field 속성은 색상용이므로 None.
        assert exc.field is None, f"field should be None for missing-required, got {exc.field!r}"
    print("  [OK] 필수 필드 단일 누락 → invalid-style-profile + missing=[field] (요구사항 4.6)")


def test_missing_required_multiple():
    """요구사항 4.6: 여러 필수 필드 누락 → 누락된 모든 필드명이 missing에 포함."""
    # 일부(2개) 누락
    exc = _expect_error(
        _json_without("primaryColor", "bodyFont"),
        expected_code="invalid-style-profile",
    )
    assert set(exc.missing) == {"primaryColor", "bodyFont"}, exc.missing
    # 필수 필드 전체 누락 → 4개 모두 보고.
    exc_all = _expect_error(
        _json_without("primaryColor", "textColor", "headingFont", "bodyFont"),
        expected_code="invalid-style-profile",
    )
    assert set(exc_all.missing) == {
        "primaryColor", "textColor", "headingFont", "bodyFont"
    }, exc_all.missing
    # 빈 객체 {} → 필수 4개 모두 누락.
    exc_empty = _expect_error("{}", expected_code="invalid-style-profile")
    assert set(exc_empty.missing) == {
        "primaryColor", "textColor", "headingFont", "bodyFont"
    }, exc_empty.missing
    print("  [OK] 다중 필수 필드 누락 → missing에 모든 누락 필드 포함, 부분 객체 미생성 (요구사항 4.6)")


def test_invalid_color_field_reported():
    """요구사항 4.7: 색상 형식 위반 → invalid-color, field에 해당 색상 필드명 포함."""
    bad_values = ["red", "#12", "#GGGGGG", "1E1E1", "#1E1E1E1", "#FFF", ""]
    for field in ("primaryColor", "textColor"):  # 필수 색상 필드 대표 검증
        for bad in bad_values:
            exc = _expect_error(_json_with(**{field: bad}),
                                expected_code="invalid-color")
            assert exc.field == field, \
                f"field should be {field!r} for value {bad!r}, got {exc.field!r}"
            assert exc.missing is None, "missing should be None for invalid-color"
    # 선택 색상 필드(존재하지만 무효)도 invalid-color로 보고되어야 한다.
    exc_opt = _expect_error(_json_with(secondaryColor="not-a-color"),
                            expected_code="invalid-color")
    assert exc_opt.field == "secondaryColor", exc_opt.field
    print("  [OK] 색상 형식 위반(red/#12/#GGGGGG 등) → invalid-color + field 보고 (요구사항 4.7)")


def test_invalid_color_stops_at_first_failure():
    """요구사항 4.7: 색상 검증은 첫 실패에서 즉시 중단(COLOR_FIELDS 선언 순서 기준)."""
    # COLOR_FIELDS 순서: primary, secondary, accent, text, background.
    # primaryColor와 textColor 둘 다 무효지만, 먼저 검사되는 primaryColor가 보고됨.
    exc = _expect_error(
        _json_with(primaryColor="bad1", textColor="bad2"),
        expected_code="invalid-color",
    )
    assert exc.field == "primaryColor", \
        f"first failure should be primaryColor (COLOR_FIELDS 순서), got {exc.field!r}"
    # secondaryColor와 textColor 둘 다 무효 → secondary가 text보다 먼저 검사됨.
    exc2 = _expect_error(
        _json_with(secondaryColor="bad", textColor="alsobad"),
        expected_code="invalid-color",
    )
    assert exc2.field == "secondaryColor", \
        f"first failure should be secondaryColor, got {exc2.field!r}"
    print("  [OK] 다중 색상 무효 시 COLOR_FIELDS 선언 순서상 첫 실패에서 즉시 중단 (요구사항 4.7)")


def test_validation_order_missing_before_color():
    """검증 순서: 필수 누락(4.6)이 색상 형식(4.7)보다 먼저 평가된다.

    필수 필드 누락과 색상 무효가 동시에 있으면 invalid-style-profile이 우선되어야
    한다(부분 객체 생성 금지 — 색상 정규화 이전에 누락을 먼저 검사).
    """
    # primaryColor 누락 + 남은 textColor 무효 → invalid-style-profile 우선.
    text = _json_without("primaryColor")
    import json
    data = json.loads(text)
    data["textColor"] = "not-a-color"  # 색상 무효를 추가로 주입
    exc = _expect_error(json.dumps(data), expected_code="invalid-style-profile")
    assert exc.missing == ["primaryColor"], exc.missing
    print("  [OK] 검증 순서: invalid-style-profile(4.6)이 invalid-color(4.7)보다 우선")


if __name__ == "__main__":
    print("== Task 1.5: deserialize 검증 에러 단위 테스트 ==")
    test_sanity_valid_roundtrips()
    test_invalid_json()
    test_missing_required_single()
    test_missing_required_multiple()
    test_invalid_color_field_reported()
    test_invalid_color_stops_at_first_failure()
    test_validation_order_missing_before_color()
    print("ALL PASSED")
