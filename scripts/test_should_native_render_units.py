"""단위 테스트: `ai_engine/server.py`의 `_should_native_render(sd, layout, html_enabled)`.

Feature: pptx-native-density-render, Task 17.1
Validates: Requirements 9.1, 9.3

`_should_native_render` 는 본문 콘텐츠 슬라이드를 네이티브_렌더러로 라우팅할지
결정하는 순수 함수다(네트워크 0·LLM 0). 본 테스트는 Hypothesis 를 쓰지 않는
예시 기반 분기 단위 테스트로, 다음 분기를 고정한다.

  - 명시 imageFile/slideBackground/nativeDiagram → False (명시 경로 보존, Req 9.1)
  - 알려진_레이아웃 + 콘텐츠 텍스트 + 비명시 + html_enabled=True → True (네이티브 라우팅)
  - 미지원 레이아웃("unknown") → False
  - html_enabled=False → False (기존 AE_PREFER_EDITABLE_DIAGRAM 경로 보존, Req 9.3)
  - 콘텐츠 텍스트 없음 → False
  - 비-dict/None sd → graceful False

실행: ./venv/bin/python -m pytest scripts/test_should_native_render_units.py -p no:cacheprovider -q
"""
import os
import sys

import pytest

# 레포 루트를 import 경로에 추가(기존 scripts/ 컨벤션) → `ai_engine.server` import 가능.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 대상 함수 import. 작업 9.1 에서 import 0.7s 내 OK·hang 없음 확인됨.
# 만약 import 가 무겁거나 실패하면 명확히 skip 사유를 남긴다(테스트 hang 방지는
# pytest 실행 측 timeout 으로 처리).
from ai_engine.server import _should_native_render  # noqa: E402

# 알려진_레이아웃 키 확인용(미지원 레이아웃 케이스가 실제로 레지스트리에 없음을 보장).
try:
    from ai_engine.native_layout_renderer import NATIVE_LAYOUT_REGISTRY
except ImportError:  # pragma: no cover - fallback
    from native_layout_renderer import NATIVE_LAYOUT_REGISTRY


# --- 명시 경로 보존 (Req 9.1) ------------------------------------------------

def test_explicit_imagefile_returns_false():
    """명시 imageFile 지정 → False (명시 경로 보존)."""
    sd = {"title": "표지 제목", "imageFile": "/abs/path/cover.png"}
    assert _should_native_render(sd, "cover", html_enabled=True) is False


def test_explicit_slidebackground_returns_false():
    """명시 slideBackground 지정 → False (명시 경로 보존)."""
    sd = {"title": "본문 제목", "slideBackground": "/abs/path/bg.png"}
    assert _should_native_render(sd, "feature_grid", html_enabled=True) is False


def test_native_diagram_returns_false():
    """nativeDiagram 지정 → False (기존 네이티브 다이어그램 경로 보존, Req 9.3)."""
    sd = {"title": "다이어그램", "nativeDiagram": {"type": "tree"}}
    assert _should_native_render(sd, "architecture", html_enabled=True) is False


# --- 네이티브 라우팅 True 경로 ----------------------------------------------

def test_known_layout_with_title_and_no_explicit_returns_true():
    """알려진 레이아웃(cover) + 콘텐츠 텍스트(title) + 비명시 + html_enabled=True → True."""
    sd = {"title": "분기별 사업 성과"}
    assert _should_native_render(sd, "cover", html_enabled=True) is True


def test_known_layout_with_bullets_only_returns_true():
    """제목 없이 불릿 콘텐츠만 있어도 콘텐츠 텍스트 존재로 True."""
    sd = {"bullets": ["첫째 항목", "둘째 항목"]}
    assert _should_native_render(sd, "two_column", html_enabled=True) is True


@pytest.mark.parametrize("layout", sorted(NATIVE_LAYOUT_REGISTRY.keys()))
def test_all_known_layouts_route_native(layout):
    """7개 알려진_레이아웃 전부 콘텐츠+비명시+html_enabled=True 에서 True 로 라우팅."""
    sd = {"title": f"{layout} 제목"}
    assert _should_native_render(sd, layout, html_enabled=True) is True


# --- 미지원 레이아웃 ---------------------------------------------------------

def test_unknown_layout_returns_false():
    """미지원 레이아웃("unknown") → False (알려진_레이아웃 한정)."""
    assert "unknown" not in NATIVE_LAYOUT_REGISTRY
    sd = {"title": "콘텐츠 있음"}
    assert _should_native_render(sd, "unknown", html_enabled=True) is False


def test_empty_layout_returns_false():
    """빈 레이아웃 문자열 → False."""
    sd = {"title": "콘텐츠 있음"}
    assert _should_native_render(sd, "", html_enabled=True) is False


# --- html_enabled 게이트 (Req 9.3) ------------------------------------------

def test_html_disabled_returns_false():
    """html_enabled=False → False (기존 네이티브 다이어그램 경로 보존)."""
    sd = {"title": "콘텐츠 있음"}
    assert _should_native_render(sd, "cover", html_enabled=False) is False


# --- 콘텐츠 텍스트 부재 ------------------------------------------------------

def test_no_content_text_returns_false():
    """제목·불릿 모두 없음 → False."""
    sd = {"layoutHint": "cover"}
    assert _should_native_render(sd, "cover", html_enabled=True) is False


def test_blank_title_and_blank_bullets_returns_false():
    """공백뿐인 제목 + 공백 불릿 → 콘텐츠 텍스트 없음으로 False."""
    sd = {"title": "   ", "bullets": ["", "   "]}
    assert _should_native_render(sd, "cover", html_enabled=True) is False


# --- 비-dict / None sd graceful 처리 ----------------------------------------

@pytest.mark.parametrize("bad_sd", [None, "not-a-dict", 123, ["list"], 3.14])
def test_non_dict_sd_returns_false_gracefully(bad_sd):
    """비-dict/None sd → 예외 없이 graceful False."""
    assert _should_native_render(bad_sd, "cover", html_enabled=True) is False


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-p", "no:cacheprovider", "-q"]))
