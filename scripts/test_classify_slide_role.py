"""Focused unit tests for ``_classify_slide_role`` — spec: pptx-quality-vertex-images, Task 3.1.

``_classify_slide_role(slide, is_cover, doc_title="") -> str`` returns one of
``cover | section | structural | content | visual``. These tests pin the role
boundaries from design Fix Implementation §1 (classifyRole):

  - is_cover                                   -> cover
  - kind in {flow, tree, architecture}         -> structural   (Req 3.1 preservation)
  - imagePrompt present (no structural signal) -> visual        (when NOT a diagram kind)
  - everything else (kpi/cards/twocol/...)     -> content

Pure / deterministic — NO network, NO LLM, NO gateway. Reuses only the existing
``_classify_section_diagram`` / ``_looks_structural`` heuristics.

Run:
  ./venv/bin/python -m pytest scripts/test_classify_slide_role.py -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

import ai_engine.server as srv  # noqa: E402

VALID_ROLES = {"cover", "section", "structural", "content", "visual"}


def test_cover_short_circuits_regardless_of_content():
    """is_cover=True wins over everything — even a structural-looking slide."""
    slide = {"title": "처리 흐름", "bullets": ["수집 -> 정제 -> 적재"]}
    assert srv._classify_slide_role(slide, True, "doc") == "cover"


def test_structural_flow_arrow_chain():
    slide = {"title": "데이터 처리 흐름", "bullets": ["수집 -> 정제 -> 변환 -> 적재"]}
    # Precondition: classifier sees a real structural kind.
    kind, _ = srv._classify_section_diagram(
        slide["title"], "\n".join(slide["bullets"]), "doc")
    assert kind in srv._STRUCTURAL_DIAGRAM_KINDS
    assert srv._classify_slide_role(slide, False, "doc") == "structural"


def test_structural_tree_directory():
    slide = {
        "title": "프로젝트 폴더 구조",
        "bullets": ["src/", "  components/", "  utils/", "tests/", "docs/"],
    }
    kind, _ = srv._classify_section_diagram(
        slide["title"], "\n".join(slide["bullets"]), "doc")
    assert kind == "tree"
    assert srv._classify_slide_role(slide, False, "doc") == "structural"


def test_structural_architecture():
    slide = {
        "title": "시스템 아키텍처",
        "bullets": ["프레젠테이션 계층", "애플리케이션 계층", "데이터 계층"],
    }
    kind, _ = srv._classify_section_diagram(
        slide["title"], "\n".join(slide["bullets"]), "doc")
    assert kind == "architecture"
    assert srv._classify_slide_role(slide, False, "doc") == "structural"


def test_visual_photo_prompt_without_diagram_kind():
    """imagePrompt present + no diagram kind + no structural signal -> visual."""
    slide = {
        "title": "회사 소개",
        "bullets": ["우리의 비전"],
        "imagePrompt": "modern flat illustration of a friendly team, blue palette, professional",
    }
    kind, _ = srv._classify_section_diagram(
        slide["title"], "\n".join(slide["bullets"]), "doc")
    # body is non-structural; imagePrompt is a photo/illustration description.
    assert kind == ""
    assert srv._classify_slide_role(slide, False, "doc") == "visual"


def test_content_when_no_image_prompt_and_no_structural_kind():
    """Plain bullets, no imagePrompt, not a structural diagram -> content."""
    slide = {"title": "개요", "bullets": ["요점 하나", "요점 둘"]}
    kind, _ = srv._classify_section_diagram(
        slide["title"], "\n".join(slide["bullets"]), "doc")
    assert kind not in srv._STRUCTURAL_DIAGRAM_KINDS
    assert srv._classify_slide_role(slide, False, "doc") == "content"


def test_content_for_high_density_cards():
    """KPI/cards-style high-density content is absorbed into content (NOT structural)."""
    slide = {
        "title": "핵심 지표",
        "bullets": ["매출: 120억", "성장률: 35%", "고객수: 4200", "재방문율: 68%"],
    }
    kind, _ = srv._classify_section_diagram(
        slide["title"], "\n".join(slide["bullets"]), "doc")
    # Real structural kinds must NOT include kpi/cards (Req 3.1 preservation).
    assert kind not in srv._STRUCTURAL_DIAGRAM_KINDS
    assert srv._classify_slide_role(slide, False, "doc") == "content"


def test_image_prompt_with_structural_signal_is_not_visual():
    """A structural-looking imagePrompt (arrow chain) must not be classified visual.

    It is a diagram intent, so it falls through to structural (kind) or content,
    never visual.
    """
    slide = {
        "title": "배포 파이프라인",
        "bullets": [],
        "imagePrompt": "빌드 -> 테스트 -> 스테이징 -> 배포",
    }
    role = srv._classify_slide_role(slide, False, "doc")
    assert role != "visual"
    assert role in VALID_ROLES


@pytest.mark.parametrize("bad", [None, 123, "string-not-dict", []])
def test_non_dict_slide_is_safe(bad):
    """Defensive: non-dict slide never raises and yields a valid role."""
    assert srv._classify_slide_role(bad, False, "doc") in VALID_ROLES


def test_returns_valid_role_enum_member():
    slide = {"title": "무엇이든", "bullets": ["x"]}
    for is_cover in (True, False):
        assert srv._classify_slide_role(slide, is_cover, "doc") in VALID_ROLES
