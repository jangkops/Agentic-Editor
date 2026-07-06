"""Regression — _force_generate_from_text가 use_native=False 경로에서도 PPTX를 생성한다.

근본 버그(문제2 네이티브 다이어그램 구현 시 유입):
  section_diagram_specs를 `if use_native:` 블록 안에서만 정의 → use_native=False
  (visual_intent 없음) 또는 게이트웨이 실패로 native 단계를 못 탈 때, PPTX 매핑부의
  section_diagram_specs.get() 참조가 UnboundLocalError → 전체 PPTX 0건.
  사용자 증상: 원격 SSH + 게이트웨이 403 환경에서 "강제 생성 폴백도 실패".

수정: section_diagram_specs를 section_diagrams/section_backgrounds와 동일하게
  use_native 블록 *밖*에서 무조건 초기화.

Correctness property:
  P1. 게이트웨이가 전부 실패(403 DENY)해도 _force_generate_from_text(generate_pptx)는
      실재 PPTX 파일(>0 bytes)을 반환한다 (UnboundLocalError 없음).
  P2. visual_intent가 약한 일반 텍스트 PPT 요청에서도 동일하게 파일을 생성한다.
  P3. 반환된 각 파일의 absPath는 디스크에 실재하고 크기 > 0.
"""
import os
import sys
import json
import asyncio
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai_engine"))

import server  # noqa: E402


class _DenyGW:
    """게이트웨이 403 DENY 모사 — 모든 호출 실패(원격+권한거부 환경)."""
    async def converse(self, **kwargs):
        return {"decision": "DENY", "error": "HTTP 403: access denied"}

    async def invoke_model(self, *a, **k):
        return {"error": "HTTP 403: access denied"}


@pytest.fixture
def deny_gw(monkeypatch, tmp_path):
    monkeypatch.setenv("AE_GENERATED_ROOT", str(tmp_path))
    monkeypatch.setattr(server, "_get_gw", lambda *a, **k: _DenyGW())


def _cleanup(forced):
    for _, info in forced:
        ap = info.get("absPath")
        if ap and os.path.isfile(ap):
            try:
                os.remove(ap)
                if os.path.isfile(ap + ".meta.json"):
                    os.remove(ap + ".meta.json")
            except OSError:
                pass


@pytest.mark.unit
@pytest.mark.parametrize("prompt,desc", [
    # 흐름도/구조 — visual_intent 강함 (use_native=True 경로)
    ("프로젝트 흐름도 PPTX를 만들어줘", "프로젝트 구조와 데이터 흐름을 설명하는 발표자료"),
    # 일반 텍스트 PPT — visual_intent 약함 (use_native=False 경로, 버그 직접 트리거)
    ("회사 소개 PPTX 발표자료 만들어줘", "회사의 비전과 미션을 소개하는 내용"),
])
def test_force_generate_pptx_survives_gateway_failure(deny_gw, prompt, desc):
    """P1+P2+P3 — 게이트웨이 실패 + 다양한 visual_intent에서 PPTX 생성 성공."""
    forced = asyncio.run(server._force_generate_from_text(
        primary_tool="generate_pptx", target_files=[],
        title=prompt[:80], description=desc,
        final_text=desc,  # enrich 실패 시뮬레이션 — 짧은 본문
        project_path="/fsx/home/cgjang/proj",  # 원격 경로 (로컬 홈으로 폴백돼야)
        aws_profile="bedrock-gw", bedrock_user="cgjang",
        template_id="",
    ))
    try:
        assert len(forced) >= 1, "강제 폴백이 0건 — UnboundLocalError 회귀"
        for rel, info in forced:
            ap = info.get("absPath")
            assert ap and os.path.isfile(ap), f"파일 미존재: {ap}"
            assert os.path.getsize(ap) > 0, f"0바이트 파일: {ap}"
            assert rel.endswith(".pptx")
    finally:
        _cleanup(forced)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
