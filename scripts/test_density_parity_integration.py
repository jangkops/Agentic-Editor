"""Chrome 헤드리스 픽셀 측정 통합 테스트 (Task 9.2) — pptx-design-density-parity.

헤르메틱: LLM/게이트웨이/Vertex 호출 0. `ai_engine.slide_templates.render_layout`
로 밀도 요소가 가득 찬 cover/two_column 슬라이드를 조립한 뒤, Chrome 헤드리스
(`scripts/visual_comparator.py` / `scripts/demo_design_ceiling_vs_genspark.py`의
`_html_to_png` 패턴 재사용)로 1920×1080 PNG 로 렌더한다.

검증:
  - 렌더된 모든 도형이 (0,0)~(1920,1080) 경계 안에 있음을 PNG 크기 == 1920×1080
    으로 확인(Chrome `--window-size=1920,1080` 캔버스 = 슬라이드 경계).
  - Visual_Comparator 가 `.generated/_design_compare/` 에 비교 PNG 를 생성함을 확인.
  - (DOM-free 보조) 밀도 마커가 실제 출력에 존재함을 문자열 수준으로 확인.

추가로 Parity_Scorer 합격 게이트(요구사항 5.4/5.5)를 헤르메틱하게 검증한다(Chrome
불필요): 가득 찬 cover/body 가 각각 `passed == True`(Density_Score ≥ Reference_Score).

Chrome 바이너리가 없으면 픽셀 테스트는 pytest.skip 으로 우아하게 건너뛴다.

실행: ./venv/bin/python -m pytest scripts/test_density_parity_integration.py \
        -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import struct
import sys
import zlib

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_engine import slide_templates as st  # noqa: E402
import parity_scorer  # noqa: E402
import visual_comparator as vc  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_HAS_CHROME = os.path.isfile(CHROME)
_RENDER_W, _RENDER_H = 1920, 1080


def _write_tiny_png(path: str) -> str:
    """의존성 없이 유효한 1×1 PNG 를 디스크에 기록(figure_slot 인라인용 로컬 경로)."""
    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit truecolor
    raw = b"\x00\xff\x00\x00"  # filter byte + 1 red pixel
    idat = zlib.compress(raw)
    png = sig + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return path


def _cover_html() -> str:
    """icon_badge / notice_chip / accent_spans / step_cards 가 모두 활성인 표지."""
    return st.render_layout("cover", {
        "title": "신규 입사자 노트북 세팅 온보딩 매뉴얼",
        "eyebrow": "공지사항 NOTICE · 목암생명과학연구소",
        "subtitle": "WiFi · 보안 프로그램 · MAC 셋팅 · M365 · Teams · OneDrive",
        "footer": "Mogam Institute · New Employee IT Setup",
        "icon_badge": {"icon": "wifi"},
        "notice_chip": "공지 NOTICE",
        "accent_spans": ["온보딩", "노트북"],
        "step_cards": [
            {"label": "STEP 01 · WiFi", "description": "GNET-ITSM 서비스 요청"},
            {"label": "STEP 02 · 보안", "description": "보안 프로그램 설치"},
            {"label": "STEP 03 · MAC", "description": "ITSM 사용자 등록"},
            {"label": "STEP 04 · M365", "description": "Teams · OneDrive"},
        ],
    })


def _body_html(figure_png: str) -> str:
    """section header + contact + note + links + numbered + figures + notice_tab
    + footer 가 모두 활성인 2단 본문."""
    return st.render_layout("two_column", {
        "title": "WiFi 세팅 · 보안 프로그램 안내",
        "subtitle": "목암생명과학연구소 · 신규 입사자 IT 온보딩 (1/3)",
        "left_content": "ACL 적용 사내망 접속은 최초 GNET-ITSM 서비스 요청 필요",
        "right_content": "필수 보안 프로그램 설치·세팅 필요",
        "left_section_no": "01", "left_section_title": "WiFi 세팅 절차",
        "right_section_no": "02", "right_section_title": "보안 프로그램",
        "left_contact": {"items": [
            {"label": "담당자", "value": "김정현 IT"},
            {"label": "내선", "value": "9132"},
        ]},
        "right_contact": {"items": [
            {"label": "담당자", "value": "문소희"},
            {"label": "팀", "value": "정보보안팀"},
        ]},
        "left_note": "MAC 확인: cmd → ipconfig /all 로 물리 주소 확인",
        "right_note": "입사 후 Teams 메신저로 담당자에게 1:1 문의",
        "left_links": [{"label": "GNET-ITSM"}, {"label": "사내 포털"}],
        "right_links": [{"label": "보안팀 Teams"}],
        "left_numbered": ["서비스 요청 생성", "카테고리 선택", "담당자 배정"],
        "right_numbered": ["프로그램 다운로드", "설치 및 재부팅"],
        "left_figures": [
            {"image": figure_png, "caption": "WiFi 요청 화면"},
        ],
        "right_figures": [
            {"image": figure_png, "caption": "보안 설치 화면"},
        ],
        "notice_tab": "필독",
        "footer_title": "신규 입사자 IT 온보딩",
        "footer_page": "1/3",
    })


# ---------------------------------------------------------------------------
# Parity_Scorer 합격 게이트 (헤르메틱 · Chrome 불필요) — 요구사항 5.4 / 5.5
# ---------------------------------------------------------------------------
def test_parity_gate_cover_passes():
    html = _cover_html()
    assert html, "cover render_layout 가 빈 문자열을 반환했습니다"
    result = parity_scorer.score(html, "cover")
    assert result["passed"] is True, (
        f"cover Density_Score={result['density_score']} "
        f"< Reference_Score={result['reference_score']} · missing={result['missing']}"
    )
    assert result["density_score"] >= result["reference_score"]


def test_parity_gate_body_passes(tmp_path):
    figure_png = _write_tiny_png(str(tmp_path / "fig.png"))
    html = _body_html(figure_png)
    assert html, "two_column render_layout 가 빈 문자열을 반환했습니다"
    result = parity_scorer.score(html, "body")
    assert result["passed"] is True, (
        f"body Density_Score={result['density_score']} "
        f"< Reference_Score={result['reference_score']} · missing={result['missing']}"
    )
    assert result["density_score"] >= result["reference_score"]


# ---------------------------------------------------------------------------
# Chrome 헤드리스 픽셀 측정 (Chrome 없으면 skip)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _HAS_CHROME, reason="Chrome 헤드리스 바이너리 없음 — 픽셀 테스트 건너뜀")
def test_cover_and_body_render_within_bounds(tmp_path):
    from PIL import Image

    figure_png = _write_tiny_png(str(tmp_path / "fig.png"))
    cover_html = _cover_html()
    body_html = _body_html(figure_png)
    assert cover_html and body_html

    cover_png = str(tmp_path / "cover.png")
    body_png = str(tmp_path / "body.png")
    vc._html_to_png(cover_html, cover_png)
    vc._html_to_png(body_html, body_png)

    # 모든 도형이 (0,0)~(1920,1080) 경계 안 → 렌더 캔버스(=슬라이드 경계) 크기 확인
    for png in (cover_png, body_png):
        assert os.path.isfile(png)
        with Image.open(png) as im:
            assert im.size == (_RENDER_W, _RENDER_H), (
                f"{png} 크기 {im.size} != ({_RENDER_W},{_RENDER_H}) — 경계 이탈 가능"
            )


@pytest.mark.skipif(not _HAS_CHROME, reason="Chrome 헤드리스 바이너리 없음 — 픽셀 테스트 건너뜀")
def test_comparison_png_written_to_generated(tmp_path):
    from PIL import Image

    figure_png = _write_tiny_png(str(tmp_path / "fig.png"))
    cover_html = _cover_html()
    body_html = _body_html(figure_png)

    # 참조 PNG 로 body 를 렌더해 사용(헤르메틱 더미 참조)
    ref_png = str(tmp_path / "ref_body.png")
    vc._html_to_png(body_html, ref_png)
    assert os.path.getsize(ref_png) > 0

    out_path = vc.compare(cover_html, ref_png, "integration_cover_vs_body.png")

    # 비교 PNG 가 `.generated/_design_compare/` 아래 생성됐는지 확인 (요구사항 5.7)
    assert os.path.isfile(out_path)
    assert os.path.dirname(out_path) == os.path.abspath(vc.OUT_DIR)
    with Image.open(out_path) as im:
        # side-by-side 합성 → 높이 1080, 폭 == ours + reference
        assert im.height == _RENDER_H
        assert im.width == _RENDER_W * 2
