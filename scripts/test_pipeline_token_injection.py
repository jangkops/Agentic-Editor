"""Task 11.4 검증 — 배경 Tier 토큰 주입 격리 단위 테스트 (요구사항 7.6 / 9.4).

직접 실행형: ai_engine/.venv/bin/python scripts/test_pipeline_token_injection.py

목적
  Style_Profile 지정 시 각 배경 Tier가 SP 토큰을 사용하고, 특정 토큰이 부재/무효일 때
  해당 토큰만 기본값으로 대체하며 렌더링을 중단(예외)하지 않고 계속 진행함을 검증한다.

범위 / 중복 회피
  - 11.2(scripts/test_native_diagram_palette.py)가 _normalize_palette 다양한 케이스와
    실제 PNG 렌더링(palette None / palette 적용)을 이미 검증했다. 본 테스트는 중복을
    피하고 보완적으로 다음에 집중한다:
      1. Tier 1 Mermaid 테마 헤더 주입 (요구사항 7.2 보강 + 7.6) — stub gateway로
         네트워크 호출 없이 LLM 비의존 부분(테마 헤더 조립/주입)만 검증.
      2. Tier 2 matplotlib _build_palette per-token 폴백 (요구사항 7.3 + 7.6) —
         Style_Profile 관점의 무효 토큰 제외/2색 미만 None 폴백.
      3. 격리/중단 없음 (요구사항 9.4) — 무효 토큰을 섞은 어떤 profile에도 헬퍼들이
         예외를 던지지 않고 항상 유효 결과 또는 안전한 None 폴백을 반환.

중요: Bedrock/Vertex 네트워크 호출을 절대 발생시키지 않는다. Mermaid 함수는 stub
      gateway를 주입해 LLM 호출을 가로채고, 그 이후 순수 테마 주입 로직만 테스트한다.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

import server  # noqa: E402


class _StubGateway:
    """네트워크 없이 고정 mermaid 코드를 반환하는 가짜 gateway.

    `_llm_generate_mermaid`가 `gw.converse(model_id, messages, system)`를 await 한다.
    실제 Bedrock Gateway를 호출하지 않으므로 어떤 네트워크 트래픽도 발생하지 않는다.
    """

    def __init__(self, text="graph TD\n  A[시작] --> B[끝]"):
        self._text = text
        self.calls = []

    async def converse(self, model_id, messages, system):
        self.calls.append((model_id, messages, system))
        return {
            "decision": "ALLOW",
            "output": {"message": {"content": [{"text": self._text}]}},
        }


def _gen_mermaid(gw, profile):
    """_llm_generate_mermaid를 동기적으로 실행하는 헬퍼."""
    return asyncio.run(server._llm_generate_mermaid(
        gw, "stub-model", "섹션 제목", "섹션 본문", style_profile=profile,
    ))


# ---------------------------------------------------------------------------
# 1. Tier 1 Mermaid 테마 헤더 주입 (요구사항 7.2 보강 + 7.6)
# ---------------------------------------------------------------------------

def test_mermaid_theme_injection_valid():
    """유효 primary/text → `%%{init...}%%` 테마 헤더가 SP 색으로 주입되고 본문 보존."""
    gw = _StubGateway()
    profile = {
        "primaryColor": "#112233",
        "textColor": "#445566",
        # 나머지 토큰은 mermaid 주입에 사용되지 않으므로 임의값/부재 무관.
        "headingFont": "Inter",
        "bodyFont": "Roboto",
    }
    out = _gen_mermaid(gw, profile)
    assert out.lstrip().startswith("%%{init"), out
    assert "#112233" in out, "primaryColor 미주입"
    assert "#445566" in out, "textColor 미주입"
    assert "graph TD" in out, "원본 mermaid 코드가 보존되어야 함"
    assert len(gw.calls) == 1, "gateway.converse가 정확히 1회 호출돼야 함(네트워크 stub)"
    print("  [OK] 유효 SP → Mermaid 테마 헤더 주입 + 본문 보존 (요구사항 7.2)")


def test_mermaid_no_profile_no_injection():
    """profile None → 주입 없음, 출력이 stub 원본과 바이트 단위 동일 (요구사항 7.5)."""
    gw = _StubGateway()
    out = _gen_mermaid(gw, None)
    assert not out.lstrip().startswith("%%{init"), out
    assert out.strip() == "graph TD\n  A[시작] --> B[끝]", repr(out)
    print("  [OK] profile None → 테마 주입 없음, 기존 출력 보존 (요구사항 7.5)")


def test_mermaid_invalid_token_no_injection_no_crash():
    """primary/text 중 하나라도 무효면 주입하지 않으며, 예외 없이 계속 (요구사항 7.6/9.4)."""
    gw = _StubGateway()
    # primary 무효(형식 위반), text 유효 → 둘 다 유효해야 주입되므로 주입 안 됨.
    out1 = _gen_mermaid(gw, {"primaryColor": "nothex", "textColor": "#445566"})
    assert not out1.lstrip().startswith("%%{init"), out1
    # text 무효, primary 유효 → 주입 안 됨.
    out2 = _gen_mermaid(gw, {"primaryColor": "#112233", "textColor": "ZZZ"})
    assert not out2.lstrip().startswith("%%{init"), out2
    # 색상 키 자체가 부재 → 주입 없음, 예외 없음.
    out3 = _gen_mermaid(gw, {"headingFont": "Inter"})
    assert not out3.lstrip().startswith("%%{init"), out3
    # 비문자열/구조 이상 토큰 → normalize_color가 None 반환, 예외 없음.
    out4 = _gen_mermaid(gw, {"primaryColor": 123, "textColor": ["#445566"]})
    assert isinstance(out4, str) and not out4.lstrip().startswith("%%{init"), out4
    print("  [OK] primary/text 일부·전부 무효 → 주입 없음, 중단 없음 (요구사항 7.6/9.4)")


def test_mermaid_lowercase_color_normalized():
    """소문자/`#` 생략 색상도 대문자 #RRGGBB로 정규화되어 주입 (요구사항 3.2/7.2)."""
    gw = _StubGateway()
    out = _gen_mermaid(gw, {"primaryColor": "aabbcc", "textColor": "#ddeeff"})
    assert out.lstrip().startswith("%%{init"), out
    assert "#AABBCC" in out, "primaryColor 대문자 정규화 실패"
    assert "#DDEEFF" in out, "textColor 대문자 정규화 실패"
    print("  [OK] 소문자/# 생략 색상 → 대문자 #RRGGBB 정규화 후 주입 (요구사항 3.2/7.2)")


# ---------------------------------------------------------------------------
# 2. Tier 2 matplotlib _build_palette per-token 폴백 (요구사항 7.3 + 7.6)
# ---------------------------------------------------------------------------

def test_build_palette_per_token_fallback():
    """무효 토큰만 제외하고 유효 색은 유지, primary가 첫 항목, 2색 미만이면 None."""
    # primary 유효 + secondary 무효 + accent 유효 → [primary, accent] (무효만 제외, 7.6)
    pal = server._build_palette({
        "primaryColor": "#112233", "secondaryColor": "bad", "accentColor": "#778899",
    })
    assert pal == ["#112233", "#778899"], pal
    assert pal[0] == "#112233", "primary가 팔레트 첫 항목이어야 함 (요구사항 7.3)"

    # primary 무효 + secondary/accent 유효 → primary만 제외, 남은 유효색 유지(2색)
    pal2 = server._build_palette({
        "primaryColor": "zzz", "secondaryColor": "#445566", "accentColor": "#778899",
    })
    assert pal2 == ["#445566", "#778899"], pal2

    # 유효 1색뿐(나머지 무효) → 2색 미만 → None (기본 팔레트 폴백, 요구사항 7.5)
    assert server._build_palette({
        "primaryColor": "#112233", "secondaryColor": "x", "accentColor": "y",
    }) is None

    # profile None / 비 dict → None (기본 색상 폴백)
    assert server._build_palette(None) is None
    assert server._build_palette("not-a-dict") is None
    assert server._build_palette(["#112233", "#445566"]) is None  # list는 dict 아님
    print("  [OK] _build_palette per-token 폴백: 무효만 제외·primary 우선·2색미만 None (7.3/7.6)")


# ---------------------------------------------------------------------------
# 3. 격리 / 중단 없음 (요구사항 9.4)
# ---------------------------------------------------------------------------

def test_face_edge_helpers_never_raise():
    """_hex_to_face_edge / _palette_face_edges가 무효 입력에도 예외 없이 안전한 값 반환."""
    # 무효 입력 → None, 예외 없음
    for bad in ("notacolor", "", "#FFF", None, 123, ["#112233"]):
        assert server._hex_to_face_edge(bad) is None, bad
    # 유효 색 → (face, edge), edge는 원색 유지
    fe = server._hex_to_face_edge("#112233")
    assert fe is not None and fe[1] == "#112233", fe

    # _palette_face_edges: None/1쌍 → None, 2쌍 이상 → 리스트
    assert server._palette_face_edges(None) is None
    assert server._palette_face_edges(["#112233"]) is None
    assert server._palette_face_edges(["bad", "#112233"]) is None  # 유효쌍 1개 → None
    fe2 = server._palette_face_edges(["#112233", "#445566"])
    assert fe2 is not None and len(fe2) == 2, fe2
    print("  [OK] _hex_to_face_edge / _palette_face_edges 무효 입력에도 예외 없음 (요구사항 9.4)")


def test_isolation_mixed_invalid_profiles_no_crash():
    """무효 토큰을 섞은 어떤 profile에도 모든 헬퍼가 예외 없이 안전한 결과를 반환."""
    messy_profiles = [
        {"primaryColor": "#112233", "secondaryColor": None, "accentColor": 123, "textColor": "bad"},
        {"primaryColor": "", "secondaryColor": "#445566", "accentColor": "#778899", "textColor": "#000000"},
        {},
        {"primaryColor": "#ABCABC"},
        {"primaryColor": ["list"], "secondaryColor": {"d": 1}, "accentColor": 3.14},
        {"primaryColor": "#112233", "secondaryColor": "#445566", "accentColor": "#778899",
         "textColor": "#101010"},
    ]
    gw = _StubGateway()
    for prof in messy_profiles:
        # matplotlib 팔레트: 예외 없이 None 또는 2색 이상 리스트
        pal = server._build_palette(prof)
        assert pal is None or (isinstance(pal, list) and len(pal) >= 2), (prof, pal)
        if pal:
            fe = server._palette_face_edges(pal)
            assert fe is None or len(fe) >= 2, (prof, fe)
        # Mermaid: 예외 없이 항상 문자열 반환 (네트워크 stub)
        out = _gen_mermaid(gw, prof)
        assert isinstance(out, str), (prof, type(out))
    print("  [OK] 무효 토큰 혼합 profile 전체 → 헬퍼 예외 없음, 안전 폴백 (요구사항 9.4)")


if __name__ == "__main__":
    print("== Task 11.4: 배경 Tier 토큰 주입 격리 검증 ==")
    print("[Tier 1] Mermaid 테마 헤더 주입 (요구사항 7.2/7.6)")
    test_mermaid_theme_injection_valid()
    test_mermaid_no_profile_no_injection()
    test_mermaid_invalid_token_no_injection_no_crash()
    test_mermaid_lowercase_color_normalized()
    print("[Tier 2] matplotlib 팔레트 per-token 폴백 (요구사항 7.3/7.6)")
    test_build_palette_per_token_fallback()
    print("[격리] 중단 없음 (요구사항 9.4)")
    test_face_edge_helpers_never_raise()
    test_isolation_mixed_invalid_profiles_no_crash()
    print("ALL PASSED")
