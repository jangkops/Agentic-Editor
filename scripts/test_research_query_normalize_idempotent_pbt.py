# Feature: deep-research-engine, Property 11: 질의 정규화 멱등성
"""Property-based test: 질의 정규화(`normalize_query`)의 멱등성.

Feature: deep-research-engine, Property 11: 질의 정규화 멱등성
**Validates: Requirements 4.7, 5**

For any 질의 문자열 `q`에 대해:
    normalize_query(normalize_query(q)) == normalize_query(q)

즉 정규화를 2회 적용한 결과가 1회 적용한 결과와 동일해야 한다(멱등성 — P11 /
요구사항 4.7). 이는 정규화 결과를 캐시 키·질의 대조에 안전하게 재사용하기 위한
핵심 불변식이다(design.md "Property 11", requirements.md 4.7).

검증 대상은 순수 함수 `ai_engine.research.normalize.normalize_query` 하나다(외부
상태 비의존이므로 100% 결정적 — 속성 테스트에 이상적).

생성기(hypothesis strategies)는 정규화가 실제로 손대는 입력 공간을 지능적으로
겨냥하도록 다음 엣지 케이스를 **명시적으로** 포함한다(design.md "생성기 엣지
케이스", 본 태스크 요구):
    - 빈 문자열("")
    - 공백류: ASCII 공백/탭/개행/CR/FF/VT + 유니코드 공백(U+00A0 NBSP,
      U+2003 EM SPACE, U+3000 IDEOGRAPHIC SPACE, U+2028/U+2029 라인/문단 분리자 등)
    - 대소문자 혼합(대문자·소문자)
    - 전각(fullwidth) 문자: U+FF01–U+FF5E 전각 ASCII 변형 + U+3000
    - 합자(ligature) 및 호환 분해 대상: ﬁ/ﬀ/ﬂ(U+FB00–), ½, ㎡, 로마숫자(Ⅷ) 등
    - 결합 문자(combining marks): U+0300–U+036F 범위. canonical combining class가
      서로 다른 마크(예: U+0307 ccc=230, U+0316 ccc=220)를 포함해 정규 재정렬 경계를
      자극한다.
    - 소문자화 시 결합 문자를 도입/접는 까다로운 base: İ(U+0130), ẛ(U+1E9B), ß, ẞ,
      µ(U+00B5), Å(U+212B), Ω, 점 없는 ı 등
    - 제어 문자(control): U+0000–U+001F, U+007F

Stack: Python 3.11+, hypothesis library. 기존 scripts/test_*_pbt.py 관례를 따른다
(sys.path 삽입 후 ai_engine import, __main__ 에서 pytest 실행).
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

from ai_engine.research.normalize import normalize_query  # noqa: E402


# --------------------------------------------------------------------------- #
# 생성기 — 정규화가 손대는 입력 공간을 지능적으로 겨냥 (스마트 제너레이터)
# --------------------------------------------------------------------------- #

# 공백류: ASCII 공백 + 정규화 대상 유니코드 공백(전각/비분리/라인·문단 분리자 포함).
_WHITESPACE = st.sampled_from(
    [
        " ", "\t", "\n", "\r", "\f", "\v",
        "\u00A0",  # NO-BREAK SPACE
        "\u2003",  # EM SPACE
        "\u200A",  # HAIR SPACE
        "\u2028",  # LINE SEPARATOR
        "\u2029",  # PARAGRAPH SEPARATOR
        "\u3000",  # IDEOGRAPHIC SPACE (전각 공백)
    ]
)

# 대소문자 혼합.
_ASCII_CASE = st.sampled_from(list("aAbBmMzZiIkK"))

# 전각 ASCII 변형(U+FF01–U+FF5E) + 전각 공백.
_FULLWIDTH = st.sampled_from([chr(cp) for cp in range(0xFF01, 0xFF5F)] + ["\u3000"])

# 합자 및 호환 분해 대상(NFKC 로 접히는 문자).
_LIGATURE_COMPAT = st.sampled_from(
    [
        "\uFB00",  # ﬀ
        "\uFB01",  # ﬁ
        "\uFB02",  # ﬂ
        "\uFB03",  # ﬃ
        "\uFB04",  # ﬄ
        "\uFB05",  # ﬅ (long s + t)
        "\uFB06",  # ﬆ
        "½", "¼", "㎡", "㎥", "㎏",
        "Ⅷ", "ⅷ", "Ⅳ",  # 로마숫자(호환)
        "\u3371",  # ㍱ SQUARE HPA (호환 분해)
    ]
)

# 결합 문자: ccc 가 서로 다른 마크를 포함해 정규 재정렬 경계를 자극한다.
_COMBINING = st.sampled_from(
    [
        "\u0300",  # GRAVE (ccc 230)
        "\u0301",  # ACUTE (ccc 230)
        "\u0307",  # DOT ABOVE (ccc 230)
        "\u0308",  # DIAERESIS (ccc 230)
        "\u0316",  # GRAVE ACCENT BELOW (ccc 220)
        "\u0323",  # DOT BELOW (ccc 220)
        "\u0327",  # CEDILLA (ccc 202)
        "\u031B",  # HORN (ccc 216)
        "\u0345",  # YPOGEGRAMMENI (ccc 240)
    ]
)

# 소문자화 시 결합 문자를 도입/접는 까다로운 base(멱등 경계를 강하게 자극).
_TRICKY_BASE = st.sampled_from(
    [
        "\u0130",  # İ  LATIN CAPITAL LETTER I WITH DOT ABOVE (lower → i + U+0307)
        "\u1E9B",  # ẛ  LATIN SMALL LETTER LONG S WITH DOT ABOVE
        "I", "i", "\u0131",  # ı DOTLESS I
        "ß", "\u1E9E",       # ẞ CAPITAL SHARP S
        "\u00B5",  # µ MICRO SIGN
        "\u212B",  # Å ANGSTROM SIGN
        "\u2126",  # Ω OHM SIGN
        "\u01F0",  # ǰ
        "\u1F80",  # ᾀ (그리스 이중 분해)
    ]
)

# 제어 문자.
_CONTROL = st.sampled_from([chr(cp) for cp in range(0x00, 0x20)] + ["\u007F"])

# 개별 흥미 문자 풀.
_special_char = st.one_of(
    _WHITESPACE,
    _ASCII_CASE,
    _FULLWIDTH,
    _LIGATURE_COMPAT,
    _COMBINING,
    _TRICKY_BASE,
    _CONTROL,
)

# 한 조각(chunk)은 흥미 문자이거나 넓은 유니코드 텍스트 런이다.
_chunk = st.one_of(_special_char, st.text(max_size=3))

# 최종 질의 생성기: 빈 문자열 / 넓은 유니코드 / 흥미 문자 혼합을 모두 포섭.
_query = st.one_of(
    st.just(""),
    st.text(max_size=48),
    st.text(
        alphabet=st.characters(min_codepoint=0, max_codepoint=0x2FFFF),
        max_size=24,
    ),
    st.lists(_chunk, max_size=16).map("".join),
)


# --------------------------------------------------------------------------- #
# Property 11 — 멱등성: normalize_query(normalize_query(q)) == normalize_query(q)
# --------------------------------------------------------------------------- #

@settings(max_examples=200)
@given(q=_query)
# 결정적으로 각 실행마다 핵심 엣지 케이스를 반드시 포함한다.
@example(q="")                              # 빈 문자열
@example(q="   ")                           # 공백만
@example(q="  Ｈｅｌｌｏ　ＷＯＲＬＤ ")       # 전각 + 전각공백 + 앞뒤 공백
@example(q="ﬁ ﬀ ½ Ⅷ")                       # 합자·호환 분해
@example(q="\u0130\u0316")                  # İ + 아래 그레이브(결합 재정렬 경계)
@example(q="\u0130\u0301\u0316")            # İ + 위/아래 결합 마크 혼합
@example(q="ẛ\u0323")                        # ẛ + 아래 점
@example(q="A\u0300b\u0301C")               # 대소문자 + 결합 마크
@example(q="tab\tnew\nline\u3000space")     # 다양한 공백류
@example(q="\x00\x01ctrl\x7f")              # 제어 문자
def test_normalize_query_idempotent(q: str) -> None:
    """정규화는 멱등하다: 2회 적용 == 1회 적용 (P11 / 요구사항 4.7)."""
    once = normalize_query(q)
    twice = normalize_query(once)
    assert once == twice, (
        "질의 정규화 멱등성 위반(P11): "
        f"normalize_query(normalize_query(q)) != normalize_query(q)\n"
        f"  q       = {q!r}\n"
        f"  once    = {once!r}  codepoints={[hex(ord(c)) for c in once]}\n"
        f"  twice   = {twice!r}  codepoints={[hex(ord(c)) for c in twice]}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
