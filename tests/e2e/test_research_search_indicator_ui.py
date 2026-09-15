# Feature: deep-research-engine, Task 20.6 — 검색 인디케이터 UI Playwright 테스트
# Requirements: 18.1, 18.3, 18.7
#
# <search-indicator> Web Component (src/components/search-indicator.js)의 라이프사이클과
# 프라이버시 표기 범위를 headless Chromium(Playwright)으로 검증한다. webapp-testing 스킬의
# "정적 HTML → file:// 로드 → 셀렉터/JS 구동으로 상호작용" 패턴(Task 17.3)을 따른다.
# Electron 앱 전체(SSO/파이썬 백엔드/SSE)를 띄우지 않고 컴포넌트를
# tests/e2e/fixtures/search-indicator-harness.html 안에 격리 마운트해 결정적으로 검증한다.
#
# 검증 대상(요구사항 18.1/18.3/18.7, design.md "10) 프론트엔드 — Search_Indicator"):
#   (a) phase=start → 인디케이터가 활성/가시 상태가 되고 kind 라벨 + 제공자 이름 + 질의 요약을 표기 (18.1)
#   (b) kind별 라벨 매핑(web/academic/deep) 정합 (18.1, 18.2)
#   (c) phase=end (status ok) → 완료 표기 후 해제(잔류 없음, display:none 복귀) (18.3)
#   (d) phase=end (status error) → 실패 표기 후 해제(멈춘 인디케이터 없음) (18.3)
#   (e) 프라이버시 범위: 제공자 "이름"과 (절단된) 질의 요약만 표기 — 자격증명/시크릿 미표기 (18.7, P9)
#
# 실행:
#   venv/bin/python3 -m pytest tests/e2e/test_research_search_indicator_ui.py -v
# (playwright + chromium 필요: `python -m playwright install chromium`)

from pathlib import Path

import pytest

playwright_sync = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright 미설치 — `pip install playwright && python -m playwright install chromium`",
)
from playwright.sync_api import sync_playwright, expect  # noqa: E402

HARNESS_PATH = (Path(__file__).parent / "fixtures" / "search-indicator-harness.html").resolve()
HARNESS_URL = HARNESS_PATH.as_uri()

# 셀렉터 (search-indicator.js 의 실제 마크업 계약)
SEL_INDICATOR = "search-indicator"
SEL_LABEL = "search-indicator .si-label"

# 컴포넌트 업그레이드(customElements.define) + 공개 API 준비 대기.
# 초기 상태는 data-state 미설정 → display:none 이므로 state="visible" 대기는 쓰지 않는다.
_UPGRADED_JS = (
    "() => { const el = document.querySelector('search-indicator');"
    " return !!el && typeof el.onSearchStatus === 'function'"
    " && typeof window.__driveSearchStatus === 'function'; }"
)


def _drive(page, payload):
    """하네스의 __driveSearchStatus 로 searchStatus payload 를 컴포넌트에 주입한다."""
    ok = page.evaluate("(p) => window.__driveSearchStatus(p)", payload)
    assert ok is True, "하네스가 <search-indicator>.onSearchStatus 를 구동하지 못했다"


@pytest.fixture(scope="module")
def browser():
    if not HARNESS_PATH.exists():  # 하네스 누락 시 명확히 실패
        pytest.fail(f"하네스 HTML을 찾을 수 없음: {HARNESS_PATH}")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser):
    """테스트마다 신선한 페이지에 컴포넌트를 재마운트해 상태를 격리한다."""
    pg = browser.new_page()
    pg.goto(HARNESS_URL)
    pg.wait_for_selector(SEL_INDICATOR, state="attached")
    pg.wait_for_function(_UPGRADED_JS)
    try:
        yield pg
    finally:
        pg.close()


def test_initial_state_is_hidden(page):
    """기본값: searchStatus 수신 전에는 data-state 미설정 → display:none(숨김)."""
    indicator = page.locator(SEL_INDICATOR)
    expect(indicator).to_be_hidden()
    assert page.get_attribute(SEL_INDICATOR, "data-state") is None


def test_start_activates_with_kind_provider_and_query_summary(page):
    """(a) phase=start → 활성/가시 + kind 라벨 + 제공자 이름 + 질의 요약 표기 (18.1)."""
    _drive(page, {
        "phase": "start",
        "kind": "web",
        "providers": ["tavily", "exa"],
        "query_summary": "기후 변화 최신 연구",
    })

    indicator = page.locator(SEL_INDICATOR)
    # 상태 전환은 동기 → data-state=active 로 즉시 전이하고 가시 상태가 된다.
    expect(indicator).to_have_attribute("data-state", "active")
    expect(indicator).to_be_visible()

    label = page.locator(SEL_LABEL)
    expect(label).to_contain_text("웹 검색 중")   # kind=web 활성 라벨
    expect(label).to_contain_text("tavily")        # 제공자 이름
    expect(label).to_contain_text("exa")           # 제공자 이름
    expect(label).to_contain_text("기후 변화 최신 연구")  # 질의 요약


@pytest.mark.parametrize("kind,expected_label", [
    ("web", "웹 검색 중"),
    ("academic", "논문 검색 중"),
    ("deep", "딥리서치 중"),
])
def test_kind_label_mapping(page, kind, expected_label):
    """(b) kind별 활성 라벨 매핑(web/academic/deep) 정합 (18.1, 18.2)."""
    _drive(page, {
        "phase": "start",
        "kind": kind,
        "providers": ["p1"],
        "query_summary": "요약",
    })
    expect(page.locator(SEL_INDICATOR)).to_have_attribute("data-state", "active")
    expect(page.locator(SEL_LABEL)).to_contain_text(expected_label)


def test_end_ok_shows_done_then_clears(page):
    """(c) phase=end(status=ok) → 완료 상태 표기 후 해제(display:none 복귀, 잔류 없음) (18.3)."""
    _drive(page, {"phase": "start", "kind": "web", "providers": ["tavily"], "query_summary": "요약"})
    expect(page.locator(SEL_INDICATOR)).to_have_attribute("data-state", "active")

    _drive(page, {"phase": "end", "kind": "web", "providers": ["tavily"], "query_summary": "요약", "status": "ok"})

    # 종료 상태 전이는 동기 → 즉시 'done' (해제만 타이머로 지연됨).
    assert page.get_attribute(SEL_INDICATOR, "data-state") == "done"

    # hold(900ms) + fade(400ms) 후 해제 → 숨김(Playwright 자동 대기).
    expect(page.locator(SEL_INDICATOR)).to_be_hidden()
    assert page.get_attribute(SEL_INDICATOR, "data-state") is None, "종료 후 인디케이터가 활성으로 잔류하면 안 된다"

    # 해제 이벤트가 status=ok 로 방출되었는지 확인.
    events = page.evaluate("() => window.__indicatorEvents")
    dismissed = [e for e in events if e["type"] == "dismissed"]
    assert dismissed, "indicator-dismissed 이벤트가 방출되어야 한다"
    assert dismissed[-1]["detail"]["status"] == "ok"


def test_end_error_shows_error_then_clears(page):
    """(d) phase=end(status=error) → 실패 표기 후 해제(멈춘 인디케이터 없음) (18.3)."""
    _drive(page, {"phase": "start", "kind": "academic", "providers": ["openalex"], "query_summary": "요약"})
    expect(page.locator(SEL_INDICATOR)).to_have_attribute("data-state", "active")

    _drive(page, {"phase": "end", "kind": "academic", "providers": ["openalex"], "query_summary": "요약", "status": "error"})

    # 종료 상태 전이는 동기 → 즉시 'error'.
    assert page.get_attribute(SEL_INDICATOR, "data-state") == "error"
    expect(page.locator(SEL_LABEL)).to_contain_text("검색 실패")

    # 오류 종료도 반드시 해제되어야 한다(잔류 금지).
    expect(page.locator(SEL_INDICATOR)).to_be_hidden()
    assert page.get_attribute(SEL_INDICATOR, "data-state") is None

    events = page.evaluate("() => window.__indicatorEvents")
    dismissed = [e for e in events if e["type"] == "dismissed"]
    assert dismissed, "오류 종료 시에도 indicator-dismissed 이벤트가 방출되어야 한다"
    assert dismissed[-1]["detail"]["status"] == "error"


def test_privacy_scope_shows_only_provider_names_and_query_summary(page):
    """(e) 프라이버시 범위(18.7, P9): 제공자 이름 + (절단된) 질의 요약만 표기.

    인디케이터에 자격증명/시크릿처럼 보이는 문자열이 없어야 한다(요구사항 14 프라이버시 고지 범위 정합).
    """
    _drive(page, {
        "phase": "start",
        "kind": "academic",
        "providers": ["semantic_scholar", "openalex"],
        "query_summary": "양자 오류 정정 표면 부호",  # 절단된 질의 요약(요약문만 전송)
    })

    text = page.inner_text(SEL_INDICATOR)
    # 제공자 이름과 질의 요약은 표기된다.
    assert "semantic_scholar" in text
    assert "openalex" in text
    assert "양자 오류 정정 표면 부호" in text
    assert "논문 검색 중" in text

    # 컴포넌트 서브트리 어디에도 자격증명/시크릿 마커가 없어야 한다.
    html = page.eval_on_selector(SEL_INDICATOR, "el => el.outerHTML").lower()
    for banned in ("api_key", "apikey", "secret", "token", "bearer", "password", "credential"):
        assert banned not in html, f"인디케이터에 자격증명 흔적('{banned}')이 없어야 한다"


def test_indicator_ignores_non_whitelisted_payload_fields(page):
    """(e-2) 프라이버시 정합(18.7, P9): payload 에 자격증명류 필드가 섞여 들어와도

    인디케이터는 제공자 이름 + 질의 요약만 렌더하고 그 외 필드(예: api_key/secret)는 표기하지 않는다.
    (백엔드는 자격증명을 방출하지 않지만, 표시 계층이 화이트리스트 필드만 렌더함을 방어적으로 검증.)
    """
    _drive(page, {
        "phase": "start",
        "kind": "web",
        "providers": ["tavily"],
        "query_summary": "기후 정책 요약",
        # 아래는 인디케이터가 절대 표기해선 안 되는 방어적 주입 필드.
        "api_key": "sk-live-SHOULD-NOT-RENDER-1234",
        "secret": "TOP-SECRET-XYZ",
        "authorization": "Bearer NOPE",
    })

    text = page.inner_text(SEL_INDICATOR)
    assert "tavily" in text
    assert "기후 정책 요약" in text

    # 자격증명 값은 렌더되지 않는다.
    assert "SHOULD-NOT-RENDER" not in text
    assert "TOP-SECRET" not in text
    assert "NOPE" not in text

    html = page.eval_on_selector(SEL_INDICATOR, "el => el.outerHTML").lower()
    assert "sk-live" not in html
    assert "api_key" not in html
    assert "top-secret" not in html
