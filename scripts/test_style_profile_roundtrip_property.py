"""Property 1: Style_Profile 직렬화 왕복 보존 + 결정론적 직렬화.

Validates: Requirements 4.2, 4.4

이 모듈은 본 기능(pptx-template-styling)의 PBT 1순위 대상이다. 두 가지 불변식을
hypothesis로 검증한다.

  - 왕복 보존 (요구사항 4.4):
      임의의 유효한 StyleProfile 객체 ``p`` 에 대해
        serialize(p) == serialize(deserialize(serialize(p)))
      가 **바이트 단위**로 성립한다. 즉 `직렬화 = 직렬화 ∘ 역직렬화 ∘ 직렬화`
      항등을 만족한다.

  - 결정론적 직렬화 (요구사항 4.2):
      동일한 StyleProfile 객체 ``p`` 를 여러 번 serialize 해도 매 호출마다
      바이트 단위로 동일한 JSON 문자열이 나온다(키 순서/공백 고정).

전략 설계 메모
--------------
serialize 는 객체 필드 값을 그대로 직렬화하고, deserialize 는 색상 필드에만
``normalize_color``(대문자 `#RRGGBB`)를 적용한다. 따라서 왕복 보존이 바이트
단위로 성립하려면, 생성하는 StyleProfile 의 색상 필드가 **이미 정규화된**
대문자 6자리 hex 형식이어야 한다(소문자/축약형이면 deserialize 가 대문자로
정규화하여 두 번째 serialize 결과가 첫 번째와 달라진다). 이는 요구사항 3.2/4.7
이 규정하는 정규 형태와 일치한다. 폰트 필드는 deserialize 가 변형하지 않으므로
JSON 왕복이 안정적인 1–64자 문자열(제어문자/서로게이트 제외)로 생성한다.

Run:
  ai_engine/.venv/bin/python scripts/test_style_profile_roundtrip_property.py
"""
from __future__ import annotations

import os
import sys

# Make the ai_engine package importable from the repo root (기존 scripts/ PBT 관례).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st  # noqa: E402

from ai_engine.style_profile import (  # noqa: E402
    STYLE_PROFILE_KEY_ORDER,
    StyleProfile,
    deserialize,
    serialize,
)


# ---------- hypothesis strategies ----------

# 색상: 정규 형태인 대문자 `#RRGGBB` (요구사항 3.2/4.7). 0x000000..0xFFFFFF 전 범위를
# 균일하게 덮어 normalize_color 항등성(이미 대문자 정규형 → 그대로 유지)을 보장한다.
_color = st.integers(min_value=0x000000, max_value=0xFFFFFF).map(lambda n: "#%06X" % n)

# 폰트: 1–64자 문자열. 제어문자(Cc)와 서로게이트(Cs)를 제외해 JSON 왕복이 안정적인
# 문자만 사용한다(한글/CJK 포함). deserialize 는 폰트를 변형하지 않으므로 이 문자열은
# serialize→deserialize→serialize 를 거쳐도 바이트 단위로 보존된다.
_font = st.text(
    alphabet=st.characters(
        min_codepoint=0x20,
        max_codepoint=0xFFFF,
        blacklist_categories=("Cs", "Cc"),
    ),
    min_size=1,
    max_size=64,
)

# 임의의 유효한 StyleProfile (7필드 모두 채움 → 선택 필드 기본값 채우기 경로를 타지
# 않으므로 왕복이 객체 동일성으로 닫힌다).
_style_profile = st.builds(
    StyleProfile,
    primaryColor=_color,
    secondaryColor=_color,
    accentColor=_color,
    textColor=_color,
    backgroundColor=_color,
    headingFont=_font,
    bodyFont=_font,
)


# ---------- Property 1: 왕복 보존 (요구사항 4.4) ----------

@settings(max_examples=300, deadline=None)
@given(p=_style_profile)
def test_roundtrip_preservation(p: StyleProfile):
    """serialize(p) == serialize(deserialize(serialize(p))) 바이트 단위 (요구사항 4.4)."""
    once = serialize(p)
    roundtrip = serialize(deserialize(once))
    assert once == roundtrip, (
        "왕복 보존 위반 (요구사항 4.4):\n"
        f"  serialize(p)                        = {once!r}\n"
        f"  serialize(deserialize(serialize(p)))= {roundtrip!r}"
    )


# ---------- 결정론적 직렬화 (요구사항 4.2) ----------

@settings(max_examples=300, deadline=None)
@given(p=_style_profile)
def test_deterministic_serialization(p: StyleProfile):
    """동일 객체를 여러 번 serialize → 매번 바이트 단위 동일 (요구사항 4.2)."""
    outputs = [serialize(p) for _ in range(5)]
    first = outputs[0]
    for i, out in enumerate(outputs[1:], start=1):
        assert out == first, (
            "결정론적 직렬화 위반 (요구사항 4.2):\n"
            f"  serialize 호출 #0 = {first!r}\n"
            f"  serialize 호출 #{i} = {out!r}"
        )
    # 키 순서가 STYLE_PROFILE_KEY_ORDER 로 고정되었는지도 함께 확인.
    last_pos = -1
    for key in STYLE_PROFILE_KEY_ORDER:
        pos = first.find(f'"{key}"')
        assert pos != -1, f"직렬화 출력에 키 누락: {key} ({first!r})"
        assert pos > last_pos, (
            f"키 순서가 STYLE_PROFILE_KEY_ORDER 와 불일치: {key} ({first!r})"
        )
        last_pos = pos


# ---------- deterministic sanity case ----------

def test_canonical_example_roundtrip():
    """설계 §데이터 모델의 정규 예시로 왕복·결정론을 명시적으로 점검한다.

    hypothesis 가 확률적으로 이미 덮지만, 회귀가 CI 로그에서 즉시 드러나도록
    결정론적 스모크 케이스를 남긴다.
    """
    p = StyleProfile(
        primaryColor="#0066FF",
        secondaryColor="#00C896",
        accentColor="#FF6B35",
        textColor="#1A1A1A",
        backgroundColor="#FAFAFA",
        headingFont="Apple SD Gothic Neo",
        bodyFont="Apple SD Gothic Neo",
    )
    once = serialize(p)
    # 왕복 보존 (요구사항 4.4)
    assert serialize(deserialize(once)) == once
    # 결정론 (요구사항 4.2)
    assert serialize(p) == once
    # 키 순서 + 공백 없는 고정 구분자 확인
    expected = (
        '{"primaryColor":"#0066FF","secondaryColor":"#00C896",'
        '"accentColor":"#FF6B35","textColor":"#1A1A1A",'
        '"backgroundColor":"#FAFAFA","headingFont":"Apple SD Gothic Neo",'
        '"bodyFont":"Apple SD Gothic Neo"}'
    )
    assert once == expected, f"정규 직렬화 형태 불일치:\n  got={once!r}\n  exp={expected!r}"


def main():
    print("=== Property 1: Style_Profile 직렬화 왕복 보존 + 결정론적 직렬화 ===")
    test_canonical_example_roundtrip()
    print("  deterministic canonical example                 OK")
    test_roundtrip_preservation()
    print("  hypothesis property: 왕복 보존 (요구사항 4.4)    OK")
    test_deterministic_serialization()
    print("  hypothesis property: 결정론적 직렬화 (요구사항 4.2) OK")
    print("All Property 1 cases passed.")


if __name__ == "__main__":
    main()
