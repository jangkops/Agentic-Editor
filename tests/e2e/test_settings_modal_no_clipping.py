#!/usr/bin/env python3
"""설정 모달 세로 잘림 회귀 테스트 (Playwright, Electron 불필요).

── 배경 ──────────────────────────────────────────────────────────────────
`.dialog` 에 `max-height` 가 없어서 설정 내용이 길어지면(리서치 탭에 제공자 API 키
입력 행이 추가되면서) 다이얼로그가 뷰포트 밖으로 자랐다. `.overlay` 가
`align-items:center` 로 세로 중앙 정렬하기 때문에 위아래가 화면 밖으로 밀려 잘리고,
`.settings-content` 의 `overflow-y:auto` 는 높이 제약이 없어 스크롤이 발동하지 않았다.

── 이 테스트가 고정하는 성질 ────────────────────────────────────────────
  1. 다이얼로그가 뷰포트 안에 들어온다 (top >= 0, bottom <= viewport height)
  2. 내용이 길어도 잘리지 않는다 — 본문(#settings-body)이 스크롤 가능해진다
  3. 제목·닫기 버튼은 스크롤과 무관하게 항상 보인다 (헤더 고정)
  4. 짧은 창(700px)에서도 1~3이 성립한다

실제 `src/styles/*.css` 와 `src/components/research-settings.js` 를 그대로 로드한다.
Electron/백엔드/SSO 없이 순수 렌더링만 검증하므로 CI 에서도 돌 수 있다.
"""
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "src"

pytest.importorskip("playwright.sync_api", reason="playwright 미설치")
from playwright.sync_api import sync_playwright  # noqa: E402


def _css_links() -> str:
    """src/styles 의 실제 CSS 를 모두 인라인 link 로 건다(순서 유지)."""
    css_dir = SRC / "styles"
    order = ["variables.css", "base.css", "layout.css", "components.css"]
    names = [n for n in order if (css_dir / n).exists()]
    names += sorted(
        p.name for p in css_dir.glob("*.css") if p.name not in names
    )
    return "\n".join(
        f'<link rel="stylesheet" href="file://{css_dir / n}">' for n in names
    )


def _settings_dialog_style() -> str:
    """`src/main.js` 의 설정 다이얼로그 인라인 style 을 **실제 소스에서** 추출한다.

    하네스에 스타일을 복사해두면 main.js 가 회귀해도 테스트가 통과해버린다. 높이
    제약(`max-height`)이 걸리는 지점이 바로 이 인라인 style 이므로 소스를 읽어 쓴다.
    """
    src = (SRC / "main.js").read_text(encoding="utf-8")
    m = re.search(
        r'<div class="dialog" style="([^"]*min-width:580px[^"]*)"', src
    )
    assert m, "src/main.js 에서 설정 다이얼로그 인라인 style 을 찾지 못함"
    return m.group(1)


def _settings_content_header_style() -> str:
    """설정 헤더(제목+닫기) 행의 인라인 style 을 실제 소스에서 추출한다."""
    src = (SRC / "main.js").read_text(encoding="utf-8")
    m = re.search(
        r'<div style="(display:flex;justify-content:space-between;'
        r'align-items:center;margin-bottom:20px[^"]*)"',
        src,
    )
    assert m, "src/main.js 에서 설정 헤더 인라인 style 을 찾지 못함"
    return m.group(1)


def _harness_html() -> str:
    """설정 모달 마크업 + 실제 research-settings 컴포넌트를 올린 최소 페이지.

    main.js 전체를 로드하지 않고, showSettingsDialog() 가 만드는 것과 **동일한**
    다이얼로그 골격을 재현한다. 골격의 인라인 style 은 하드코딩하지 않고
    `src/main.js` 에서 추출하므로, main.js 가 회귀하면 이 테스트가 잡는다.
    """
    component = SRC / "components" / "research-settings.js"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">{_css_links()}</head>
<body>
<div id="sso-dialog" style="display:block">
  <div class="overlay">
    <div class="dialog" style="{_settings_dialog_style()}">
      <div class="settings-sidebar">
        <div class="settings-title">설정</div>
        <button class="settings-nav-btn" data-stab="appearance">외관</button>
        <button class="settings-nav-btn" data-stab="cli">CLI</button>
        <button class="settings-nav-btn active" data-stab="research">리서치</button>
        <button class="settings-nav-btn" data-stab="account">계정</button>
      </div>
      <div class="settings-content">
        <div id="settings-header" style="{_settings_content_header_style()}">
          <h3 id="settings-content-title" style="margin:0;font-size:16px;font-weight:700">리서치</h3>
          <button id="settings-close" class="sm-btn" style="font-size:14px;padding:4px 8px">X</button>
        </div>
        <div id="settings-body"></div>
      </div>
    </div>
  </div>
</div>
<script>
  // 컴포넌트가 참조하는 최소 전역(키 조회 IPC 부재 → status 는 unknown 유지).
  window.electronAPI = undefined;
</script>
<script src="file://{component}"></script>
<script>
  // 실제 컴포넌트를 마운트하고 리서치를 "켠" 상태로 만들어 최대 높이를 만든다
  // (제공자 칩 + 동의 + 프라이버시 고지 + 키 입력 4행이 모두 펼쳐진다).
  const el = document.createElement('research-settings');
  document.getElementById('settings-body').appendChild(el);
  el.setSettings({{ research: {{
      enabled: true, consent: true,
      webProviders: ['tavily', 'exa', 'brave'],
      academicProviders: ['openalex', 'europepmc', 'pubmed', 'semantic_scholar', 'arxiv'],
  }} }});
</script>
</body></html>"""


def _measure(page):
    return page.evaluate(
        """() => {
        const dlg = document.querySelector('.dialog');
        const body = document.getElementById('settings-body');
        const header = document.getElementById('settings-header');
        const comp = document.querySelector('research-settings');
        const r = dlg.getBoundingClientRect();
        const hr = header.getBoundingClientRect();
        return {
          viewportH: window.innerHeight,
          dialogTop: r.top, dialogBottom: r.bottom, dialogH: r.height,
          bodyClientH: body.clientHeight, bodyScrollH: body.scrollHeight,
          bodyOverflowY: getComputedStyle(body).overflowY,
          headerTop: hr.top, headerBottom: hr.bottom,
          componentH: comp ? comp.getBoundingClientRect().height : 0,
          keyRows: document.querySelectorAll('research-settings .rs-key-row').length,
          chips: document.querySelectorAll('research-settings .rs-chip').length,
        };
    }"""
    )


@pytest.fixture(scope="module")
def harness_url(tmp_path_factory):
    """하네스를 실제 파일로 써서 file:// 로 띄운다.

    ``set_content`` 는 about:blank 출처라 ``file://`` 스크립트·CSS 가 차단된다.
    실제 CSS/컴포넌트를 그대로 로드하는 것이 이 테스트의 목적이므로 파일로 쓴다.
    """
    d = tmp_path_factory.mktemp("settings-harness")
    f = d / "harness.html"
    f.write_text(_harness_html(), encoding="utf-8")
    return f"file://{f}"


@pytest.fixture(scope="module")
def page_factory():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


def _open(browser, height: int, url: str):
    page = browser.new_page(viewport={"width": 1280, "height": height})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    try:
        page.wait_for_selector("research-settings .rs-key-row", timeout=8000)
    except Exception:
        page.close()
        raise AssertionError(
            "컴포넌트가 렌더되지 않음. page errors=" + "; ".join(errors)
        )
    return page


@pytest.mark.parametrize("viewport_h", [900, 700])
def test_dialog_fits_in_viewport(page_factory, harness_url, viewport_h):
    """다이얼로그가 뷰포트를 벗어나지 않는다 — 위아래 잘림 없음."""
    page = _open(page_factory, viewport_h, harness_url)
    try:
        m = _measure(page)
        assert m["keyRows"] == 4, f"키 입력 행이 렌더되지 않음: {m['keyRows']}"
        assert m["chips"] >= 8, f"제공자 칩이 렌더되지 않음: {m['chips']}"
        # 핵심: 다이얼로그 상·하단이 화면 안에 있어야 한다.
        assert m["dialogTop"] >= -0.5, f"다이얼로그 상단이 화면 위로 잘림: {m}"
        assert m["dialogBottom"] <= m["viewportH"] + 0.5, (
            f"다이얼로그 하단이 화면 아래로 잘림: {m}"
        )
    finally:
        page.close()


@pytest.mark.parametrize("viewport_h", [900, 700])
def test_body_scrolls_instead_of_clipping(page_factory, harness_url, viewport_h):
    """내용이 넘치면 잘리지 않고 본문이 스크롤된다."""
    page = _open(page_factory, viewport_h, harness_url)
    try:
        m = _measure(page)
        assert m["bodyOverflowY"] in ("auto", "scroll"), (
            f"#settings-body 가 스크롤 컨테이너가 아님: {m['bodyOverflowY']}"
        )
        if m["bodyScrollH"] > m["bodyClientH"] + 1:
            # 넘칠 때: 실제로 스크롤이 되어 끝까지 도달할 수 있어야 한다.
            reached = page.evaluate(
                """() => {
                const b = document.getElementById('settings-body');
                b.scrollTop = b.scrollHeight;
                return b.scrollTop > 0;
            }"""
            )
            assert reached, f"본문이 넘치는데 스크롤되지 않음: {m}"
    finally:
        page.close()


@pytest.mark.parametrize("viewport_h", [900, 700])
def test_header_stays_visible_while_scrolling(page_factory, harness_url, viewport_h):
    """본문을 끝까지 스크롤해도 제목·닫기 버튼은 화면에 남는다(헤더 고정)."""
    page = _open(page_factory, viewport_h, harness_url)
    try:
        page.evaluate(
            """() => { const b = document.getElementById('settings-body');
                       b.scrollTop = b.scrollHeight; }"""
        )
        m = _measure(page)
        assert 0 <= m["headerTop"] < m["viewportH"], f"헤더가 화면 밖: {m}"
        assert m["headerBottom"] <= m["viewportH"] + 0.5
        assert page.is_visible("#settings-close"), "닫기 버튼이 보이지 않음"
        assert page.is_visible("#settings-content-title"), "제목이 보이지 않음"
    finally:
        page.close()


def test_last_key_row_reachable(page_factory, harness_url):
    """스크롤하면 마지막 키 입력 행까지 도달·조작 가능하다(잘려 있지 않음)."""
    page = _open(page_factory, 700, harness_url)
    try:
        rows = page.query_selector_all("research-settings .rs-key-row")
        last = rows[-1]
        last.scroll_into_view_if_needed()
        box = last.bounding_box()
        vp = page.viewport_size["height"]
        assert box is not None, "마지막 키 행의 박스를 얻을 수 없음"
        assert box["y"] >= -0.5 and box["y"] + box["height"] <= vp + 0.5, (
            f"마지막 키 행이 화면 밖: y={box['y']} h={box['height']} vp={vp}"
        )
        # 입력란이 실제로 클릭·타이핑 가능한 상태여야 한다.
        inp = last.query_selector("input")
        inp.click()
        inp.type("x")
        assert inp.input_value() == "x"
    finally:
        page.close()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
