# Feature: gateway-models-effort-support, Property 3: unsupported effort 비주입 —
# 임의의 Exact_Model_ID, Known_Route, 사용자 입력과 임의의 effort 설정에 대해, 다음 중
# 하나라도 성립하면(effort 미선택, effort UI 숨김, Effort_Support_Status ≠ `SUPPORTED`,
# 선택 value가 verified domain 이탈, 저장된 Capability_Fingerprint가 현재 값과 불일치)
# 생성 body는 Baseline_Request_Body와 구조적으로 동일하며, body의 모든 중첩 경로에서
# Effort_Contract field 발생 횟수는 0이다.
"""Property 3 (unsupported effort 비주입) property-based test — task 7.4.

검증 대상 모듈: ``ai_engine/capability/request_builder.py``
  :func:`~ai_engine.capability.request_builder._inject_effort`,
  :func:`~ai_engine.capability.request_builder.count_key_occurrences`,
  :func:`~ai_engine.capability.request_builder.effort_bound_client`
  (design.md "effort 주입 규칙", Components 5 Request_Builder)

**Validates: Requirements 7.15, 7.16, 7.17, 8.17, 8.18, 8.19, 12.3, 12.11**

단정 내용
  1. **baseline 보존** — 주입 차단 조건이 하나라도 성립하면 ``_inject_effort``는
     Baseline_Request_Body를 **동일 객체로** 반환한다. canonical 직렬화 바이트와 기존
     transport가 실제로 보내는 ``json.dumps`` 바이트가 모두 baseline과 같고, 입력 baseline은
     변형되지 않는다(Requirement 7.17, 8.17, 8.18, 8.19).
  2. **effort field 0회** — 생성 body의 **모든 중첩 경로**에서 Effort_Contract field path의
     모든 성분 key 발생 횟수가 0이고, field path 전체 경로도 존재하지 않는다
     (Requirement 7.15, 7.16).
  3. **차단 이유의 결정론** — ``injection_reason``·``effort_injection_plan``이 불일치 종류마다
     :data:`VARIANT_REASONS`의 확정된 첫 이유 코드를 반환하고 ``inject``는 항상 거짓이다.
  4. **builder seam 무회귀** — ``EffortBoundClient``의 ``_build_payload``(Converse·SSE)와
     ``_build_openai_payload``(동기 Responses)가 만든 body가 base ``GatewayClient`` body와
     **바이트 동일**하다(Requirement 12.3). 즉 effort는 seam에서도 생성되지 않는다.
  5. **불일치 6종 전수** — 한 example에서 :data:`_capability_strategies.MISMATCH_KINDS`
     6종 전부와 UI 숨김·field path 충돌 변형까지 8종이 모두 도달함을 단정한다.

불일치 변형(8종)
  ``noSelection``          effort 미선택(selection ``None``)
  ``uiHidden``             effort UI 숨김 → 값 없는 selection(``{}``)
  ``modelIdMismatch``      저장 tuple의 Exact_Model_ID 불일치
  ``routeMismatch``        저장 tuple의 Known_Route 불일치
  ``fingerprintMismatch``  저장 Capability_Fingerprint가 현재 값과 불일치
  ``statusNotSupported``   Effort_Support_Status ≠ `SUPPORTED`(UNVERIFIED·UNSUPPORTED·STALE)
  ``valueOutOfDomain``     선택 value가 verified domain(enum 멤버·inclusive range) 이탈
  ``pathConflict``         계약 field path 중간 노드가 dict가 아닌 baseline 구조
                           (`EFFORT_PATH_CONFLICT` — 주입하지 않고 baseline을 보존한다)

  ``pathConflict``는 baseline **구조**가 원인인 변형이므로 seam 검사(4번)에서는 제외한다.
  기존 builder가 만드는 실제 baseline에는 그 충돌이 존재하지 않기 때문이다.

입력 생성기
  공통 생성기 ``scripts/_capability_strategies.py``만 사용한다
  (:func:`~_capability_strategies.unsupported_effort_case_matrix`,
  :func:`~_capability_strategies.unsupported_effort_cases`,
  :func:`~_capability_strategies.supported_effort_cases`,
  :func:`~_capability_strategies.injection_bundles`,
  :func:`~_capability_strategies.baseline_bodies`). 경계값(빈 body, 최대 중첩 body, enum
  단일값, range 상·하한 동일, 빈 문자열 대신 무작위 심볼 ID)은 생성기가 포함한다.

절대 원칙
  model ID·provider·route 지원 여부·effort field path·effort 허용값을 확정 상수로 두지 않는다.
  모든 식별자와 경로는 생성기의 무작위 심볼이며, 이 테스트의 성공은 **Gateway 지원 근거가
  아니다**(Requirement 12.22). 외부 Gateway를 호출하지 않는다(네트워크 0, 파일 쓰기 0).

PBT 규약 (design.md "PBT 구성 규칙")
  ``@seed(AE_PBT_SEED)`` 고정 seed, ``max_examples`` 최소 100, ``deadline=None``,
  ``database=None``, ``print_blob=True``. 실패 시 Hypothesis가 최소화한 counterexample과
  재현 blob을 ``pbt.counterexamples`` JSON 레코드로 표준 출력에 남기고, ``AE_PBT_REPORT``
  환경변수가 가리키는 파일이 있으면 같은 레코드를 JSON Lines로 덧붙인다.

실행:
  ai_engine/.venv/bin/python -m pytest scripts/test_effort_injection_baseline_preservation_pbt.py -q
  ai_engine/.venv/bin/python scripts/test_effort_injection_baseline_preservation_pbt.py

_Requirements: 7.15, 7.16, 7.17, 12.11, 12.19, 12.21_
"""
from __future__ import annotations

import copy
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
PROPERTY_ID = 3
PROPERTY_NAME = "unsupported effort 비주입"

#: 재현 blob(`@reproduce_failure(...)`) 추출 패턴.
_BLOB_PATTERN = re.compile(r"@reproduce_failure\([^)]*\)")

# 공통 생성기가 만드는 불일치 종류(6종) + 이 파일이 파생하는 변형(2종).
UI_HIDDEN = "uiHidden"
PATH_CONFLICT = "pathConflict"

#: 변형별로 기대하는 **첫** 차단 이유 코드(`INJECTION_REASONS` 판정 순서와 일치).
VARIANT_REASONS: dict[str, str] = {
    "noSelection": rb.EFFORT_NO_SELECTION,
    UI_HIDDEN: rb.EFFORT_NO_SELECTION,
    "modelIdMismatch": rb.EFFORT_MODEL_ID_MISMATCH,
    "routeMismatch": rb.EFFORT_ROUTE_MISMATCH,
    "fingerprintMismatch": rb.EFFORT_FINGERPRINT_MISMATCH,
    "statusNotSupported": rb.EFFORT_NOT_SUPPORTED,
    "valueOutOfDomain": rb.EFFORT_DOMAIN_VIOLATION,
    PATH_CONFLICT: rb.EFFORT_PATH_CONFLICT,
}

#: builder seam 검사를 수행하는 변형(baseline 구조가 원인인 변형은 제외).
SEAM_VARIANTS: frozenset[str] = frozenset(VARIANT_REASONS) - {PATH_CONFLICT}

# 공통 생성기의 불일치 6종이 빠짐없이 이 매핑에 들어 있어야 한다(전수 도달의 전제).
assert set(S.MISMATCH_KINDS) <= set(VARIANT_REASONS), "MISMATCH_KINDS 전수가 매핑에 없다"

#: 네트워크·자격증명 접근 없이 body 생성만 하는 base 클라이언트(요청 전송 0건).
_BASE = GatewayClient(gateway_url="https://sym-gateway.invalid/v1", region="us-west-2")


# ─────────────────────────────────────────────────────────────────
# 헬퍼
# ─────────────────────────────────────────────────────────────────
def _raw(body: Any) -> bytes:
    """기존 transport가 실제로 전송하는 바이트열(dict key 순서까지 비교한다)."""
    return json.dumps(body).encode()


def _all_keys(value: Any) -> set[str]:
    """body의 모든 중첩 경로에 나타나는 dict key 집합."""
    keys: set[str] = set()
    for _, node in S.iter_nodes(value):
        if isinstance(node, dict):
            keys.update(key for key in node if isinstance(key, str))
    return keys


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


# ─────────────────────────────────────────────────────────────────
# 입력 생성기 — 6종 불일치 전수 + UI 숨김 + field path 충돌
# ─────────────────────────────────────────────────────────────────
@st.composite
def user_inputs(draw: Any) -> dict:
    """사용자 입력과 system 지시문(문자열 입력과 Bedrock 스타일 messages 모두)."""
    text = draw(S.symbols())
    messages = draw(
        st.one_of(
            st.just(text),
            st.lists(
                st.fixed_dictionaries(
                    {
                        "role": st.sampled_from(["user", "assistant"]),
                        "content": st.just([{"text": text}]),
                    }
                ),
                min_size=1,
                max_size=2,
            ),
        )
    )
    return {
        "messages": messages,
        "system": draw(st.one_of(st.just(""), S.symbols())),
    }


@st.composite
def baseline_preservation_cases(draw: Any) -> list[dict]:
    """Property 3 입력 — 한 example에 8종 변형을 각각 하나씩 담는다.

    ``unsupported_effort_case_matrix``가 :data:`_capability_strategies.MISMATCH_KINDS`
    6종 전수를 보장하고, 여기서 UI 숨김(값 없는 selection)과 field path 충돌 baseline
    변형을 더한다.
    """
    cases = [dict(case) for case in draw(S.unsupported_effort_case_matrix())]

    # UI 숨김 — effort UI가 렌더되지 않으면 값 없는 selection이 전달된다(Requirement 7.15).
    hidden = dict(draw(S.unsupported_effort_cases(mismatch_kind="noSelection")))
    hidden["mismatchKind"] = UI_HIDDEN
    hidden["selection"] = {}
    cases.append(hidden)

    # field path 충돌 — 계약·tuple·domain은 모두 일치하지만 baseline 구조가 기록을 막는다.
    # `EFFORT_PATH_CONFLICT`도 baseline을 보존해야 하므로 동일성 단정에 포함한다.
    conflict = dict(
        draw(S.supported_effort_cases().filter(lambda case: len(case["fieldPath"]) >= 2))
    )
    conflict["mismatchKind"] = PATH_CONFLICT
    conflicted_body = dict(conflict["baselineBody"])
    # 중간 노드를 dict가 아닌 scalar로 만든다(json_scalars는 dict·list를 만들지 않는다).
    conflicted_body[conflict["fieldPath"][0]] = draw(S.json_scalars())
    conflict["baselineBody"] = conflicted_body
    cases.append(conflict)

    for case in cases:
        case["userInput"] = draw(user_inputs())
    return cases


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
# 단정 helper
# ─────────────────────────────────────────────────────────────────
def _assert_effort_absent(body: Any, field_path: list[str], *, skip: frozenset[str]) -> None:
    """body의 모든 중첩 경로에서 effort field 발생 횟수가 0임을 단정한다.

    ``skip``에는 baseline이 이미 갖고 있던 key만 넣는다(field path 충돌 변형에서 우리가
    직접 놓은 중간 노드). 그 밖의 성분은 발생 횟수가 정확히 0이어야 한다.
    """
    for part in field_path:
        if part in skip:
            continue
        assert rb.count_key_occurrences(body, part) == 0, f"effort field key가 생성됐다: {part!r}"
        assert S.count_key_occurrences(body, part) == 0
    assert S.count_field_path_occurrences(body, field_path) == 0, "effort field path가 생성됐다"
    assert not rb.path_exists(body, field_path), "effort field path가 body에 존재한다"


def _assert_reason(case: dict, binding: dict, baseline: Any) -> None:
    """차단 이유 코드와 주입 계획이 결정론적으로 비주입임을 단정한다."""
    kind = case["mismatchKind"]
    expected = VARIANT_REASONS[kind]
    selection = case["selection"]

    reason = rb.injection_reason(binding, selection, baseline_body=baseline)
    assert reason in rb.INJECTION_REASONS, f"닫힌 이유 코드 집합 이탈: {reason!r}"
    assert reason == expected, f"{kind}: 기대 이유 {expected!r}, 실제 {reason!r}"

    plan = rb.effort_injection_plan(binding, selection, baseline_body=baseline)
    assert plan["inject"] is False, f"{kind}: 주입 계획이 참이다"
    assert plan["reason"] == expected
    assert plan["fieldPath"] is None
    assert plan["value"] is None


# ─────────────────────────────────────────────────────────────────
# Property 3
# ─────────────────────────────────────────────────────────────────
# Feature: gateway-models-effort-support, Property 3: unsupported effort 비주입
@seed(S.AE_PBT_SEED)
@S.PBT_SETTINGS
@given(baseline_preservation_cases())
def _property_unsupported_effort_not_injected(cases: list[dict]) -> None:
    """비주입 조건에서 생성 body는 baseline과 동일하고 effort field는 0회다.

    Requirements 7.15, 7.16, 7.17, 8.17, 8.18, 8.19, 12.3, 12.11.
    """
    observed: set[str] = set()

    for case in cases:
        kind = case["mismatchKind"]
        observed.add(kind)

        bundle = case["bundle"]
        binding = binding_for(bundle)
        selection = case["selection"]
        field_path = case["fieldPath"]
        baseline = case["baselineBody"]
        snapshot = copy.deepcopy(baseline)

        # 생성기 전제: field path는 계약이 확정한 순서 의미 경로다.
        assert rb.is_field_path(field_path), "생성기가 유효한 field path를 만들지 않았다"
        status = S.effort_status(bundle)
        if kind == "statusNotSupported":
            assert status != contracts.Effort_Support_Status.SUPPORTED
        else:
            assert status == contracts.Effort_Support_Status.SUPPORTED

        # (3) 차단 이유의 결정론.
        _assert_reason(case, binding, baseline)

        # (1) baseline 보존 — 동일 객체, canonical 바이트 동일, 전송 바이트 동일.
        body = rb._inject_effort(baseline, binding, selection)
        assert body is baseline, f"{kind}: baseline과 다른 객체를 반환했다"
        assert canonicalizer.serialize(body) == canonicalizer.serialize(snapshot)
        assert _raw(body) == _raw(snapshot), f"{kind}: 전송 바이트가 baseline과 다르다"
        assert baseline == snapshot, f"{kind}: 입력 baseline이 변형됐다"
        assert rb.inject_effort(baseline, binding, selection) is baseline

        # (2) 모든 중첩 경로에서 effort field 발생 횟수 0.
        #     충돌 변형에서 우리가 직접 놓은 중간 노드만 예외로 둔다.
        preexisting = frozenset({field_path[0]}) if kind == PATH_CONFLICT else frozenset()
        _assert_effort_absent(body, field_path, skip=preexisting)

        if kind not in SEAM_VARIANTS:
            continue

        # (4) builder seam 무회귀 — 기존 builder가 만든 body와 바이트 동일.
        model_id = bundle["modelId"]
        messages = case["userInput"]["messages"]
        system = case["userInput"]["system"]
        client = rb.effort_bound_client(_BASE, binding, selection)

        base_converse = _BASE._build_payload(model_id, messages, system)
        base_openai = _BASE._build_openai_payload(model_id, messages, system)
        # 실제 baseline이 우연히 같은 무작위 심볼 key를 갖는 조합은 제외한다(생성기 충돌 방어).
        assume(not (set(field_path) & (_all_keys(base_converse) | _all_keys(base_openai))))

        seam_converse = client._build_payload(model_id, messages, system)
        seam_openai = client._build_openai_payload(model_id, messages, system)

        assert _raw(seam_converse) == _raw(base_converse), f"{kind}: Converse body가 기준선과 다르다"
        assert _raw(seam_openai) == _raw(base_openai), f"{kind}: Responses body가 기준선과 다르다"
        _assert_effort_absent(seam_converse, field_path, skip=frozenset())
        _assert_effort_absent(seam_openai, field_path, skip=frozenset())

    # (5) 불일치 6종 전수 + 파생 변형 2종이 이 example에서 모두 도달했다.
    assert observed == set(VARIANT_REASONS), f"변형 전수 미도달: {sorted(set(VARIANT_REASONS) - observed)}"
    assert set(S.MISMATCH_KINDS) <= observed


def test_unsupported_effort_not_injected() -> None:
    """Property 3 pytest 진입점 — 실패 시 최소화 counterexample과 재현 blob을 기록한다."""
    try:
        _property_unsupported_effort_not_injected()
    except BaseException as exc:
        _report_counterexample("test_unsupported_effort_not_injected", exc)
        raise


if __name__ == "__main__":
    # 단발 실행 드라이버(워치 모드 금지).
    test_unsupported_effort_not_injected()
    print(
        f"PASSED: Property {PROPERTY_ID} — {PROPERTY_NAME} "
        f"(seed={S.AE_PBT_SEED}, max_examples={S.MAX_EXAMPLES})"
    )
