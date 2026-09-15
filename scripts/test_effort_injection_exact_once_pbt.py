# Feature: gateway-models-effort-support, Property 4: supported effort exact-once —
# `Effort_Support_Status == SUPPORTED`인 계약과 그 verified domain(enum 멤버 또는 inclusive
# range 내 값)에서 뽑은 임의 value에 대해, 생성 body에는 Effort_Contract의 exact field path에
# 그 value가 정확히 1회 기록되고, 그 경로를 제외한 나머지 body는 Baseline_Request_Body와
# 동일하다.
"""Property 4 (supported effort exact-once) property-based test — task 7.5.

검증 대상 모듈: ``ai_engine/capability/request_builder.py``
  (``_inject_effort``, ``value_at_path``, ``count_key_occurrences``, ``without_key``)

**Validates: Requirements 8.16, 12.12**

단정 내용
  1. **주입 발생** — 계약 identity(`modelId`·`routeKey`·Capability_Fingerprint)와 selection이
     일치하고 status가 `SUPPORTED`이며 값이 verified domain 안이면 차단 이유가 없고
     (``injection_reason == REASON_OK``) ``_inject_effort``는 baseline과 **다른 새 객체**를
     반환한다(Requirement 8.16).
  2. **exact field path 1회** — 계약 field path에 값이 기록되고, 그 경로와 정확히 일치하는
     노드 수는 1이며, field path의 **모든 성분 key**는 body 전체에서 1회만 나타난다
     (누적·중복 기록 없음).
  3. **경로 외 baseline 동일** — 주입 경로(``fieldPath[0]`` 서브트리)를 제거하면 body는
     Baseline_Request_Body와 canonical 직렬화 **바이트까지 동일**하다. 최상위 key 집합은
     baseline ∪ {``fieldPath[0]``}이고, baseline의 각 key 서브트리는 변형되지 않는다.
  4. **입력 불변(순수성)** — ``_inject_effort``는 baseline body와 계약·selection을 변형하지
     않는다. 같은 입력을 다시 주입해도 발생 횟수는 여전히 1이다(재주입 누적 금지).
  5. 공개 별칭 ``inject_effort``의 결과는 ``_inject_effort``와 바이트 동일하다.

입력 생성기
  공통 생성기 ``scripts/_capability_strategies.py`` 만 사용한다
  (:func:`~_capability_strategies.supported_effort_cases`,
  :func:`~_capability_strategies.injection_bundles`,
  :func:`~_capability_strategies.domain_values`,
  :func:`~_capability_strategies.baseline_bodies`,
  :func:`~_capability_strategies.enum_domains`,
  :func:`~_capability_strategies.range_domains`).
  baseline body는 effort field path 성분을 key로 갖지 않도록 예약되어 생성되므로, 주입 후
  해당 경로의 발생 횟수는 정확히 1이어야 한다. 얕은 body와 최대 중첩 body를 모두 포함한다.

  경계값은 example마다 **반드시** 포함한다(분포에 의존하지 않는다):
  ``enumSingleton`` = enum 단일값 domain, ``rangeDegenerate`` = range 상·하한 동일 domain.
  :data:`CASE_KINDS` 전 종류를 한 example에 하나씩 담아 단정한다.

절대 원칙
  model ID·provider·effort field path·effort 허용값을 확정 상수로 두지 않는다. 모든 식별자와
  경로·값은 공통 생성기의 무작위 심볼이며, 실제 값은 Authoritative_Evidence(작업 18의
  production path probe)만이 채운다. 이 테스트의 성공은 **Gateway 지원 근거가 아니다**
  (Requirement 12.22). 외부 Gateway를 호출하지 않는다(네트워크 0, 파일 쓰기 0).

PBT 규약 (design.md "PBT 구성 규칙")
  ``@seed(AE_PBT_SEED)`` 고정 seed, ``max_examples`` 최소 100, ``deadline=None``,
  ``database=None``, ``print_blob=True``. 실패 시 최소화된 counterexample과 재현 blob을
  ``pbt.counterexamples`` JSON 레코드로 표준 출력에 남기고, ``AE_PBT_REPORT`` 환경변수가
  가리키는 파일이 있으면 같은 레코드를 JSON Lines로 덧붙인다(Validation_Runner 보고서 입력).

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_effort_injection_exact_once_pbt.py -q
  ai_engine/.venv/bin/python scripts/test_effort_injection_exact_once_pbt.py

_Requirements: 8.16, 12.12, 12.19, 12.21_
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

from ai_engine.capability import canonicalizer, contracts  # noqa: E402
from ai_engine.capability import request_builder as rb  # noqa: E402

FEATURE = "gateway-models-effort-support"
PROPERTY_ID = 4
PROPERTY_NAME = "supported effort exact-once"

#: 재현 blob(`@reproduce_failure(...)`) 추출 패턴.
_BLOB_PATTERN = re.compile(r"@reproduce_failure\([^)]*\)")

#: 한 example에 반드시 함께 담는 케이스 종류(domain 경계값 전수 도달 보장).
CASE_KINDS: tuple[str, ...] = ("random", "enumSingleton", "rangeDegenerate")


# ─────────────────────────────────────────────────────────────────
# 입력 생성기 — supported 케이스 + domain 경계값 강제
# ─────────────────────────────────────────────────────────────────
@st.composite
def _random_case(draw: Any) -> dict:
    """임의 domain(enum·range)의 `SUPPORTED` 케이스."""
    case = draw(S.supported_effort_cases())
    case["kind"] = "random"
    return case


@st.composite
def _boundary_case(draw: Any, *, kind: str) -> dict:
    """domain 경계값을 강제한 `SUPPORTED` 케이스.

    ``enumSingleton``은 멤버가 정확히 1개인 enum domain, ``rangeDegenerate``는 상·하한이
    같은 inclusive range domain을 만든다. domain 밖의 요소(identity·field path·baseline
    body)는 공통 생성기가 만든 값을 그대로 쓰고, 값은 바뀐 domain에서 다시 뽑는다.
    """
    case = copy.deepcopy(draw(S.supported_effort_cases()))
    contract = case["bundle"]["effort"]["contract"]

    if kind == "enumSingleton":
        value_type = draw(S.value_types())
        # BOOLEAN domain 생성기는 max_size를 2로 고정하므로 첫 멤버만 남겨 단일값을 보장한다.
        members = list(draw(S.enum_domains(value_type, min_size=1, max_size=1)))[:1]
        contract["valueType"] = value_type
        contract["domainKind"] = str(contracts.Domain_Kind.ENUM)
        contract["enumValues"] = members
        contract["rangeLowerInclusive"] = None
        contract["rangeUpperInclusive"] = None
        contract["verifiedValues"] = list(members)
    else:  # rangeDegenerate — 상한과 하한이 같은 경계 하나만 허용하는 domain
        value_type = draw(st.sampled_from(S.RANGE_VALUE_TYPES))
        bound, _upper = draw(S.range_domains(value_type))
        contract["valueType"] = value_type
        contract["domainKind"] = str(contracts.Domain_Kind.RANGE)
        contract["enumValues"] = None
        contract["rangeLowerInclusive"] = bound
        contract["rangeUpperInclusive"] = bound
        contract["verifiedValues"] = [bound]

    value = draw(S.domain_values(contract))
    case["kind"] = kind
    case["value"] = value
    case["selection"] = S.matching_selection(case["bundle"], value)
    return case


@st.composite
def exact_once_case_matrix(draw: Any) -> list[dict]:
    """:data:`CASE_KINDS` 전 종류를 한 example에 하나씩 담은 목록.

    ``max_examples=100``에서도 "enum 단일값"과 "range 상·하한 동일" 경계값 도달을 보장한다
    (``sampled_from`` 분포에 의존하지 않는다).
    """
    return [
        draw(_random_case()),
        draw(_boundary_case(kind="enumSingleton")),
        draw(_boundary_case(kind="rangeDegenerate")),
    ]


# ─────────────────────────────────────────────────────────────────
# 케이스 단정 — Property 4의 본문
# ─────────────────────────────────────────────────────────────────
def _bytes(value: Any) -> str:
    """canonical 직렬화 문자열(바이트 비교용)."""
    return canonicalizer.serialize(value)


def _check_case(case: dict) -> None:
    """한 케이스에 대해 exact-once 주입과 경로 외 baseline 동일성을 단정한다."""
    kind = case["kind"]
    bundle = case["bundle"]
    selection = case["selection"]
    value = case["value"]
    path = list(case["fieldPath"])
    baseline = case["baselineBody"]
    contract = bundle["effort"]["contract"]

    # ── 생성기 전제조건 — `SUPPORTED` + 완전한 계약 + verified domain 내 값
    assert S.effort_status(bundle) == contracts.Effort_Support_Status.SUPPORTED, kind
    assert contracts.effort_contract_is_complete(contract), f"{kind}: 계약이 불완전하다"
    assert (
        contracts.validate_effort_contract(
            contract, route_key=bundle["routeKey"], model_id=bundle["modelId"]
        )
        == []
    ), f"{kind}: Effort_Contract 스키마 위반"
    assert path and path == S.effort_field_path(bundle), f"{kind}: field path 불일치"
    assert S.in_domain(contract, value), f"{kind}: 값이 verified domain 밖이다"
    # baseline은 field path 성분을 key로 갖지 않는다(주입 전 발생 횟수 0).
    assert S.count_field_path_occurrences(baseline, path) == 0, kind
    for part in path:
        assert rb.count_key_occurrences(baseline, part) == 0, f"{kind}: baseline에 {part} 존재"

    before_baseline = copy.deepcopy(baseline)
    before_bundle = copy.deepcopy(bundle)
    before_selection = copy.deepcopy(selection)

    # ── (1) 주입 발생 — 차단 이유 없음
    assert (
        rb.injection_reason(bundle, selection, baseline_body=baseline) == rb.REASON_OK
    ), f"{kind}: supported 계약인데 주입이 차단됐다"
    plan = rb.effort_injection_plan(bundle, selection, baseline_body=baseline)
    assert plan["inject"] is True, f"{kind}: 주입 계획이 거짓이다"
    assert plan["reason"] == rb.REASON_OK
    assert plan["fieldPath"] == path, f"{kind}: 계획 field path가 계약과 다르다"
    assert canonicalizer.canonical_equal(plan["value"], value)

    body = rb._inject_effort(baseline, bundle, selection)
    assert body is not baseline, f"{kind}: 주입했는데 baseline 객체를 그대로 돌려줬다"
    assert isinstance(body, dict)

    # ── (2) exact field path에 정확히 1회
    written = rb.value_at_path(body, path, default=rb.MISSING)
    assert written is not rb.MISSING, f"{kind}: field path에 값이 기록되지 않았다"
    assert canonicalizer.canonical_equal(written, value), f"{kind}: 기록된 값이 선택값과 다르다"
    assert S.count_field_path_occurrences(body, path) == 1, f"{kind}: 경로 일치 노드가 1개가 아니다"
    for part in path:
        assert rb.count_key_occurrences(body, part) == 1, f"{kind}: {part} key가 1회가 아니다"

    # ── (3) 그 경로를 제외한 나머지 body는 baseline과 동일
    stripped = rb.without_key(body, path[0])
    assert _bytes(stripped) == _bytes(baseline), f"{kind}: 경로 외 body가 baseline과 다르다"
    assert stripped == baseline
    assert set(body) == set(baseline) | {path[0]}, f"{kind}: 최상위 key 집합이 변했다"
    assert len(body) == len(baseline) + 1
    for key in baseline:
        assert _bytes(body[key]) == _bytes(baseline[key]), f"{kind}: {key} 서브트리가 변형됐다"

    # ── (4) 입력 불변 + 재주입 누적 금지
    assert baseline == before_baseline, f"{kind}: baseline body가 변형됐다"
    assert bundle == before_bundle, f"{kind}: 계약 묶음이 변형됐다"
    assert selection == before_selection, f"{kind}: selection이 변형됐다"

    again = rb._inject_effort(body, bundle, selection)
    assert S.count_field_path_occurrences(again, path) == 1, f"{kind}: 재주입이 누적됐다"
    assert _bytes(again) == _bytes(body)

    # ── (5) 공개 별칭도 같은 결과
    assert _bytes(rb.inject_effort(baseline, bundle, selection)) == _bytes(body)


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
# Property 4
# ─────────────────────────────────────────────────────────────────
# Feature: gateway-models-effort-support, Property 4: supported effort exact-once
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(exact_once_case_matrix())
def _property_supported_effort_exact_once(cases: list[dict]) -> None:
    """supported effort 값의 exact field path 1회 기록과 경로 외 baseline 동일성.

    (Requirements 8.16, 12.12)
    """
    assert [case["kind"] for case in cases] == list(CASE_KINDS), "경계값 케이스 전수가 아니다"
    for case in cases:
        _check_case(case)


def test_supported_effort_exact_once() -> None:
    """Property 4 pytest 진입점 — 실패 시 최소화 counterexample과 재현 blob을 기록한다."""
    try:
        _property_supported_effort_exact_once()
    except BaseException as exc:
        _report_counterexample("test_supported_effort_exact_once", exc)
        raise


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지).
    test_supported_effort_exact_once()
    print(
        f"PASSED: Property {PROPERTY_ID} — {PROPERTY_NAME} "
        f"(seed={S.AE_PBT_SEED}, max_examples={S.MAX_EXAMPLES})"
    )
