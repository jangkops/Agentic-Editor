# Feature: gateway-models-effort-support, Task 14.4 — effort UI Playwright 테스트
# Requirements: 7.1, 7.2, 7.3, 7.12, 7.13, 7.14, 12.2
#
# `<effort-control>`(src/effort-control.js)과 그 배선(src/main.js task 14.2 seam)을 headless
# Chromium(Playwright)으로 검증한다. 기존 e2e 규약(Task 17.3 / 20.6)을 그대로 따른다:
# "정적 하네스 HTML → file:// 로드 → 셀렉터/공개 API 로 구동". Electron 앱 전체(SSO·파이썬
# 백엔드)는 띄우지 않고 tests/e2e/fixtures/effort-control-harness.html 안에 격리 마운트한다.
#
# 검증 대상(tasks.md 14.4 / requirements.md Requirement 7):
#   (a) capability payload 없음 → effort UI 미표시 + 요청 body 에 effort 필드 부재      (7.3, 7.15)
#   (b) capability payload 주입이 모델 드롭다운 항목 수·표시를 바꾸지 않음(기준선 동일)  (12.2, 6.14)
#   (c) `SUPPORTED` tuple → effort 셀렉트 박스 표시, 후보가 verified domain 과 정확히 일치 (7.1, 7.2)
#   (d) 모델 변경으로 tuple 불일치 → 즉시 숨김 + 저장 값 미부착, 복귀 시 정확 일치만 복원  (7.3, 7.7, 7.14)
#   (e) `STALE` 전이(fingerprint 변경 / 활성 목록 이탈) → 저장 값 제거 + 선택이
#       Active_Model 또는 명시적 미선택으로 복구                                        (7.12, 7.14)
#   (f) route 가 `SUPPORTED` 를 잃으면 저장 값 제거                                     (7.13)
#   (g) 다크 산업풍 토큰 적용 확인 + 스크린샷 저장                                       (steering ui.md)
#
# 값 추론 금지 규약:
#   - model ID·provider·표시 이름·fingerprint·effort 허용값은 **고정 시드 RNG 가 만든 무작위
#     심볼**이다. 실제 Gateway 모델명·실제 effort 값 상수를 이 파일에 두지 않는다.
#   - route·status·domain kind·value type 은 계약의 **닫힌 enum**이므로
#     `ai_engine/capability/contracts.py`에서 읽어 온다(직접 타이핑하지 않음). 어떤 route 를
#     쓸지는 그 닫힌 집합에서 시드로 고른 임의 원소이며 실제 지원 주장을 담지 않는다.
#   - 선택 후보 기대값은 `src/effort-control.js`의 상수(MAX_RANGE_OPTIONS / RANGE_INTERVALS)와
#     순수 로직(`EffortControl.buildOptions`)에서 읽어 계산한다. 후보 목록을 하드코딩하지 않는다.
#
# 실행:
#   ai_engine/.venv/bin/python -m pytest tests/e2e/test_effort_control_ui.py -v
#   (playwright 가 없는 인터프리터에서는 importorskip 으로 skip 된다. 기존 e2e 자산과 같은
#    실행 규약: `venv/bin/python3 -m pytest tests/e2e/test_effort_control_ui.py -v`)

import os
import random
import sys
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright 미설치 — `pip install playwright && python -m playwright install chromium`",
)
from playwright.sync_api import sync_playwright, expect  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = (Path(__file__).parent / "fixtures" / "effort-control-harness.html").resolve()
HARNESS_URL = HARNESS_PATH.as_uri()
SHOTS_DIR = Path(__file__).parent / "screenshots"

# ── 계약 닫힌 enum (단일 출처: ai_engine/capability/contracts.py) ────────────────────────
if str(REPO_ROOT / "ai_engine") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "ai_engine"))
from capability import contracts  # noqa: E402

KNOWN_ROUTES = tuple(contracts.Known_Route.values())
ROUTE_SUPPORTED = str(contracts.Route_Support_Status.SUPPORTED)
ROUTE_UNSUPPORTED = str(contracts.Route_Support_Status.UNSUPPORTED)
EFFORT_SUPPORTED = str(contracts.Effort_Support_Status.SUPPORTED)
EFFORT_UNSUPPORTED = str(contracts.Effort_Support_Status.UNSUPPORTED)
EFFORT_STALE = str(contracts.Effort_Support_Status.STALE)
ALLOWLIST_ALLOWED = str(contracts.Allowlist_Result.ALLOWED)
ALLOWLIST_UNVERIFIED = str(contracts.Allowlist_Result.UNVERIFIED)
EXEC_SYNC = str(contracts.Execution_Mode.SYNC)
VERIFIED = str(contracts.Verification_Status.VERIFIED)
DOMAIN_ENUM = str(contracts.Domain_Kind.ENUM)
DOMAIN_RANGE = str(contracts.Domain_Kind.RANGE)
VALUE_STRING = str(contracts.Value_Type.STRING)
VALUE_INTEGER = str(contracts.Value_Type.INTEGER)
VALUE_NUMBER = str(contracts.Value_Type.NUMBER)
SCHEMA_VERSION = contracts.SCHEMA_VERSION

# ── 무작위 심볼(고정 시드) — 실제 모델명·effort 값은 등장하지 않는다 ─────────────────────
AE_PBT_SEED = int(os.environ.get("AE_PBT_SEED", "20260803"))
_RNG = random.Random(AE_PBT_SEED)
_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def _sym(prefix, size=6):
    return prefix + "-" + "".join(_RNG.choice(_ALPHABET) for _ in range(size))


def _fingerprint():
    return "cfp1:sha256:" + "".join(_RNG.choice("0123456789abcdef") for _ in range(64))


PROVIDER = _sym("prv")
MODEL_A = _sym("mdl")
MODEL_B = _sym("mdl")
NAME_A = _sym("disp")
NAME_B = _sym("disp")
FP_A = _fingerprint()
FP_A_NEXT = _fingerprint()  # Effort_Contract 변경 → 새 Capability_Fingerprint (STALE 유발)
FP_B = _fingerprint()
# effort 허용값(enum) — 무작위 심볼. 마지막 값은 verifiedValues 에서 제외해 교집합을 강제한다.
ENUM_VALUES = [_sym("ev", 4) for _ in range(4)]
ENUM_UNVERIFIED = ENUM_VALUES[2]
ENUM_VERIFIED = [v for v in ENUM_VALUES if v != ENUM_UNVERIFIED]
# route 는 닫힌 집합에서 고른 임의 원소(실제 지원 주장 아님)
ROUTE_EFFORT = _RNG.choice(KNOWN_ROUTES)
# 정수 range 경계 — 무작위. (작은 range: 후보 예산 이내, 큰 range: 예산 초과 표본)
INT_SMALL_LOW = _RNG.randint(-40, 12)
INT_SMALL_HIGH = INT_SMALL_LOW + _RNG.randint(4, 18)
INT_LARGE_LOW = _RNG.randint(-900, -120)
INT_LARGE_HIGH = INT_LARGE_LOW + _RNG.randint(300, 1200)
# 실수 range 하한 — 반값 격자(0.5 단위)라 균등 분할이 부동소수 오차 없이 표현된다.
NUM_LOW = float(_RNG.randint(-30, 30)) + 0.5

# ── 셀렉터 (effort-control.js / index.html 의 실제 마크업 계약) ──────────────────────────
SEL_EFFORT = "#effort-control"
SEL_SELECT = "#effort-control select.efc-select"
SEL_LABEL = "#effort-control label.efc-label"
SEL_MODEL_ITEM = "#model-dropdown-list .model-dropdown-item"

_READY_JS = "() => window.__harnessReady === true"


# ── capability UI payload 빌더 (capability_map.to_ui_payload 형식) ───────────────────────
def _route_view(status):
    return {
        "status": status,
        "allowlist": ALLOWLIST_ALLOWED if status == ROUTE_SUPPORTED else ALLOWLIST_UNVERIFIED,
        "executionMode": EXEC_SYNC,
        "purposes": [],
        "fallbackRank": 0,
    }


def _effort_off(status=EFFORT_UNSUPPORTED):
    """effort 비지원 view — domain 필드를 싣지 않는다(미검증 값 렌더 불가)."""
    return {"status": status, "supported": False}


def _effort_enum(values, verified):
    return {
        "status": EFFORT_SUPPORTED,
        "supported": True,
        "valueType": VALUE_STRING,
        "domainKind": DOMAIN_ENUM,
        "enumValues": list(values),
        "verifiedValues": list(verified),
    }


def _effort_range(value_type, lower, upper):
    return {
        "status": EFFORT_SUPPORTED,
        "supported": True,
        "valueType": value_type,
        "domainKind": DOMAIN_RANGE,
        "rangeLowerInclusive": lower,
        "rangeUpperInclusive": upper,
        "verifiedValues": [],
    }


def _model_view(model_id, fingerprint, effort_route=None, effort_view=None, route_status=None):
    """Active_Model 하나의 UI view. 모든 Known_Route 키를 담는다(payload 계약)."""
    effort = {}
    routes = {}
    for route in KNOWN_ROUTES:
        is_effort_route = effort_route == route
        effort[route] = effort_view if (is_effort_route and effort_view) else _effort_off()
        if is_effort_route:
            routes[route] = _route_view(route_status or ROUTE_SUPPORTED)
        else:
            routes[route] = _route_view(ROUTE_UNSUPPORTED)
    return {
        "modelId": model_id,
        "provider": PROVIDER,
        "capabilityFingerprint": fingerprint,
        "verificationStatus": VERIFIED,
        "syncSupport": ROUTE_SUPPORTED,
        "asyncSupport": ROUTE_UNSUPPORTED,
        "streamingSupport": ROUTE_UNSUPPORTED,
        "routes": routes,
        "effort": effort,
        "effortRoutes": [r for r in KNOWN_ROUTES if effort[r]["supported"]],
    }


def _capabilities(*model_views):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "modelIds": [v["modelId"] for v in model_views],
        "models": {v["modelId"]: v for v in model_views},
    }


def _catalog(*models):
    """`/api/models` 의 기존 `models` 카탈로그(기준선 형식 — capability 와 무관)."""
    return {PROVIDER: [{"id": mid, "name": name} for mid, name in models]}


BOTH_MODELS = ((MODEL_A, NAME_A), (MODEL_B, NAME_B))


def _response(catalog, capabilities=None):
    body = {"models": catalog}
    if capabilities is not None:
        body["capabilities"] = capabilities
    return body


# ── 페이지 구동 헬퍼 ────────────────────────────────────────────────────────────────────
def _refresh(page, response):
    """`/api/models` 응답을 갈아끼우고 실물 새로고침 seam 을 1회 구동한다."""
    return page.evaluate("(r) => window.__refreshModels(r)", response)


def _effort_state(page):
    return page.evaluate("() => window.__effortState()")


def _api_body(page, model_id):
    return page.evaluate("(m) => window.__apiBody({ prompt: 'p', model: m })", model_id)


def _expected_domain(page, model_id, route):
    """선택 후보 기대값 — 컴포넌트의 순수 로직(buildOptions)으로 산출(하드코딩 금지)."""
    return page.evaluate(
        "([m, r]) => window.__expectedDomain(m, r)", [model_id, route]
    )


def _saved_entries(page):
    """마지막으로 영속된 Effort_Settings 스냅샷의 entries(저장 호출이 없으면 None)."""
    return page.evaluate(
        "() => window.__effortSaves.length"
        " ? window.__effortSaves[window.__effortSaves.length - 1].entries : null"
    )


def _select_first_candidate(page, state=None):
    """셀렉트 박스에서 첫 후보를 선택(실제 사용자 조작)하고 선택한 값을 돌려준다."""
    state = state or _effort_state(page)
    value = state["optionValues"][1]
    page.select_option(SEL_SELECT, value)
    return value


def _click_model(page, name):
    assert page.evaluate("(n) => window.__clickModelItem(n)", name) is True, (
        f"모델 드롭다운에서 '{name}' 항목을 찾지 못했다"
    )


def _floats(values):
    return [float(v) for v in values]


# ── fixtures (기존 e2e 규약: module scope browser + per-test page) ───────────────────────
@pytest.fixture(scope="module")
def browser():
    if not HARNESS_PATH.exists():
        pytest.fail(f"하네스 HTML을 찾을 수 없음: {HARNESS_PATH}")
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def page(browser):
    """테스트마다 신선한 페이지에 컴포넌트+배선을 재마운트해 상태를 격리한다."""
    pg = browser.new_page(viewport={"width": 520, "height": 260})
    pg.goto(HARNESS_URL)
    pg.wait_for_selector(SEL_EFFORT, state="attached")
    pg.wait_for_function(_READY_JS)
    assert pg.evaluate("() => window.__harnessError") is None, "하네스 초기화 실패"
    try:
        yield pg
    finally:
        pg.close()


# =======================================================================================
# (a) capability payload 없음 → effort UI 미표시, 요청 body 에 effort 필드 부재
# =======================================================================================
def test_without_capability_payload_effort_control_is_hidden(page):
    """capability payload 가 없으면 렌더 트리를 만들지 않고 요청 body 도 기준선과 같다 (7.3, 7.15)."""
    result = _refresh(page, _response(_catalog(*BOTH_MODELS)))

    assert result["hasCapabilities"] is False, "capabilities 키가 없으면 state.capabilities 는 미설정"
    assert result["selectedId"] == MODEL_A, "선택은 기존 로직(chat 가능 첫 모델)이 그대로 결정한다"

    expect(page.locator(SEL_EFFORT)).to_be_hidden()
    st = _effort_state(page)
    assert st["dataState"] is None, "미표시 상태에서는 data-state 를 남기지 않는다"
    assert st["innerHTML"] == "", "렌더 트리를 만들지 않는다(빈 innerHTML)"
    assert st["supported"] is False
    assert st["tuple"] is None
    assert page.locator(SEL_SELECT).count() == 0

    # 요청 body 어디에도 effort 필드가 없다(7.15/7.16).
    body = _api_body(page, MODEL_A)
    assert "effort" not in body


# =======================================================================================
# (b) capability payload 주입이 모델 드롭다운을 바꾸지 않는다 (기준선 동일)
# =======================================================================================
def test_capability_payload_does_not_change_model_dropdown(page):
    """capability payload 주입 전후로 드롭다운 항목 수·표시가 완전히 같다 (12.2, 6.14)."""
    _refresh(page, _response(_catalog(*BOTH_MODELS)))
    baseline = page.evaluate("() => window.__dropdownSnapshot('')")
    assert baseline["itemCount"] == 2, "주입한 모델 수만큼 항목이 렌더된다"
    assert [i["text"] for i in baseline["items"]] == [NAME_A, NAME_B]

    caps = _capabilities(
        _model_view(MODEL_A, FP_A, ROUTE_EFFORT, _effort_enum(ENUM_VALUES, ENUM_VERIFIED)),
        _model_view(MODEL_B, FP_B),
    )
    result = _refresh(page, _response(_catalog(*BOTH_MODELS), caps))
    assert result["hasCapabilities"] is True

    after = page.evaluate("() => window.__dropdownSnapshot('')")
    assert after["itemCount"] == baseline["itemCount"]
    assert after["items"] == baseline["items"]
    assert after["groups"] == baseline["groups"]
    assert after["html"] == baseline["html"], "capability payload 는 드롭다운 마크업을 바꾸지 않는다"
    assert result["modelIds"] == [MODEL_A, MODEL_B], "모델 목록·순서도 기준선과 같다"

    # 같은 payload 를 다시 받아도 아무 것도 바뀌지 않는다(동일 payload 무변경).
    again = _refresh(page, _response(_catalog(*BOTH_MODELS), caps))
    assert again["modelIds"] == [MODEL_A, MODEL_B]
    assert again["selectedId"] == result["selectedId"]
    assert page.evaluate("() => window.__dropdownSnapshot('')")["html"] == baseline["html"]

    # effort UI 는 이제 표시된다(7.1).
    expect(page.locator(SEL_EFFORT)).to_be_visible()


# =======================================================================================
# (c) SUPPORTED tuple → effort 표시 + 후보가 verified domain 과 정확히 일치
# =======================================================================================
def test_supported_tuple_shows_select_with_exact_verified_enum_domain(page):
    """enum domain: 후보 집합이 verified domain 과 정확히 일치한다(전량, 미검증 값 제외) (7.1, 7.2)."""
    caps = _capabilities(
        _model_view(MODEL_A, FP_A, ROUTE_EFFORT, _effort_enum(ENUM_VALUES, ENUM_VERIFIED)),
        _model_view(MODEL_B, FP_B),
    )
    _refresh(page, _response(_catalog(*BOTH_MODELS), caps))

    expect(page.locator(SEL_EFFORT)).to_be_visible()
    expect(page.locator(SEL_EFFORT)).to_have_attribute("data-state", "ready")
    expect(page.locator(SEL_SELECT)).to_have_count(1)  # 셀렉트 박스(슬라이더·버튼 그룹 아님)

    st = _effort_state(page)
    assert st["tuple"] == {
        "modelId": MODEL_A,
        "route": ROUTE_EFFORT,
        "capabilityFingerprint": FP_A,
    }

    # 첫 option 은 항상 미선택(= Gateway 기본 동작)이고 value 는 빈 문자열이다.
    placeholder_label = page.evaluate("() => window.EffortControl.UNSELECTED_LABEL")
    assert st["optionValues"][0] == ""
    assert st["optionLabels"][0] == placeholder_label
    assert st["selectedIndex"] == 0, "저장 값이 없으면 미선택 상태로 시작한다"

    # 선택 후보 = verified domain 과 정확히 일치(enum 전량, 미검증 값은 제외).
    assert st["optionValues"][1:] == ENUM_VERIFIED
    assert st["domainValues"] == ENUM_VERIFIED
    assert ENUM_UNVERIFIED not in st["optionValues"], "verifiedValues 밖의 enum 값은 후보가 아니다"
    assert st["domainValues"] == _expected_domain(page, MODEL_A, ROUTE_EFFORT)


def test_integer_range_domain_covers_every_verified_integer(page):
    """정수 range(예산 이내): inclusive 경계를 포함한 모든 정수가 후보다 (7.2)."""
    caps = _capabilities(
        _model_view(
            MODEL_A, FP_A, ROUTE_EFFORT,
            _effort_range(VALUE_INTEGER, INT_SMALL_LOW, INT_SMALL_HIGH),
        ),
        _model_view(MODEL_B, FP_B),
    )
    _refresh(page, _response(_catalog(*BOTH_MODELS), caps))

    st = _effort_state(page)
    budget = page.evaluate("() => window.EffortControl.MAX_RANGE_OPTIONS")
    expected = list(range(INT_SMALL_LOW, INT_SMALL_HIGH + 1))
    assert len(expected) <= budget, "이 케이스는 후보 예산 이내여야 한다"

    assert st["domainValues"] == expected
    assert st["optionValues"][1:] == [str(v) for v in expected]
    assert st["domainValues"] == _expected_domain(page, MODEL_A, ROUTE_EFFORT)


def test_large_integer_range_is_uniform_sample_within_budget(page):
    """정수 range(예산 초과): 양 경계를 포함한 균등 표본이며 MAX_RANGE_OPTIONS 를 넘지 않는다 (7.2)."""
    caps = _capabilities(
        _model_view(
            MODEL_A, FP_A, ROUTE_EFFORT,
            _effort_range(VALUE_INTEGER, INT_LARGE_LOW, INT_LARGE_HIGH),
        ),
        _model_view(MODEL_B, FP_B),
    )
    _refresh(page, _response(_catalog(*BOTH_MODELS), caps))

    st = _effort_state(page)
    budget = page.evaluate("() => window.EffortControl.MAX_RANGE_OPTIONS")
    values = st["domainValues"]
    assert INT_LARGE_HIGH - INT_LARGE_LOW + 1 > budget, "이 케이스는 후보 예산을 넘겨야 한다"

    assert len(values) == budget, "후보 수는 예산 상한과 같다"
    assert values[0] == INT_LARGE_LOW and values[-1] == INT_LARGE_HIGH, "양 경계는 항상 포함"
    assert all(isinstance(v, int) for v in values), "INTEGER value type 은 정수만 싣는다"
    assert all(a < b for a, b in zip(values, values[1:])), "표본은 순증가한다"
    assert all(INT_LARGE_LOW <= v <= INT_LARGE_HIGH for v in values), "경계 밖 값은 없다"
    # 균등 간격: 이웃 간격의 최대·최소 차이는 반올림 오차(1) 이내
    gaps = [b - a for a, b in zip(values, values[1:])]
    assert max(gaps) - min(gaps) <= 1, f"균등 표본이 아니다: gaps={sorted(set(gaps))}"
    assert values == _expected_domain(page, MODEL_A, ROUTE_EFFORT)


def test_number_range_domain_is_boundaries_plus_uniform_splits(page):
    """실수 range: 경계 + RANGE_INTERVALS 균등 분할 후보 (7.2)."""
    intervals = page.evaluate("() => window.EffortControl.RANGE_INTERVALS")
    span = float(intervals)  # 분할이 정확히 표현되도록 span 을 분할 수의 배수로 잡는다
    upper = NUM_LOW + span
    caps = _capabilities(
        _model_view(MODEL_A, FP_A, ROUTE_EFFORT, _effort_range(VALUE_NUMBER, NUM_LOW, upper)),
        _model_view(MODEL_B, FP_B),
    )
    _refresh(page, _response(_catalog(*BOTH_MODELS), caps))

    st = _effort_state(page)
    expected = [NUM_LOW + span * i / intervals for i in range(intervals + 1)]
    assert len(st["domainValues"]) == intervals + 1
    assert _floats(st["domainValues"]) == expected
    assert _floats(st["domainValues"])[0] == NUM_LOW
    assert _floats(st["domainValues"])[-1] == upper
    assert _floats(st["domainValues"]) == _floats(_expected_domain(page, MODEL_A, ROUTE_EFFORT))


# =======================================================================================
# (d) 모델 변경 → 즉시 숨김 + effort 필드 부재, 복귀 시 정확 일치만 복원
# =======================================================================================
def test_model_change_hides_effort_and_request_omits_effort_field(page):
    """tuple 불일치 모델로 바꾸면 즉시 숨고 어떤 모델의 요청 body 에도 effort 가 없다 (7.3, 7.7, 7.15)."""
    caps = _capabilities(
        _model_view(MODEL_A, FP_A, ROUTE_EFFORT, _effort_enum(ENUM_VALUES, ENUM_VERIFIED)),
        _model_view(MODEL_B, FP_B),  # effort 비지원 모델
    )
    _refresh(page, _response(_catalog(*BOTH_MODELS), caps))

    chosen = _select_first_candidate(page)
    assert chosen == ENUM_VERIFIED[0]

    # 사용자 조작 → effort-change CustomEvent(detail 에 tuple 3요소 + value) 발행
    events = page.evaluate("() => window.__effortEvents")
    assert events[-1] == {
        "modelId": MODEL_A,
        "route": ROUTE_EFFORT,
        "capabilityFingerprint": FP_A,
        "value": chosen,
    }
    # tuple 과 함께 저장된다(7.4~7.6)
    entries = _saved_entries(page)
    assert len(entries) == 1
    assert entries[0]["modelId"] == MODEL_A
    assert entries[0]["route"] == ROUTE_EFFORT
    assert entries[0]["capabilityFingerprint"] == FP_A
    assert entries[0]["value"] == chosen
    assert entries[0]["valueType"] == VALUE_STRING
    # 선택된 값은 요청 body 에 정확히 1회 실린다
    body = _api_body(page, MODEL_A)
    assert body["effort"] == {
        "modelId": MODEL_A,
        "route": ROUTE_EFFORT,
        "capabilityFingerprint": FP_A,
        "value": chosen,
        "valueType": VALUE_STRING,
    }

    # 모델 변경(드롭다운 항목 클릭 = 실제 사용자 경로) → 즉시 숨김
    _click_model(page, NAME_B)
    expect(page.locator(SEL_EFFORT)).to_be_hidden()
    st = _effort_state(page)
    assert st["dataState"] is None and st["innerHTML"] == ""
    assert st["supported"] is False and st["tuple"] is None
    # 저장 값은 어떤 모델의 요청에도 실리지 않는다(7.7 — request 생성 전 제거)
    assert "effort" not in _api_body(page, MODEL_B)
    assert "effort" not in _api_body(page, MODEL_A)

    # 원래 모델로 복귀 → 정확히 일치하는 tuple 의 저장 값만 복원된다(7.14)
    _click_model(page, NAME_A)
    expect(page.locator(SEL_EFFORT)).to_be_visible()
    st = _effort_state(page)
    assert st["value"] == chosen
    assert st["dataSelected"] == "1"
    assert st["optionValues"][st["selectedIndex"]] == chosen
    assert _api_body(page, MODEL_A)["effort"]["value"] == chosen


# =======================================================================================
# (e) STALE 전이 → 저장 값 제거 + 선택이 Active_Model 또는 명시적 미선택으로 복구
# =======================================================================================
def test_stale_fingerprint_transition_drops_stored_effort(page):
    """fingerprint 가 바뀌면(STALE) 저장 값을 제거하고 선택은 Active_Model 로 남는다 (7.12, 7.14)."""
    supported = _effort_enum(ENUM_VALUES, ENUM_VERIFIED)
    _refresh(page, _response(
        _catalog(*BOTH_MODELS),
        _capabilities(_model_view(MODEL_A, FP_A, ROUTE_EFFORT, supported), _model_view(MODEL_B, FP_B)),
    ))
    chosen = _select_first_candidate(page)
    assert len(_saved_entries(page)) == 1

    # Effort_Contract 변경 → 새 Capability_Fingerprint (같은 모델, 여전히 SUPPORTED)
    _refresh(page, _response(
        _catalog(*BOTH_MODELS),
        _capabilities(
            _model_view(MODEL_A, FP_A_NEXT, ROUTE_EFFORT, supported),
            _model_view(MODEL_B, FP_B),
        ),
    ))

    st = _effort_state(page)
    assert st["tuple"]["capabilityFingerprint"] == FP_A_NEXT, "새 fingerprint tuple 로 결속된다"
    assert st["value"] is None, "이전 fingerprint 의 저장 값은 복원하지 않는다(7.14)"
    assert st["selectedIndex"] == 0, "명시적 미선택 상태"
    assert st["dataSelected"] is None
    assert _saved_entries(page) == [], "STALE 이 된 저장 값은 제거된다(7.12)"
    assert "effort" not in _api_body(page, MODEL_A)
    assert chosen not in [e.get("value") for e in (_saved_entries(page) or [])]

    # 선택은 여전히 Active_Model 이다(payload 에 있는 모델)
    active_ids = page.evaluate("() => Object.keys(state.capabilities.models)")
    assert page.evaluate("() => state.selectedModel.id") in active_ids


def test_stale_entry_removal_recovers_selection_to_active_model(page):
    """STALE 로 활성 목록에서 빠지면 선택이 Active_Model 또는 명시적 미선택으로 복구된다 (7.12)."""
    supported = _effort_enum(ENUM_VALUES, ENUM_VERIFIED)
    _refresh(page, _response(
        _catalog(*BOTH_MODELS),
        _capabilities(_model_view(MODEL_A, FP_A, ROUTE_EFFORT, supported), _model_view(MODEL_B, FP_B)),
    ))
    _select_first_candidate(page)
    assert len(_saved_entries(page)) == 1

    # 선택 모델이 STALE → Active_Model 집합에서 제외되고 카탈로그에서도 사라진다
    result = _refresh(page, _response(
        _catalog((MODEL_B, NAME_B)),
        _capabilities(_model_view(MODEL_B, FP_B)),
    ))

    active_ids = page.evaluate("() => Object.keys(state.capabilities.models)")
    assert result["selectedId"] is None or result["selectedId"] in active_ids, (
        "선택은 Active_Model 이거나 명시적 미선택이어야 한다"
    )
    assert result["selectedId"] == MODEL_B
    assert MODEL_A not in result["modelIds"]

    expect(page.locator(SEL_EFFORT)).to_be_hidden()
    st = _effort_state(page)
    assert st["supported"] is False and st["tuple"] is None
    assert _saved_entries(page) == [], "삭제된 entry 의 Effort_Settings 는 제거된다(7.11/7.12)"
    assert "effort" not in _api_body(page, MODEL_B)
    assert "effort" not in _api_body(page, MODEL_A)


# =======================================================================================
# (f) route 가 SUPPORTED 를 잃으면 저장 값 제거
# =======================================================================================
def test_route_losing_supported_status_drops_stored_effort(page):
    """route 의 effort status 가 STALE/UNSUPPORTED 로 떨어지면 저장 값을 제거하고 UI 를 숨긴다 (7.13, 7.3)."""
    _refresh(page, _response(
        _catalog(*BOTH_MODELS),
        _capabilities(
            _model_view(MODEL_A, FP_A, ROUTE_EFFORT, _effort_enum(ENUM_VALUES, ENUM_VERIFIED)),
            _model_view(MODEL_B, FP_B),
        ),
    ))
    _select_first_candidate(page)
    assert len(_saved_entries(page)) == 1

    # 같은 fingerprint·같은 모델이지만 route 가 effort SUPPORTED 를 잃는다
    stale_route_view = _model_view(
        MODEL_A, FP_A, ROUTE_EFFORT, _effort_off(EFFORT_STALE), route_status=ROUTE_UNSUPPORTED
    )
    assert stale_route_view["effort"][ROUTE_EFFORT]["status"] == EFFORT_STALE
    assert stale_route_view["effortRoutes"] == []
    _refresh(page, _response(
        _catalog(*BOTH_MODELS),
        _capabilities(stale_route_view, _model_view(MODEL_B, FP_B)),
    ))

    expect(page.locator(SEL_EFFORT)).to_be_hidden()
    st = _effort_state(page)
    assert st["dataState"] is None and st["innerHTML"] == ""
    assert st["tuple"] is None, "SUPPORTED route 가 없으면 tuple 자체가 만들어지지 않는다"
    assert _saved_entries(page) == [], "route 가 SUPPORTED 를 잃으면 저장 값을 제거한다(7.13)"
    assert "effort" not in _api_body(page, MODEL_A)


# =======================================================================================
# (g) 다크 산업풍 토큰 + 스크린샷
# =======================================================================================
def test_dark_industrial_tokens_applied_and_screenshot_saved(page):
    """effort UI 가 variables.css 토큰(다크 산업풍)으로 렌더되는지 확인하고 스크린샷을 남긴다."""
    _refresh(page, _response(
        _catalog(*BOTH_MODELS),
        _capabilities(
            _model_view(MODEL_A, FP_A, ROUTE_EFFORT, _effort_enum(ENUM_VALUES, ENUM_VERIFIED)),
            _model_view(MODEL_B, FP_B),
        ),
    ))
    expect(page.locator(SEL_EFFORT)).to_be_visible()

    tokens = page.evaluate(
        """() => {
            const cs = (sel) => getComputedStyle(document.querySelector(sel));
            return {
                selectBg: cs('#effort-control select.efc-select').backgroundColor,
                selectFont: cs('#effort-control select.efc-select').fontSize,
                labelColor: cs('#effort-control label.efc-label').color,
                tokenBgInput: cs('#token-probe-bg-input').backgroundColor,
                tokenAccent: cs('#token-probe-accent').color,
                tokenMuted: cs('#token-probe-muted').color,
                tokenFontXs: getComputedStyle(document.documentElement)
                    .getPropertyValue('--font-size-xs').trim(),
            };
        }"""
    )
    # 하드코딩한 색상 대신 문서에서 해석된 토큰 값과 비교한다.
    assert tokens["selectBg"] == tokens["tokenBgInput"], "셀렉트 배경은 --color-bg-input 토큰"
    assert tokens["labelColor"] == tokens["tokenMuted"], "미선택 라벨은 --color-text-muted 토큰"
    assert tokens["selectFont"] == tokens["tokenFontXs"], "폰트 크기는 --font-size-xs 토큰"

    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    unselected = SHOTS_DIR / "effort_control_dark_tokens_unselected.png"
    page.locator("#single-model-bar").screenshot(path=str(unselected))

    # 값이 선택되면 accent 토큰으로 강조된다(미세 상호작용).
    _select_first_candidate(page)
    expect(page.locator(SEL_EFFORT)).to_have_attribute("data-selected", "1")
    selected_tokens = page.evaluate(
        """() => ({
            labelColor: getComputedStyle(document.querySelector('#effort-control label.efc-label')).color,
            tokenAccent: getComputedStyle(document.querySelector('#token-probe-accent')).color,
        })"""
    )
    assert selected_tokens["labelColor"] == selected_tokens["tokenAccent"], "선택 상태 라벨은 --color-accent"

    selected = SHOTS_DIR / "effort_control_dark_tokens_selected.png"
    page.locator("#single-model-bar").screenshot(path=str(selected))

    for shot in (unselected, selected):
        assert shot.exists() and shot.stat().st_size > 1024, f"스크린샷 저장 실패: {shot}"
