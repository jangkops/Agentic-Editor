# Feature: gateway-models-effort-support, Property 9: sync Responses `modelId` 부재 —
# 임의의 사용자 입력, system 지시문, 선택 field 조합 및 임의 effort 주입 상태에 대해,
# `/openai/responses` body 전체를 재귀 탐색했을 때 `modelId` key의 개수는 0이고, `model`에는
# Exact_Model_ID가 기록되며 `input`이 존재하고, verified Route_Contract가 열거한 선택 field
# 외의 key는 존재하지 않는다.
"""Property 9 (sync Responses `modelId` 부재) property-based test — task 7.6.

검증 대상 모듈
  ``ai_engine/gateway_module.py`` :meth:`GatewayClient._build_openai_payload`
    (동기 Responses Baseline_Request_Body 생성기 — `model`/`input`/`instructions`만 구성)
  ``ai_engine/capability/request_builder.py``
    ``EffortBoundClient._build_openai_payload``(builder seam), ``count_key_occurrences``,
    ``apply_jobs_model_id``, ``without_key``, ``value_at_path``
    (design.md "route별 body 생성 규칙", Components 5 Request_Builder)

**Validates: Requirements 8.4, 8.5, 8.6, 8.7, 12.17**

단정 내용
  1. **`modelId` 0개** — baseline body와 seam body **양쪽** 모두에서 body 전체를 재귀 탐색해
     ``modelId`` key 발생 횟수가 0이다. 독립 구현 두 개(``request_builder.count_key_occurrences``,
     ``_capability_strategies.count_key_occurrences``)와 모든 중첩 노드 직접 순회로 확인하며,
     사용자 입력·system 지시문이 ``modelId``를 key 또는 문자열로 포함해도 body에 key로
     새지 않는다. 동기 binding에는 jobs model ID seam이 적용되지 않는다
     (``apply_jobs_model_id``가 ``None``) — Requirement 8.4.
  2. **`model` = Exact_Model_ID** — ``model``이 정확히 1회 존재하고 값이 Exact_Model_ID와
     문자 단위로 같다(대소문자·문자 변경 없음) — Requirement 8.5.
  3. **`input` 존재** — ``input``이 정확히 1회 존재하고, 값은 production 정규화기
     (``_to_openai_input``)의 결과 또는 문자열 입력 원본이다 — Requirement 8.6.
  4. **열거된 선택 field만** — body 최상위 key 집합이
     ``{model, input} ∪ (system 지시문이 있을 때 instructions) ∪ (주입 시 effort field path
     의 root)``와 **정확히 같고**, 언제나 ``{model, input} ∪ Route_Contract.optionalFields``의
     부분집합이다. 계약이 열거했지만 이번 요청이 쓰지 않는 선택 field는 body의 어떤 중첩
     경로에도 나타나지 않는다 — Requirement 8.7.
  5. **effort 주입 상태 무관** — effort 미선택·불일치 5종에서는 seam body가 baseline과 바이트
     동일하고 effort field 발생 횟수가 0이며, 일치 시에는 계약 field path에 값이 정확히 1회
     기록되고 그 경로를 제외한 body는 baseline과 바이트 동일하다. 어느 상태에서도 1~4는
     그대로 성립한다.
  6. **전송 바이트** — 실제 동기 route 호출 경로(``openai_responses_sync``)가 만든 전송
     바이트를 예제마다 1건 포착해 URL이 ``/openai/responses``이고 본문에 ``modelId`` key가
     0개임을 확인한다(전송은 포착 stub에서 끝나며 네트워크로 나가지 않는다).

입력 조합(예제마다 전수 도달)
  입력 형태 3종 × system 지시문 유무 2종 = 6조합을 effort 미선택·effort 주입 두 상태에서
  각각 만들고(12 케이스), 여기에 effort 불일치 5종(:data:`MISMATCH_KINDS`)을 더한다
  (총 17 케이스). ``sampled_from`` 분포에 의존하지 않고 목록으로 강제한다.

  ``stringInput``    사용자 입력이 문자열(``input``에 그대로 기록)
  ``messagesInput``  Bedrock 스타일 messages — 메시지 dict와 content block에 ``modelId``·
                     ``model`` key를 섞은 **적대적 입력**(정규화가 버려야 한다)
  ``emptyMessages``  빈 messages(경계값 — ``input``은 빈 목록으로 존재)

생성기 전제조건(값 추론 금지 원칙과 실제 계약에서 유도)
  - 동기 Responses Route_Contract는 ``modelIdRequired = False``다. 이 route는 본문을 백엔드로
    그대로 전달하므로 게이트웨이 전용 필드를 요구할 수 없다(Requirement 8.4).
  - Effort_Contract의 field path 성분은 :data:`RESERVED_BODY_KEYS`(동기 Responses 표준 field와
    입력 정규화 key)와 겹치지 않는다. 겹치는 field path는 이 route의 verified 계약이 될 수
    없다(게이트웨이가 표준 외 필드를 거부한다).
  - effort가 `SUPPORTED`인 계약만 effort field path root를 ``optionalFields``에 열거한다.

절대 원칙
  model ID·provider·effort field path·effort 허용값을 확정 상수로 두지 않는다. 모든 식별자와
  경로·값은 공통 생성기 ``scripts/_capability_strategies.py``의 무작위 심볼이며, 실제 값은
  Authoritative_Evidence(작업 18의 production path probe)만이 채운다. 이 테스트의 성공은
  **Gateway 지원 근거가 아니다**(Requirement 12.22). 외부 Gateway를 호출하지 않는다
  (네트워크 0, 파일 쓰기 0).

  ``model``·``input``·``instructions``·``modelId``는 model identity가 아니라 Requirement
  8.4~8.6과 기존 builder(Baseline_Record)가 확정한 **route schema field 이름**이므로 상수로
  둔다.

PBT 규약 (design.md "PBT 구성 규칙")
  ``@seed(AE_PBT_SEED)`` 고정 seed, ``max_examples`` 최소 100, ``deadline=None``,
  ``database=None``, ``print_blob=True``. 실패 시 Hypothesis가 최소화한 counterexample과
  재현 blob을 ``pbt.counterexamples`` JSON 레코드로 표준 출력에 남기고, ``AE_PBT_REPORT``
  환경변수가 가리키는 파일이 있으면 같은 레코드를 JSON Lines로 덧붙인다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_openai_sync_body_no_modelid_pbt.py -q
  ai_engine/.venv/bin/python scripts/test_openai_sync_body_no_modelid_pbt.py

_Requirements: 8.4, 8.5, 8.6, 8.7, 12.17, 12.19, 12.21_
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any

from hypothesis import assume, given, seed
from hypothesis import strategies as st

# repo 루트와 scripts/ 를 import 경로에 추가한다(_capability_strategies 관행 재사용).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _capability_strategies as S  # noqa: E402

from ai_engine.capability import canonicalizer, contracts  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402
from ai_engine.gateway_module import GatewayClient  # noqa: E402

FEATURE = "gateway-models-effort-support"
PROPERTY_ID = 9
PROPERTY_NAME = "sync Responses `modelId` 부재"

#: 재현 blob(`@reproduce_failure(...)`) 추출 패턴.
_BLOB_PATTERN = re.compile(r"@reproduce_failure\([^)]*\)")

#: 검증 대상 route(동기 Responses).
SYNC_ROUTE = str(contracts.Known_Route.OPENAI_RESPONSES)

# ── 동기 Responses route schema field 이름 (model identity가 아니다) ──────────
#: Exact_Model_ID를 기록하는 field(Requirement 8.5).
MODEL_FIELD = "model"
#: 사용자 입력 field(Requirement 8.6).
INPUT_FIELD = "input"
#: 기존 builder가 구성하는 유일한 선택 field(design.md — system 지시문이 있을 때만 부착).
INSTRUCTIONS_FIELD = "instructions"
#: 계약이 요구하는 필수 field.
REQUIRED_SYNC_FIELDS: frozenset[str] = frozenset({MODEL_FIELD, INPUT_FIELD})
#: 입력 정규화기가 만드는 중첩 key(무작위 심볼과 충돌하면 발생 횟수 단정이 흐려진다).
NORMALIZED_INPUT_KEYS: frozenset[str] = frozenset({"role", "content", "type", "text"})
#: 무작위 심볼(effort field path·추가 선택 field)이 사용할 수 없는 이름.
RESERVED_BODY_KEYS: frozenset[str] = (
    REQUIRED_SYNC_FIELDS | NORMALIZED_INPUT_KEYS | {INSTRUCTIONS_FIELD, rb.MODEL_ID_KEY}
)

#: 입력 형태 3종(경계값 ``emptyMessages`` 포함).
INPUT_SHAPES: tuple[str, ...] = ("stringInput", "messagesInput", "emptyMessages")
#: system 지시문 유무 2종.
SYSTEM_STATES: tuple[str, ...] = ("noSystem", "withSystem")

#: effort 미선택 상태(공통 생성기의 ``noSelection``과 같은 조건).
NO_EFFORT = "noEffort"
#: tuple·status·domain이 모두 일치해 주입이 일어나는 상태.
INJECTED = "supportedInjected"
#: 주입을 막아야 하는 불일치 종류(``noSelection``은 :data:`NO_EFFORT`가 담당).
MISMATCH_KINDS: tuple[str, ...] = tuple(kind for kind in S.MISMATCH_KINDS if kind != "noSelection")

#: 상태별로 기대하는 **첫** 주입 차단 이유(:data:`request_builder.INJECTION_REASONS` 순서).
EXPECTED_REASONS: dict[str, str] = {
    NO_EFFORT: rb.EFFORT_NO_SELECTION,
    "modelIdMismatch": rb.EFFORT_MODEL_ID_MISMATCH,
    "routeMismatch": rb.EFFORT_ROUTE_MISMATCH,
    "fingerprintMismatch": rb.EFFORT_FINGERPRINT_MISMATCH,
    "statusNotSupported": rb.EFFORT_NOT_SUPPORTED,
    "valueOutOfDomain": rb.EFFORT_DOMAIN_VIOLATION,
}
assert set(MISMATCH_KINDS) <= set(EXPECTED_REASONS), "불일치 전수가 이유 매핑에 없다"

#: 한 example에서 반드시 도달해야 하는 상태 전수.
CASE_KINDS: frozenset[str] = frozenset({NO_EFFORT, INJECTED, *MISMATCH_KINDS})

#: 네트워크·자격증명 접근 없이 body 생성만 하는 base 클라이언트(요청 전송 0건).
_BASE = GatewayClient(gateway_url="https://sym-gateway.invalid/v1", region="us-west-2")


# ─────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────
def _raw(body: Any) -> bytes:
    """기존 transport가 실제로 전송하는 바이트열(dict key 순서까지 비교한다)."""
    return json.dumps(body).encode()


def binding_for(bundle: dict) -> dict:
    """생성기 묶음을 production Request_Binding으로 만든다.

    :func:`~ai_engine.capability.request_builder.bind_contract`를 그대로 사용하므로
    binding에는 라벨·provider 문자열이 담기지 않는다(Requirement 8.2, 8.3).
    """
    route_key = bundle["routeKey"]
    entry = {
        "modelId": bundle["modelId"],
        "capabilityFingerprint": bundle["capabilityFingerprint"],
        "invocationModelIds": [],
        "effort": {route_key: bundle["effort"]},
    }
    return rb.bind_contract(entry, bundle["route"])


def expected_input(messages: Any) -> Any:
    """production 정규화 결과(문자열 입력은 원본 그대로)."""
    return messages if isinstance(messages, str) else _BASE._to_openai_input(messages)


# ─────────────────────────────────────────────────────────────────
# 입력 생성기 — 적대적 사용자 입력과 system 지시문
# ─────────────────────────────────────────────────────────────────
def texts() -> st.SearchStrategy[str]:
    """사용자 텍스트. ``modelId``를 **문자열 값으로** 포함하는 적대적 입력도 만든다."""
    return st.one_of(
        S.symbols(),
        S.symbols().map(lambda text: f"{text} {rb.MODEL_ID_KEY}:{MODEL_FIELD}"),
    )


@st.composite
def adversarial_messages(draw: Any) -> list[dict]:
    """Bedrock 스타일 messages — 메시지·content block에 ``modelId`` key를 섞는다.

    ``_to_openai_input``은 role과 content의 ``text``만 읽으므로 이 key들은 정규화 과정에서
    버려져야 한다(body에 key로 새면 Requirement 8.4 위반).
    """
    messages: list[dict] = []
    for _ in range(draw(st.integers(min_value=1, max_value=2))):
        block: dict[str, Any] = {"text": draw(texts())}
        if draw(st.booleans()):
            block[rb.MODEL_ID_KEY] = draw(S.symbols())
        if draw(st.booleans()):
            block[MODEL_FIELD] = draw(S.symbols())
        message: dict[str, Any] = {
            "role": draw(st.sampled_from(["user", "assistant"])),
            "content": [block],
        }
        if draw(st.booleans()):
            message[rb.MODEL_ID_KEY] = draw(S.symbols())
        messages.append(message)
    return messages


@st.composite
def user_inputs(draw: Any, *, shape: str, with_system: bool) -> dict:
    """입력 형태·system 지시문 유무를 고정한 사용자 입력."""
    if shape == "stringInput":
        messages: Any = draw(texts())
    elif shape == "emptyMessages":
        messages = []
    else:
        messages = draw(adversarial_messages())
    return {
        "shape": shape,
        "messages": messages,
        "system": draw(texts()) if with_system else "",
    }


# ─────────────────────────────────────────────────────────────────
# 계약 생성기 — 동기 Responses Route_Contract + Effort_Entry
# ─────────────────────────────────────────────────────────────────
@st.composite
def sync_bundles(draw: Any, *, effort_status: str | None = None) -> dict:
    """동기 Responses route에 결속된 계약 묶음(+ Request_Binding).

    공통 생성기 :func:`~_capability_strategies.injection_bundles`가 만든 계약에서 이 route의
    확정 사실만 고정한다: 실행 모드는 sync, 서명은 `execute-api`, ``modelIdRequired``는 거짓
    (Requirement 8.4). 선택 field는 기존 builder가 구성할 수 있는 ``instructions``와 무작위
    심볼 extras를 열거하고, effort가 `SUPPORTED`인 계약만 effort field path root를 함께
    열거한다(Requirement 8.7).
    """
    status = str(effort_status or contracts.Effort_Support_Status.SUPPORTED)
    bundle = draw(S.injection_bundles(route_key=SYNC_ROUTE, effort_status=status))

    path = S.effort_field_path(bundle)
    # 생성기 전제 — 이 route의 verified effort field path는 표준 field·정규화 key와 겹치지 않는다.
    assume(not (set(path) & RESERVED_BODY_KEYS))

    contract = bundle["route"]
    contract["executionMode"] = str(contracts.Execution_Mode.SYNC)
    contract["signingService"] = str(contracts.Signing_Service.EXECUTE_API)
    contract["modelIdRequired"] = False
    contract["modelIdFieldPath"] = None

    extras = [
        name
        for name in draw(st.lists(S.symbols(), max_size=2, unique=True))
        if name not in RESERVED_BODY_KEYS and name not in set(path)
    ]
    enumerated = [INSTRUCTIONS_FIELD, *extras]
    if status == contracts.Effort_Support_Status.SUPPORTED:
        enumerated.append(path[0])
    contract["optionalFields"] = enumerated

    bundle["fieldPath"] = path
    bundle["binding"] = binding_for(bundle)
    return bundle


def effort_contract_of(bundle: dict) -> dict:
    """묶음의 Effort_Contract."""
    return bundle["effort"]["contract"]


@st.composite
def mismatched_pairs(draw: Any, *, kind: str) -> tuple[dict, dict]:
    """주입이 일어나서는 **안 되는** ``(묶음, selection)`` 쌍.

    :func:`~_capability_strategies.unsupported_effort_cases`의 불일치 규칙을 동기 Responses
    route에 고정해 재현한다(공통 생성기는 route를 고정하는 인자를 받지 않는다).
    """
    status = (
        draw(S.effort_support_statuses(exclude=(contracts.Effort_Support_Status.SUPPORTED,)))
        if kind == "statusNotSupported"
        else str(contracts.Effort_Support_Status.SUPPORTED)
    )
    bundle = draw(sync_bundles(effort_status=status))
    contract = effort_contract_of(bundle)

    if kind == "valueOutOfDomain":
        return bundle, S.matching_selection(bundle, draw(S.out_of_domain_values(contract)))

    selection = S.matching_selection(bundle, draw(S.domain_values(contract)))
    if kind == "modelIdMismatch":
        selection["modelId"] = draw(
            S.model_ids(allow_empty=False).filter(lambda mid: mid != bundle["modelId"])
        )
    elif kind == "routeMismatch":
        selection["route"] = draw(S.known_routes(exclude=(bundle["routeKey"],)))
    elif kind == "fingerprintMismatch":
        selection["capabilityFingerprint"] = draw(
            S.capability_fingerprint_strings().filter(
                lambda text: text != bundle["capabilityFingerprint"]
            )
        )
    return bundle, selection


@st.composite
def sync_body_cases(draw: Any) -> list[dict]:
    """Property 9 입력 — 상태 전수 × 입력 조합 전수를 한 example에 담는다.

    입력 형태 3종 × system 유무 2종을 effort 미선택·주입 두 상태에서 각각 만들고(12개),
    effort 불일치 5종을 더한다(총 17개).
    """
    cases: list[dict] = []

    supported = draw(sync_bundles())
    value = draw(S.domain_values(effort_contract_of(supported)))
    matching = S.matching_selection(supported, value)

    for shape in INPUT_SHAPES:
        for system_state in SYSTEM_STATES:
            user = draw(user_inputs(shape=shape, with_system=system_state == "withSystem"))
            cases.append(
                {
                    "kind": NO_EFFORT,
                    "bundle": supported,
                    "selection": None,
                    "injected": False,
                    "value": None,
                    "user": user,
                }
            )
            cases.append(
                {
                    "kind": INJECTED,
                    "bundle": supported,
                    "selection": matching,
                    "injected": True,
                    "value": value,
                    "user": user,
                }
            )

    for kind in MISMATCH_KINDS:
        bundle, selection = draw(mismatched_pairs(kind=kind))
        cases.append(
            {
                "kind": kind,
                "bundle": bundle,
                "selection": selection,
                "injected": False,
                "value": selection.get("value"),
                "user": draw(
                    user_inputs(
                        shape=draw(st.sampled_from(INPUT_SHAPES)),
                        with_system=draw(st.booleans()),
                    )
                ),
            }
        )
    return cases


# ─────────────────────────────────────────────────────────────────
# 단정 helper
# ─────────────────────────────────────────────────────────────────
def _assert_no_model_id(body: Any, label: str) -> None:
    """body 전체를 재귀 탐색해 ``modelId`` key 발생 횟수가 0임을 단정(Requirement 8.4)."""
    assert rb.count_key_occurrences(body, rb.MODEL_ID_KEY) == 0, f"{label}: modelId key가 생성됐다"
    assert S.count_key_occurrences(body, rb.MODEL_ID_KEY) == 0, f"{label}: modelId key가 생성됐다"
    assert not rb.path_exists(body, [rb.MODEL_ID_KEY]), f"{label}: top-level modelId가 존재한다"
    for path, node in S.iter_nodes(body):
        if isinstance(node, dict):
            assert rb.MODEL_ID_KEY not in node, f"{label}: 중첩 경로 {path}에 modelId key"


def _assert_sync_body(
    body: Any,
    *,
    case: dict,
    injected: bool,
    label: str,
) -> None:
    """동기 Responses body의 네 가지 성질을 단정한다(Requirement 8.4~8.7)."""
    bundle = case["bundle"]
    contract = bundle["route"]
    user = case["user"]
    path = bundle["fieldPath"]

    assert isinstance(body, dict), f"{label}: body가 dict가 아니다"

    # (1) modelId 0개 — 사용자 입력·system 지시문이 무엇이든.
    _assert_no_model_id(body, label)

    # (2) model = Exact_Model_ID (문자 변경 없음).
    assert rb.count_key_occurrences(body, MODEL_FIELD) == 1, f"{label}: model key가 1회가 아니다"
    assert body[MODEL_FIELD] == bundle["modelId"], f"{label}: model 값이 Exact_Model_ID와 다르다"

    # (3) input 존재 — production 정규화 결과.
    assert rb.count_key_occurrences(body, INPUT_FIELD) == 1, f"{label}: input key가 1회가 아니다"
    assert body[INPUT_FIELD] == expected_input(user["messages"]), f"{label}: input이 사용자 입력과 다르다"

    # (4) 계약이 열거하지 않은 key 부재 — 최상위 key 집합을 정확히 특징짓는다.
    expected_keys = set(REQUIRED_SYNC_FIELDS)
    if user["system"]:
        expected_keys.add(INSTRUCTIONS_FIELD)
    if injected:
        expected_keys.add(path[0])
    assert set(body) == expected_keys, f"{label}: 최상위 key 집합이 계약과 다르다"

    enumerated = set(contract["optionalFields"])
    assert set(body) <= REQUIRED_SYNC_FIELDS | enumerated, f"{label}: 열거되지 않은 key가 있다"
    # 열거됐지만 이번 요청이 쓰지 않는 선택 field는 어떤 중첩 경로에도 없다.
    for name in enumerated - expected_keys:
        assert rb.count_key_occurrences(body, name) == 0, f"{label}: 미사용 선택 field {name!r} 생성"

    if user["system"]:
        assert body[INSTRUCTIONS_FIELD] == user["system"], f"{label}: instructions가 지시문과 다르다"


def _check_case(case: dict) -> None:
    """한 케이스에서 baseline body와 seam body 모두를 단정한다."""
    kind = case["kind"]
    bundle = case["bundle"]
    binding = bundle["binding"]
    contract = bundle["route"]
    path = bundle["fieldPath"]
    model_id = bundle["modelId"]
    messages = case["user"]["messages"]
    system = case["user"]["system"]

    # ── 생성기 전제조건 ─────────────────────────────────────────
    assert contract["routeKey"] == SYNC_ROUTE, f"{kind}: 동기 Responses 계약이 아니다"
    assert contracts.route_contract_is_complete(contract), f"{kind}: Route_Contract가 불완전하다"
    assert contract["modelIdRequired"] is False, f"{kind}: 동기 계약이 modelId를 요구한다"
    assert rb.is_field_path(path), f"{kind}: 유효한 effort field path가 아니다"
    assert not (set(path) & RESERVED_BODY_KEYS), f"{kind}: field path가 표준 field와 겹친다"

    # ── baseline (기존 builder) ─────────────────────────────────
    baseline = _BASE._build_openai_payload(model_id, messages, system)
    _assert_sync_body(baseline, case=case, injected=False, label=f"{kind}/baseline")

    # ── seam (EffortBoundClient) ────────────────────────────────
    client = rb.effort_bound_client(_BASE, binding, case["selection"])
    body = client._build_openai_payload(model_id, messages, system)
    _assert_sync_body(body, case=case, injected=case["injected"], label=f"{kind}/seam")

    # ── 주입 판정의 결정론 ──────────────────────────────────────
    reason = rb.injection_reason(binding, case["selection"], baseline_body=baseline)
    if case["injected"]:
        assert reason == rb.REASON_OK, f"{kind}: supported 계약인데 주입이 차단됐다({reason})"
        # 계약 field path에 정확히 1회, 그 경로 외는 baseline과 바이트 동일.
        assert S.count_field_path_occurrences(body, path) == 1, f"{kind}: 경로 일치 노드가 1개가 아니다"
        for part in path:
            assert rb.count_key_occurrences(body, part) == 1, f"{kind}: {part} key가 1회가 아니다"
        assert canonicalizer.canonical_equal(rb.value_at_path(body, path), case["value"])
        assert _raw(rb.without_key(body, path[0])) == _raw(baseline), f"{kind}: 경로 외 body가 다르다"
    else:
        assert reason == EXPECTED_REASONS[kind], f"{kind}: 기대 이유 {EXPECTED_REASONS[kind]!r} ≠ {reason!r}"
        assert _raw(body) == _raw(baseline), f"{kind}: seam body가 baseline과 다르다"
        for part in path:
            assert rb.count_key_occurrences(body, part) == 0, f"{kind}: effort field {part!r} 생성"
        assert S.count_field_path_occurrences(body, path) == 0, f"{kind}: effort field path 생성"

    # ── 동기 binding에는 jobs model ID seam이 적용되지 않는다 ────
    assert rb.apply_jobs_model_id(body, model_id, binding) is None, f"{kind}: jobs seam이 적용됐다"


def _check_transmission(case: dict) -> None:
    """실제 동기 route 호출 경로가 만든 전송 바이트를 포착해 단정한다.

    ``_openai_post_with_retry``를 포착 stub으로 대체하므로 네트워크·자격증명 접근이 없다.
    """
    bundle = case["bundle"]
    client = rb.effort_bound_client(_BASE, bundle["binding"], case["selection"])
    captured: dict[str, Any] = {}

    async def _capture(url: str, body_bytes: bytes, timeout: Any, label: str = "") -> dict:
        captured["url"] = url
        captured["bytes"] = body_bytes
        return {"output": []}

    client._openai_post_with_retry = _capture  # type: ignore[method-assign]
    asyncio.run(
        client.openai_responses_sync(
            bundle["modelId"], case["user"]["messages"], case["user"]["system"]
        )
    )

    assert captured["url"].endswith("/openai/responses"), "동기 route URL이 아니다"
    sent = json.loads(captured["bytes"].decode())
    _assert_no_model_id(sent, "transmitted")
    _assert_sync_body(sent, case=case, injected=case["injected"], label="transmitted")


# ─────────────────────────────────────────────────────────────────
# counterexample 기록 (Requirement 12.21)
# ─────────────────────────────────────────────────────────────────
def _counterexample_record(test_name: str, exc: BaseException) -> dict:
    """Hypothesis가 최소화한 counterexample과 재현 blob을 보고서 레코드로 만든다."""
    notes = [str(note) for note in getattr(exc, "__notes__", []) or []]
    falsifying = [note for note in notes if note.lstrip().startswith("Falsifying example")]
    blobs = [match.group(0) for note in notes for match in _BLOB_PATTERN.finditer(note)]
    return {
        "feature": FEATURE,
        "property": PROPERTY_ID,
        "propertyName": PROPERTY_NAME,
        "test": test_name,
        "seed": S.AE_PBT_SEED,
        "maxExamples": S.MAX_EXAMPLES,
        "caseKinds": sorted(CASE_KINDS),
        "inputShapes": list(INPUT_SHAPES),
        "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        # 최소화된 counterexample(마지막 note가 최종 축소 결과다).
        "counterexample": falsifying[-1] if falsifying else "",
        "reproduceBlob": blobs[-1] if blobs else "",
    }


def _report_counterexample(test_name: str, exc: BaseException) -> None:
    """counterexample 레코드를 표준 출력에 남기고 `AE_PBT_REPORT`가 있으면 파일에 덧붙인다."""
    record = _counterexample_record(test_name, exc)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True)
    print(f"pbt.counterexamples: {line}")

    report_path = (os.environ.get("AE_PBT_REPORT") or "").strip()
    if not report_path:
        return
    try:  # 보고서 기록 실패가 원래 실패를 가려서는 안 된다.
        with open(report_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError as write_error:
        print(f"pbt.counterexamples 기록 실패: {str(write_error)[:200]}")


# ─────────────────────────────────────────────────────────────────
# Property 9
# ─────────────────────────────────────────────────────────────────
# Feature: gateway-models-effort-support, Property 9: sync Responses `modelId` 부재
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(sync_body_cases())
def _property_sync_body_has_no_model_id(cases: list[dict]) -> None:
    """동기 Responses body의 `modelId` 0개·`model`·`input`·열거 field 성질.

    (Requirements 8.4, 8.5, 8.6, 8.7, 12.17)
    """
    observed: set[str] = set()
    shapes: set[str] = set()
    systems: set[bool] = set()

    for case in cases:
        observed.add(case["kind"])
        shapes.add(case["user"]["shape"])
        systems.add(bool(case["user"]["system"]))
        _check_case(case)

    # 상태·입력 조합 전수 도달(분포에 의존하지 않는다).
    assert observed == CASE_KINDS, f"상태 전수 미도달: {sorted(CASE_KINDS - observed)}"
    assert shapes == set(INPUT_SHAPES), f"입력 형태 전수 미도달: {sorted(set(INPUT_SHAPES) - shapes)}"
    assert systems == {False, True}, "system 지시문 유무 전수 미도달"

    # 실제 전송 경로 확인 — 예제당 1건(effort가 주입된 케이스를 우선 고른다).
    transmitted = next(case for case in cases if case["kind"] == INJECTED and case["user"]["system"])
    _check_transmission(transmitted)


def test_sync_body_has_no_model_id() -> None:
    """Property 9 pytest 진입점 — 실패 시 최소화 counterexample과 재현 blob을 기록한다."""
    try:
        _property_sync_body_has_no_model_id()
    except BaseException as exc:
        _report_counterexample("test_sync_body_has_no_model_id", exc)
        raise


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지).
    test_sync_body_has_no_model_id()
    print(
        f"PASSED: Property {PROPERTY_ID} — {PROPERTY_NAME} "
        f"(seed={S.AE_PBT_SEED}, max_examples={S.MAX_EXAMPLES})"
    )
