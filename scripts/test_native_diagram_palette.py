"""Task 11.2 검증 — matplotlib 팔레트 주입 (요구사항 7.3 / 7.5 / 7.6).

직접 실행형: ai_engine/.venv/bin/python scripts/test_native_diagram_palette.py

검증 항목:
  1. palette=None → 기존 기본 색상 사용, path JSON 정상 반환 (요구사항 7.5/5.2).
  2. palette=['#112233','#445566'] → 그 색상이 PNG에 실제로 나타남, primary=palette[0] (요구사항 7.3).
  3. _normalize_palette / _build_palette per-token 폴백 (요구사항 7.6).
"""
import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

import server  # noqa: E402


def _png_colors(abs_path):
    """PNG에서 등장하는 (r,g,b) 색상 집합을 반환 (알파 무시)."""
    from PIL import Image
    with Image.open(abs_path) as im:
        rgb = im.convert("RGB")
        return {c for _, c in rgb.getcolors(maxcolors=1_000_000)}


def _hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _near(colors, target, tol=10):
    """target 색이 colors 집합 내에 (허용오차 tol 이내로) 존재하는지."""
    tr, tg, tb = target
    for (r, g, b) in colors:
        if abs(r - tr) <= tol and abs(g - tg) <= tol and abs(b - tb) <= tol:
            return True
    return False


def test_normalize_and_build_palette():
    # 유효 2색 이상 → 대문자 #RRGGBB, primary 첫 항목 유지
    p = server._normalize_palette(["#112233", "#445566", "#778899"])
    assert p == ["#112233", "#445566", "#778899"], p
    # None / 비리스트 / 빈 리스트 → None
    assert server._normalize_palette(None) is None
    assert server._normalize_palette("notalist") is None
    assert server._normalize_palette([]) is None
    # 유효 1색뿐 → 2색 미만이라 None (요구사항 7.5 폴백)
    assert server._normalize_palette(["#112233"]) is None
    # per-token 폴백 — 무효 토큰 제외, 남은 2색이 유효하면 사용 (요구사항 7.6)
    assert server._normalize_palette(["bad", "#112233", "zzz", "#445566"]) == [
        "#112233", "#445566"]
    # 무효 제외 후 1색뿐 → None
    assert server._normalize_palette(["#112233", "bad", "nope"]) is None
    # '#' 없는 6자리, 소문자 → 정규화
    assert server._normalize_palette(["112233", "aabbcc"]) == ["#112233", "#AABBCC"]
    # _build_palette: profile dict → [primary, secondary, accent]
    prof = {
        "primaryColor": "#112233", "secondaryColor": "#445566",
        "accentColor": "#778899", "textColor": "#000000",
        "backgroundColor": "#FFFFFF",
    }
    assert server._build_palette(prof) == ["#112233", "#445566", "#778899"]
    assert server._build_palette(prof)[0] == "#112233"  # primary 첫 항목 (요구사항 7.3)
    assert server._build_palette(None) is None
    # primary만 유효, 나머지 무효 → 2색 미만 → None
    assert server._build_palette({"primaryColor": "#112233"}) is None
    print("  [OK] _normalize_palette / _build_palette per-token 폴백 (요구사항 7.3/7.6)")


def test_default_behavior_unchanged():
    """palette=None → 기본 색상, path JSON 정상 (요구사항 7.5/5.2)."""
    tmp = tempfile.mkdtemp(prefix="ae-pal-")
    content = "Frontend: React\nBackend: FastAPI\nDatabase: Postgres"
    res = asyncio.run(server._tool_generate_native_diagram(
        "architecture", "Default", content, project_path=tmp, palette=None,
    ))
    d = json.loads(res)
    assert d.get("path"), d
    abs_path = os.path.join(tmp, d["path"])
    assert os.path.isfile(abs_path), abs_path
    colors = _png_colors(abs_path)
    # 기존 하드코딩 색상(프론트엔드 레이어 edge #3c78d8)이 남아있어야 함
    assert _near(colors, _hex_rgb("#3c78d8")), "default edge color #3c78d8 missing"
    # 주입 색이 등장하면 안 됨
    assert not _near(colors, _hex_rgb("#112233"), tol=4), "injected color leaked into default"
    print("  [OK] palette=None → 기본 색상 유지 + path JSON 정상 (요구사항 7.5/5.2)")


def test_palette_applied():
    """palette=['#112233','#445566'] → 그 색이 PNG에 실제 등장 (요구사항 7.3)."""
    tmp = tempfile.mkdtemp(prefix="ae-pal-")
    pal = ["#112233", "#445566"]
    for dtype, content in [
        ("architecture", "Frontend: React\nBackend: FastAPI\nDB: Postgres"),
        ("flow", "Ingest -> Validate -> Output"),
        ("block", "First\nSecond\nThird"),
        ("tree", "src/\n    a.py\n    b.py"),
    ]:
        res = asyncio.run(server._tool_generate_native_diagram(
            dtype, f"Palette-{dtype}", content, project_path=tmp, palette=pal,
        ))
        d = json.loads(res)
        assert d.get("path"), (dtype, d)
        abs_path = os.path.join(tmp, d["path"])
        colors = _png_colors(abs_path)
        # primary(palette[0]) edge 색이 실제로 그려졌는지 (요구사항 7.3)
        assert _near(colors, _hex_rgb("#112233")), \
            f"{dtype}: primary palette color #112233 not found in output"
        print(f"  [OK] {dtype}: primary={pal[0]} 적용 확인 (요구사항 7.3)")


if __name__ == "__main__":
    print("== Task 11.2: matplotlib 팔레트 주입 검증 ==")
    test_normalize_and_build_palette()
    test_default_behavior_unchanged()
    test_palette_applied()
    print("ALL PASSED")
