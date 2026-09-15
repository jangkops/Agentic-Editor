# Feature: deep-research-engine, Task 17.3 — 설정 UI Playwright 테스트
# Requirements: 14.1, 14.3
#
# <research-settings> Web Component (src/components/research-settings.js)의 사용자 상호작용을
# headless Chromium(Playwright)으로 검증한다. webapp-testing 스킬의 "정적 HTML → file:// 로드 →
# 셀렉터로 상호작용" 패턴을 따른다. Electron 앱 전체(SSO/파이썬 백엔드)를 띄우지 않고 컴포넌트를
# tests/e2e/fixtures/research-settings-harness.html 안에 격리 마운트해 결정적으로 검증한다.
#
# 검증 대상(요구사항 14.1/14.3, design.md "10) 프론트엔드 — 설정 UI"):
#   (a) 옵트인 스위치 토글 → aria-checked 반전 + 제공자/동의 하위 섹션 노출(data-enabled)
#   (b) 제공자 칩 선택/해제 → aria-pressed 토글
#   (c) 동의 체크박스 체크 → 프라이버시 고지 갱신(warn → ok)
#   (d) 옵트인 ON + 동의 미체크 → 프라이버시 박스가 로컬 전용("로컬 검색만") 경고(data-tone="warn")
#
# 실행:
#   venv/bin/python3 -m pytest tests/e2e/test_research_settings_ui.py -v
# (playwright + chromium 필요: `python -m playwright install chromium`)

from pathlib import Path

import pytest

playwright_sync = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright 미설치 — `pip install playwright && python -m playwright install chromium`",
)
from playwright.sync_api import sync_playwright, expect  # noqa: E402

HARNESS_PATH = (Path(__file__).parent / "fixtures" / "research-settings-harness.html").resolve()
HARNESS_URL = HARNESS_PATH.as_uri()

# 셀렉터 (research-settings.js 의 실제 마크업 계약)
SEL_SWITCH = 'research-settings .rs-switch[data-key="enabled"]'
SEL_SUB = "research-settings .rs-sub"
SEL_CONSENT = 'research-settings input[data-key="consent"]'
SEL_PRIVACY = "research-settings .rs-privacy"
SEL_CHIP = "research-settings .rs-chip[data-id='{}']"

# 프라이버시 박스가 채워질 때까지 대기(컴포넌트 _lazyLoad 는 비동기 → 마운트 직후 텍스트가 빈 상태).
_PRIVACY_READY_JS = (
    "() => { const b = document.querySelector('research-settings .rs-privacy');"
    " return !!b && b.textContent.trim().length > 0; }"
)


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
    # 커스텀 엘리먼트 업그레이드 + 비동기 정착(프라이버시 렌더) 대기
    pg.wait_for_selector(SEL_PRIVACY, state="attached")
    pg.wait_for_function(_PRIVACY_READY_JS)
    try:
        yield pg
    finally:
        pg.close()


def test_initial_state_is_local_only_and_disabled(page):
    """기본값: 옵트인 off → 스위치 aria-checked=false, 하위섹션 data-enabled=false, 프라이버시 tone=off.

    문구 단정 갱신: 예전에는 "로컬 검색만" 을 요구했으나, 그 약속은 지킬 수 없었다 —
    AI 가 실행하는 터미널 명령(run_command → curl)은 이 게이트를 지나지 않기 때문이다.
    실측으로 확인된 우회 경로다. 이제 약속의 주체를 **리서치 도구**로 한정하고,
    셸 우회 가능성을 같은 박스에 고지한다. 테스트는 그 두 가지를 요구한다.
    """
    expect(page.locator(SEL_SWITCH)).to_have_attribute("aria-checked", "false")
    expect(page.locator(SEL_SUB)).to_have_attribute("data-enabled", "false")
    privacy = page.locator(SEL_PRIVACY)
    expect(privacy).to_have_attribute("data-tone", "off")
    expect(privacy).to_contain_text("리서치 도구")
    expect(privacy).to_contain_text("질의를 전송하지 않습니다")
    # 지킬 수 없는 약속이 되살아나지 않는다
    expect(privacy).not_to_contain_text("로컬 검색만")
    # 통제하지 못하는 경로를 밝힌다 — 껐을 때도 고지가 붙어야 한다
    expect(privacy).to_contain_text("터미널 명령")


def test_optin_toggle_flips_aria_checked_and_reveals_subsection(page):
    """(a) 옵트인 스위치 토글 → aria-checked 반전 + 제공자/동의 하위 섹션 노출(data-enabled=true)."""
    switch = page.locator(SEL_SWITCH)
    sub = page.locator(SEL_SUB)

    expect(switch).to_have_attribute("aria-checked", "false")
    expect(sub).to_have_attribute("data-enabled", "false")

    switch.click()

    expect(switch).to_have_attribute("aria-checked", "true")
    expect(sub).to_have_attribute("data-enabled", "true")

    # 다시 토글하면 원복(반전 확인)
    switch.click()
    expect(switch).to_have_attribute("aria-checked", "false")
    expect(sub).to_have_attribute("data-enabled", "false")


def test_provider_chips_toggle_aria_pressed(page):
    """(b) 제공자 칩 선택/해제 → aria-pressed 토글 (웹/논문 그룹 모두)."""
    page.locator(SEL_SWITCH).click()  # 옵트인 활성화(하위섹션 활성 상태에서 상호작용)

    # 기본 선택 상태: 웹 tavily=선택, 논문 arxiv=미선택
    tavily = page.locator(SEL_CHIP.format("tavily"))
    arxiv = page.locator(SEL_CHIP.format("arxiv"))
    expect(tavily).to_have_attribute("aria-pressed", "true")
    expect(arxiv).to_have_attribute("aria-pressed", "false")

    # 선택된 웹 칩 해제
    tavily.click()
    expect(tavily).to_have_attribute("aria-pressed", "false")

    # 미선택 논문 칩 선택
    arxiv.click()
    expect(arxiv).to_have_attribute("aria-pressed", "true")

    # 재클릭 시 원복(토글 왕복)
    arxiv.click()
    expect(arxiv).to_have_attribute("aria-pressed", "false")


def test_consent_checkbox_updates_privacy_notice(page):
    """(c) 동의 체크박스 체크 → 프라이버시 고지가 warn(대기)에서 ok(전송 고지)로 갱신."""
    page.locator(SEL_SWITCH).click()  # 옵트인 활성화
    privacy = page.locator(SEL_PRIVACY)
    consent = page.locator(SEL_CONSENT)

    # 옵트인 ON + 동의 OFF → 대기(warn)
    expect(privacy).to_have_attribute("data-tone", "warn")

    consent.check()
    # 동의 ON → 전송 고지(ok): 대상 제공자 + 전송 데이터(질의문) 표기, 로컬 전용 문구 없음
    expect(privacy).to_have_attribute("data-tone", "ok")
    expect(privacy).to_contain_text("전송 데이터")
    expect(privacy).to_contain_text("질의문")

    # 동의 해제 → 다시 대기(warn)
    consent.uncheck()
    expect(privacy).to_have_attribute("data-tone", "warn")


def test_optin_without_consent_shows_local_only_warning(page):
    """(d) 옵트인 ON + 동의 미체크 → 프라이버시 박스가 대기 경고(data-tone="warn").

    문구 단정 갱신 근거는 test_initial_state_is_local_only_and_disabled 주석 참고.
    """
    page.locator(SEL_SWITCH).click()  # 옵트인만 켜고 동의는 두지 않음

    privacy = page.locator(SEL_PRIVACY)
    expect(page.locator(SEL_CONSENT)).not_to_be_checked()
    expect(privacy).to_have_attribute("data-tone", "warn")
    expect(privacy).to_contain_text("동의 전까지")
    expect(privacy).to_contain_text("리서치 도구")
    expect(privacy).not_to_contain_text("로컬 검색만")
    expect(privacy).to_contain_text("터미널 명령")


def test_shell_bypass_disclosed_in_every_privacy_state(page):
    """셸 우회 고지가 off / 동의대기 / 동의완료 **세 상태 모두**에 붙는다.

    켜져 있을 때만 고지하면 "끄면 안전하다" 는 반대 오해가 남는다. 실제로 통제되지
    않는 경로(run_command → curl)는 세 상태에서 모두 열려 있으므로, 고지도 셋 다 붙는다.
    서버는 같은 사실을 감사 로그로 남긴다(server.py::_audit_shell_egress).
    """
    privacy = page.locator(SEL_PRIVACY)
    switch = page.locator(SEL_SWITCH)
    consent = page.locator(SEL_CONSENT)

    # 1) 옵트인 off
    expect(privacy).to_have_attribute("data-tone", "off")
    expect(privacy).to_contain_text("터미널 명령")

    # 2) 옵트인 on + 동의 off
    switch.click()
    expect(privacy).to_have_attribute("data-tone", "warn")
    expect(privacy).to_contain_text("터미널 명령")

    # 3) 옵트인 on + 동의 on
    consent.check()
    expect(privacy).to_have_attribute("data-tone", "ok")
    expect(privacy).to_contain_text("터미널 명령")
    # 동의 상태에서도 약속의 주체는 리서치 도구로 한정돼 있어야 한다
    expect(privacy).to_contain_text("리서치 도구는 로컬 프로젝트 파일 내용을 전송하지 않습니다")


def test_persisted_settings_are_flags_only_without_credentials(page):
    """보안 정합(요구사항 11/14.3, 15.2): 저장 페이로드는 플래그/제공자 이름만 포함하고 자격증명 원문은 없다."""
    page.locator(SEL_SWITCH).click()  # 변경 발생 → saveSettings 호출

    saved = page.evaluate("() => window.__savedSettings")
    assert saved is not None, "saveSettings 가 호출되어 저장 페이로드가 기록되어야 한다"
    research = saved.get("research")
    assert research is not None, "settings.research 가 병합되어야 한다"

    # 플래그/제공자 이름만 저장 — 키 집합이 정확히 4개
    assert set(research.keys()) == {"enabled", "consent", "webProviders", "academicProviders"}
    assert research["enabled"] is True
    assert isinstance(research["webProviders"], list)
    assert isinstance(research["academicProviders"], list)

    # 저장 페이로드 어디에도 자격증명/키로 보이는 필드가 없어야 한다
    import json

    blob = json.dumps(saved, ensure_ascii=False).lower()
    for banned in ("api_key", "apikey", "secret", "token", "credential", "password"):
        assert banned not in blob, f"저장 페이로드에 자격증명 흔적('{banned}')이 없어야 한다"
