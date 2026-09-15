# Feature: gateway-models-effort-support, Property 10: jobs `modelId` 계약 일치 —
# verified `/openai/responses-jobs` Route_Contract에 대해, 계약이 `modelId`를 요구하면 생성
# body의 exact field path에 Invocation_Model_ID가 정확히 1회 존재하고 그 외 경로에는 `modelId`
# key가 없으며, 계약이 요구하지 않으면 body 전체에서 `modelId` key 개수는 0이다.
"""Property 10 (jobs `modelId` 계약 일치) property-based test — task 7.7.

검증 대상 모듈
  - ``ai_engine/capability/request_builder.py``
    (:func:`~ai_engine.capability.request_builder.apply_jobs_model_id`,
    ``EffortBoundClient._apply_jobs_model_id``,
    :func:`~ai_engine.capability.request_builder.invocation_model_id`,
    :func:`~ai_engine.capability.request_builder.count_key_occurrences`,
    :func:`~ai_engine.capability.request_builder.value_at_path`)
  - ``ai_engine/gateway_module.py`` (``GatewayClient._apply_jobs_model_id`` 기본 구현)

**Validates: Requirements 8.11, 8.12, 12.18**

단정 내용
  1. **요구 시 exact path 1회** — ``modelIdRequired``가 참이면 계약 ``modelIdFieldPath``에
     Invocation_Model_ID가 정확히 1회 기록되고(경로 일치 노드 수 1), ``modelId`` key를 가진
     노드의 경로는 계약 path 하나뿐이다 → **그 외 경로 0회**(Requirement 8.11).
  2. **미요구 시 body 전체 0회** — ``modelIdRequired``가 거짓이면 body의 모든 중첩 경로에서
     ``modelId`` key 개수가 0이고, 계약에 남아 있는 ``modelIdFieldPath``는 사용되지 않는다
     (Requirement 8.12).
  3. **경로 외 baseline 보존** — 두 경우 모두 결과 body는 `modelId` key를 제거한
     Baseline_Request_Body와 (요구 시에는 계약 path 서브트리를 제외하고) **바이트까지
     동일**하다. 최상위 key 집합은 baseline ∪ {``modelIdFieldPath[0]``}이다.
  4. **미확정은 기본 구현 위임** — ``modelIdRequired``가 ``None``(성공 probe로 확정되지 않아
     jobs route가 `UNVERIFIED`)이면 :func:`apply_jobs_model_id`는 ``None``을 반환하고,
     ``EffortBoundClient._apply_jobs_model_id``의 결과는 base ``GatewayClient``의 기본 구현
     결과(``{**body, "modelId": model_id}``)와 **전송 바이트까지 동일**하다.
  5. **seam 일치** — 확정 계약에서는 ``EffortBoundClient._apply_jobs_model_id``의 결과가
     순수 함수 :func:`apply_jobs_model_id` 결과와 바이트 동일하다. 계약 path가 기존 위치
     (top-level ``modelId``)이고 Invocation_Model_ID가 transport가 넘긴 model ID와 같으면
     기본 구현과도 바이트 동일하다(무회귀 경계값).
  6. **Invocation_Model_ID를 만들어내지 않음** — 기록되는 값은 계약에 명시된 값, 관측된 단일
     ID, 또는 transport가 넘긴 model ID 중 하나이며 그 밖의 문자열은 생성되지 않는다.
  7. **입력 불변·누적 금지** — baseline body와 binding은 변형되지 않고, 결과에 같은 계약을
     다시 적용해도 발생 횟수는 여전히 1회(또는 0회)이며 바이트가 변하지 않는다.

입력 생성기
  공통 생성기 ``scripts/_capability_strategies.py`` 만 사용한다
  (:func:`~_capability_strategies.route_contracts`,
  :func:`~_capability_strategies.field_paths`,
  :func:`~_capability_strategies.model_ids`,
  :func:`~_capability_strategies.baseline_bodies`,
  :func:`~_capability_strategies.evidence_ids`,
  :func:`~_capability_strategies.iter_nodes`,
  :func:`~_capability_strategies.count_field_path_occurrences`).
  baseline body는 계약 field path 성분과 ``modelId``를 key로 갖지 않도록 예약해 생성한 뒤,
  ``modelId`` key를 임의의 중첩 dict 노드에 0~3회 주입한다(오염된 body에서도 계약 일치가
  성립해야 한다).

  경계값은 example마다 **반드시** 포함한다(분포에 의존하지 않는다) — :data:`CASE_KINDS`:
  ``requiredTopLevel``(계약 path가 기존 위치), ``requiredGatewayKey``(중첩 path의 마지막
  성분이 게이트웨이 key 이름), ``requiredCustomPath``(성분 전부 무작위 심볼),
  ``notRequired``(0회), ``undetermined``(``None`` → 기본 구현 위임). baseline body는 빈 body와
  최대 중첩 body를 모두 포함한다.

절대 원칙
  model ID·provider·route 지원 여부·effort field path·jobs ``modelIdFieldPath``를 확정 상수로
  두지 않는다. 모든 식별자와 경로는 공통 생성기의 무작위 심볼이며, 실제 값은
  Authoritative_Evidence(작업 18의 production path probe)만이 채운다. ``modelId``는 게이트웨이
  전용 **key 이름**이라 계약 어휘로만 쓰고(:data:`request_builder.MODEL_ID_KEY`) 어떤 모델
  identity도 상수로 두지 않는다. 이 테스트의 성공은 **Gateway 지원 근거가 아니다**
  (Requirement 12.22). 외부 Gateway를 호출하지 않는다(네트워크 0, 파일 쓰기 0).

PBT 규약 (design.md "PBT 구성 규칙")
  ``@seed(AE_PBT_SEED)`` 고정 seed, ``max_examples`` 최소 100, ``deadline=None``,
  ``database=None``, ``print_blob=True``. 실패 시 최소화된 counterexample과 재현 blob을
  ``pbt.counterexamples`` JSON 레코드로 표준 출력에 남기고, ``AE_PBT_REPORT`` 환경변수가
  가리키는 파일이 있으면 같은 레코드를 JSON Lines로 덧붙인다(Validation_Runner 보고서 입력).

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_openai_jobs_modelid_contract_pbt.py -q
  ai_engine/.venv/bin/python scripts/test_openai_jobs_modelid_contract_pbt.py

_Requirements: 8.11, 8.12, 12.18, 12.19, 12.21_
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from typing import Any

from hypothesis import given, seed
from hypothesis import strategies as st

# repo 루트와 scripts/ 를 import 경로에 추가한다(_capability_strategies 관행 재사용).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _path in (_ROOT, _HERE):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import _capability_strategies as S  # noqa: E402

from ai_engine.capability import contracts  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402
from ai_engine.gateway_module import GatewayClient  # noqa: E402

FEATURE = "gateway-models-effort-support"
PROPERTY_ID = 10
PROPERTY_NAME = "jobs `modelId` 계약 일치"

#: 재현 blob(`@reproduce_failure(...)`) 추출 패턴.
_BLOB_PATTERN = re.compile(r"@reproduce_failure\([^)]*\)")

#: 검증 대상 route(닫힌 Known_Route 집합의 값 — 문자열 상수를 새로 만들지 않는다).
JOBS = str(contracts.Known_Route.OPENAI_RESPONSES_JOBS)

#: 네트워크·자격증명에 접근하지 않는 자리표시자 endpoint(실제 Gateway URL이 아니다).
PLACEHOLDER_GATEWAY_URL = "https://sym-gateway.invalid/v1"

#: 한 example에 반드시 함께 담는 케이스 종류(계약 분기와 경계값 전수 도달 보장).
CASE_KINDS: tuple[str, ...] = (
    "requiredTopLevel",
    "requiredGatewayKey",
    "requiredCustomPath",
    "notRequired",
    "undetermined",
)

#: ``modelIdRequired``가 참인 케이스 종류.
REQUIRED_KINDS: frozenset[str] = frozenset(
    {"requiredTopLevel", "requiredGatewayKey", "requiredCustomPath"}
)

#: Invocation_Model_ID 결정 입력 형태(값을 만들어내지 않는지 확인하기 위한 전수).
INVOCATION_FLAVORS: tuple[str, ...] = ("explicit", "singleObserved", "multiObserved", "none")


# ─────────────────────────────────────────────────────────────────
# body 노드 유틸 — 단정에만 쓰는 순수 helper(구현 코드를 재사용하지 않는 독립 계산)
# ─────────────────────────────────────────────────────────────────
def _dict_node_paths(body: Any) -> list[tuple[Any, ...]]:
    """body 안의 모든 dict 노드 경로(root 포함, list index도 경로 성분에 들어간다)."""
    return [path for path, node in S.iter_nodes(body) if isinstance(node, dict)]


def _node_at(body: Any, node_path: tuple[Any, ...]) -> Any:
    """``node_path``가 가리키는 노드(존재가 보장된 경로만 넘긴다)."""
    node = body
    for part in node_path:
        node = node[part]
    return node


def _model_id_key_paths(body: Any) -> list[tuple[Any, ...]]:
    """``modelId`` key를 담은 노드의 전체 경로 목록(모든 중첩 경로를 재귀 탐색)."""
    return [
        tuple(path) + (rb.MODEL_ID_KEY,)
        for path, node in S.iter_nodes(body)
        if isinstance(node, dict) and rb.MODEL_ID_KEY in node
    ]


def _raw(body: Any) -> bytes:
    """기존 transport가 실제로 전송하는 바이트열(dict key 순서까지 비교한다)."""
    return json.dumps(body).encode()


# ─────────────────────────────────────────────────────────────────
# 입력 생성기 — verified jobs Route_Contract + 오염된 baseline body
# ─────────────────────────────────────────────────────────────────
@st.composite
def _contract_field_paths(
    draw: Any, *, min_size: int = 1, max_size: int = 3, suffix: str | None = None
) -> list[str]:
    """계약 field path. 성분은 무작위 심볼이며 게이트웨이 key 이름을 포함하지 않는다.

    ``suffix``를 주면 마지막 성분으로 덧붙인다(예: 게이트웨이 key 이름으로 끝나는 중첩 경로).
    """
    parts = draw(
        S.field_paths(min_size=min_size, max_size=max_size).filter(
            lambda path: rb.MODEL_ID_KEY not in path
        )
    )
    return parts + [suffix] if suffix is not None else parts


@st.composite
def _polluted_bodies(draw: Any, *, reserved: frozenset[str], pollute: bool) -> dict:
    """Baseline_Request_Body. ``pollute``면 ``modelId`` key를 임의 중첩 노드에 0~3회 넣는다.

    예약 key(계약 field path 성분 + ``modelId``)는 생성기가 만들지 않으므로, 주입 위치는
    전부 이 함수가 통제한다 → "계약 path 외의 ``modelId``"를 결정론적으로 만들 수 있다.
    """
    body = copy.deepcopy(draw(S.baseline_bodies(reserved_keys=reserved)))
    if not pollute:
        return body
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        target = draw(st.sampled_from(_dict_node_paths(body)))
        _node_at(body, target)[rb.MODEL_ID_KEY] = draw(
            st.one_of(S.symbols(), S.json_scalars())
        )
    return body


@st.composite
def _jobs_cases(draw: Any, *, kind: str) -> dict:
    """한 케이스 종류의 입력 묶음.

    반환: ``{"kind", "entry", "binding", "contract", "fieldPath", "modelIdRequired",
    "modelIdArg", "explicitInvocationId", "observedInvocationIds", "baselineBody",
    "expectsBaselineSeamEquality"}``
    """
    model_id = draw(S.model_ids(allow_empty=False))
    evidence_ref = draw(S.evidence_ids())

    # ── 계약 field path — 실제 경로는 evidence만이 확정한다(상수 없음)
    if kind == "requiredTopLevel":
        field_path = [rb.MODEL_ID_KEY]  # 기존 구현이 쓰는 위치(무회귀 경계값)
    elif kind == "requiredGatewayKey":
        field_path = draw(_contract_field_paths(max_size=2, suffix=rb.MODEL_ID_KEY))
    else:
        field_path = draw(_contract_field_paths())

    required: bool | None = None if kind == "undetermined" else (kind in REQUIRED_KINDS)
    contract = draw(
        S.route_contracts(
            route_key=JOBS,
            complete=True,
            model_id_required=bool(required),
            evidence_ref=evidence_ref,
        )
    )
    # 계약이 요구하지 않아도 path는 계약에 남아 있을 수 있다(무시돼야 한다).
    contract["modelIdFieldPath"] = list(field_path)
    contract["modelIdRequired"] = required

    # ── Invocation_Model_ID 결정 입력
    flavor = "none" if kind == "requiredTopLevel" else draw(st.sampled_from(INVOCATION_FLAVORS))
    explicit = draw(S.model_ids(allow_empty=False)) if flavor == "explicit" else None
    if flavor == "singleObserved":
        observed = [draw(S.model_ids(allow_empty=False))]
    elif flavor == "multiObserved":
        observed = draw(
            st.lists(S.model_ids(allow_empty=False), min_size=2, max_size=3, unique=True)
        )
    elif flavor == "explicit":
        observed = draw(st.lists(S.model_ids(allow_empty=False), max_size=2, unique=True))
    else:
        observed = []

    # transport가 넘기는 model ID — prefix 교정 결과일 수 있으므로 entry ID와 달라도 된다.
    model_id_arg = (
        model_id
        if kind == "requiredTopLevel"
        else draw(st.one_of(st.just(model_id), S.model_ids(allow_empty=False)))
    )

    # ── Capability_Map entry(유효 entry). 미확정 계약이면 jobs route는 `UNVERIFIED`로 남는다.
    determined = required is not None
    entry = contracts.new_entry(draw(S.candidate_labels()))
    entry["modelId"] = model_id
    entry["provider"] = draw(S.providers(allow_empty=False))
    entry["invocationModelIds"] = list(observed)
    entry["catalogFingerprint"] = draw(S.catalog_fingerprints(allow_empty=False))
    entry["verifiedAt"] = draw(S.utc_timestamps())
    entry["revision"] = draw(S.revisions(allow_empty=False))
    entry["evidence"] = [evidence_ref]
    entry["routes"][JOBS] = {
        "status": str(
            contracts.Route_Support_Status.SUPPORTED
            if determined
            else contracts.Route_Support_Status.UNVERIFIED
        ),
        "allowlist": str(contracts.Allowlist_Result.ALLOWED),
        "contract": contract,
        "evidenceRef": evidence_ref,
    }
    entry["verificationStatus"] = str(
        contracts.Verification_Status.VERIFIED if determined else contracts.Verification_Status.UNVERIFIED
    )
    entry = S.refresh_fingerprint(entry)

    binding = rb.bind_contract(entry, contract, invocation_model_id=explicit)

    # ── baseline body. top-level 경계값만 오염 없이(기본 구현과 바이트 비교) 만든다.
    pollute = kind != "requiredTopLevel"
    baseline = draw(
        _polluted_bodies(reserved=frozenset(field_path) | {rb.MODEL_ID_KEY}, pollute=pollute)
    )

    return {
        "kind": kind,
        "entry": entry,
        "binding": binding,
        "contract": contract,
        "fieldPath": list(field_path),
        "modelIdRequired": required,
        "modelIdArg": model_id_arg,
        "explicitInvocationId": explicit,
        "observedInvocationIds": list(observed),
        "baselineBody": baseline,
        "expectsBaselineSeamEquality": kind == "requiredTopLevel",
    }


@st.composite
def jobs_case_matrix(draw: Any) -> list[dict]:
    """:data:`CASE_KINDS` 전 종류를 한 example에 하나씩 담은 목록.

    ``max_examples=100``에서도 요구/미요구/미확정 세 분기와 path 경계값 전수 도달을 보장한다
    (``sampled_from`` 분포에 의존하지 않는다).
    """
    return [draw(_jobs_cases(kind=kind)) for kind in CASE_KINDS]


# ─────────────────────────────────────────────────────────────────
# 기대값 계산 — 구현을 호출하지 않는 독립 oracle
# ─────────────────────────────────────────────────────────────────
def _expected_invocation_id(case: dict) -> str:
    """계약에 명시된 값 → 관측된 단일 ID → transport가 넘긴 model ID 순으로 정한다."""
    explicit = case["explicitInvocationId"]
    if isinstance(explicit, str) and explicit:
        return explicit
    observed = [item for item in case["observedInvocationIds"] if isinstance(item, str) and item]
    if len(observed) == 1:
        return observed[0]
    return case["modelIdArg"]


def _invocation_sources(case: dict) -> set[str]:
    """Invocation_Model_ID로 쓰일 수 있는 값의 전체 출처(이 밖의 값은 생성 금지)."""
    sources = {case["modelIdArg"], *case["observedInvocationIds"]}
    explicit = case["explicitInvocationId"]
    if isinstance(explicit, str) and explicit:
        sources.add(explicit)
    return sources


# ─────────────────────────────────────────────────────────────────
# 케이스 단정 — Property 10의 본문
# ─────────────────────────────────────────────────────────────────
def _check_generator_preconditions(case: dict) -> None:
    """생성기가 만든 입력이 verified jobs 계약 형태인지 확인한다(단정의 전제)."""
    kind = case["kind"]
    contract, binding, path = case["contract"], case["binding"], case["fieldPath"]

    assert S.is_valid_entry(case["entry"]), f"{kind}: Malformed entry로는 계약을 검증할 수 없다"
    assert contracts.validate_route_contract(contract, route_key=JOBS) == [], f"{kind}: 계약 스키마 위반"
    assert contract["modelIdRequired"] is case["modelIdRequired"], kind
    assert contract["modelIdFieldPath"] == path, kind
    if case["modelIdRequired"] is None:
        # 성공 probe로 확정되지 않은 계약은 완전하지 않다(jobs route는 `UNVERIFIED` 유지).
        assert contracts.route_contract_missing(contract) == ["modelIdRequired"], kind
    else:
        assert contracts.route_contract_is_complete(contract), f"{kind}: 계약이 불완전하다"

    assert rb.jobs_contract(binding) is binding, f"{kind}: jobs 계약으로 인식되지 않았다"
    assert binding["routeKey"] == JOBS and binding["modelId"], kind
    assert rb.is_field_path(path), kind
    # baseline은 계약 path 성분을 key로 갖지 않는다(주입 전 경로 발생 횟수 0).
    baseline = case["baselineBody"]
    assert S.count_field_path_occurrences(baseline, path) == 0, kind
    for part in path:
        if part == rb.MODEL_ID_KEY:
            continue  # `modelId`는 오염 단계에서 의도적으로 넣을 수 있다
        assert rb.count_key_occurrences(baseline, part) == 0, f"{kind}: baseline에 {part} 존재"


def _check_required(case: dict, base: GatewayClient, client: Any) -> None:
    """``modelIdRequired``가 참인 경우 — exact path 1회 + 그 외 경로 0회."""
    kind, path = case["kind"], case["fieldPath"]
    baseline, binding, model_id_arg = case["baselineBody"], case["binding"], case["modelIdArg"]
    expected_id = _expected_invocation_id(case)

    body = rb.apply_jobs_model_id(baseline, model_id_arg, binding)
    assert isinstance(body, dict), f"{kind}: 확정 계약인데 기본 구현으로 위임됐다"
    assert body is not baseline, f"{kind}: baseline 객체를 그대로 돌려줬다"

    # ── (1) exact field path에 Invocation_Model_ID 정확히 1회
    assert rb.value_at_path(body, path) == expected_id, f"{kind}: 기록된 model ID가 다르다"
    assert S.count_field_path_occurrences(body, path) == 1, f"{kind}: 경로 일치 노드가 1개가 아니다"

    # ── (2) 그 외 경로에는 `modelId` key가 없다
    assert _model_id_key_paths(body) == (
        [tuple(path)] if path[-1] == rb.MODEL_ID_KEY else []
    ), f"{kind}: 계약 path 밖에 modelId key가 남았다"
    assert rb.count_key_occurrences(body, rb.MODEL_ID_KEY) == (
        1 if path[-1] == rb.MODEL_ID_KEY else 0
    ), kind

    # ── (3) 경로를 제외한 나머지 body는 `modelId`를 제거한 baseline과 동일
    stripped = rb.without_key(baseline, rb.MODEL_ID_KEY)
    assert _raw(rb.without_key(body, path[0])) == _raw(stripped), f"{kind}: 경로 외 body가 변했다"
    assert set(body) == set(stripped) | {path[0]}, f"{kind}: 최상위 key 집합이 변했다"
    for key in stripped:
        if key == path[0]:
            continue
        assert _raw(body[key]) == _raw(stripped[key]), f"{kind}: {key} 서브트리가 변형됐다"

    # ── (4) Invocation_Model_ID를 만들어내지 않는다
    assert expected_id in _invocation_sources(case), f"{kind}: 출처 없는 model ID"
    assert rb.invocation_model_id(binding, model_id_arg) == expected_id, kind

    # ── (5) seam이 계약 결과를 그대로 전송한다
    assert _raw(client._apply_jobs_model_id(baseline, model_id_arg)) == _raw(body), kind
    if case["expectsBaselineSeamEquality"]:
        # 계약 path가 기존 위치이고 ID가 transport 인자와 같으면 기존 동작과 바이트 동일.
        assert _raw(body) == _raw(base._apply_jobs_model_id(baseline, model_id_arg)), kind

    # ── (6) 재적용 누적 금지
    again = rb.apply_jobs_model_id(body, model_id_arg, binding)
    assert _raw(again) == _raw(body), f"{kind}: 재적용으로 body가 변했다"


def _check_not_required(case: dict, client: Any) -> None:
    """``modelIdRequired``가 거짓인 경우 — body 전체에서 `modelId` key 0회."""
    kind, path = case["kind"], case["fieldPath"]
    baseline, binding, model_id_arg = case["baselineBody"], case["binding"], case["modelIdArg"]

    body = rb.apply_jobs_model_id(baseline, model_id_arg, binding)
    assert isinstance(body, dict), f"{kind}: 확정 계약인데 기본 구현으로 위임됐다"

    assert rb.count_key_occurrences(body, rb.MODEL_ID_KEY) == 0, f"{kind}: modelId key가 남았다"
    assert _model_id_key_paths(body) == [], kind
    # 계약에 남아 있는 path는 사용되지 않는다.
    assert S.count_field_path_occurrences(body, path) == 0, f"{kind}: 미요구인데 path에 기록됐다"

    stripped = rb.without_key(baseline, rb.MODEL_ID_KEY)
    assert _raw(body) == _raw(stripped), f"{kind}: baseline이 변형됐다"
    assert _raw(client._apply_jobs_model_id(baseline, model_id_arg)) == _raw(body), kind

    again = rb.apply_jobs_model_id(body, model_id_arg, binding)
    assert _raw(again) == _raw(body), f"{kind}: 재적용으로 body가 변했다"


def _check_undetermined(case: dict, base: GatewayClient, client: Any) -> None:
    """``modelIdRequired``가 미확정이면 기존 기본 구현에 위임한다(바이트 동일)."""
    kind = case["kind"]
    baseline, binding, model_id_arg = case["baselineBody"], case["binding"], case["modelIdArg"]

    assert (
        rb.apply_jobs_model_id(baseline, model_id_arg, binding) is None
    ), f"{kind}: 미확정 계약으로 body를 바꿨다"

    seam = client._apply_jobs_model_id(baseline, model_id_arg)
    assert _raw(seam) == _raw(base._apply_jobs_model_id(baseline, model_id_arg)), kind
    # 기존 인라인 동작: top-level `modelId` 부착(키 순서까지 동일).
    assert _raw(seam) == _raw({**baseline, rb.MODEL_ID_KEY: model_id_arg}), kind


def _check_case(case: dict) -> None:
    """한 케이스에 대해 jobs `modelId` 계약 일치를 단정한다."""
    _check_generator_preconditions(case)

    before_baseline = copy.deepcopy(case["baselineBody"])
    before_binding = copy.deepcopy(case["binding"])

    base = GatewayClient(gateway_url=PLACEHOLDER_GATEWAY_URL, region="us-west-2")
    client = rb.effort_bound_client(base, case["binding"], None)

    if case["modelIdRequired"] is None:
        _check_undetermined(case, base, client)
    elif case["modelIdRequired"]:
        _check_required(case, base, client)
    else:
        _check_not_required(case, client)

    # 입력 불변(순수성) — baseline body와 binding은 변형되지 않는다.
    assert case["baselineBody"] == before_baseline, f"{case['kind']}: baseline body가 변형됐다"
    assert case["binding"] == before_binding, f"{case['kind']}: binding이 변형됐다"


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
        "caseKinds": list(CASE_KINDS),
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
# Property 10
# ─────────────────────────────────────────────────────────────────
# Feature: gateway-models-effort-support, Property 10: jobs `modelId` 계약 일치
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(jobs_case_matrix())
def _property_jobs_model_id_matches_contract(cases: list[dict]) -> None:
    """jobs 계약이 요구하면 exact path 1회, 요구하지 않으면 body 전체 0회.

    (Requirements 8.11, 8.12, 12.18)
    """
    assert [case["kind"] for case in cases] == list(CASE_KINDS), "케이스 종류 전수가 아니다"
    for case in cases:
        _check_case(case)


def test_jobs_model_id_matches_contract() -> None:
    """Property 10 pytest 진입점 — 실패 시 최소화 counterexample과 재현 blob을 기록한다."""
    try:
        _property_jobs_model_id_matches_contract()
    except BaseException as exc:
        _report_counterexample("test_jobs_model_id_matches_contract", exc)
        raise


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지).
    test_jobs_model_id_matches_contract()
    print(
        f"PASSED: Property {PROPERTY_ID} — {PROPERTY_NAME} "
        f"(seed={S.AE_PBT_SEED}, max_examples={S.MAX_EXAMPLES})"
    )
