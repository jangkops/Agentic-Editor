"""Regression — Genspark급 HTML 디자인 슬라이드가 직접 생성 경로에서 풀블리드로 적용된다.

사용자 요구: "고퀄리티로 젠스파크 이미지 슬라이드 급" 결과물.

수정: `_tool_generate_pptx`가 무템플릿 + Electron 브리지(render-html-to-png) 가용 시
각 슬라이드(표지 포함)를 HTML 디자인 레이아웃으로 렌더해 풀블리드 배경으로 사용한다.
브리지가 없으면(헤드리스/테스트) 네이티브 도형 경로로 자동 폴백한다(회귀 없음).

Correctness properties:
  P1. 브리지 가용 시: 모든 슬라이드가 풀블리드 HTML PNG(슬라이드 전체를 덮는 Picture)를 갖는다.
  P2. HTML 슬라이드가 적용되면 네이티브 다이어그램 추론은 일어나지 않는다.
  P3. 브리지 미가용 시: HTML 호출 없이 기존 네이티브 경로로 폴백한다.

실행: pytest scripts/test_pptx_html_slides.py -q
"""
from __future__ import annotations

import os
import sys
import io
import json
import asyncio
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "ai_engine"))

server = pytest.importorskip("ai_engine.server")
pptx = pytest.importorskip("pptx")

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f9f0000000049454e44ae426082")


async def _img_gen_disabled(*_a, **_k):
    """imagePrompt 가 있는 visual 슬라이드가 Bedrock 이미지 생성(네트워크)으로 빠지지 않게 한다."""
    return json.dumps({"error": "disabled in test"})


async def _fake_render_png(html, output_path, width=1920, height=1080, timeout=30, **_k):
    """표지 HTML→PNG(Chrome/브리지) 대체 — 유효한 PNG 를 기록한다."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(_PNG)
    return {"ok": True}


def _cleanup(res):
    ap = res.get("absPath")
    if ap and os.path.isfile(ap):
        try:
            os.remove(ap)
            if os.path.isfile(ap + ".meta.json"):
                os.remove(ap + ".meta.json")
        except OSError:
            pass


def test_html_slides_used_when_bridge_available(tmp_path, monkeypatch):
    """P1+P2 — 브리지 가용 시 모든 슬라이드가 풀블리드 HTML PNG로 채워진다."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.util import Emu

    os.environ["AE_GENERATED_ROOT"] = str(tmp_path)
    monkeypatch.setenv("AE_ENABLE_HTML_SLIDES", "1")  # 이 파일은 HTML 슬라이드 기능을 검증(기본 OFF를 opt-in)
    gen = tmp_path / ".generated"
    gen.mkdir(parents=True, exist_ok=True)

    # 브리지 가용으로 위장
    monkeypatch.setattr(server, "_call_bridge", lambda ep, payload, timeout=30.0: {"remote": False})
    monkeypatch.setattr(server, "_get_gw", lambda *a, **k: object())
    monkeypatch.setattr(server, "_specialized_model_for_task",
                        lambda *a, **k: "us.anthropic.claude-sonnet-4-6")

    calls = {"n": 0}

    async def _fake_html(gw, model, heading, body, ctx, project_path, style_profile=None, **_k):
        calls["n"] += 1
        fn = gen / f"html-slide-{calls['n']}.png"
        fn.write_bytes(_PNG)
        return f".generated/{fn.name}"

    monkeypatch.setattr(server, "_generate_html_slide_for_section", _fake_html)
    # 네이티브 다이어그램이 절대 호출되지 않아야 함(HTML 우선)
    import native_diagram_pptx as nd
    monkeypatch.setattr(nd, "build_native_diagram",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("native diagram should not run")))

    # 하이브리드 렌더(R1.2/R1.6): HTML 풀블리드는 Vertex 비활성 상태의 cover/section/visual 슬라이드 경로다.
    # 구조형(아키텍처/흐름)은 편집 가능 네이티브 도형이 주 렌더러(R1.4)라 여기서는 visual 슬라이드로 검증한다.
    monkeypatch.setattr(server, "_tool_generate_image", _img_gen_disabled)
    monkeypatch.setattr(server, "_render_html_slide_to_png", _fake_render_png)
    monkeypatch.setenv("AE_PREFER_VERTEX_IMAGE", "0")   # Vertex 비활성 → visual 은 HTML 풀블리드 경로(R1.6)
    slides = [
        {"title": "브랜드 비주얼", "bullets": ["신뢰를 최우선으로"],
         "imagePrompt": "a modern corporate office photograph, wide angle"},
        {"title": "팀 문화", "bullets": ["함께 성장"],
         "imagePrompt": "a bright collaborative workspace photograph"},
    ]
    out = asyncio.run(server._tool_generate_pptx({"title": "프로젝트 개요", "slides": slides}, ""))
    res = json.loads(out)
    try:
        assert "error" not in res, res
        prs = Presentation(res["absPath"])
        # cover + 2 content = 3, 각 슬라이드에 풀블리드 그림이 있어야 함
        assert len(prs.slides._sldIdLst) == 3
        slide_w, slide_h = prs.slide_width, prs.slide_height
        for i, sl in enumerate(prs.slides):
            if i == 0:
                # 표지: 콘텐츠를 구운 HTML 표지 풀블리드는 기본 미채택(task20 수정 B, AE_COVER_HTML_FULLBLEED=1 옵트인) —
                # 표지는 편집 가능 네이티브로 남는다. 본문 visual 슬라이드만 HTML 풀블리드를 검증한다(2026-09-16 갱신).
                continue
            pics = [sh for sh in sl.shapes if sh.shape_type == MSO_SHAPE_TYPE.PICTURE]
            assert pics, f"슬라이드 {i}에 HTML 풀블리드 그림 없음"
            # 풀블리드: 슬라이드 크기의 ~95% 이상 덮는 그림이 하나 이상
            full = [p for p in pics if p.width >= slide_w * 0.95 and p.height >= slide_h * 0.95]
            assert full, f"슬라이드 {i} 그림이 풀블리드가 아님"
        # 표지(1) + 콘텐츠(2) = 3회 HTML 렌더 호출
        # 표지는 별도 렌더러(_render_html_slide_to_png)를 쓰고 기본 미채택이라 섹션 렌더 호출은 본문 visual 2장에 대해서만 일어난다(2026-09-16 갱신).
        assert calls["n"] == 2, f"HTML 렌더 호출 수={calls['n']} (기대 2: 본문 visual 슬라이드)"
    finally:
        _cleanup(res)


def test_falls_back_to_native_when_bridge_unavailable(tmp_path, monkeypatch):
    """P3 — 브리지 미가용 시 HTML 호출 없이 네이티브 경로로 폴백."""
    from pptx import Presentation
    os.environ["AE_GENERATED_ROOT"] = str(tmp_path)
    monkeypatch.setenv("AE_ENABLE_HTML_SLIDES", "1")  # 이 파일은 HTML 슬라이드 기능을 검증(기본 OFF를 opt-in)

    monkeypatch.setattr(server, "_call_bridge", lambda *a, **k: None)  # 브리지 없음
    called = {"n": 0}

    async def _fake_html(*a, **k):
        called["n"] += 1
        return ""

    monkeypatch.setattr(server, "_generate_html_slide_for_section", _fake_html)

    slides = [{"title": "개요", "bullets": ["항목 1", "항목 2"]}]
    out = asyncio.run(server._tool_generate_pptx({"title": "덱", "slides": slides}, ""))
    res = json.loads(out)
    try:
        assert "error" not in res, res
        assert called["n"] == 0, "브리지 없는데 HTML 렌더가 호출됨"
        prs = Presentation(res["absPath"])
        assert len(prs.slides._sldIdLst) == 2
    finally:
        _cleanup(res)


def test_template_uses_html_with_style_profile(tmp_path, monkeypatch):
    """템플릿 사용 시에도 HTML 고품질 렌더를 사용한다(젠스파크급 레이아웃 + 템플릿 색/폰트).

    근거(사용자 결정): used_template일 때 HTML을 끄면 항상 휑한 네이티브 도너로 빠져
    품질이 급락. 진단 로그(used_template=True → _html_enabled=False)로 근본 원인 확정.
    따라서 템플릿이어도 HTML 렌더를 쓰되 Style_Profile(색/폰트)을 HTML 디자인 토큰에
    주입한다(_generate_html_slide_for_section의 style_profile 인자).

    Correctness properties:
      T1. 템플릿 + 브리지 가용 시 _generate_html_slide_for_section이 호출된다(HTML 사용).
      T2. style_profile이 HTML 렌더 호출에 전달된다(템플릿 색 반영).
      T3. 출력 슬라이드가 풀블리드 HTML PNG를 갖는다."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.util import Inches
    from pptx.oxml.ns import qn

    os.environ["AE_GENERATED_ROOT"] = str(tmp_path)
    monkeypatch.setenv("AE_ENABLE_HTML_SLIDES", "1")
    gen = tmp_path / ".generated"; gen.mkdir(parents=True, exist_ok=True)

    tpl = Presentation(); tpl.slide_width = Inches(13.333); tpl.slide_height = Inches(7.5)
    for i in range(3):
        s = tpl.slides.add_slide(tpl.slide_layouts[1])
        s.shapes.title.text = f"TPL_SAMPLE_{i}"
    tpl_path = tmp_path / "tpl.pptx"
    tpl.save(str(tpl_path))

    monkeypatch.setattr(server, "_call_bridge", lambda ep, payload, timeout=30.0: {"remote": False})
    monkeypatch.setattr(server, "_get_gw", lambda *a, **k: object())
    monkeypatch.setattr(server, "_specialized_model_for_task", lambda *a, **k: "us.anthropic.claude-sonnet-4-6")

    seen = {"n": 0, "profiles": []}

    async def _fake_html(gw, model, heading, body, ctx, project_path, style_profile=None, **_k):
        seen["n"] += 1
        seen["profiles"].append(style_profile)
        fn = gen / f"h-{seen['n']}.png"
        fn.write_bytes(_PNG)
        return f".generated/{fn.name}"

    monkeypatch.setattr(server, "_generate_html_slide_for_section", _fake_html)

    style_profile = {"primaryColor": "#0B5394", "textColor": "#1A1A1A",
                     "headingFont": "Pretendard", "bodyFont": "Pretendard"}
    # 구조형 슬라이드는 하이브리드에서 네이티브 도형(R1.4)이므로 HTML 렌더 검증에는 visual 슬라이드를 쓴다.
    monkeypatch.setattr(server, "_tool_generate_image", _img_gen_disabled)
    monkeypatch.setattr(server, "_render_html_slide_to_png", _fake_render_png)
    monkeypatch.setenv("AE_PREFER_VERTEX_IMAGE", "0")   # Vertex 비활성 → visual 은 HTML 풀블리드 경로(R1.6)
    slides = [{"title": "비전", "bullets": ["신뢰"], "imagePrompt": "abstract corporate vision visual"},
              {"title": "문화", "bullets": ["협업"], "imagePrompt": "team collaboration photograph"}]
    out = asyncio.run(server._tool_generate_pptx(
        {"title": "덱", "slides": slides,
         "templatePath": str(tpl_path), "styleProfile": style_profile}, ""))
    res = json.loads(out)
    try:
        assert "error" not in res, res
        # T1 — 템플릿이어도 HTML 렌더가 호출된다 (표지 + 콘텐츠)
        assert seen["n"] >= 1, "템플릿인데 HTML 렌더가 호출되지 않음(휑한 네이티브로 빠짐)"
        # T2 — style_profile이 전달된다(템플릿 색 반영)
        assert any(p == style_profile for p in seen["profiles"]), \
            f"HTML 렌더에 style_profile 미전달: {seen['profiles']}"
        # T3 — 풀블리드 HTML PNG가 슬라이드에 임베드된다
        prs = Presentation(res["absPath"])
        slide_w, slide_h = prs.slide_width, prs.slide_height
        full = sum(1 for sl in prs.slides for sh in sl.shapes
                   if sh.shape_type == MSO_SHAPE_TYPE.PICTURE
                   and sh.width >= slide_w * 0.95 and sh.height >= slide_h * 0.95)
        assert full >= 1, "템플릿+HTML인데 풀블리드 PNG가 없음"
    finally:
        _cleanup(res)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
