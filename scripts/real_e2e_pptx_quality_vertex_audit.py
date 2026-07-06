"""REAL end-to-end PPTX generation runner (verification, NOT a mock test).

목적: hermetic 목 테스트가 아니라, 실제 ``ai_engine/server.py:_tool_generate_pptx``
코드 경로를 오프라인(네트워크 0)으로 구동해 실물 .pptx 를 ``.generated/`` 에 산출하고
기존 감사 도구로 검수하기 위한 러너.

환경 제약(정직성):
  - Live Vertex 자격증명 없음: ``AE_ENABLE_VERTEX_IMAGE`` / ``AE_PREFER_VERTEX_IMAGE``
    를 설정하지 않아 실제 ``get_vertex_image_client()`` 가 disabled 로 뜬다 → Vertex
    네트워크 호출 0, ``_vertex_pre`` 는 비어 네이티브/HTML 폴백 경로가 실물로 동작한다.
  - Electron 브리지 미가동: HTML-bake 경로는 시도되지만 ``_call_bridge`` 가 즉시
    None 을 반환(연결 불가) → 네이티브 폴백. 이것이 사용자가 서버만 띄운 상태의 실제
    동작이다.
  - 게이트웨이 LLM 호출로 인한 네트워크 행 방지: ``_get_gw`` 를 None 으로 대체해
    LLM 레이아웃 선정을 건너뛰고 결정론적 휴리스틱 경로를 태운다. (이미지 생성/구조
    JSON 은 어차피 Vertex/게이트웨이 미가동으로 호출되지 않음.)

즉, 이 러너가 산출하는 실물 .pptx 는 "사용자 로컬에서 서버만 띄우고 Vertex/브리지가
없을 때" 실제로 나오는 네이티브 렌더 결과물이다. Vertex 이미지 품질/임베드는 이 경로
로는 증명 불가(별도 자격증명 필요) — 최종 보고서 한계 절에 명시한다.

Run:
  ./venv/bin/python scripts/real_e2e_pptx_quality_vertex_audit.py
"""
from __future__ import annotations

import os
import sys
import json
import asyncio
from unittest.mock import patch

_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
sys.path.insert(0, _ROOT)

import ai_engine.server as srv  # noqa: E402

# 산출물은 프로젝트 .generated/ 에 남겨 사용자가 파일 패널에서 즉시 미리보기 가능.
GEN_ROOT = _ROOT
OUT_DIR = os.path.join(GEN_ROOT, ".generated")
os.makedirs(OUT_DIR, exist_ok=True)

# 혼합 덱: 표지 + 고밀도 콘텐츠 + 구조형 흐름 + 사진 의도 슬라이드
DECK = {
    "title": "2026 프로젝트 실행 계획 — 통합 검증 덱",
    "subtitle": "표지 · 고밀도 콘텐츠 · 구조형 흐름 · 사진형 혼합",
    "slides": [
        {
            "title": "핵심 기능 요약",
            "bullets": [
                "빠른 문서 처리 파이프라인",
                "안정적 운영 및 장애 복구",
                "유연한 수평 확장",
                "강력한 접근 제어와 보안",
                "운영 비용 절감",
                "직관적 사용자 경험",
            ],
        },
        {
            "title": "업무 처리 프로세스",
            "bullets": ["접수", "검토", "승인", "배포", "완료"],
        },
        {
            "title": "시스템 아키텍처 구성",
            "bullets": [
                "Frontend: Electron + Vanilla JS",
                "Backend: Python FastAPI",
                "Gateway: Bedrock",
                "Storage: userData",
            ],
        },
        {
            "title": "회사 소개",
            "bullets": ["신뢰를 최우선으로 하는 파트너"],
            "imagePrompt": (
                "a high quality professional photograph of a modern corporate "
                "office, natural light, wide angle, no text"
            ),
        },
    ],
}


async def _run() -> dict:
    # HTML 경로 토글 — AE_E2E_HTML=0 이면 HTML-bake 를 끄고 네이티브 편집가능
    # 경로(Req 3.1 보존 대상)를 실물로 검증한다.
    _html_on = os.environ.get("AE_E2E_HTML", "1") != "0"
    env = {
        "AE_ENABLE_HTML_SLIDES": "1" if _html_on else "0",
        "AE_DISABLE_HTML_SLIDES": "0" if _html_on else "1",
        "AE_PPTX_TOC": "0",
        "AE_ENABLE_VERTEX_BG": "0",
        "AE_GENERATED_ROOT": GEN_ROOT,
        # AE_ENABLE_VERTEX_IMAGE / AE_PREFER_VERTEX_IMAGE 는 의도적으로 건드리지
        # 않는다 — 환경에 자격증명이 있으면 실제 Vertex, 없으면 자연 비활성.
    }
    if not _html_on:
        # 네이티브 편집가능 다이어그램 경로를 결정론적으로 태운다.
        env["AE_PREFER_EDITABLE_DIAGRAM"] = "1"
        env["AE_DISABLE_NATIVE_DIAGRAM"] = "0"
    # 게이트웨이 LLM 호출로 인한 네트워크 타임아웃(행) 방지: gw=None → 휴리스틱 경로.
    with patch.dict(os.environ, env, clear=False), \
            patch.object(srv, "_get_gw", lambda *a, **k: None):
        raw = await asyncio.wait_for(
            srv._tool_generate_pptx(DECK, project_path=GEN_ROOT),
            timeout=90.0,
        )
    return json.loads(raw)


def main() -> int:
    try:
        result = asyncio.run(_run())
    except asyncio.TimeoutError:
        print("TIMEOUT: _tool_generate_pptx 90s 초과 — 네트워크/렌더 행 의심. 중단.")
        return 2
    print("=== _tool_generate_pptx 결과(요약) ===")
    for k in ("path", "absPath", "model", "slideCount", "sizeBytes"):
        print(f"  {k}: {result.get(k)}")
    rr = result.get("renderReport")
    if isinstance(rr, dict):
        print("=== renderReport ===")
        print(f"  htmlEnabled={rr.get('htmlEnabled')} htmlRenderer={rr.get('htmlRenderer')!r} "
              f"htmlDisabledReason={rr.get('htmlDisabledReason')!r}")
        print(f"  vertexEnabled={rr.get('vertexEnabled')} vertexDisabledReason={rr.get('vertexDisabledReason')!r}")
        print(f"  vertexGenerated={rr.get('vertexGenerated')} vertexEmbedded={rr.get('vertexEmbedded')} "
              f"vertexUnused={rr.get('vertexUnused')}")
        for s in rr.get("slides", []):
            print(f"    slide[{s.get('index')}] role={s.get('role')} path={s.get('path')} "
                  f"vertexEmbedded={s.get('vertexEmbedded')}")
    abs_path = result.get("absPath", "")
    if not abs_path or not os.path.exists(abs_path):
        print(f"FAIL: 산출 pptx 없음 — {result}")
        return 1
    # 감사 러너가 읽을 수 있도록 절대경로를 파일로 남긴다.
    _tag = "html_on" if os.environ.get("AE_E2E_HTML", "1") != "0" else "html_off"
    with open(os.path.join(OUT_DIR, f"_real_e2e_last_pptx_path_{_tag}.txt"), "w") as f:
        f.write(abs_path)
    print(f"\nOK: 실물 pptx 산출 → {abs_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
