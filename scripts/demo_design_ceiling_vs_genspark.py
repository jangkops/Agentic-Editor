"""우리 HTML 슬라이드 렌더러의 '디자인 천장'을 실제 렌더해 Genspark PDF와 비교.

네트워크 0 — LLM/게이트웨이/Vertex 호출 없이 slide_templates 의 render_layout 으로
온보딩 매뉴얼 콘텐츠를 직접 조립해 HTML 을 만들고, Chrome 헤드리스로 1920×1080 PNG 를
렌더한다. 이것은 '템플릿 자체 품질(천장)'을 보여줄 뿐, 실제 LLM 자동 생성 품질과는
다르다(자동 매핑은 게이트웨이 필요).

출력: <out>/ours_cover.png, ours_p1.png, ours_p2.png
"""
from __future__ import annotations

import os
import sys
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai_engine import slide_templates as st  # noqa: E402

OUT = os.path.join(_ROOT, ".generated", "_design_compare")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _html_to_png(html: str, out_png: str):
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, dir=OUT) as f:
        f.write(html)
        html_path = f.name
    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", "--window-size=1920,1080",
        f"--screenshot={out_png}", f"file://{html_path}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    os.unlink(html_path)
    if not os.path.exists(out_png):
        raise RuntimeError(f"render failed: {r.stderr[-400:]}")
    return out_png


def main():
    os.makedirs(OUT, exist_ok=True)

    # 표지 (cover) ---------------------------------------------------------
    cover = st.render_layout("cover", {
        "title": "신규 입사자 노트북 세팅 온보딩 매뉴얼",
        "eyebrow": "공지사항 NOTICE · 목암생명과학연구소",
        "subtitle": "WiFi · 보안 프로그램 · MAC 사용자 셋팅 · M365 · Teams · OneDrive · 프린터",
        "footer": "Mogam Institute · New Employee IT Setup",
    })

    # STEP 카드 (feature_grid) -------------------------------------------
    steps = st.render_layout("feature_grid", {
        "title": "온보딩 STEP 한눈에 보기",
        "subtitle": "STEP 01 → 07 · 담당자/채널 안내",
        "features": [
            {"icon": "wifi", "title": "STEP 01 · WiFi 세팅",
             "description": "GNET-ITSM 서비스 요청 · 김정현/오세영 IT (9132)"},
            {"icon": "shield", "title": "STEP 02 · 보안 프로그램",
             "description": "문소희 · 정보보안팀 · Teams 1:1 문의"},
            {"icon": "settings", "title": "STEP 03-04 · MAC 셋팅",
             "description": "ITSM 사용자 등록 + 보안 3종(NAC/Kaspersky/#3)"},
            {"icon": "folder", "title": "STEP 05-07 · M365",
             "description": "Teams · OneDrive · 복합기 · 공용폴더"},
        ],
    })

    # 본문 2단 (two_column) ----------------------------------------------
    body = st.render_layout("two_column", {
        "title": "WiFi 세팅 · 보안 프로그램 안내",
        "subtitle": "목암생명과학연구소 · 신규 입사자 IT 온보딩 (1/3)",
        "left_badge": "STEP 01 · WiFi",
        "right_badge": "STEP 02 · 보안",
        "left_content": (
            "ACL 적용 사내망 접속은 최초 GNET-ITSM 서비스 요청 필요\n"
            "네트워크 > 유선·무선 WiFi 문의 및 요청 카테고리 선택\n"
            "담당자: 김정현 IT · 오세영 IT (내선 9132)\n"
            "사전 확인: 신청 사유 · PC 위치 · MAC Address\n"
            "MAC 확인: cmd → ipconfig /all"
        ),
        "right_content": (
            "필수 보안 프로그램 설치·세팅 필요\n"
            "입사 후 Teams 메신저로 담당자 1:1 문의\n"
            "담당자: 문소희 · 정보보안팀\n"
            "정보보안팀: 보안전략·운영·인증·모니터링\n"
            "IT 인프라팀: PC/계정·서버/클라우드·네트워크"
        ),
    })

    targets = [("ours_cover.png", cover), ("ours_steps.png", steps),
               ("ours_body.png", body)]
    for name, html in targets:
        if not html:
            print(f"[WARN] 빈 HTML: {name}")
            continue
        out = os.path.join(OUT, name)
        _html_to_png(html, out)
        print(f"RENDERED: {out}")


if __name__ == "__main__":
    main()
