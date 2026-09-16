"""공통 Hypothesis 생성기 — Feature: gateway-models-effort-support (Property 1~10 공용).

이 모듈은 **테스트 데이터 생성기만** 담고 단정(assertion)은 담지 않는다. Correctness
Properties 1~10의 property test 파일(`scripts/test_*_pbt.py`)이 모두 여기서 생성기를
가져와 쓴다.

절대 원칙 — **확정 상수 금지**
  model ID, provider, route 지원 여부, effort field path, effort 허용값을 이 모듈에
  상수로 두지 않는다. 전부 무작위 심볼(:func:`symbols`)로만 생성한다. 실제 값은
  Authoritative_Evidence(작업 18의 production path probe)만이 채운다.
  Candidate_Label도 의미 없는 무작위 심볼로 생성한다(라벨에서 값 유도 금지).
  이 모듈의 결과는 Gateway 지원 근거가 아니다(Requirement 12.22).

공통 헤더 규약 (design.md "PBT 구성 규칙")
  ``AE_PBT_SEED = 20260803``  — `AE_PBT_SEED` 환경변수로 오버라이드 가능(보고서 기록용)
  ``@seed(AE_PBT_SEED)``
  ``@settings(max_examples=100, deadline=None, database=None, print_blob=True)``

  사용 예::

      from hypothesis import given, seed, settings
      from _capability_strategies import AE_PBT_SEED, PBT_SETTINGS, capability_maps

      # Feature: gateway-models-effort-support, Property 1: Activation subset
      @seed(AE_PBT_SEED)
      @PBT_SETTINGS
      @given(capability_maps())
      def test_activation_subset(map_obj): ...

명시적으로 포함하는 경계값(design.md "PBT 구성 규칙")
  - 빈 map                → :func:`capability_maps` 의 ``min_entries=0``
  - 단일 entry            → :func:`capability_maps` 의 크기 1 케이스
  - 동시각 tie            → :func:`tie_capability_maps`, :data:`SIBLING_KINDS` 의 ``tie``
  - 빈 문자열 ID          → :func:`model_ids`, :func:`providers` (``UNDETERMINED``)
  - enum 단일값           → :func:`enum_domains` 의 ``min_size=1``
  - range 상·하한 동일     → :func:`range_domains` 의 delta ``0``
  - 최대 중첩 body        → :func:`max_depth_bodies` (:data:`MAX_BODY_DEPTH`)

제공 생성기 요약
  enum 전수      :func:`verification_statuses` … :func:`failure_categories`
  심볼           :func:`symbols`, :func:`model_ids`, :func:`providers`, :func:`field_paths` …
  계약           :func:`route_contracts`, :func:`effort_contracts`
  entry·map      :func:`route_entries`, :func:`effort_entries`, :func:`entries`,
                 :func:`capability_maps`, :func:`tie_capability_maps`, :func:`activation_contexts`
  malformed      :data:`MUTATION_KINDS`, :func:`malformed_mutations`, :func:`apply_mutation`,
                 :func:`malformed_entries`
  순서 치환      :func:`reorder`, :func:`reorderings`, :func:`reordered_pairs`,
                 :func:`excluded_perturbations`, :func:`apply_excluded_perturbation`
  중첩 body      :func:`bodies`, :func:`max_depth_bodies`, :func:`baseline_bodies`,
                 :func:`supported_effort_cases`, :func:`unsupported_effort_cases`,
                 :func:`unsupported_effort_case_matrix`(불일치 6종 전수)
  effort settings :func:`effort_settings`, :func:`effort_settings_entries`,
                 :func:`effort_settings_from_map`

실행: 모든 Python 실행은 `ai_engine/.venv/bin/python`을 사용한다.

_Requirements: 12.19, 12.22_
"""
from __future__ import annotations

import copy
import os
import random
import string
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence

from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

# repo 루트를 import 경로에 추가한다(scripts/ 하위 실행 관행 재사용).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ai_engine.capability import canonicalizer, contracts  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# 공통 헤더 규약 (design.md "PBT 구성 규칙")
# ─────────────────────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    """환경변수를 정수로 읽는다(미설정·파싱 실패 시 기본값)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


#: 보고서에 기록되는 기본 재현 seed.
AE_PBT_SEED_DEFAULT = 20260803

#: 실제 사용 seed. `AE_PBT_SEED` 환경변수로 오버라이드한다.
AE_PBT_SEED = _env_int("AE_PBT_SEED", AE_PBT_SEED_DEFAULT)

#: 최소 실행 case 수(Requirement 12.19 — 최소 100). 하한 아래로는 내려가지 않는다.
MIN_EXAMPLES = 100

#: 실제 사용 case 수. `AE_PBT_MAX_EXAMPLES` 환경변수로 늘릴 수 있다(줄일 수는 없다).
MAX_EXAMPLES = max(MIN_EXAMPLES, _env_int("AE_PBT_MAX_EXAMPLES", MIN_EXAMPLES))


def pbt_settings(**overrides: Any) -> Any:
    """property test 공통 `@settings` 데코레이터.

    기본값: ``max_examples=100, deadline=None, database=None, print_blob=True``.
    ``max_examples``는 :data:`MIN_EXAMPLES` 미만으로 내려가지 않는다.
    """
    resolved: dict[str, Any] = {
        "max_examples": MAX_EXAMPLES,
        "deadline": None,
        "database": None,
        "print_blob": True,
    }
    resolved.update(overrides)
    resolved["max_examples"] = max(MIN_EXAMPLES, int(resolved["max_examples"]))
    return hypothesis_settings(**resolved)


#: 그대로 붙여 쓰는 공통 settings 데코레이터.
PBT_SETTINGS = pbt_settings()


# ─────────────────────────────────────────────────────────────────
# 닫힌 enum 전수 (contracts의 닫힌 집합을 그대로 재사용)
# ─────────────────────────────────────────────────────────────────
VERIFICATION_STATUSES: tuple[str, ...] = contracts.Verification_Status.values()
ROUTE_SUPPORT_STATUSES: tuple[str, ...] = contracts.Route_Support_Status.values()
EFFORT_SUPPORT_STATUSES: tuple[str, ...] = contracts.Effort_Support_Status.values()
ALLOWLIST_RESULTS: tuple[str, ...] = contracts.Allowlist_Result.values()
KNOWN_ROUTES: tuple[str, ...] = contracts.KNOWN_ROUTES
EXECUTION_MODES: tuple[str, ...] = contracts.Execution_Mode.values()
SIGNING_SERVICES: tuple[str, ...] = contracts.Signing_Service.values()
DOMAIN_KINDS: tuple[str, ...] = contracts.Domain_Kind.values()
VALUE_TYPES: tuple[str, ...] = contracts.Value_Type.values()
SOURCE_KINDS: tuple[str, ...] = contracts.Source_Kind.values()
FAILURE_CATEGORIES: tuple[str, ...] = contracts.Failure_Category.values()

#: `RANGE` domain에서 사용 가능한 value type(경계값이 number여야 계약이 완전해진다).
RANGE_VALUE_TYPES: tuple[str, ...] = (
    str(contracts.Value_Type.INTEGER),
    str(contracts.Value_Type.NUMBER),
)


def _sampled(values: Sequence[str], exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    excluded = set(exclude)
    remaining = [value for value in values if value not in excluded]
    if not remaining:
        raise ValueError(f"모든 값이 제외됐다: {values!r} - {sorted(excluded)!r}")
    return st.sampled_from(remaining)


def verification_statuses(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Verification_Status` 전수(제외 목록 지원)."""
    return _sampled(VERIFICATION_STATUSES, exclude)


def route_support_statuses(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Route_Support_Status` 전수."""
    return _sampled(ROUTE_SUPPORT_STATUSES, exclude)


def effort_support_statuses(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Effort_Support_Status` 전수."""
    return _sampled(EFFORT_SUPPORT_STATUSES, exclude)


def allowlist_results(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Allowlist_Result` 전수."""
    return _sampled(ALLOWLIST_RESULTS, exclude)


def known_routes(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Known_Route` 전수."""
    return _sampled(KNOWN_ROUTES, exclude)


def execution_modes(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Execution_Mode` 전수."""
    return _sampled(EXECUTION_MODES, exclude)


def signing_services(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Signing_Service` 전수."""
    return _sampled(SIGNING_SERVICES, exclude)


def domain_kinds(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Domain_Kind` 전수."""
    return _sampled(DOMAIN_KINDS, exclude)


def value_types(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Value_Type` 전수."""
    return _sampled(VALUE_TYPES, exclude)


def source_kinds(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Source_Kind` 전수."""
    return _sampled(SOURCE_KINDS, exclude)


def failure_categories(*, exclude: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """`Failure_Category` 전수."""
    return _sampled(FAILURE_CATEGORIES, exclude)


def non_member_strings(values: Iterable[str]) -> st.SearchStrategy[str]:
    """닫힌 집합을 이탈하는 문자열(enum 이탈 mutation용). 빈 문자열도 포함한다."""
    members = set(values)
    return st.one_of(st.just(contracts.UNDETERMINED), symbols()).filter(
        lambda text: text not in members
    )


# ─────────────────────────────────────────────────────────────────
# 무작위 심볼 — model ID·provider·field path·effort value의 유일한 출처
# ─────────────────────────────────────────────────────────────────

#: 심볼 알파벳. ASCII 외 문자를 섞어 UTF-8 바이트 순 정렬 경로도 함께 exercise한다.
SYMBOL_ALPHABET = string.ascii_letters + string.digits + "-_.:" + "가나ÄÖ日"

#: fingerprint 본문(hex) 알파벳.
_HEX_ALPHABET = "0123456789abcdef"


def symbols(*, min_size: int = 1, max_size: int = 12) -> st.SearchStrategy[str]:
    """의미 없는 무작위 심볼(모든 식별자·경로 성분·키의 출처)."""
    return st.text(alphabet=SYMBOL_ALPHABET, min_size=min_size, max_size=max_size)


def model_ids(*, allow_empty: bool = True) -> st.SearchStrategy[str]:
    """Exact_Model_ID 자리. 라벨·family 추정 없이 무작위 심볼만 만든다.

    ``allow_empty``면 빈 문자열(미확정 = Exact_Model_ID 미발견) 경계값을 낮은 빈도로 섞는다.
    """
    base = symbols()
    if not allow_empty:
        return base
    return st.one_of(base, base, base, st.just(contracts.UNDETERMINED))


def providers(*, allow_empty: bool = True) -> st.SearchStrategy[str]:
    """Provider_String 자리(무작위 심볼). 빈 문자열은 미확정 경계값."""
    return model_ids(allow_empty=allow_empty)


def display_names() -> st.SearchStrategy[str | None]:
    """표시명(fingerprint 제외 입력). ``None``도 포함한다."""
    return st.one_of(st.none(), symbols())


def candidate_labels() -> st.SearchStrategy[str]:
    """검색 라벨 자리. 라벨 문자열에서 어떤 값도 유도하지 않으므로 무작위 심볼로 둔다."""
    return symbols()


def revisions(*, allow_empty: bool = True) -> st.SearchStrategy[str]:
    """Current_Revision 자리(무작위 심볼)."""
    return model_ids(allow_empty=allow_empty)


def _hex_body(min_size: int = 8, max_size: int = 16) -> st.SearchStrategy[str]:
    return st.text(alphabet=_HEX_ALPHABET, min_size=min_size, max_size=max_size)


def catalog_fingerprints(*, allow_empty: bool = True) -> st.SearchStrategy[str]:
    """Catalog_Fingerprint 형식(`cat1:sha256:<hex>`) 문자열."""
    values = _hex_body().map(lambda body: canonicalizer.CATALOG_FINGERPRINT_PREFIX + body)
    if not allow_empty:
        return values
    return st.one_of(values, values, values, st.just(contracts.UNDETERMINED))


def capability_fingerprint_strings() -> st.SearchStrategy[str]:
    """Capability_Fingerprint 형식 문자열(불일치 mutation·tuple 불일치 생성용)."""
    return _hex_body().map(lambda body: canonicalizer.CAPABILITY_FINGERPRINT_PREFIX + body)


def evidence_ids() -> st.SearchStrategy[str]:
    """Evidence_Record_ID 형식(`evr1:sha256:<hex>`) 문자열."""
    return _hex_body().map(lambda body: canonicalizer.EVIDENCE_RECORD_ID_PREFIX + body)


def evidence_id_lists(*, min_size: int = 0, max_size: int = 3) -> st.SearchStrategy[list[str]]:
    """entry의 evidence 목록(중복 없음). ``min_size=0``은 evidence 미보유 경계값."""
    return st.lists(evidence_ids(), min_size=min_size, max_size=max_size, unique=True)


def utc_timestamps(*, allow_undetermined: bool = False) -> st.SearchStrategy[str]:
    """UTC ISO 8601 시각 문자열. ``allow_undetermined``면 빈 문자열(미확정)도 포함."""
    stamps = st.datetimes(
        min_value=datetime(2020, 1, 1),
        max_value=datetime(2035, 1, 1),
        timezones=st.just(timezone.utc),
    ).map(contracts.to_utc_iso)
    if not allow_undetermined:
        return stamps
    return st.one_of(stamps, stamps, stamps, st.just(contracts.UNDETERMINED))


def field_paths(*, min_size: int = 1, max_size: int = 3) -> st.SearchStrategy[list[str]]:
    """순서 의미 field path(무작위 심볼 목록). 실제 경로는 evidence만이 확정한다."""
    return st.lists(symbols(), min_size=min_size, max_size=max_size, unique=True)


def purpose_lists(*, min_size: int = 1, max_size: int = 3) -> st.SearchStrategy[list[str]]:
    """요청 목적 집합(집합 의미)."""
    return st.lists(symbols(), min_size=min_size, max_size=max_size, unique=True)


def min_output_bounds() -> st.SearchStrategy[dict]:
    """Minimal_Request의 최소 output/token bound(키·값 모두 무작위)."""
    return st.dictionaries(
        symbols(), st.integers(min_value=1, max_value=8), min_size=1, max_size=2
    )


# ─────────────────────────────────────────────────────────────────
# Route_Contract
# ─────────────────────────────────────────────────────────────────

#: 계약 완전성을 깨는 blank 값(스키마 타입은 유지 → Malformed가 아니라 "미확정").
_ROUTE_BLANKS: dict[str, Any] = {
    "endpointRef": contracts.UNDETERMINED,
    "httpMethod": contracts.UNDETERMINED,
    "outputValidatorRef": contracts.UNDETERMINED,
    "terminalConditionRef": contracts.UNDETERMINED,
    "retryPolicyRef": contracts.UNDETERMINED,
    "executionMode": None,
    "signingService": None,
    "modelIdRequired": None,
    "messageFieldPath": None,
    "purposes": [],
    "minOutputBound": {},
}


@st.composite
def route_contracts(
    draw: Any,
    *,
    route_key: str | None = None,
    complete: bool | None = None,
    model_id_required: bool | None = None,
    evidence_ref: str | None = None,
    purposes: Sequence[str] | None = None,
) -> dict:
    """Route_Contract 생성기.

    ``complete=True``면 :func:`contracts.route_contract_is_complete`를 만족하는 계약,
    ``False``면 완전성 필드 일부가 미확정인 계약을 만든다(스키마는 항상 유효).
    endpoint·method·validator 참조자와 field path는 전부 무작위 심볼이다.
    """
    route = route_key if route_key is not None else draw(known_routes())
    is_complete = draw(st.booleans()) if complete is None else complete
    requires_model_id = draw(st.booleans()) if model_id_required is None else model_id_required

    contract = contracts.new_route_contract(
        route,
        endpoint_ref=draw(symbols()),
        http_method=draw(symbols()),
        execution_mode=draw(execution_modes()),
        signing_service=draw(signing_services()),
        model_id_required=requires_model_id,
        model_id_field_path=draw(field_paths()),
        message_field_path=draw(field_paths()),
        inference_config_field_path=draw(st.one_of(st.none(), field_paths())),
        optional_fields=draw(st.lists(symbols(), max_size=3, unique=True)),
        output_validator_ref=draw(symbols()),
        terminal_condition_ref=draw(symbols()),
        retry_policy_ref=draw(symbols()),
        fallback_rank=draw(st.integers(min_value=0, max_value=8)),
        purposes=list(purposes) if purposes is not None else draw(purpose_lists()),
        min_output_bound=draw(min_output_bounds()),
        evidence_ref=evidence_ref,
    )

    if not is_complete:
        blanked = draw(
            st.lists(
                st.sampled_from(sorted(_ROUTE_BLANKS)), min_size=1, max_size=3, unique=True
            )
        )
        for field in blanked:
            contract[field] = copy.deepcopy(_ROUTE_BLANKS[field])
    return contract


# ─────────────────────────────────────────────────────────────────
# Effort_Contract — domain(enum/range)과 경계값
# ─────────────────────────────────────────────────────────────────
def enum_domains(value_type: str, *, min_size: int = 1, max_size: int = 4) -> st.SearchStrategy[list]:
    """`ENUM` domain 값 목록. ``min_size=1``이 "enum 단일값" 경계값이다."""
    if value_type == contracts.Value_Type.STRING:
        element = symbols()
    elif value_type == contracts.Value_Type.INTEGER:
        element = st.integers(min_value=-64, max_value=64)
    elif value_type == contracts.Value_Type.NUMBER:
        element = st.floats(
            min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False
        )
    else:  # BOOLEAN
        return st.lists(st.booleans(), min_size=min_size, max_size=2, unique=True)
    return st.lists(element, min_size=min_size, max_size=max_size, unique=True)


@st.composite
def range_domains(draw: Any, value_type: str) -> tuple[Any, Any]:
    """`RANGE` inclusive 경계 쌍. delta ``0``(상·하한 동일)을 경계값으로 명시 포함한다."""
    if value_type == contracts.Value_Type.INTEGER:
        lower = draw(st.integers(min_value=-64, max_value=64))
        delta = draw(st.one_of(st.just(0), st.integers(min_value=1, max_value=32)))
        return lower, lower + delta
    lower = draw(
        st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False)
    )
    delta = draw(
        st.one_of(
            st.just(0.0),
            st.floats(
                min_value=0.5, max_value=1e3, allow_nan=False, allow_infinity=False
            ),
        )
    )
    return lower, lower + delta


def in_domain(effort_contract: Any, value: Any) -> bool:
    """value가 계약의 verified domain에 속하는지(enum 멤버십 또는 inclusive range).

    domain이 미확정이면 어떤 값도 domain 안에 있다고 보지 않는다(값 추론 금지).
    enum 멤버십은 canonical 표현으로 비교하므로 ``True``와 ``1``을 혼동하지 않는다.
    """
    if not isinstance(effort_contract, dict):
        return False
    value_type = effort_contract.get("valueType")
    if not contracts.Value_Type.has(value_type):
        return False
    if not contracts.value_matches_type(value, value_type):
        return False

    domain_kind = effort_contract.get("domainKind")
    if domain_kind == contracts.Domain_Kind.ENUM:
        members = effort_contract.get("enumValues")
        if not isinstance(members, list) or not members:
            return False
        return any(canonicalizer.canonical_equal(value, member) for member in members)
    if domain_kind == contracts.Domain_Kind.RANGE:
        lower = effort_contract.get("rangeLowerInclusive")
        upper = effort_contract.get("rangeUpperInclusive")
        if not isinstance(lower, (int, float)) or isinstance(lower, bool):
            return False
        if not isinstance(upper, (int, float)) or isinstance(upper, bool):
            return False
        return lower <= value <= upper
    return False


def domain_values(effort_contract: dict) -> st.SearchStrategy[Any]:
    """계약의 verified domain **안**의 값. range는 두 경계를 명시적으로 포함한다."""
    value_type = effort_contract.get("valueType")
    domain_kind = effort_contract.get("domainKind")
    if domain_kind == contracts.Domain_Kind.ENUM:
        members = list(effort_contract.get("enumValues") or [])
        if not members:
            raise ValueError("enum domain이 비어 있어 in-domain 값을 만들 수 없다")
        return st.sampled_from(members)
    if domain_kind == contracts.Domain_Kind.RANGE:
        lower = effort_contract.get("rangeLowerInclusive")
        upper = effort_contract.get("rangeUpperInclusive")
        bounds = st.sampled_from([lower, upper])
        if value_type == contracts.Value_Type.INTEGER:
            return st.one_of(bounds, st.integers(min_value=int(lower), max_value=int(upper)))
        return st.one_of(
            bounds,
            st.floats(
                min_value=float(lower),
                max_value=float(upper),
                allow_nan=False,
                allow_infinity=False,
            ),
        )
    raise ValueError("domain이 미확정인 계약에서는 in-domain 값을 만들 수 없다")


def out_of_domain_values(effort_contract: dict) -> st.SearchStrategy[Any]:
    """계약의 verified domain을 **이탈**하는 값(타입 이탈도 domain 이탈이다)."""
    candidates = st.one_of(
        symbols(),
        st.integers(min_value=-4096, max_value=4096),
        st.floats(min_value=-1e7, max_value=1e7, allow_nan=False, allow_infinity=False),
        st.booleans(),
    )
    return candidates.filter(lambda value: not in_domain(effort_contract, value))


@st.composite
def effort_contracts(
    draw: Any,
    *,
    model_id: str | None = None,
    route_key: str | None = None,
    complete: bool | None = None,
    domain_kind: str | None = None,
    value_type: str | None = None,
    evidence_ref: str | None = None,
) -> dict:
    """Effort_Contract 생성기.

    field path·value type·domain은 모두 무작위이며, ``complete=False``면 그중 하나 이상이
    미확정인 계약(→ `Effort_Support_Status`는 `UNVERIFIED` 유지)을 만든다.
    ``modelId``는 entry의 Exact_Model_ID와 결속되므로 호출자가 주입할 수 있다.
    """
    mid = model_id if model_id is not None else draw(model_ids(allow_empty=False))
    route = route_key if route_key is not None else draw(known_routes())
    kind = domain_kind if domain_kind is not None else draw(domain_kinds())
    is_complete = draw(st.booleans()) if complete is None else complete

    if kind == contracts.Domain_Kind.ENUM:
        vtype = value_type if value_type is not None else draw(value_types())
        enum_values = draw(enum_domains(vtype))
        lower = upper = None
        pool = list(enum_values)
    else:
        vtype = value_type if value_type is not None else draw(st.sampled_from(RANGE_VALUE_TYPES))
        lower, upper = draw(range_domains(vtype))
        enum_values = None
        pool = [lower, upper]

    verified = draw(st.lists(st.sampled_from(pool), max_size=2, unique_by=repr))

    contract = contracts.new_effort_contract(
        mid,
        route,
        field_path=draw(field_paths()),
        value_type=vtype,
        domain_kind=kind,
        enum_values=enum_values,
        range_lower_inclusive=lower,
        range_upper_inclusive=upper,
        verified_values=verified,
        evidence_ref=evidence_ref,
    )

    if not is_complete:
        blanks = ["fieldPath", "valueType", "domainKind"]
        blanks.append("enumValues" if kind == contracts.Domain_Kind.ENUM else "rangeBounds")
        chosen = draw(st.lists(st.sampled_from(blanks), min_size=1, max_size=2, unique=True))
        for field in chosen:
            if field == "rangeBounds":
                contract["rangeLowerInclusive"] = None
                contract["rangeUpperInclusive"] = None
            else:
                contract[field] = None
        if contract.get("valueType") is None:
            # value type이 미확정이면 저장된 domain 값의 타입 검사를 걸 수 없다.
            contract["verifiedValues"] = []
    return contract


# ─────────────────────────────────────────────────────────────────
# Route_Entry / Effort_Entry
# ─────────────────────────────────────────────────────────────────
@st.composite
def route_entries(
    draw: Any,
    *,
    route_key: str,
    evidence: Sequence[str] = (),
    status: str | None = None,
    allowlist: str | None = None,
    purposes: Sequence[str] | None = None,
) -> dict:
    """Route_Entry 생성기.

    `SUPPORTED`는 완전한 계약과 Current_Evidence reference를 요구하므로(Requirement 3.3,
    3.4) evidence가 없으면 `SUPPORTED`를 만들지 않는다.
    """
    refs = list(evidence)
    resolved_status = status if status is not None else draw(
        route_support_statuses(exclude=() if refs else (contracts.Route_Support_Status.SUPPORTED,))
    )
    resolved_allowlist = allowlist if allowlist is not None else draw(allowlist_results())

    if resolved_status == contracts.Route_Support_Status.SUPPORTED:
        if not refs:
            raise ValueError("SUPPORTED route는 evidence reference가 필요하다")
        ref = draw(st.sampled_from(refs))
        contract = draw(
            route_contracts(
                route_key=route_key, complete=True, evidence_ref=ref, purposes=purposes
            )
        )
        entry = contracts.new_route_entry(resolved_status, resolved_allowlist)
        entry["contract"] = contract
        entry["evidenceRef"] = ref
        return entry

    ref = draw(st.one_of(st.none(), st.sampled_from(refs))) if refs else None
    contract = draw(
        st.one_of(
            st.none(),
            route_contracts(route_key=route_key, evidence_ref=ref, purposes=purposes),
        )
    )
    entry = contracts.new_route_entry(resolved_status, resolved_allowlist)
    entry["contract"] = contract
    entry["evidenceRef"] = ref
    return entry


@st.composite
def effort_entries(
    draw: Any,
    *,
    route_key: str,
    model_id: str,
    evidence: Sequence[str] = (),
    status: str | None = None,
) -> dict:
    """Effort_Entry 생성기.

    `SUPPORTED`는 완전한 계약(비어 있지 않은 modelId 포함)과 evidence reference를
    요구하므로(Requirement 3.6, 3.7) 그 조건을 만들 수 없으면 `SUPPORTED`를 만들지 않는다.
    """
    refs = list(evidence)
    can_support = bool(refs) and model_id != contracts.UNDETERMINED
    resolved_status = status if status is not None else draw(
        effort_support_statuses(
            exclude=() if can_support else (contracts.Effort_Support_Status.SUPPORTED,)
        )
    )

    if resolved_status == contracts.Effort_Support_Status.SUPPORTED:
        if not can_support:
            raise ValueError("SUPPORTED effort는 evidence와 비어 있지 않은 modelId가 필요하다")
        ref = draw(st.sampled_from(refs))
        contract = draw(
            effort_contracts(
                model_id=model_id, route_key=route_key, complete=True, evidence_ref=ref
            )
        )
        entry = contracts.new_effort_entry(resolved_status)
        entry["contract"] = contract
        entry["evidenceRef"] = ref
        return entry

    ref = draw(st.one_of(st.none(), st.sampled_from(refs))) if refs else None
    contract = draw(
        st.one_of(
            st.none(),
            effort_contracts(model_id=model_id, route_key=route_key, evidence_ref=ref),
        )
    )
    entry = contracts.new_effort_entry(resolved_status)
    entry["contract"] = contract
    entry["evidenceRef"] = ref
    return entry


# ─────────────────────────────────────────────────────────────────
# Capability_Map entry
# ─────────────────────────────────────────────────────────────────
def validate_entry_reasons(entry: Any) -> list[str]:
    """entry의 Malformed 판정 이유(빈 목록이면 유효 entry)."""
    return contracts.validate_entry(
        entry, fingerprint_fn=canonicalizer.capability_fingerprint
    )


def is_valid_entry(entry: Any) -> bool:
    """Malformed_Entry가 아닌지."""
    return not validate_entry_reasons(entry)


def refresh_fingerprint(entry: dict) -> dict:
    """`capabilityFingerprint`를 재계산해 기록한 새 dict를 돌려준다."""
    updated = copy.deepcopy(entry)
    updated["capabilityFingerprint"] = canonicalizer.capability_fingerprint(updated)
    return updated


@st.composite
def entries(
    draw: Any,
    *,
    active_ready: bool | None = None,
    model_id: str | None = None,
    provider: str | None = None,
    verification_status: str | None = None,
    source_kind: str | None = None,
    verified_at: str | None = None,
    evidence: Sequence[str] | None = None,
    candidate_label: str | None = None,
    purposes: Sequence[str] | None = None,
    complete_routes: bool | None = None,
) -> dict:
    """Capability_Map entry 생성기(항상 Malformed가 아닌 **유효** entry).

    ``active_ready=True``면 Activation_Gate 통과 후보가 될 수 있는 형태로 만든다
    (`VERIFIED`, 비어 있지 않은 modelId·provider, 모든 Known_Route 키, `SUPPORTED`
    route 1개 이상 + `ALLOWED` + 완전한 계약 + evidence, ``sourceKind != SEED``).
    ``False``면 임의 상태(빈 문자열 ID, `SEED`, 미완성 계약, evidence 미보유 등)를 섞는다.

    Malformed entry가 필요하면 :func:`malformed_entries`를 쓴다.
    """
    ready = draw(st.booleans()) if active_ready is None else active_ready

    label = candidate_label if candidate_label is not None else draw(candidate_labels())
    mid = model_id if model_id is not None else draw(model_ids(allow_empty=not ready))
    prov = provider if provider is not None else draw(providers(allow_empty=not ready))
    if ready:
        # ready entry는 identity와 evidence가 반드시 확정돼 있어야 한다.
        mid = mid or draw(model_ids(allow_empty=False))
        prov = prov or draw(providers(allow_empty=False))

    evidence_list = (
        list(evidence)
        if evidence is not None
        else draw(evidence_id_lists(min_size=1 if ready else 0))
    )

    route_keys = list(KNOWN_ROUTES)
    include_all = True if ready else (draw(st.booleans()) if complete_routes is None else complete_routes)
    if not include_all:
        # 최소 1개 route 키는 남긴다(Complete_Record 미충족 케이스).
        dropped = draw(
            st.lists(
                st.sampled_from(route_keys),
                min_size=1,
                max_size=len(route_keys) - 1,
                unique=True,
            )
        )
        route_keys = [route for route in route_keys if route not in dropped]

    shared_purposes = list(purposes) if purposes is not None else draw(purpose_lists())

    routes: dict[str, dict] = {}
    if ready:
        supported_route = draw(st.sampled_from(route_keys))
        routes[supported_route] = draw(
            route_entries(
                route_key=supported_route,
                evidence=evidence_list,
                status=str(contracts.Route_Support_Status.SUPPORTED),
                allowlist=str(contracts.Allowlist_Result.ALLOWED),
                purposes=shared_purposes,
            )
        )
    for route in route_keys:
        if route in routes:
            continue
        routes[route] = draw(
            route_entries(route_key=route, evidence=evidence_list, purposes=shared_purposes)
        )

    effort: dict[str, dict] = {
        route: draw(effort_entries(route_key=route, model_id=mid, evidence=evidence_list))
        for route in route_keys
    }

    entry = {
        "schemaVersion": contracts.SCHEMA_VERSION,
        "candidateLabel": label,
        "modelId": mid,
        "invocationModelIds": draw(st.lists(symbols(), max_size=3, unique=True)),
        "provider": prov,
        "displayName": draw(display_names()),
        "sourceKind": (
            source_kind
            if source_kind is not None
            else draw(source_kinds(exclude=(contracts.Source_Kind.SEED,) if ready else ()))
        ),
        "catalogFingerprint": draw(catalog_fingerprints(allow_empty=not ready)),
        "routes": routes,
        "syncSupport": draw(route_support_statuses()),
        "asyncSupport": draw(route_support_statuses()),
        "streamingSupport": draw(route_support_statuses()),
        "effort": effort,
        "verifiedAt": (
            verified_at
            if verified_at is not None
            else draw(utc_timestamps(allow_undetermined=not ready))
        ),
        "revision": draw(revisions(allow_empty=not ready)),
        "evidence": evidence_list,
        "verificationStatus": (
            verification_status
            if verification_status is not None
            else (
                str(contracts.Verification_Status.VERIFIED)
                if ready
                else draw(verification_statuses())
            )
        ),
        "capabilityFingerprint": contracts.UNDETERMINED,
    }
    entry["capabilityFingerprint"] = canonicalizer.capability_fingerprint(entry)
    return entry


def verified_entries(**kwargs: Any) -> st.SearchStrategy[dict]:
    """Activation_Gate 통과 후보가 될 수 있는 entry(편의 래퍼)."""
    kwargs.setdefault("active_ready", True)
    return entries(**kwargs)


# ─────────────────────────────────────────────────────────────────
# 동일 modelId 형제 entry — duplicate 축약·tie·fingerprint 불일치 경계값
# ─────────────────────────────────────────────────────────────────

#: 형제 entry 종류.
#:   ``duplicate``            canonical serialization이 동일한 완전 중복
#:   ``tie``                  같은 modelId·같은 `verifiedAt`(동시각 tie), fingerprint 동일
#:   ``divergentFingerprint`` 같은 modelId·같은 `verifiedAt`, fingerprint 불일치
#:   ``newer``                같은 modelId, 더 늦은 `verifiedAt`
SIBLING_KINDS: tuple[str, ...] = ("duplicate", "tie", "divergentFingerprint", "newer")


@st.composite
def sibling_entries(draw: Any, base: dict, *, kind: str | None = None) -> tuple[str, dict]:
    """``base``와 같은 modelId를 갖는 형제 entry를 만든다. 반환값은 ``(kind, entry)``."""
    resolved = kind if kind is not None else draw(st.sampled_from(SIBLING_KINDS))
    sibling = copy.deepcopy(base)

    if resolved == "duplicate":
        return resolved, sibling

    # fingerprint 제외 입력(label·displayName·evidence)만 흔들어 별도 record처럼 보이게 한다.
    sibling["candidateLabel"] = draw(candidate_labels())
    sibling["displayName"] = draw(display_names())

    if resolved == "tie":
        return resolved, refresh_fingerprint(sibling)

    if resolved == "newer":
        base_stamp = base.get("verifiedAt") or ""
        newer = draw(utc_timestamps().filter(lambda stamp: stamp > base_stamp))
        sibling["verifiedAt"] = newer
        return resolved, refresh_fingerprint(sibling)

    # divergentFingerprint — 계약 상태를 바꿔 fingerprint 입력을 실제로 다르게 만든다.
    route = draw(st.sampled_from(sorted(sibling.get("routes") or KNOWN_ROUTES)))
    route_entry = sibling.setdefault("routes", {}).get(route)
    if not isinstance(route_entry, dict):
        route_entry = contracts.new_route_entry()
        sibling["routes"][route] = route_entry
    current = route_entry.get("status")
    if current == contracts.Route_Support_Status.SUPPORTED:
        # SUPPORTED를 유지해야 계약·evidence 요구를 계속 만족한다 → allowlist를 바꾼다.
        route_entry["allowlist"] = draw(
            allowlist_results(exclude=(route_entry.get("allowlist"),))
        )
    else:
        route_entry["status"] = draw(
            route_support_statuses(
                exclude=(current, contracts.Route_Support_Status.SUPPORTED)
            )
        )
    return resolved, refresh_fingerprint(sibling)


# ─────────────────────────────────────────────────────────────────
# Capability_Map
# ─────────────────────────────────────────────────────────────────
def new_capability_map(entry_list: Sequence[dict], *, updated_at: str = contracts.UNDETERMINED) -> dict:
    """`{schemaVersion, updatedAt, entries}` 형태의 Capability_Map을 만든다."""
    return {
        "schemaVersion": contracts.SCHEMA_VERSION,
        "updatedAt": updated_at,
        "entries": [copy.deepcopy(entry) for entry in entry_list],
    }


@st.composite
def capability_maps(
    draw: Any,
    *,
    min_entries: int = 0,
    max_entries: int = 4,
    siblings: bool = True,
    entry_strategy: st.SearchStrategy[dict] | None = None,
) -> dict:
    """Capability_Map 생성기.

    경계값을 명시적으로 포함한다: 빈 map(``min_entries=0``), 단일 entry, 동일 modelId
    duplicate·동시각 tie·fingerprint 불일치 형제(:data:`SIBLING_KINDS`).
    모든 entry는 유효(Malformed 아님)하며 상태·출처는 전수로 섞인다.
    """
    source = entry_strategy if entry_strategy is not None else entries()
    count = draw(st.integers(min_value=min_entries, max_value=max_entries))
    items: list[dict] = [draw(source) for _ in range(count)]

    if siblings and items and draw(st.booleans()):
        base = draw(st.sampled_from(items))
        _, sibling = draw(sibling_entries(base))
        items.append(sibling)

    return new_capability_map(items, updated_at=draw(utc_timestamps(allow_undetermined=True)))


@st.composite
def tie_capability_maps(draw: Any, *, kind: str = "tie") -> dict:
    """동시각 tie 경계값 전용 map(같은 modelId·같은 `verifiedAt` entry 2개 이상)."""
    base = draw(entries(active_ready=True))
    _, sibling = draw(sibling_entries(base, kind=kind))
    extras = draw(st.lists(entries(), max_size=2))
    items = [base, sibling, *extras]
    return new_capability_map(items, updated_at=draw(utc_timestamps()))


@st.composite
def activation_contexts(draw: Any, map_obj: dict | None = None) -> dict:
    """Activation_Gate ctx(`{revision, catalogFingerprint, catalogModelIds, nowUtc}`).

    ``map_obj``를 주면 그 map의 revision·fingerprint·modelId에서 값을 뽑아 "일치하는 ctx"와
    "불일치하는 ctx"를 모두 만든다.
    """
    entry_list = list((map_obj or {}).get("entries") or [])
    revision_pool = [
        entry.get("revision")
        for entry in entry_list
        if isinstance(entry.get("revision"), str) and entry.get("revision")
    ]
    catalog_pool = [
        entry.get("catalogFingerprint")
        for entry in entry_list
        if isinstance(entry.get("catalogFingerprint"), str) and entry.get("catalogFingerprint")
    ]
    model_pool = sorted(
        {
            entry.get("modelId")
            for entry in entry_list
            if isinstance(entry.get("modelId"), str) and entry.get("modelId")
        }
    )

    revision = draw(
        st.sampled_from(revision_pool) if revision_pool else revisions(allow_empty=False)
    )
    catalog_fingerprint = draw(
        st.sampled_from(catalog_pool) if catalog_pool else catalog_fingerprints(allow_empty=False)
    )
    if model_pool:
        catalog_model_ids = draw(
            st.lists(st.sampled_from(model_pool), max_size=len(model_pool), unique=True)
        )
    else:
        catalog_model_ids = draw(st.lists(model_ids(allow_empty=False), max_size=2, unique=True))
    catalog_model_ids.extend(draw(st.lists(model_ids(allow_empty=False), max_size=1)))

    return {
        "revision": revision,
        "catalogFingerprint": catalog_fingerprint,
        "catalogModelIds": catalog_model_ids,
        "nowUtc": draw(utc_timestamps()),
    }


# ─────────────────────────────────────────────────────────────────
# Malformed mutation — 5개 판정 범주 전수
# ─────────────────────────────────────────────────────────────────

#: mutation 종류는 `contracts.MALFORMED_CODES`와 1:1이다.
MUTATION_KINDS: tuple[str, ...] = contracts.MALFORMED_CODES

#: TYPE_MISMATCH mutation 대상 필드.
_TYPE_MISMATCH_FIELDS: tuple[str, ...] = (
    "schemaVersion",
    "candidateLabel",
    "modelId",
    "provider",
    "catalogFingerprint",
    "revision",
    "capabilityFingerprint",
    "invocationModelIds",
    "evidence",
    "routes",
    "effort",
    "verifiedAt",
    "displayName",
)

#: entry 최상위 enum 필드.
_ENUM_ENTRY_FIELDS: dict[str, tuple[str, ...]] = {
    "sourceKind": SOURCE_KINDS,
    "verificationStatus": VERIFICATION_STATUSES,
    "syncSupport": ROUTE_SUPPORT_STATUSES,
    "asyncSupport": ROUTE_SUPPORT_STATUSES,
    "streamingSupport": ROUTE_SUPPORT_STATUSES,
}

#: ENUM_VIOLATION mutation 대상.
_ENUM_TARGETS: tuple[str, ...] = (
    "entryField",
    "routeStatus",
    "routeAllowlist",
    "effortStatus",
    "unknownRouteKey",
    "unknownEffortKey",
)

#: EVIDENCE_INTEGRITY mutation 대상.
_EVIDENCE_TARGETS: tuple[str, ...] = ("routeRef", "effortRef", "emptyEvidenceId")


def _non_iso_strings() -> st.SearchStrategy[str]:
    return symbols().filter(lambda text: not contracts.is_utc_iso8601(text))


@st.composite
def malformed_mutations(draw: Any, entry: dict, *, kind: str | None = None) -> dict:
    """``entry``에 주입할 Malformed mutation 명세를 만든다.

    반환 spec은 ``{"kind": <MALFORMED_CODE>, ...}`` 형태의 순수 데이터이며
    :func:`apply_mutation`으로 적용한다(spec을 그대로 보고서에 기록할 수 있다).
    """
    resolved = kind if kind is not None else draw(st.sampled_from(MUTATION_KINDS))
    route_keys = sorted(entry.get("routes") or {})
    effort_keys = sorted(entry.get("effort") or {})

    if resolved == contracts.MISSING_FIELD:
        return {
            "kind": resolved,
            "field": draw(st.sampled_from(sorted(contracts.REQUIRED_ENTRY_FIELDS))),
        }

    if resolved == contracts.TYPE_MISMATCH:
        field = draw(st.sampled_from(sorted(_TYPE_MISMATCH_FIELDS)))
        if field == "verifiedAt":
            value: Any = draw(_non_iso_strings())
        elif field in ("invocationModelIds", "evidence"):
            value = draw(st.one_of(symbols(), st.dictionaries(symbols(), symbols(), max_size=1)))
        elif field in ("routes", "effort"):
            value = draw(st.one_of(symbols(), st.lists(symbols(), max_size=2)))
        elif field == "schemaVersion":
            value = draw(symbols())
        else:  # 문자열 식별자 필드 → 비문자열
            value = draw(st.one_of(st.integers(), st.none(), st.lists(symbols(), max_size=1)))
        return {"kind": resolved, "field": field, "value": value}

    if resolved == contracts.ENUM_VIOLATION:
        available = [
            target
            for target in _ENUM_TARGETS
            if (target not in ("routeStatus", "routeAllowlist") or route_keys)
            and (target != "effortStatus" or effort_keys)
        ]
        target = draw(st.sampled_from(available))
        if target == "entryField":
            field = draw(st.sampled_from(sorted(_ENUM_ENTRY_FIELDS)))
            return {
                "kind": resolved,
                "target": target,
                "field": field,
                "value": draw(non_member_strings(_ENUM_ENTRY_FIELDS[field])),
            }
        if target in ("routeStatus", "routeAllowlist"):
            return {
                "kind": resolved,
                "target": target,
                "route": draw(st.sampled_from(route_keys)),
                "value": draw(
                    non_member_strings(
                        ROUTE_SUPPORT_STATUSES if target == "routeStatus" else ALLOWLIST_RESULTS
                    )
                ),
            }
        if target == "effortStatus":
            return {
                "kind": resolved,
                "target": target,
                "route": draw(st.sampled_from(effort_keys)),
                "value": draw(non_member_strings(EFFORT_SUPPORT_STATUSES)),
            }
        # unknownRouteKey / unknownEffortKey — Known_Route 닫힌 집합 이탈 키 추가
        return {
            "kind": resolved,
            "target": target,
            "key": draw(non_member_strings(KNOWN_ROUTES).filter(bool)),
        }

    if resolved == contracts.FINGERPRINT_MISMATCH:
        stored = entry.get("capabilityFingerprint")
        return {
            "kind": resolved,
            "value": draw(
                capability_fingerprint_strings().filter(lambda text: text != stored)
            ),
        }

    # EVIDENCE_INTEGRITY
    known = set(entry.get("evidence") or [])
    available = [
        target
        for target in _EVIDENCE_TARGETS
        if (target != "routeRef" or route_keys) and (target != "effortRef" or effort_keys)
    ]
    target = draw(st.sampled_from(available))
    if target == "emptyEvidenceId":
        return {"kind": resolved, "target": target}
    return {
        "kind": resolved,
        "target": target,
        "route": draw(st.sampled_from(route_keys if target == "routeRef" else effort_keys)),
        "value": draw(evidence_ids().filter(lambda ref: ref not in known)),
    }


def apply_mutation(entry: dict, mutation: dict) -> dict:
    """:func:`malformed_mutations` spec을 적용한 **새** entry를 돌려준다(입력 불변).

    적용 결과는 항상 Malformed_Entry이며, ``mutation["kind"]`` 코드가 판정 이유에 나타난다.
    """
    mutated = copy.deepcopy(entry)
    kind = mutation.get("kind")

    if kind == contracts.MISSING_FIELD:
        mutated.pop(mutation["field"], None)
        return mutated

    if kind == contracts.TYPE_MISMATCH:
        mutated[mutation["field"]] = copy.deepcopy(mutation["value"])
        return mutated

    if kind == contracts.ENUM_VIOLATION:
        target = mutation.get("target")
        if target == "entryField":
            mutated[mutation["field"]] = mutation["value"]
        elif target == "routeStatus":
            mutated.setdefault("routes", {}).setdefault(mutation["route"], contracts.new_route_entry())[
                "status"
            ] = mutation["value"]
        elif target == "routeAllowlist":
            mutated.setdefault("routes", {}).setdefault(mutation["route"], contracts.new_route_entry())[
                "allowlist"
            ] = mutation["value"]
        elif target == "effortStatus":
            mutated.setdefault("effort", {}).setdefault(
                mutation["route"], contracts.new_effort_entry()
            )["status"] = mutation["value"]
        elif target == "unknownRouteKey":
            mutated.setdefault("routes", {})[mutation["key"]] = contracts.new_route_entry()
        elif target == "unknownEffortKey":
            mutated.setdefault("effort", {})[mutation["key"]] = contracts.new_effort_entry()
        return mutated

    if kind == contracts.FINGERPRINT_MISMATCH:
        mutated["capabilityFingerprint"] = mutation["value"]
        return mutated

    if kind == contracts.EVIDENCE_INTEGRITY:
        target = mutation.get("target")
        if target == "emptyEvidenceId":
            evidence = mutated.get("evidence")
            if isinstance(evidence, list):
                evidence.append(contracts.UNDETERMINED)
            else:
                mutated["evidence"] = [contracts.UNDETERMINED]
        elif target == "routeRef":
            mutated.setdefault("routes", {}).setdefault(
                mutation["route"], contracts.new_route_entry()
            )["evidenceRef"] = mutation["value"]
        elif target == "effortRef":
            mutated.setdefault("effort", {}).setdefault(
                mutation["route"], contracts.new_effort_entry()
            )["evidenceRef"] = mutation["value"]
        return mutated

    raise ValueError(f"알 수 없는 mutation kind: {kind!r}")


@st.composite
def malformed_entries(
    draw: Any,
    *,
    kind: str | None = None,
    base: st.SearchStrategy[dict] | None = None,
    max_mutations: int = 2,
) -> dict:
    """Malformed entry 케이스.

    반환: ``{"original": <유효 entry>, "mutations": [spec, ...], "mutated": <Malformed entry>,
    "expectedCodes": [<MALFORMED_CODE>, ...]}``
    """
    original = draw(base if base is not None else entries())
    count = 1 if kind is not None else draw(st.integers(min_value=1, max_value=max_mutations))

    mutated = original
    specs: list[dict] = []
    for _ in range(count):
        spec = draw(malformed_mutations(mutated, kind=kind))
        mutated = apply_mutation(mutated, spec)
        specs.append(spec)

    return {
        "original": original,
        "mutations": specs,
        "mutated": mutated,
        "expectedCodes": [spec["kind"] for spec in specs],
    }


# ─────────────────────────────────────────────────────────────────
# 순서 치환 (Property 7) — 집합 의미만 섞고 순서 의미는 보존
# ─────────────────────────────────────────────────────────────────

#: canonicalizer의 `SET_LIKE_PATHS` 밖에 있지만 순서에 의미가 없는 자리.
#: Capability_Map의 `entries`는 저장 순서일 뿐이므로 치환 대상이다.
REORDER_SET_LIKE_EXTRA: tuple[str, ...] = ("entries",)


def _reorder(value: Any, rng: random.Random, path: tuple[str, ...], extra: frozenset[str]) -> Any:
    if isinstance(value, dict):
        items = [
            (key, _reorder(item, rng, path + (str(key),), extra)) for key, item in value.items()
        ]
        rng.shuffle(items)
        return dict(items)
    if isinstance(value, (list, tuple)):
        items = [
            _reorder(item, rng, path + (canonicalizer.WILDCARD,), extra) for item in value
        ]
        if canonicalizer.is_order_bearing_path(path):
            return items  # field path 등 순서 의미 → 절대 재배열하지 않는다
        if canonicalizer.is_set_like_path(path) or (path and path[-1] in extra):
            rng.shuffle(items)
        return items
    return value


def reorder(
    value: Any, seed: int, *, extra_set_like: Iterable[str] = REORDER_SET_LIKE_EXTRA
) -> Any:
    """의미를 보존한 채 **순서만** 치환한 복사본을 만든다.

    - dict: key 삽입 순서 치환(canonical은 key를 정렬하므로 의미 불변)
    - 집합 의미 목록(`SET_LIKE_PATHS` + ``extra_set_like``): 원소 순서 치환
    - 순서 의미 목록(`ORDER_BEARING_PATHS`, field path): 원 순서 보존
    """
    return _reorder(value, random.Random(seed), (), frozenset(extra_set_like))


def reorder_seeds() -> st.SearchStrategy[int]:
    """순서 치환용 seed(재현 가능한 정수)."""
    return st.integers(min_value=0, max_value=2**31 - 1)


def reorderings(
    value: Any, *, extra_set_like: Iterable[str] = REORDER_SET_LIKE_EXTRA
) -> st.SearchStrategy[Any]:
    """``value``의 순서 치환본 전략."""
    return reorder_seeds().map(lambda seed: reorder(value, seed, extra_set_like=extra_set_like))


@st.composite
def reordered_pairs(
    draw: Any,
    source: st.SearchStrategy[Any],
    *,
    extra_set_like: Iterable[str] = REORDER_SET_LIKE_EXTRA,
) -> tuple[Any, Any]:
    """``(원본, 순서 치환본)`` 쌍. Property 7의 기본 입력 형태다."""
    value = draw(source)
    seed = draw(reorder_seeds())
    return value, reorder(value, seed, extra_set_like=extra_set_like)


# ─────────────────────────────────────────────────────────────────
# fingerprint 제외 입력 변경 (Property 7 후반)
# ─────────────────────────────────────────────────────────────────
@st.composite
def excluded_perturbations(draw: Any) -> dict:
    """fingerprint **제외** 입력만 바꾸는 perturbation spec.

    대상: `candidateLabel`, `displayName`, `verifiedAt`, `revision`, evidence ID 집합
    (evidence ID를 바꿀 때는 entry의 모든 evidence reference를 함께 갱신해 참조 무결성을
    유지한다). 계약 하위 `evidenceRef`도 같은 매핑으로 갱신된다.
    """
    return {
        "candidateLabel": draw(candidate_labels()),
        "displayName": draw(display_names()),
        "verifiedAt": draw(utc_timestamps(allow_undetermined=True)),
        "revision": draw(revisions()),
        "evidenceIds": draw(st.one_of(st.none(), evidence_id_lists(min_size=1, max_size=3))),
    }


def apply_excluded_perturbation(entry: dict, spec: dict) -> dict:
    """:func:`excluded_perturbations` spec을 적용한 새 entry(fingerprint 재계산 없음).

    fingerprint 입력에 포함되지 않는 필드만 바꾸므로, 적용 후에도 저장된
    `capabilityFingerprint`는 재계산값과 일치해야 한다(Property 7의 단정 대상).
    """
    updated = copy.deepcopy(entry)
    updated["candidateLabel"] = spec["candidateLabel"]
    updated["displayName"] = spec["displayName"]
    updated["verifiedAt"] = spec["verifiedAt"]
    updated["revision"] = spec["revision"]

    replacements = spec.get("evidenceIds")
    old_ids = updated.get("evidence")
    if replacements and isinstance(old_ids, list) and old_ids:
        mapping = {
            old: replacements[index % len(replacements)] for index, old in enumerate(old_ids)
        }
        updated["evidence"] = [mapping[old] for old in old_ids]
        for container_key in ("routes", "effort"):
            container = updated.get(container_key)
            if not isinstance(container, dict):
                continue
            for sub_entry in container.values():
                if not isinstance(sub_entry, dict):
                    continue
                ref = sub_entry.get("evidenceRef")
                if isinstance(ref, str) and ref in mapping:
                    sub_entry["evidenceRef"] = mapping[ref]
                contract = sub_entry.get("contract")
                if isinstance(contract, dict):
                    contract_ref = contract.get("evidenceRef")
                    if isinstance(contract_ref, str) and contract_ref in mapping:
                        contract["evidenceRef"] = mapping[contract_ref]
    return updated


# ─────────────────────────────────────────────────────────────────
# 중첩 request body (Property 3·4·9·10)
# ─────────────────────────────────────────────────────────────────

#: 생성하는 body의 최대 중첩 깊이(경계값 "최대 중첩 body").
MAX_BODY_DEPTH = 4


def json_scalars() -> st.SearchStrategy[Any]:
    """body leaf 값(JSON 기본 타입, NaN·Infinity 제외)."""
    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1024, max_value=1024),
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        symbols(),
    )


def body_keys(reserved_keys: Iterable[str] = ()) -> st.SearchStrategy[str]:
    """body의 dict key. ``reserved_keys``(effort field path·`modelId` 등)는 만들지 않는다."""
    reserved = frozenset(reserved_keys)
    keys = symbols()
    if not reserved:
        return keys
    return keys.filter(lambda key: key not in reserved)


def _nested_values(reserved: Iterable[str], max_leaves: int) -> st.SearchStrategy[Any]:
    return st.recursive(
        json_scalars(),
        lambda children: st.one_of(
            st.lists(children, max_size=3),
            st.dictionaries(body_keys(reserved), children, max_size=3),
        ),
        max_leaves=max_leaves,
    )


def bodies(
    *, reserved_keys: Iterable[str] = (), max_size: int = 4, max_leaves: int = 6
) -> st.SearchStrategy[dict]:
    """임의 중첩 request body(최상위는 dict). 빈 body도 포함한다."""
    reserved = frozenset(reserved_keys)
    return st.dictionaries(
        body_keys(reserved), _nested_values(reserved, max_leaves), max_size=max_size
    )


@st.composite
def max_depth_bodies(
    draw: Any, *, reserved_keys: Iterable[str] = (), depth: int = MAX_BODY_DEPTH
) -> dict:
    """정확히 ``depth``만큼 중첩한 body(경계값 "최대 중첩 body")."""
    reserved = frozenset(reserved_keys)
    node: Any = draw(json_scalars())
    for _ in range(max(1, depth)):
        if draw(st.booleans()):
            node = {draw(body_keys(reserved)): node}
        else:
            node = [node]
    if not isinstance(node, dict):
        node = {draw(body_keys(reserved)): node}
    return node


def baseline_bodies(*, reserved_keys: Iterable[str] = ()) -> st.SearchStrategy[dict]:
    """Baseline_Request_Body 자리. 얕은 body와 최대 중첩 body를 함께 만든다."""
    return st.one_of(
        bodies(reserved_keys=reserved_keys),
        max_depth_bodies(reserved_keys=reserved_keys),
    )


def iter_nodes(body: Any, path: tuple[Any, ...] = ()) -> Iterator[tuple[tuple[Any, ...], Any]]:
    """body의 모든 중첩 노드를 ``(path, value)``로 순회한다(list index 포함)."""
    yield path, body
    if isinstance(body, dict):
        for key, value in body.items():
            yield from iter_nodes(value, path + (key,))
    elif isinstance(body, (list, tuple)):
        for index, value in enumerate(body):
            yield from iter_nodes(value, path + (index,))


def count_key_occurrences(body: Any, key: str) -> int:
    """body 전체(모든 중첩 경로)에서 dict key ``key``의 발생 횟수."""
    total = 0
    for _, node in iter_nodes(body):
        if isinstance(node, dict) and key in node:
            total += 1
    return total


def count_field_path_occurrences(body: Any, field_path: Sequence[str]) -> int:
    """dict key 경로가 ``field_path``와 정확히 일치하는 노드 수(list index는 건너뛴다)."""
    target = tuple(field_path)
    if not target:
        return 0
    total = 0
    for path, _ in iter_nodes(body):
        keys = tuple(component for component in path if isinstance(component, str))
        if keys == target and len(keys) == len(path):
            total += 1
    return total


_MISSING = object()


def read_field_path(body: Any, field_path: Sequence[str]) -> tuple[bool, Any]:
    """``field_path``를 dict만 따라 읽는다. 반환값은 ``(존재 여부, 값)``."""
    node: Any = body
    for component in field_path:
        if not isinstance(node, dict) or component not in node:
            return False, _MISSING
        node = node[component]
    return True, node


# ─────────────────────────────────────────────────────────────────
# effort 주입 케이스 (Property 3·4)
# ─────────────────────────────────────────────────────────────────

#: effort 주입을 막아야 하는 불일치 종류(design.md `_inject_effort` 조건과 1:1).
MISMATCH_KINDS: tuple[str, ...] = (
    "noSelection",
    "modelIdMismatch",
    "routeMismatch",
    "fingerprintMismatch",
    "statusNotSupported",
    "valueOutOfDomain",
)


@st.composite
def injection_bundles(
    draw: Any,
    *,
    effort_status: str | None = None,
    route_key: str | None = None,
    model_id: str | None = None,
    capability_fingerprint: str | None = None,
) -> dict:
    """`_inject_effort`에 넘기는 계약 묶음.

    반환 형태::

        {"modelId", "routeKey", "capabilityFingerprint",
         "route": <Route_Contract>, "effort": <Effort_Entry>}

    ``effort["contract"]``가 Effort_Contract(field path·value type·domain)이며,
    ``effort["status"]``가 `Effort_Support_Status`다.
    """
    mid = model_id if model_id is not None else draw(model_ids(allow_empty=False))
    route = route_key if route_key is not None else draw(known_routes())
    fingerprint = (
        capability_fingerprint
        if capability_fingerprint is not None
        else draw(capability_fingerprint_strings())
    )
    status = (
        effort_status
        if effort_status is not None
        else str(contracts.Effort_Support_Status.SUPPORTED)
    )
    ref = draw(evidence_ids())

    effort_entry = contracts.new_effort_entry(status)
    effort_entry["contract"] = draw(
        effort_contracts(model_id=mid, route_key=route, complete=True, evidence_ref=ref)
    )
    effort_entry["evidenceRef"] = ref

    return {
        "modelId": mid,
        "routeKey": route,
        "capabilityFingerprint": fingerprint,
        "route": draw(route_contracts(route_key=route, complete=True, evidence_ref=ref)),
        "effort": effort_entry,
    }


def effort_field_path(bundle: dict) -> list[str]:
    """묶음의 effort field path(순서 의미)."""
    contract = (bundle.get("effort") or {}).get("contract") or {}
    return list(contract.get("fieldPath") or [])


def effort_status(bundle: dict) -> Any:
    """묶음의 `Effort_Support_Status`."""
    return (bundle.get("effort") or {}).get("status")


def matching_selection(bundle: dict, value: Any) -> dict:
    """묶음과 tuple 3요소가 모두 일치하는 effort selection."""
    return {
        "modelId": bundle["modelId"],
        "route": bundle["routeKey"],
        "capabilityFingerprint": bundle["capabilityFingerprint"],
        "value": value,
    }


@st.composite
def supported_effort_cases(draw: Any, *, reserve_model_id_key: bool = False) -> dict:
    """Property 4 입력: `SUPPORTED` 계약 + verified domain 값 + baseline body.

    반환: ``{"bundle", "selection", "value", "fieldPath", "baselineBody"}``.
    baseline body는 effort field path 성분을 key로 갖지 않으므로, 주입 후 해당 경로의
    발생 횟수는 정확히 1이어야 한다.
    """
    bundle = draw(injection_bundles(effort_status=str(contracts.Effort_Support_Status.SUPPORTED)))
    contract = bundle["effort"]["contract"]
    value = draw(domain_values(contract))
    path = effort_field_path(bundle)

    reserved = set(path)
    if reserve_model_id_key:
        reserved.add("modelId")
    body = draw(baseline_bodies(reserved_keys=reserved))

    return {
        "bundle": bundle,
        "selection": matching_selection(bundle, value),
        "value": value,
        "fieldPath": path,
        "baselineBody": body,
    }


@st.composite
def unsupported_effort_cases(
    draw: Any, *, mismatch_kind: str | None = None, reserve_model_id_key: bool = False
) -> dict:
    """Property 3 입력: effort가 주입돼서는 **안 되는** 조합.

    반환: ``{"bundle", "selection", "mismatchKind", "fieldPath", "baselineBody"}``.
    ``selection``은 ``mismatchKind == "noSelection"``이면 ``None``이다.
    """
    kind = mismatch_kind if mismatch_kind is not None else draw(st.sampled_from(MISMATCH_KINDS))

    status = (
        draw(effort_support_statuses(exclude=(contracts.Effort_Support_Status.SUPPORTED,)))
        if kind == "statusNotSupported"
        else str(contracts.Effort_Support_Status.SUPPORTED)
    )
    bundle = draw(injection_bundles(effort_status=status))
    contract = bundle["effort"]["contract"]
    path = effort_field_path(bundle)

    if kind == "noSelection":
        selection: dict | None = None
    elif kind == "valueOutOfDomain":
        selection = matching_selection(bundle, draw(out_of_domain_values(contract)))
    else:
        selection = matching_selection(bundle, draw(domain_values(contract)))
        if kind == "modelIdMismatch":
            selection["modelId"] = draw(
                model_ids(allow_empty=False).filter(lambda mid: mid != bundle["modelId"])
            )
        elif kind == "routeMismatch":
            selection["route"] = draw(known_routes(exclude=(bundle["routeKey"],)))
        elif kind == "fingerprintMismatch":
            selection["capabilityFingerprint"] = draw(
                capability_fingerprint_strings().filter(
                    lambda text: text != bundle["capabilityFingerprint"]
                )
            )

    reserved = set(path)
    if reserve_model_id_key:
        reserved.add("modelId")
    body = draw(baseline_bodies(reserved_keys=reserved))

    return {
        "bundle": bundle,
        "selection": selection,
        "mismatchKind": kind,
        "fieldPath": path,
        "baselineBody": body,
    }


@st.composite
def unsupported_effort_case_matrix(
    draw: Any, *, reserve_model_id_key: bool = False
) -> list[dict]:
    """:data:`MISMATCH_KINDS` **전 종류**를 한 example에 하나씩 담은 목록.

    ``max_examples=100``에서도 6개 불일치 종류 전수 도달을 보장한다(sampled_from의 분포에
    의존하지 않는다). Property 3은 이 목록의 모든 케이스에 대해 baseline 동일성을 단정한다.
    """
    return [
        draw(
            unsupported_effort_cases(
                mismatch_kind=kind, reserve_model_id_key=reserve_model_id_key
            )
        )
        for kind in MISMATCH_KINDS
    ]


# ─────────────────────────────────────────────────────────────────
# Effort_Settings (Property 8)
# ─────────────────────────────────────────────────────────────────
def new_effort_settings(entry_list: Sequence[dict]) -> dict:
    """design.md Effort_Settings 스키마(`{schemaVersion, entries}`)."""
    return {
        "schemaVersion": contracts.SCHEMA_VERSION,
        "entries": [copy.deepcopy(item) for item in entry_list],
    }


def effort_settings_entry(
    *, model_id: str, route: str, capability_fingerprint: str, value: Any, value_type: Any, updated_at: str
) -> dict:
    """Effort_Settings 항목 하나(tuple 키 = ``(modelId, route, capabilityFingerprint)``)."""
    return {
        "modelId": model_id,
        "route": route,
        "capabilityFingerprint": capability_fingerprint,
        "value": value,
        "valueType": value_type,
        "updatedAt": updated_at,
    }


@st.composite
def effort_settings_entries(
    draw: Any,
    *,
    model_id: str | None = None,
    route: str | None = None,
    capability_fingerprint: str | None = None,
    contract: dict | None = None,
    in_domain_value: bool = True,
) -> dict:
    """Effort_Settings 항목 생성기.

    ``contract``를 주면 그 계약의 domain에서 값을 뽑고(``in_domain_value=False``면 이탈값),
    주지 않으면 임의 Effort_Contract를 만들어 그 domain을 쓴다.
    """
    mid = model_id if model_id is not None else draw(model_ids(allow_empty=False))
    route_key = route if route is not None else draw(known_routes())
    fingerprint = (
        capability_fingerprint
        if capability_fingerprint is not None
        else draw(capability_fingerprint_strings())
    )
    resolved = (
        contract
        if contract is not None
        else draw(effort_contracts(model_id=mid, route_key=route_key, complete=True))
    )
    value = draw(
        domain_values(resolved) if in_domain_value else out_of_domain_values(resolved)
    )
    return effort_settings_entry(
        model_id=mid,
        route=route_key,
        capability_fingerprint=fingerprint,
        value=value,
        value_type=resolved.get("valueType"),
        updated_at=draw(utc_timestamps()),
    )


@st.composite
def effort_settings(draw: Any, *, min_entries: int = 0, max_entries: int = 3) -> dict:
    """임의 Effort_Settings(빈 settings 경계값 포함)."""
    count = draw(st.integers(min_value=min_entries, max_value=max_entries))
    items = [
        draw(effort_settings_entries(in_domain_value=draw(st.booleans())))
        for _ in range(count)
    ]
    return new_effort_settings(items)


def _supported_effort_slots(map_obj: dict) -> list[tuple[str, str, str, dict]]:
    """map에서 `SUPPORTED` effort를 가진 ``(modelId, route, fingerprint, contract)`` 목록."""
    slots: list[tuple[str, str, str, dict]] = []
    for entry in (map_obj or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        effort_container = entry.get("effort")
        if not isinstance(effort_container, dict):
            continue
        for route, sub_entry in effort_container.items():
            if not isinstance(sub_entry, dict):
                continue
            if sub_entry.get("status") != contracts.Effort_Support_Status.SUPPORTED:
                continue
            contract = sub_entry.get("contract")
            if not contracts.effort_contract_is_complete(contract):
                continue
            slots.append(
                (
                    entry.get("modelId") or contracts.UNDETERMINED,
                    route,
                    entry.get("capabilityFingerprint") or contracts.UNDETERMINED,
                    contract,
                )
            )
    return slots


@st.composite
def effort_settings_from_map(
    draw: Any, map_obj: dict, *, max_entries: int = 3
) -> dict:
    """``map_obj``에 대해 일치·불일치 항목을 섞은 Effort_Settings.

    일치 항목은 map의 `SUPPORTED` effort tuple과 domain 내 값을 그대로 쓰고, 불일치 항목은
    임의 tuple 또는 domain 이탈 값을 쓴다(prune 대상). `SUPPORTED` slot이 없으면 전부
    불일치 항목이 된다.
    """
    slots = _supported_effort_slots(map_obj)
    count = draw(st.integers(min_value=0, max_value=max_entries))
    items: list[dict] = []
    for _ in range(count):
        if slots and draw(st.booleans()):
            mid, route, fingerprint, contract = draw(st.sampled_from(slots))
            items.append(
                draw(
                    effort_settings_entries(
                        model_id=mid,
                        route=route,
                        capability_fingerprint=fingerprint,
                        contract=contract,
                        in_domain_value=draw(st.booleans()),
                    )
                )
            )
        else:
            items.append(draw(effort_settings_entries(in_domain_value=draw(st.booleans()))))
    return new_effort_settings(items)


__all__ = [
    "AE_PBT_SEED",
    "AE_PBT_SEED_DEFAULT",
    "ALLOWLIST_RESULTS",
    "DOMAIN_KINDS",
    "EFFORT_SUPPORT_STATUSES",
    "EXECUTION_MODES",
    "FAILURE_CATEGORIES",
    "KNOWN_ROUTES",
    "MAX_BODY_DEPTH",
    "MAX_EXAMPLES",
    "MIN_EXAMPLES",
    "MISMATCH_KINDS",
    "MUTATION_KINDS",
    "PBT_SETTINGS",
    "RANGE_VALUE_TYPES",
    "REORDER_SET_LIKE_EXTRA",
    "ROUTE_SUPPORT_STATUSES",
    "SIBLING_KINDS",
    "SOURCE_KINDS",
    "SIGNING_SERVICES",
    "SYMBOL_ALPHABET",
    "VALUE_TYPES",
    "VERIFICATION_STATUSES",
    "activation_contexts",
    "allowlist_results",
    "apply_excluded_perturbation",
    "apply_mutation",
    "baseline_bodies",
    "bodies",
    "body_keys",
    "candidate_labels",
    "capability_fingerprint_strings",
    "capability_maps",
    "catalog_fingerprints",
    "count_field_path_occurrences",
    "count_key_occurrences",
    "display_names",
    "domain_kinds",
    "domain_values",
    "effort_contracts",
    "effort_entries",
    "effort_field_path",
    "effort_settings",
    "effort_settings_entries",
    "effort_settings_entry",
    "effort_settings_from_map",
    "effort_status",
    "effort_support_statuses",
    "entries",
    "enum_domains",
    "evidence_id_lists",
    "evidence_ids",
    "excluded_perturbations",
    "execution_modes",
    "failure_categories",
    "field_paths",
    "in_domain",
    "injection_bundles",
    "is_valid_entry",
    "iter_nodes",
    "json_scalars",
    "known_routes",
    "malformed_entries",
    "malformed_mutations",
    "matching_selection",
    "max_depth_bodies",
    "min_output_bounds",
    "model_ids",
    "new_capability_map",
    "new_effort_settings",
    "non_member_strings",
    "out_of_domain_values",
    "pbt_settings",
    "providers",
    "purpose_lists",
    "range_domains",
    "read_field_path",
    "refresh_fingerprint",
    "reorder",
    "reorder_seeds",
    "reordered_pairs",
    "reorderings",
    "revisions",
    "route_contracts",
    "route_entries",
    "route_support_statuses",
    "sibling_entries",
    "signing_services",
    "source_kinds",
    "supported_effort_cases",
    "symbols",
    "tie_capability_maps",
    "unsupported_effort_case_matrix",
    "unsupported_effort_cases",
    "utc_timestamps",
    "validate_entry_reasons",
    "value_types",
    "verification_statuses",
    "verified_entries",
]
