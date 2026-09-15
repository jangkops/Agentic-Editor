#!/usr/bin/env python3
"""Validation_Runner CLI — interpreter guard · stage 오케스트레이션 · 검증 보고서.

이 스크립트는 `gateway-models-effort-support` 기능의 **재현 가능하고 예산이 묶인**
validation run 진입점이다. 작업 12.1이 만든 골격(아래 1~4)에 작업 12.2가 stage
오케스트레이션과 보고서 본문(아래 5~7)을 더한 형태다.

1. **CLI 골격** — `--labels`, `--report`, `--dry-run`, `--stage` 인자 처리와
   `--stage` 닫힌 집합 검증(:data:`STAGE_CHOICES`).
2. **interpreter guard** — 저장소의 `ai_engine/.venv/bin/python`으로 실행되지 않으면
   Gateway_Probe 전송 건수를 **0으로 유지**하고 interpreter 환경 오류만 보고한다
   (Requirement 11.1, 11.2, 11.3). guard는 capability 모듈 import보다 **먼저** 판정된다
   (:func:`load_capability` 주석 참조) — 그래야 구버전 interpreter에서도 traceback이 아닌
   interpreter 환경 오류가 보고된다.
3. **dry-run 예산 계획** — 전송 0건으로 :class:`evidence_collector.ProbeBudget` 한도
   (조합당 성공 1회 · 교정 1회 · route당 prefix 교정 1회)와 각 Known_Route의
   최소 output/token bound(:func:`evidence_collector.min_output_bound_fields`)를 출력한다
   (Requirement 11.13, 11.14, 11.15, 11.16).
4. **보고서 헤더** — Current_Revision, interpreter absolute path, 시작 UTC ISO 8601,
   Same_Gateway_Environment identity, PBT seed를 기록하고 `--report` 경로(미지정 시
   `userData/capability/runs/{runId}.json`)에 **원자적으로** 기록한다
   (Requirement 11.4, 11.5, 11.6).

5. **stage 오케스트레이션** — :data:`STAGE_ORDER`(interpreter → baseline → discover →
   route → effort → map → activate) 순서로 실행한다. 각 stage는 기존 백엔드 모듈에만
   위임하며(:mod:`ai_engine.capability.baseline_inspector`,
   :class:`evidence_collector.EvidenceCollector`, :mod:`ai_engine.capability.capability_map`,
   :mod:`ai_engine.capability.activation_gate`) 판정 로직을 이 파일에 복제하지 않는다.
   개별 stage를 지정하면 그 stage에 필요한 **비전송 준비**(baseline 검사·catalog
   discovery)만 자동 수행하고(:func:`ensure_baseline`, :func:`ensure_discovery`), 어떤
   경로에서도 전송 계정은 :class:`evidence_collector.ProbeBudget`이 셈한 값 그대로다.
6. **보고서 본문**(:func:`build_report`의 ``reportBody``) — 6개 Candidate_Label discovery
   결과, Exact_Model_ID·Provider_String, Known_Route별 상태·evidence reference,
   model·route별 Effort_Support_Status·evidence reference, Verification_Status·fingerprint,
   probe별 Probe_ID·Sanitized_Schema, Gateway가 제공한 usage·cost(미제공은
   :data:`contracts.NOT_PROVIDED`), 미완료 조합의 `UNVERIFIED` 주장, PBT seed와
   최소화 counterexample을 기록한다(Requirement 11.7~11.12, 11.15~11.24, 12.20, 12.21,
   12.23, 12.24).
7. **예산 재확인과 activation 일치 검사** — 실행이 끝난 뒤 ProbeBudget 카운터를 다시 읽어
   조합당 성공 1회·교정 1회·route당 prefix 교정 1회를 **runner 수준에서** 재확인하고
   (:func:`budget_reverification`), 이 실행이 만들거나 바꾼 Active_Model은
   Current_Evidence·revision·Capability_Fingerprint 일치를 검사해 불일치 entry를
   activation 결과에서 제외한다(:func:`run_evidence_reasons`).

값 추론 금지(핵심 불변식):
  - model ID·provider·route 지원 여부·effort field path·effort 허용값을 이 파일에 상수로
    두지 않는다. `--labels` 기본값은 :data:`evidence_collector.CANDIDATE_LABELS`(검색
    라벨)뿐이며, 라벨 문자열에서 어떤 identity도 유도하지 않는다.
  - Gateway_Probe 입력은 :data:`evidence_collector.PROBE_INPUT_TEXT` ·
    :data:`evidence_collector.PROBE_SYSTEM_PROMPT` ·
    :func:`evidence_collector.probe_messages`를 그대로 쓰고, output/token bound는
    Route_Contract가 허용하는 최소값(:func:`evidence_collector.min_output_bound_fields`)만
    쓴다. 이 파일에 새 입력·새 bound를 정의하지 않는다.
  - PBT seed는 `scripts/_capability_strategies.py`의 `AE_PBT_SEED`(기본 20260803,
    `AE_PBT_SEED` env 오버라이드)를 재사용한다. 구할 수 없으면 값을 만들지 않고
    `null` + note로 남긴다.
  - Gateway catalog endpoint와 Operator_Catalog_Export는 **운영자가 지정**한다
    (:data:`ENV_CATALOG_ENDPOINT`, :data:`ENV_OPERATOR_EXPORT`). 이 파일은 endpoint를
    추측하지 않으며, 지정되지 않으면 discovery가 catalog 부재로 기록하고 모든 라벨이
    `UNVERIFIED`로 남는다(probe 미생성).

종료 코드(12.1 계약 유지): 0 성공, 1 interpreter 환경 오류, 2 인자 오류(argparse),
3 보고서 기록 실패. stage 실행 중 발생한 오류는 프로세스를 죽이지 않고 해당 stage를
`failed`로 기록한 뒤 보고서와 stderr로 보고한다(전송 계정은 그대로 유지된다).

보안(steering·Requirement 10):
  - Gateway 접근은 기존 :class:`ai_engine.gateway_module.GatewayClient`에만 위임한다.
    신규 URL 하드코딩·신규 서명 코드·신규 credential 캐시를 만들지 않는다. 이 파일은
    endpoint identity와 region을 기존 client 인스턴스 속성에서 **읽기만** 한다.
  - 모든 파일 기록은 `userData` 하위로 제한되며(:class:`store.CapabilityStore`),
    루트 밖 `--report` 경로는 거부한다. 기록 전 정제(credential 제거 · raw prompt →
    Probe_ID · raw body → Sanitized_Schema)를 store가 강제한다.

실행(모든 Python 실행은 이 interpreter만 사용한다)::

    ai_engine/.venv/bin/python scripts/validate_gateway_model_capabilities.py --dry-run
    ai_engine/.venv/bin/python scripts/validate_gateway_model_capabilities.py \\
        --labels "opus 5" "sonnet 5" "gpt 5.6" "sol" "terra" "luna" \\
        --report capability/runs/manual-run.json

참조: .kiro/specs/gateway-models-effort-support/design.md "Components and Interfaces" 8절
      + "검증 파이프라인 (validation 순서)"
Requirements: 11.1~11.24, 12.20, 12.21, 12.23, 12.24
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import os
import re
import subprocess
import sys
from typing import Any, Callable, Iterable, Sequence

# repo 루트를 import 경로에 추가한다(scripts/ 하위 실행 관행 재사용).
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ─────────────────────────────────────────────────────────────────
# capability 모듈 지연 import
# ─────────────────────────────────────────────────────────────────
# `ai_engine.capability`는 Python 3.11+ 문법(`enum.StrEnum` 등)을 쓴다. module scope에서
# import하면 구버전 interpreter로 실행했을 때 **import 오류가 interpreter guard 판정을
# 앞질러** traceback으로 끝나고, Requirement 11.3의 "interpreter 환경 오류를 보고한다"를
# 만족하지 못한다. 그래서 이 파일은 guard가 끝난 뒤에만 capability 모듈을 불러온다.
activation_gate: Any = None
baseline_inspector: Any = None
canonicalizer: Any = None
capability_map: Any = None
contracts: Any = None
evidence_collector: Any = None
store: Any = None


def load_capability() -> tuple[bool, str | None]:
    """`ai_engine.capability` 모듈들을 지연 import해 모듈 전역에 결속한다.

    Returns:
        ``(loaded, error)``. 실패해도 예외를 올리지 않고 ``(False, 원인≤200자)``를
        돌려준다 — 호출자는 Gateway_Probe를 한 건도 만들지 않고 interpreter 환경
        오류만 보고한다(Requirement 11.2, 11.3).
    """
    global activation_gate, baseline_inspector, canonicalizer, capability_map
    global contracts, evidence_collector, store
    if contracts is not None:
        return True, None
    try:
        from ai_engine.capability import activation_gate as _activation_gate
        from ai_engine.capability import baseline_inspector as _baseline_inspector
        from ai_engine.capability import canonicalizer as _canonicalizer
        from ai_engine.capability import capability_map as _capability_map
        from ai_engine.capability import contracts as _contracts
        from ai_engine.capability import evidence_collector as _evidence_collector
        from ai_engine.capability import store as _store
    except Exception as exc:
        return False, _truncate(f"{type(exc).__name__}: {exc}")
    activation_gate = _activation_gate
    baseline_inspector = _baseline_inspector
    canonicalizer = _canonicalizer
    capability_map = _capability_map
    contracts = _contracts
    evidence_collector = _evidence_collector
    store = _store
    return True, None


# ─────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────

#: 이 기능의 유일한 실행 interpreter(repo 루트 기준 상대 경로 성분).
INTERPRETER_RELATIVE_PARTS: tuple[str, ...] = ("ai_engine", ".venv", "bin", "python")

#: venv 루트(repo 루트 기준 상대 경로 성분) — 실행 중인 interpreter가 이 venv인지 판정한다.
VENV_RELATIVE_PARTS: tuple[str, ...] = ("ai_engine", ".venv")

# ── stage 이름(닫힌 집합) ─────────────────────────────────────────────────
STAGE_INTERPRETER = "interpreter"
STAGE_BASELINE = "baseline"
STAGE_DISCOVER = "discover"
STAGE_ROUTE = "route"
STAGE_EFFORT = "effort"
STAGE_MAP = "map"
STAGE_ACTIVATE = "activate"
STAGE_ALL = "all"

#: design.md "검증 파이프라인" stage 순서(interpreter 확인이 언제나 첫 stage다).
STAGE_ORDER: tuple[str, ...] = (
    STAGE_INTERPRETER,
    STAGE_BASELINE,
    STAGE_DISCOVER,
    STAGE_ROUTE,
    STAGE_EFFORT,
    STAGE_MAP,
    STAGE_ACTIVATE,
)

#: `--stage`가 허용하는 값의 닫힌 집합(그 밖의 값은 non-zero exit으로 거부된다).
STAGE_CHOICES: tuple[str, ...] = STAGE_ORDER + (STAGE_ALL,)

#: 실행이 구현된 stage(작업 12.2에서 STAGE_ORDER 전체가 구현됐다).
IMPLEMENTED_STAGES: frozenset[str] = frozenset(STAGE_ORDER)

# ── stage 결과 상태(닫힌 집합) ────────────────────────────────────────────
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"
STAGE_PENDING = "pending"
STAGE_SKIPPED = "skipped"
STAGE_BLOCKED = "blocked"

# ── 이유 코드(닫힌 집합) ──────────────────────────────────────────────────
REASON_OK = "OK"
REASON_DRY_RUN = "DRY_RUN"
REASON_STAGE_NOT_IMPLEMENTED = "STAGE_NOT_IMPLEMENTED"
REASON_INTERPRETER_ENVIRONMENT_ERROR = "INTERPRETER_ENVIRONMENT_ERROR"

#: stage 실행 이유 코드(12.2).
REASON_STAGE_QUEUED = "STAGE_QUEUED"
REASON_STAGE_ERROR = "STAGE_ERROR"
REASON_STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
REASON_STORE_ERROR = "STORE_ERROR"
REASON_BASELINE_INELIGIBLE = "BASELINE_NOT_EVIDENCE_ELIGIBLE"
REASON_CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"
REASON_NO_DISCOVERED_MODEL = "NO_DISCOVERED_MODEL"
REASON_NO_ACTIVE_MODEL = "NO_ACTIVE_MODEL"
REASON_BUDGET_VIOLATION = "BUDGET_VIOLATION"

#: 미완료 조합의 Gateway 지원 주장 이유(Requirement 11.24, 12.24).
CLAIM_PROBE_INCOMPLETE = "PROBE_NOT_COMPLETED"

#: 미완료 주장의 종류(닫힌 집합).
CLAIM_MODEL = "MODEL"
CLAIM_ROUTE = "ROUTE"
CLAIM_EFFORT = "EFFORT"

#: probe 종류(보고서 probe 목록 — 닫힌 집합).
PROBE_KIND_ROUTE = "ROUTE"
PROBE_KIND_EFFORT = "EFFORT"
PROBE_KIND_EFFORT_VALUE = "EFFORT_VALUE"

#: activation 일치 검사 탈락 코드(Requirement 11.21~11.24).
#: activation_gate의 이유 코드와 구분되도록 `RUN_` 접두사를 쓴다.
RUN_EVIDENCE_MISMATCH = "RUN_EVIDENCE_MISMATCH"
RUN_REVISION_MISMATCH = "RUN_REVISION_MISMATCH"
RUN_FINGERPRINT_MISMATCH = "RUN_FINGERPRINT_MISMATCH"
RUN_EVIDENCE_REASONS: tuple[str, ...] = (
    RUN_EVIDENCE_MISMATCH,
    RUN_REVISION_MISMATCH,
    RUN_FINGERPRINT_MISMATCH,
)

#: interpreter guard 판정 이유(닫힌 집합).
INTERPRETER_ABSENT = "INTERPRETER_ABSENT"
INTERPRETER_NOT_EXECUTABLE = "INTERPRETER_NOT_EXECUTABLE"
INTERPRETER_NOT_ACTIVE = "INTERPRETER_NOT_ACTIVE"
INTERPRETER_IMPORT_FAILED = "INTERPRETER_IMPORT_FAILED"
INTERPRETER_REASONS: tuple[str, ...] = (
    INTERPRETER_ABSENT,
    INTERPRETER_NOT_EXECUTABLE,
    INTERPRETER_NOT_ACTIVE,
    INTERPRETER_IMPORT_FAILED,
)

#: 비차단 note 코드(판정에 영향을 주지 않지만 보고서에 남긴다).
NOTE_REVISION_UNAVAILABLE = "REVISION_UNAVAILABLE"
NOTE_GATEWAY_CLIENT_UNAVAILABLE = "GATEWAY_CLIENT_UNAVAILABLE"
NOTE_ENVIRONMENT_INCOMPLETE = "ENVIRONMENT_INCOMPLETE"
NOTE_PBT_SEED_UNAVAILABLE = "PBT_SEED_UNAVAILABLE"
NOTE_LABELS_DEFAULTED = "LABELS_DEFAULTED"
NOTE_STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
NOTE_CATALOG_ENDPOINT_UNSET = "CATALOG_ENDPOINT_UNSET"
NOTE_OPERATOR_EXPORT_LOADED = "OPERATOR_EXPORT_LOADED"
NOTE_OPERATOR_EXPORT_UNREADABLE = "OPERATOR_EXPORT_UNREADABLE"

# ── 운영자 지정 입력(환경변수) ─────────────────────────────────────────────
# endpoint·export 경로를 이 파일에 하드코딩하지 않는다. 값이 없으면 discovery가
# catalog 부재로 기록하고 모든 라벨은 `UNVERIFIED`로 남는다(probe 미생성).

#: Same_Gateway_Environment의 catalog(목록) endpoint. 기존 `openai_catalog.GatewayListSource`가
#: 이 endpoint를 기존 GatewayClient로 조회한다(신규 URL·신규 서명 코드 없음).
ENV_CATALOG_ENDPOINT = "AE_GATEWAY_CATALOG_ENDPOINT"

#: Operator_Catalog_Export JSON 경로. **userData 루트 기준 상대 경로**만 허용한다
#: (store가 루트 밖 경로를 거부한다 — Requirement 10.15).
ENV_OPERATOR_EXPORT = "AE_CAPABILITY_OPERATOR_EXPORT"

#: `1`/`true`면 property-based test를 이 실행에서 수행하고 seed·counterexample을 기록한다
#: (Requirement 12.20, 12.21). 기본은 수행하지 않고 seed만 기록한다.
ENV_PBT_RUN = "AE_PBT_RUN"

#: 종료 코드.
EXIT_OK = 0
EXIT_INTERPRETER_ERROR = 1
EXIT_REPORT_ERROR = 3

#: 진단 문자열 최대 길이(프로젝트 로깅 관례와 동일하게 200자 절단).
_REASON_MAX = 200

#: 조합 키의 구성(보고서 가독성용 — 값이 아니라 축 이름이다).
COMBINATION_AXES: tuple[str, ...] = ("modelId", "route", "effortValue")

# ── 계약의 evidence 소유 자리(STALE 전이 비교에서 제외되는 자리) ─────────────
# Requirement 6.17·6.18의 "계약 변경"은 **선언 변경**이다. 아래 자리는 선언이 아니라
# evidence가 채우므로, 값 차이를 변경으로 취급하면 실제로 아무 것도 바뀌지 않은 entry를
# `STALE`로 강등한다. :func:`align_declared_contract`가 기록된 계약 값을 그대로 쓴다.

#: Route_Contract에서 evidence가 채우는 자리.
#:   ``evidenceRef``     `capability_map.upsert`가 Evidence_Record_ID로 채운다
#:   ``minOutputBound``  성공 probe가 실제로 사용한 bound(교정으로 bound를 내린 결과가 기록된다)
ROUTE_CONTRACT_EVIDENCE_FIELDS: tuple[str, ...] = ("evidenceRef", "minOutputBound")

#: Effort_Contract에서 evidence가 채우는 자리.
#:   ``evidenceRef``     동일
#:   ``verifiedValues``  성공 effort probe가 실제로 확인한 value(Requirement 5.12, 5.13)
EFFORT_CONTRACT_EVIDENCE_FIELDS: tuple[str, ...] = ("evidenceRef", "verifiedValues")

#: property test 파일 판별 접미사(파일명 규약 — design.md property ↔ 테스트 파일 매핑).
PBT_FILE_SUFFIX = "_pbt.py"

#: 이 기능의 property test가 공유하는 생성기 모듈(파일 판별의 두 번째 신호).
PBT_STRATEGY_MODULE = "_capability_strategies"

#: property test 파일 판별 시 읽는 최대 바이트(전문 파싱이 아니라 참조 확인만 한다).
PBT_SCAN_MAX_BYTES = 65536

#: property test 실행 시간 상한(초). 초과하면 timeout으로 기록하고 실행을 끝낸다.
PBT_TIMEOUT_SECONDS = 1800

#: 보고서에 남기는 counterexample 최대 개수(최소화된 예시만 담는다).
PBT_MAX_COUNTEREXAMPLES = 20

#: Hypothesis가 출력하는 최소화 counterexample·재현 blob 패턴.
#: pytest longrepr은 각 줄 앞에 `E ` 표식을 붙이므로 그것도 허용한다.
_PBT_FALSIFYING = re.compile(r"^\s*(?:E\s+)?Falsifying example:\s*(?P<text>.*)$")
_PBT_REPRODUCE = re.compile(r"@reproduce_failure\([^)]*\)")
_PBT_MARKER = re.compile(r"^\s*(?:E\s+)?")

#: 최소화 예시가 차지하는 최대 줄 수와, 재현 blob을 찾는 탐색 범위(줄).
PBT_EXAMPLE_MAX_LINES = 12
PBT_BLOB_SCAN_LINES = 12


# ─────────────────────────────────────────────────────────────────
# 작은 유틸
# ─────────────────────────────────────────────────────────────────
def _truncate(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:_REASON_MAX]


def _abspath(path: Any) -> str:
    """경로를 절대 경로로 정규화한다(존재 여부는 보지 않는다)."""
    return os.path.abspath(os.path.expanduser(str(path)))


def _same_path(left: Any, right: Any) -> bool:
    """두 경로가 symlink 해소 후 같은 대상을 가리키는지."""
    try:
        return os.path.realpath(_abspath(left)) == os.path.realpath(_abspath(right))
    except (OSError, ValueError):  # pragma: no cover - 경로 계산 실패
        return False


def repo_root() -> str:
    """저장소 루트 절대 경로(이 파일 위치 기준 — capability import 전에도 계산된다).

    interpreter guard는 capability 모듈보다 먼저 판정되어야 하므로 여기서는
    `scripts/`의 부모를 그대로 쓴다. Current_Revision은 guard 통과 후
    :func:`baseline_inspector.current_revision`이 읽는다(revision 계산을 복제하지 않는다).
    """
    return _abspath(_ROOT)


def interpreter_path(root: str | None = None) -> str:
    """`{repo}/ai_engine/.venv/bin/python` 절대 경로."""
    return os.path.join(_abspath(root or repo_root()), *INTERPRETER_RELATIVE_PARTS)


def venv_root(root: str | None = None) -> str:
    """`{repo}/ai_engine/.venv` 절대 경로."""
    return os.path.join(_abspath(root or repo_root()), *VENV_RELATIVE_PARTS)


def compact_stamp(iso_time: Any) -> str:
    """UTC ISO 8601 시각을 파일명 안전한 압축 표기로 바꾼다(`20260803T101112Z`)."""
    text = iso_time if isinstance(iso_time, str) else str(iso_time)
    head = text.split(".")[0].rstrip("Z")
    return "".join(char for char in head if char.isalnum()) + "Z"


def pbt_seed() -> tuple[int | None, str | None]:
    """PBT 재현 seed를 `scripts/_capability_strategies.py`에서 읽는다.

    Returns:
        ``(seed, note)``. seed를 구할 수 없으면 ``(None, NOTE_PBT_SEED_UNAVAILABLE)``.
        정의 위치는 하나뿐이므로 이 파일에 seed 기본값을 복제하지 않는다.
    """
    try:
        from _capability_strategies import AE_PBT_SEED  # 정의 위치 재사용
    except Exception:
        return None, NOTE_PBT_SEED_UNAVAILABLE
    return int(AE_PBT_SEED), None


# ─────────────────────────────────────────────────────────────────
# interpreter guard (Requirement 11.1, 11.2, 11.3)
# ─────────────────────────────────────────────────────────────────
def interpreter_verdict(
    root: str | None = None,
    *,
    executable: str | None = None,
    prefix: str | None = None,
) -> dict:
    """실행 중인 interpreter가 `ai_engine/.venv/bin/python`인지 판정한다(순수 판정).

    판정 축은 두 가지다.

      - **존재·실행 가능** — `{repo}/ai_engine/.venv/bin/python`이 파일로 존재하고
        실행 권한이 있는가(:data:`INTERPRETER_ABSENT`, :data:`INTERPRETER_NOT_EXECUTABLE`).
      - **활성** — 지금 이 프로세스가 그 venv로 실행되고 있는가. venv의 `python`은
        보통 `python3.x`(그리고 다시 시스템 python)로 이어지는 symlink이므로,
        실행 파일 경로 비교만으로는 판정할 수 없다. 따라서 `sys.prefix`가 venv 루트와
        같은지를 1차 근거로 쓰고, 실행 파일 경로 일치를 2차 근거로 쓴다
        (:data:`INTERPRETER_NOT_ACTIVE`).

    Args:
        root: 저장소 루트(기본은 :func:`repo_root`).
        executable: 판정할 실행 파일 경로(기본 ``sys.executable``).
        prefix: 판정할 interpreter prefix(기본 ``sys.prefix``).

    Returns:
        ``{"ok", "expectedInterpreterPath", "interpreterPath", "venvRoot", "prefix",
        "basePrefix", "exists", "executable", "active", "pathMatches",
        "prefixMatches", "pythonVersion", "reasons"}``. ``ok``가 거짓이면 호출자는
        Gateway_Probe를 **한 건도 만들지 않는다**(Requirement 11.2).
    """
    root_abs = _abspath(root or repo_root())
    expected = interpreter_path(root_abs)
    venv = venv_root(root_abs)
    running = _abspath(sys.executable if executable is None else executable)
    running_prefix = _abspath(sys.prefix if prefix is None else prefix)

    exists = os.path.isfile(expected)
    can_execute = bool(exists and os.access(expected, os.X_OK))
    path_matches = _same_path(running, expected)
    prefix_matches = _same_path(running_prefix, venv)
    active = bool(path_matches or prefix_matches)

    reasons: list[str] = []
    if not exists:
        reasons.append(INTERPRETER_ABSENT)
    elif not can_execute:
        reasons.append(INTERPRETER_NOT_EXECUTABLE)
    if not active:
        reasons.append(INTERPRETER_NOT_ACTIVE)

    return {
        "ok": not reasons,
        "expectedInterpreterPath": expected,
        "interpreterPath": running,
        "venvRoot": venv,
        "prefix": running_prefix,
        "basePrefix": _abspath(sys.base_prefix),
        "exists": exists,
        "executable": can_execute,
        "active": active,
        "pathMatches": path_matches,
        "prefixMatches": prefix_matches,
        "pythonVersion": sys.version.split()[0],
        "importError": None,
        "reasons": reasons,
    }


def with_import_failure(verdict: dict, error: str | None) -> dict:
    """capability 모듈 import 실패를 guard 판정에 합친다(전송 0건 결론은 그대로)."""
    out = dict(verdict)
    reasons = list(out.get("reasons") or [])
    if INTERPRETER_IMPORT_FAILED not in reasons:
        reasons.append(INTERPRETER_IMPORT_FAILED)
    out["reasons"] = reasons
    out["ok"] = False
    out["importError"] = _truncate(error) if error else None
    return out


def interpreter_error_message(verdict: dict) -> str:
    """interpreter 환경 오류 한 줄(비민감 — 경로와 이유 코드만)."""
    message = (
        "interpreter 환경 오류: "
        f"reasons={','.join(verdict.get('reasons') or [])} "
        f"expected={verdict.get('expectedInterpreterPath')} "
        f"running={verdict.get('interpreterPath')}"
    )
    if verdict.get("importError"):
        message += f" import={verdict['importError']}"
    return message


# ─────────────────────────────────────────────────────────────────
# stage 해석 (`--stage` 닫힌 집합 검증)
# ─────────────────────────────────────────────────────────────────
def resolve_stages(stage: Any) -> tuple[str, ...]:
    """요청 stage를 실행 순서 tuple로 해석한다.

    `all`은 :data:`STAGE_ORDER` 전체이며, 개별 stage 요청에는 언제나 interpreter 확인이
    앞에 붙는다(interpreter가 불가하면 어떤 stage도 Gateway 전송을 만들지 않는다 —
    Requirement 11.2).

    Raises:
        ValueError: :data:`STAGE_CHOICES` 밖의 값(닫힌 집합 위반).
    """
    if not isinstance(stage, str) or stage not in STAGE_CHOICES:
        raise ValueError(
            f"허용되지 않은 stage: {stage!r} (허용: {', '.join(STAGE_CHOICES)})"
        )
    if stage == STAGE_ALL:
        return STAGE_ORDER
    if stage == STAGE_INTERPRETER:
        return (STAGE_INTERPRETER,)
    return (STAGE_INTERPRETER, stage)


def stage_results(stages: Iterable[str], *, interpreter_ok: bool, dry_run: bool) -> list[dict]:
    """stage별 실행 결과를 만든다(12.1은 interpreter stage만 완결한다).

    - interpreter: guard 결과에 따라 ``completed`` 또는 ``failed``.
    - 그 외 stage:
        - interpreter guard 실패 → ``blocked``(전송 0건, Requirement 11.2)
        - `--dry-run` → ``skipped``(전송 0건, 예산 계획만 산출)
        - 그 밖에는 ``pending`` — 실행 대기(:data:`IMPLEMENTED_STAGES`에 있으면
          `STAGE_QUEUED`, 아니면 `STAGE_NOT_IMPLEMENTED`)

    이 함수가 만든 항목의 ``transmissions``는 모두 0이며, 실제 상태·전송 건수는
    :func:`execute_stages`가 stage를 실행하면서 제자리에서 갱신한다(전송 건수는
    :class:`evidence_collector.ProbeBudget` 카운터 증가분으로만 셈한다).
    """
    out: list[dict] = []
    for name in stages:
        if name == STAGE_INTERPRETER:
            out.append(
                {
                    "stage": name,
                    "status": STAGE_COMPLETED if interpreter_ok else STAGE_FAILED,
                    "reason": REASON_OK if interpreter_ok else REASON_INTERPRETER_ENVIRONMENT_ERROR,
                    "transmissions": 0,
                }
            )
            continue
        if not interpreter_ok:
            status, reason = STAGE_BLOCKED, REASON_INTERPRETER_ENVIRONMENT_ERROR
        elif dry_run:
            status, reason = STAGE_SKIPPED, REASON_DRY_RUN
        else:
            status = STAGE_PENDING
            reason = (
                REASON_STAGE_QUEUED
                if name in IMPLEMENTED_STAGES
                else REASON_STAGE_NOT_IMPLEMENTED
            )
        out.append({"stage": name, "status": status, "reason": reason, "transmissions": 0})
    return out


# ─────────────────────────────────────────────────────────────────
# 예산 계획 (전송 0건 — Requirement 11.13, 11.14, 11.15, 11.16)
# ─────────────────────────────────────────────────────────────────
def budget_plan(budget: evidence_collector.ProbeBudget | None = None) -> dict:
    """:class:`evidence_collector.ProbeBudget` 한도를 계획으로 읽는다(전송 없음).

    한도는 ProbeBudget이 강제하는 값을 그대로 읽는다 — 이 파일에 별도 한도 상수를 두지
    않는다. 조합 단위는 `(Exact_Model_ID, Known_Route, effort value)`이며 effort 미주입
    baseline은 effort value가 ``null``인 하나의 조합이다.
    """
    snapshot = (budget or evidence_collector.ProbeBudget()).snapshot()
    return {
        "combinationAxes": list(COMBINATION_AXES),
        "maxSuccessesPerCombination": snapshot["maxSuccesses"],
        "maxCorrectionsPerCombination": snapshot["maxCorrections"],
        "maxPrefixCorrectionsPerRoute": snapshot["maxPrefixCorrections"],
        "transmissionLimitPerCombination": snapshot["transmissionLimit"],
        "plannedTransmissions": 0,
        "totalTransmissions": snapshot["totalTransmissions"],
    }


def probe_input_plan() -> dict:
    """Gateway_Probe 입력 계획 — 고정된 짧은 비민감 입력(Requirement 11.13).

    입력 문자열은 :data:`evidence_collector.PROBE_INPUT_TEXT`이고 system 지시문은
    :data:`evidence_collector.PROBE_SYSTEM_PROMPT`(빈 문자열)다. 이 파일에 새 입력을
    정의하지 않으며, 계획에도 raw prompt를 남기지 않고 Probe_ID와 Sanitized_Schema만
    기록한다(Requirement 10.13, 10.14).
    """
    messages = evidence_collector.probe_messages()
    return {
        "probeId": evidence_collector.probe_id("plan", "probe-input"),
        "inputSchema": store.sanitized_schema(messages),
        "textLength": len(evidence_collector.PROBE_INPUT_TEXT),
        "systemPromptEmpty": evidence_collector.PROBE_SYSTEM_PROMPT == "",
        "effortSelection": evidence_collector.PROBE_EFFORT_SELECTION,
        "sourceRef": "evidence_collector.PROBE_INPUT_TEXT",
    }


def route_plan() -> list[dict]:
    """Known_Route별 계획 — 계약이 허용하는 최소 output/token bound(Requirement 11.14).

    bound는 :func:`evidence_collector.min_output_bound_fields`가 후보 Route_Contract에서
    읽은 값 그대로이며, `fields`가 비면 "이 route의 production builder는 output bound
    field를 만들지 않는다"는 뜻이다(body는 baseline 그대로 유지된다).
    """
    out: list[dict] = []
    for route in contracts.KNOWN_ROUTES:
        profile = evidence_collector.route_profile(route) or {}
        contract = evidence_collector.candidate_route_contract(route) or {}
        bounds = [
            {"fieldPath": list(path), "value": value}
            for path, value in evidence_collector.min_output_bound_fields(contract)
        ]
        source = profile.get("minOutputBound") if isinstance(profile.get("minOutputBound"), dict) else {}
        out.append(
            {
                "routeKey": route,
                "endpointRef": profile.get("endpointRef"),
                "httpMethod": profile.get("httpMethod"),
                "executionMode": profile.get("executionMode"),
                "signingService": profile.get("signingService"),
                "minOutputBound": bounds,
                "minOutputBoundSourceRef": source.get("sourceRef"),
                "requiresProbeInput": bool(profile.get("requiresProbeInput")),
                "effortInjectable": route in evidence_collector.EFFORT_INJECTABLE_ROUTES,
                "plannedTransmissions": 0,
            }
        )
    return out


def transmission_plan(labels: Sequence[str], *, dry_run: bool) -> dict:
    """전송 계획 전체(예산 · 입력 · route별 최소 bound). 전송 건수는 언제나 0이다."""
    return {
        "dryRun": bool(dry_run),
        "plannedTransmissions": 0,
        "candidateLabels": list(labels),
        "budget": budget_plan(),
        "probeInput": probe_input_plan(),
        "routes": route_plan(),
    }


# ─────────────────────────────────────────────────────────────────
# Same_Gateway_Environment identity (기존 GatewayClient에만 위임)
# ─────────────────────────────────────────────────────────────────
def gateway_client(env: dict | None = None) -> tuple[Any, list[str]]:
    """기존 :class:`ai_engine.gateway_module.GatewayClient`를 구성한다(전송·인증 없음).

    profile·region·bedrock user는 기존 관례대로 환경변수에서만 읽는다. 신규 URL·신규
    서명 코드·신규 credential 캐시를 만들지 않으며, 생성만으로는 자격증명 접근도
    네트워크 전송도 발생하지 않는다(:meth:`GatewayClient._get_creds`는 첫 전송에서만
    호출된다).

    Returns:
        ``(client|None, notes)``. 구성 실패는 예외가 아니라 note 코드로 남는다 —
        catalog 조회와 probe는 client 부재를 근거 부재로 취급한다.
    """
    environ = os.environ if env is None else env
    notes: list[str] = []
    try:
        from ai_engine.gateway_module import GatewayClient  # 지연 import(기존 구현 위임)

        kwargs: dict[str, Any] = {}
        profile = (environ.get("AWS_PROFILE") or "").strip()
        if profile:
            kwargs["aws_profile"] = profile
        region = (environ.get("AWS_REGION") or "").strip()
        if region:
            kwargs["region"] = region
        bedrock_user = (environ.get("AE_BEDROCK_USER") or "").strip()
        if bedrock_user:
            kwargs["bedrock_user"] = bedrock_user
        return GatewayClient(**kwargs), notes
    except Exception as exc:
        notes.append(NOTE_GATEWAY_CLIENT_UNAVAILABLE)
        notes.append(_truncate(f"gateway-client-error:{type(exc).__name__}:{exc}"))
        return None, notes


def gateway_environment(env: dict | None = None, *, client: Any = None) -> tuple[dict, list[str]]:
    """Same_Gateway_Environment identity를 기존 client 인스턴스에서 읽는다.

    endpoint identity와 region은 :class:`ai_engine.gateway_module.GatewayClient`의
    속성에서 읽고, `gatewayEnvironmentId`는 운영자가 지정한 `AE_GATEWAY_ENV_ID`만
    사용한다(:func:`evidence_collector.environment_identity`).

    Args:
        env: 환경변수 매핑(기본 ``os.environ``).
        client: 이미 구성한 GatewayClient. 주면 새로 만들지 않는다(실행 전체에서 client
            인스턴스를 하나로 유지해 credential 캐시를 단일화한다).

    Returns:
        ``(identity, notes)``. client를 구성할 수 없으면 identity의 endpoint·region이
        미확정으로 남고 note 코드가 붙는다.
    """
    environ = os.environ if env is None else env
    notes: list[str] = []
    gw = client
    if gw is None:
        gw, notes = gateway_client(environ)

    identity = evidence_collector.environment_identity(gw, env=environ)
    if not evidence_collector.environment_complete(identity):
        notes = list(notes) + [NOTE_ENVIRONMENT_INCOMPLETE]
    return identity, notes


# ─────────────────────────────────────────────────────────────────
# 실행 컨텍스트 (stage들이 공유하는 상태 — 값 추론 없음)
# ─────────────────────────────────────────────────────────────────
def store_or_none(env: dict | None = None) -> tuple[Any, str | None]:
    """:class:`store.CapabilityStore`를 만든다(실패는 예외 대신 원인 문자열).

    store가 없으면 영속화 stage는 기록 없이 보고만 하고 계속 진행한다 — 어떤 경우에도
    프로세스를 죽이지 않는다.
    """
    try:
        return store.CapabilityStore(env=env), None
    except Exception as exc:  # pragma: no cover - 경로 해석 실패
        return None, _truncate(f"{type(exc).__name__}: {exc}")


def operator_catalog_export(store_obj: Any, env: dict | None = None) -> tuple[Any, list[str]]:
    """운영자가 지정한 Operator_Catalog_Export를 읽는다(userData 하위 한정).

    경로는 :data:`ENV_OPERATOR_EXPORT`가 지정한 **userData 루트 기준 상대 경로**이며
    루트 밖 경로는 store가 거부한다(Requirement 10.15). 읽기 실패는 근거 부재로만
    기록한다 — 검증은 :func:`evidence_collector.validate_operator_catalog_export`가
    catalog 획득 시점에 수행한다(4개 검증을 이 파일에 복제하지 않는다).
    """
    environ = os.environ if env is None else env
    target = (environ.get(ENV_OPERATOR_EXPORT) or "").strip()
    if not target:
        return None, []
    if store_obj is None:
        return None, [NOTE_OPERATOR_EXPORT_UNREADABLE]
    data = store_obj.read_json_safe(target, default=None)
    if data is None:
        return None, [NOTE_OPERATOR_EXPORT_UNREADABLE]
    return data, [NOTE_OPERATOR_EXPORT_LOADED]


def new_run_state() -> dict:
    """stage 간 공유 상태(전부 이 실행에서 관측한 값만 담는다)."""
    return {
        "baseline": None,
        "baselineSource": None,
        "baselinePath": None,
        "catalog": None,
        "discovery": None,
        "routeResults": {},    # candidateLabel → [route 결과]
        "effortResults": {},   # candidateLabel → [effort summary]
        "map": None,
        "mapPath": None,
        "records": [],         # 이 실행이 만든 Verification_Record
        "recordPaths": {},     # evidenceRecordId → 저장 경로
        "evidenceIds": [],     # 이 실행의 Evidence_Record_ID(순서 보존)
        "evidenceRefs": {},    # candidateLabel → 마지막 Evidence_Record_ID
        "discoveryRefs": {},   # candidateLabel → discovery record의 Evidence_Record_ID
        "routeRefs": {},       # candidateLabel → route record의 Evidence_Record_ID
        "effortRefs": {},      # candidateLabel → effort record의 Evidence_Record_ID
        "touchedLabels": [],   # 이 실행이 record를 만든 라벨(신규·변경 대상)
        "catalogPath": None,
    }


def new_run_context(
    *,
    labels: Sequence[str],
    stage: str,
    stages: Sequence[str],
    dry_run: bool,
    root: str | None = None,
    verdict: dict | None = None,
    started_at: str | None = None,
    env: dict | None = None,
    extra_notes: Sequence[str] | None = None,
) -> dict:
    """stage 실행에 필요한 자원과 공유 상태를 한 번만 구성한다.

    구성 항목: Current_Revision, Same_Gateway_Environment identity, GatewayClient(하나만),
    CapabilityStore, Operator_Catalog_Export, PBT seed, 이 실행 전용
    :class:`evidence_collector.ProbeBudget`. 이 함수는 Gateway로 아무 것도 전송하지 않는다.
    """
    root_abs = _abspath(root or repo_root())
    guard = verdict if isinstance(verdict, dict) else interpreter_verdict(root_abs)
    environ = os.environ if env is None else env
    notes: list[str] = [str(note) for note in (extra_notes or [])]

    revision = baseline_inspector.current_revision(root_abs)
    if not revision:
        notes.append(NOTE_REVISION_UNAVAILABLE)
        revision = contracts.UNDETERMINED

    gw, client_notes = gateway_client(environ)
    notes.extend(client_notes)
    environment, env_notes = gateway_environment(environ, client=gw)
    notes.extend(env_notes)

    seed, seed_note = pbt_seed()
    if seed_note:
        notes.append(seed_note)

    store_obj, store_error = store_or_none(environ)
    if store_error:
        notes.extend([NOTE_STORE_UNAVAILABLE, store_error])

    export, export_notes = operator_catalog_export(store_obj, environ)
    notes.extend(export_notes)

    endpoint = (environ.get(ENV_CATALOG_ENDPOINT) or "").strip()
    if not endpoint:
        notes.append(NOTE_CATALOG_ENDPOINT_UNSET)

    return {
        "root": root_abs,
        "interpreter": guard,
        "labels": list(labels),
        "stage": stage,
        "stages": list(stages),
        "dryRun": bool(dry_run),
        "startedAt": started_at or contracts.utc_now_iso(),
        "revision": revision,
        "environment": environment,
        "pbtSeed": seed,
        "notes": notes,
        "env": environ,
        "gw": gw,
        "store": store_obj,
        "catalogEndpoint": endpoint,
        "operatorExport": export,
        "budget": evidence_collector.ProbeBudget(),
        "collector": None,
        "runId": contracts.UNDETERMINED,
        "state": new_run_state(),
    }


def context_note(context: dict, note: Any) -> None:
    """비차단 note를 컨텍스트에 남긴다(중복 없이, 200자 절단)."""
    text = _truncate(note)
    notes = context.setdefault("notes", [])
    if text and text not in notes:
        notes.append(text)


def total_transmissions(context: dict) -> int:
    """이 실행의 Gateway_Probe 전송 총량(ProbeBudget이 셈한 값 그대로)."""
    budget = context.get("budget")
    if not isinstance(budget, evidence_collector.ProbeBudget):
        return 0
    return int(budget.snapshot()["totalTransmissions"])


def ensure_baseline(context: dict) -> dict:
    """Baseline_Record 집합을 확보한다(stage 순서상 discovery보다 앞선다).

    이미 확보했으면 그대로 쓰고, 아니면 :func:`baseline_inspector.inspect`로 검사한다
    (파일 검사만 — Gateway 전송 없음). `evidenceEligible`이 거짓이면 Evidence_Collector가
    근거 집합에서 제외한다(Requirement 1.16).
    """
    state = context["state"]
    if state["baseline"] is None:
        state["baseline"] = baseline_inspector.inspect(
            context["root"], revision=context["revision"] or None
        )
        state["baselineSource"] = "inspected"
    return state["baseline"]


def ensure_collector(context: dict) -> Any:
    """이 실행 전용 :class:`evidence_collector.EvidenceCollector`를 만든다(한 번만).

    catalog 조회 경로·예산·baseline 적격성·environment identity·interpreter 경로를 모두
    주입하므로 collector가 이 실행 밖의 상태를 만들지 않는다.
    """
    if context.get("collector") is None:
        baseline = ensure_baseline(context)
        context["collector"] = evidence_collector.EvidenceCollector(
            gw=context.get("gw"),
            env_identity=context["environment"],
            revision=context["revision"],
            run_id=context.get("runId") or contracts.UNDETERMINED,
            budget=context["budget"],
            catalog_endpoint=context.get("catalogEndpoint") or contracts.UNDETERMINED,
            operator_export=context.get("operatorExport"),
            baseline=baseline,
            interpreter_path=str((context["interpreter"] or {}).get("interpreterPath") or ""),
            store_obj=context.get("store"),
        )
    return context["collector"]


def ensure_map(context: dict) -> dict:
    """Capability_Map을 확보한다(저장된 map을 읽고, 없으면 빈 map).

    저장 실패·손상은 :func:`capability_map.load`가 빈 map으로 처리하므로 이 함수는
    예외를 올리지 않는다. entry가 없는 Candidate_Label은 discovery 반영 시
    :meth:`EvidenceCollector.apply_discovery`가 초기 `UNVERIFIED` entry로 채운다.
    """
    state = context["state"]
    if state["map"] is None:
        store_obj = context.get("store")
        state["map"] = (
            capability_map.load(store_obj=store_obj)
            if store_obj is not None
            else capability_map.new_map()
        )
    return state["map"]


def ensure_discovery(context: dict) -> list[dict]:
    """Candidate_Label discovery 결과를 확보한다(catalog 조회 — 생성 전송 0건).

    catalog endpoint 부재·자격증명 부재·조회 실패는 모두 근거 부재로 기록되고 모든
    라벨이 `UNVERIFIED`로 남는다(probe 미생성 — Requirement 2.4, 2.12).
    """
    state = context["state"]
    if state["discovery"] is None:
        collector = ensure_collector(context)
        state["discovery"] = collector.discover(context["labels"])
        state["catalog"] = collector.fetch_catalog()
    return state["discovery"]


def catalog_result(context: dict) -> dict:
    """이 실행이 획득한 catalog 결과(없으면 discovery를 먼저 수행한다)."""
    ensure_discovery(context)
    result = context["state"].get("catalog")
    return result if isinstance(result, dict) else {}


def recorded_contract(entry: Any, field: str, route_key: str) -> Any:
    """Capability_Map entry가 보유한 route/effort 계약(없으면 ``None``)."""
    container = entry.get(field) if isinstance(entry, dict) else None
    if not isinstance(container, dict):
        return None
    sub_entry = container.get(route_key)
    contract = sub_entry.get("contract") if isinstance(sub_entry, dict) else None
    return contract if isinstance(contract, dict) else None


def align_declared_contract(
    declared: dict,
    recorded: Any,
    *,
    evidence_fields: Sequence[str],
) -> dict:
    """선언 계약의 **evidence가 채우는 자리**를 기록된 계약 값으로 맞춘다.

    Requirement 6.17·6.18이 말하는 "계약 변경"은 **선언 변경**이다. 반면
    `evidenceRef`처럼 evidence 기록이 채우는 자리는 선언이 정하지 않으므로, 그 차이를
    변경으로 취급하면 아무 것도 바뀌지 않았는데 entry를 `STALE`로 강등한다(모르는 것을
    불일치로 취급하지 않는다는 :func:`currency_ctx`의 원칙과 같다).

    맞추는 자리는 두 가지다.
      - ``evidence_fields`` — 선언이 아니라 evidence가 소유하는 자리(호출자 지정).
      - 선언이 미확정(``None``)으로 남긴 자리 — 성공 probe만이 확정할 수 있다
        (예: jobs route의 `modelIdRequired`·`modelIdFieldPath`).

    값은 전부 기록된 계약(= evidence)에서 복사한다. 이 함수는 값을 만들지 않는다.
    """
    out = copy.deepcopy(declared)
    if not isinstance(recorded, dict):
        return out
    for field in evidence_fields:
        if field in out and field in recorded:
            out[field] = copy.deepcopy(recorded[field])
    for field, value in list(out.items()):
        if value is None and recorded.get(field) is not None:
            out[field] = copy.deepcopy(recorded[field])
    return out


def declared_route_contracts(discovery: Any, entry: Any) -> dict:
    """discovery가 광고한 Known_Route의 현재 Route_Contract `{routeKey: 계약}`.

    광고 route는 catalog가 말해준 것(:func:`evidence_collector.advertised_routes`)이고
    계약 값은 기존 구현이 아는 것(:func:`evidence_collector.candidate_route_contract`)이다.
    이 파일은 endpoint·field path·bound를 만들지 않는다.
    """
    out: dict[str, Any] = {}
    for route in evidence_collector.advertised_routes(discovery):
        declared = evidence_collector.candidate_route_contract(route)
        if not isinstance(declared, dict):
            continue
        out[route] = align_declared_contract(
            declared,
            recorded_contract(entry, "routes", route),
            evidence_fields=ROUTE_CONTRACT_EVIDENCE_FIELDS,
        )
    return out


def declared_effort_contracts(discovery: Any, entry: Any) -> dict:
    """catalog가 선언한 effort의 현재 Effort_Contract `{routeKey: 계약}`.

    입력은 discovery 결과의 `modelId`와 `advertisedEffort`뿐이다
    (:func:`evidence_collector.candidate_effort_contract_for`) — field path·value type·
    허용값은 catalog 선언이 준 값 그대로이며 이 파일에 상수로 두지 않는다.
    """
    out: dict[str, Any] = {}
    for route in advertised_effort_routes(discovery):
        declared = evidence_collector.candidate_effort_contract_for(discovery, route)
        if not isinstance(declared, dict):
            continue
        out[route] = align_declared_contract(
            declared,
            recorded_contract(entry, "effort", route),
            evidence_fields=EFFORT_CONTRACT_EVIDENCE_FIELDS,
        )
    return out


def contract_currency(context: dict) -> tuple[dict, dict]:
    """`({modelId: {routeKey: Route_Contract}}, {modelId: {routeKey: Effort_Contract}})`.

    `capability_map.stale_trigger`가 Route_Contract 변경(6.17)·Effort_Contract 변경(6.18)을
    판정하는 데 쓰는 두 매핑이다. Exact_Model_ID를 확정한 discovery 결과만 열거하므로,
    catalog가 말하지 않은 model·route는 매핑에 들어가지 않고 비교 대상이 되지 않는다
    (모른다 ≠ 변경됐다).

    같은 Exact_Model_ID를 가리키는 라벨이 복수면 **먼저 발견된 라벨**의 entry 기록만
    쓴다(결정론적 선택).

    entry 기록은 :func:`ensure_map`이 확보한 **이 실행의 Capability_Map**에서 읽는다 —
    `apply_stale_triggers`가 순회하는 map과 같은 map이어야 evidence 자리 정렬
    (:func:`align_declared_contract`)이 일관되기 때문이다.
    """
    map_obj = ensure_map(context)
    routes: dict[str, Any] = {}
    efforts: dict[str, Any] = {}
    seen: set[str] = set()
    for result in context["state"].get("discovery") or []:
        if not isinstance(result, dict) or not result.get("found"):
            continue
        model_id = str(result.get("modelId") or "")
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        entry = (
            capability_map.find_entry(
                map_obj,
                candidate_label=str(result.get("candidateLabel") or ""),
                model_id=model_id,
            )
            if isinstance(map_obj, dict)
            else None
        )
        route_contracts = declared_route_contracts(result, entry)
        effort_contracts = declared_effort_contracts(result, entry)
        if route_contracts:
            routes[model_id] = route_contracts
        if effort_contracts:
            efforts[model_id] = effort_contracts
    return routes, efforts


def currency_ctx(context: dict) -> dict:
    """현재성 컨텍스트(`capability_map.promotion_blockers`·`activation_gate` 입력).

    catalog가 근거로 인정되지 않았으면 catalog 관련 키를 **넣지 않는다** — 모르는 것을
    불일치로 취급해 상태를 강등하지 않기 위해서다(값 추론 금지와 같은 원칙). 같은 원칙이
    STALE 전이 트리거 입력에도 적용된다: `routeContracts`·`effortContracts`는 catalog가
    근거로 인정됐고 그 catalog가 route·effort를 선언한 model에 대해서만 채운다
    (Requirement 6.17, 6.18).
    """
    catalog = context["state"].get("catalog")
    ctx: dict[str, Any] = {
        "revision": context["revision"],
        "environment": context["environment"],
        "nowUtc": contracts.utc_now_iso(),
    }
    if isinstance(catalog, dict) and catalog.get("available"):
        ctx["catalogFingerprint"] = catalog.get("catalogFingerprint")
        records = evidence_collector.catalog_records(catalog.get("snapshot"))
        model_ids = [
            evidence_collector.record_model_id(record)
            for record in records
            if evidence_collector.record_model_id(record)
        ]
        ctx["catalogModelIds"] = sorted(set(model_ids))
        ctx["catalogProviders"] = {
            evidence_collector.record_model_id(record): evidence_collector.record_provider(record)
            for record in records
            if evidence_collector.record_model_id(record)
        }
        route_contracts, effort_contracts = contract_currency(context)
        if route_contracts:
            ctx["routeContracts"] = route_contracts
        if effort_contracts:
            ctx["effortContracts"] = effort_contracts
    return ctx


def remember_records(context: dict, label: str, records: Iterable[dict]) -> str:
    """이 실행이 만든 Verification_Record를 상태에 남기고 저장한다.

    저장은 :meth:`EvidenceCollector.persist_record`에 위임하므로 정제 검증
    (credential·raw prompt·raw body 잔존 검사)을 그대로 통과해야 기록된다.

    Returns:
        마지막 Evidence_Record_ID(없으면 빈 문자열) — route·effort 결과의 evidence
        reference로 보고서에 기록한다.
    """
    state = context["state"]
    collector = context.get("collector")
    last = contracts.UNDETERMINED
    for record in records or []:
        if not isinstance(record, dict):
            continue
        evidence_id = str(record.get("evidenceRecordId") or "")
        state["records"].append(record)
        if evidence_id:
            last = evidence_id
            if evidence_id not in state["evidenceIds"]:
                state["evidenceIds"].append(evidence_id)
        if collector is not None and context.get("store") is not None:
            try:
                path = collector.persist_record(record)
                if evidence_id:
                    state["recordPaths"][evidence_id] = str(path)
            except Exception as exc:
                context_note(context, f"evidence-persist-error:{type(exc).__name__}:{exc}")
    if label:
        if last:
            state["evidenceRefs"][label] = last
        if label not in state["touchedLabels"]:
            state["touchedLabels"].append(label)
    return last


def save_map(context: dict) -> str | None:
    """Capability_Map을 `userData/capability/capability_map.json`에 원자적으로 기록한다."""
    store_obj = context.get("store")
    map_obj = context["state"].get("map")
    if store_obj is None or not isinstance(map_obj, dict):
        return None
    path = str(capability_map.save(map_obj, store_obj=store_obj))
    context["state"]["mapPath"] = path
    return path


def save_catalog_snapshot(context: dict) -> str | None:
    """정제 catalog snapshot을 `userData/capability/catalog/`에 기록한다(있을 때만)."""
    collector = context.get("collector")
    if collector is None or context.get("store") is None:
        return None
    try:
        path = collector.persist_catalog_snapshot(context["state"].get("catalog"))
    except Exception as exc:
        context_note(context, f"catalog-persist-error:{type(exc).__name__}:{exc}")
        return None
    if path is None:
        return None
    context["state"]["catalogPath"] = str(path)
    return str(path)


# ─────────────────────────────────────────────────────────────────
# 보고서 헤더 (Requirement 11.4, 11.5, 11.6)
# ─────────────────────────────────────────────────────────────────
def new_run_id(
    *,
    started_at: str,
    revision: str,
    interpreter: str,
    environment: dict,
    labels: Sequence[str],
    stage: str,
    dry_run: bool,
) -> str:
    """Validation_Run ID — 시작 시각 압축 표기 + 실행 입력의 결정론적 축약."""
    digest = hashlib.sha256(
        canonicalizer.canonical_bytes(
            [
                started_at,
                revision,
                interpreter,
                dict(environment),
                list(labels),
                stage,
                bool(dry_run),
            ]
        )
    ).hexdigest()
    return f"{compact_stamp(started_at)}-{digest[:12]}"


def report_header(
    *,
    labels: Sequence[str],
    stage: str,
    stages: Sequence[str],
    dry_run: bool,
    root: str | None = None,
    verdict: dict | None = None,
    started_at: str | None = None,
    env: dict | None = None,
    extra_notes: Sequence[str] | None = None,
    context: dict | None = None,
) -> dict:
    """Validation_Run 보고서 헤더를 만든다.

    필수 기록(Requirement 11.4~11.6과 design.md "보고서 헤더"):
      - `revision` — Current_Revision(git HEAD). 구할 수 없으면 미확정 + note.
      - `interpreterPath` — 사용한 interpreter **absolute path**.
      - `startedAt` — 시작 시각 UTC ISO 8601.
      - `environment` — Same_Gateway_Environment identity.
      - `pbtSeed` — PBT 재현 seed(`_capability_strategies.AE_PBT_SEED`).

    `context`(:func:`new_run_context` 결과)를 주면 그 값을 그대로 쓴다 — revision·
    environment·seed·note를 두 번 계산하지 않고 GatewayClient도 하나로 유지한다.
    """
    root_abs = _abspath(root or repo_root())
    guard = verdict if isinstance(verdict, dict) else interpreter_verdict(root_abs)

    if isinstance(context, dict):
        root_abs = _abspath(context.get("root") or root_abs)
        guard = context.get("interpreter") if isinstance(context.get("interpreter"), dict) else guard
        start = context.get("startedAt") or started_at or contracts.utc_now_iso()
        notes = list(context.get("notes") or [])
        for note in extra_notes or []:  # context 밖에서 붙인 note도 잃지 않는다.
            if str(note) not in notes:
                notes.append(str(note))
        revision = context.get("revision") or contracts.UNDETERMINED
        environment = context.get("environment") or {}
        seed = context.get("pbtSeed")
        return _finish_header(
            header_base(
                guard=guard,
                start=start,
                revision=revision,
                environment=environment,
                seed=seed,
                root_abs=root_abs,
                dry_run=dry_run,
                stage=stage,
                stages=stages,
                labels=labels,
                notes=notes,
            ),
            labels=labels,
            stage=stage,
            dry_run=dry_run,
        )

    start = started_at or contracts.utc_now_iso()
    notes: list[str] = [str(note) for note in (extra_notes or [])]

    revision = baseline_inspector.current_revision(root_abs)
    if not revision:
        notes.append(NOTE_REVISION_UNAVAILABLE)
        revision = contracts.UNDETERMINED

    environment, env_notes = gateway_environment(env)
    notes.extend(env_notes)

    seed, seed_note = pbt_seed()
    if seed_note:
        notes.append(seed_note)

    return _finish_header(
        header_base(
            guard=guard,
            start=start,
            revision=revision,
            environment=environment,
            seed=seed,
            root_abs=root_abs,
            dry_run=dry_run,
            stage=stage,
            stages=stages,
            labels=labels,
            notes=notes,
        ),
        labels=labels,
        stage=stage,
        dry_run=dry_run,
    )


def header_base(
    *,
    guard: dict,
    start: str,
    revision: str,
    environment: dict,
    seed: int | None,
    root_abs: str,
    dry_run: bool,
    stage: str,
    stages: Sequence[str],
    labels: Sequence[str],
    notes: Sequence[str],
) -> dict:
    """헤더 필드 구성(단일 정의 — context 경로와 직접 계산 경로가 같은 필드를 만든다)."""
    return {
        "schemaVersion": contracts.SCHEMA_VERSION,
        "revision": revision,
        "interpreterPath": guard.get("interpreterPath"),
        "expectedInterpreterPath": guard.get("expectedInterpreterPath"),
        "startedAt": start,
        "environment": environment,
        "pbtSeed": seed,
        "repoRoot": root_abs,
        "dryRun": bool(dry_run),
        "requestedStage": stage,
        "stages": list(stages),
        "candidateLabels": list(labels),
        "notes": [str(note) for note in notes],
    }


def _finish_header(header: dict, *, labels: Sequence[str], stage: str, dry_run: bool) -> dict:
    """헤더에 Validation_Run ID를 채운다(입력의 결정론적 축약)."""
    header["runId"] = new_run_id(
        started_at=str(header["startedAt"]),
        revision=str(header["revision"]),
        interpreter=str(header["interpreterPath"]),
        environment=header["environment"] if isinstance(header["environment"], dict) else {},
        labels=labels,
        stage=stage,
        dry_run=dry_run,
    )
    return header


# ─────────────────────────────────────────────────────────────────
# 보고서 본문 — 절별 투영기
#
# 모든 투영기는 백엔드가 만든 결과를 **읽기만** 한다. 상태 판정·계약 확정·승격은 전부
# evidence_collector·capability_map·activation_gate가 하고, 여기서는 그 결과를 보고서
# 필드로 옮긴다. raw prompt·raw body·credential은 어떤 절에도 들어가지 않는다 —
# probe별로 Probe_ID와 Sanitized_Schema만 옮긴다(Requirement 10.13, 10.14, 11.12).
# ─────────────────────────────────────────────────────────────────
def baseline_section(baseline: dict, *, source: Any = None, path: Any = None) -> dict:
    """10개 Baseline_Category 검사 결과(Requirement 1.11, 1.12, 1.16)."""
    summary = baseline.get("categorySummary") or {}
    eligibility = baseline.get("categoryEligibility") or {}
    return {
        "revision": baseline.get("revision") or contracts.UNDETERMINED,
        "inspectedAt": baseline.get("inspectedAt"),
        "evidenceEligible": bool(baseline.get("evidenceEligible")),
        "evidenceEligibleReason": baseline.get("evidenceEligibleReason"),
        "categoryCount": len(baseline.get("categories") or []),
        "recordCount": len(baseline.get("records") or []),
        "categories": [
            {
                "category": category,
                "recordCount": (summary.get(category) or {}).get("recordCount", 0),
                "foundCount": (summary.get(category) or {}).get("foundCount", 0),
                "missingCount": (summary.get(category) or {}).get("missingCount", 0),
                "eligible": bool(eligibility.get(category)),
                "missingSymbols": list((summary.get(category) or {}).get("missingSymbols") or []),
            }
            for category in (baseline.get("categories") or [])
        ],
        "missingCategories": list(baseline.get("missingCategories") or []),
        "unresolvedCategories": list(baseline.get("unresolvedCategories") or []),
        "source": source,
        "path": None if path is None else str(path),
    }


def catalog_section(catalog: dict, *, path: Any = None) -> dict:
    """catalog 획득 결과(Gateway_Catalog 또는 검증된 Operator_Catalog_Export)."""
    return {
        "available": bool(catalog.get("available")),
        "sourceKind": catalog.get("sourceKind"),
        "catalogFingerprint": catalog.get("catalogFingerprint") or contracts.UNDETERMINED,
        "recordCount": int(catalog.get("records") or 0),
        "environment": catalog.get("environment"),
        "reasons": list(catalog.get("reasons") or []),
        "notes": list(catalog.get("notes") or []),
        "path": None if path is None else str(path),
    }


#: 투영기의 `evidence_ref` 기본값은 리터럴 ``""``다(= `contracts.UNDETERMINED`).
#: capability 모듈은 interpreter guard 뒤에 지연 import되므로 module scope(기본 인자)에서
#: `contracts.*`를 평가할 수 없다.
def discovery_view(result: dict, *, evidence_ref: str = "") -> dict:
    """Candidate_Label 하나의 discovery 결과(Requirement 11.7, 11.8)."""
    return {
        "candidateLabel": result.get("candidateLabel"),
        "found": bool(result.get("found")),
        "modelId": result.get("modelId") or contracts.UNDETERMINED,
        # catalog가 명시한 전송 ID(probe가 첫 전송에 쓰는 값). 미확정이면 Exact_Model_ID를
        # 그대로 보낸다 — 이 자리에 접두사를 만들어 넣지 않는다(Requirement 2.17).
        "invocationModelId": result.get("invocationModelId") or contracts.UNDETERMINED,
        "provider": result.get("provider") or contracts.UNDETERMINED,
        "sourceKind": result.get("sourceKind"),
        "catalogFingerprint": result.get("catalogFingerprint") or contracts.UNDETERMINED,
        "advertisedRoutes": list(result.get("advertisedRoutes") or []),
        "advertisedEffortRoutes": advertised_effort_routes(result),
        "verificationStatus": result.get("verificationStatus"),
        "probeEligible": bool(result.get("probeEligible")),
        "matchKind": result.get("matchKind"),
        "recordSchema": result.get("recordSchema") or {},
        "evidenceRef": evidence_ref or contracts.UNDETERMINED,
        "reasons": list(result.get("reasons") or []),
        "notes": list(result.get("notes") or []),
    }


def advertised_effort_routes(result: Any) -> list[str]:
    """discovery가 읽은 catalog effort 선언의 route 목록(선언이 없으면 빈 목록)."""
    declaration = result.get("advertisedEffort") if isinstance(result, dict) else None
    if not isinstance(declaration, dict):
        return []
    return [route for route in contracts.KNOWN_ROUTES if route in declaration]


def route_view(label: str, result: dict, *, evidence_ref: str = "") -> dict:
    """route probe 결과 하나(Requirement 11.9, 11.12, 11.17~11.20)."""
    return {
        "candidateLabel": label,
        "modelId": result.get("modelId") or contracts.UNDETERMINED,
        "invocationModelId": result.get("invocationModelId") or contracts.UNDETERMINED,
        "routeKey": result.get("routeKey"),
        "advertised": bool(result.get("advertised")),
        "status": result.get("status"),
        "allowlist": result.get("allowlist"),
        "http": bool(result.get("http")),
        "validOutput": bool(result.get("validOutput")),
        "terminalSuccess": bool(result.get("terminalSuccess")),
        "correctionUsed": bool(result.get("correctionUsed")),
        "transmissions": int(result.get("transmissions") or 0),
        "productionPath": bool(result.get("productionPath")),
        "evidenceEligible": bool(result.get("evidenceEligible")),
        "stateBearing": bool(result.get("stateBearing")),
        "category": result.get("category"),
        "reason": result.get("reason"),
        "probeId": result.get("probeId"),
        "sanitizedSchema": result.get("sanitizedSchema") or {},
        "contractConfirmed": isinstance(result.get("contract"), dict),
        "usage": result.get("usage", contracts.NOT_PROVIDED),
        "cost": result.get("cost", contracts.NOT_PROVIDED),
        "evidenceRef": evidence_ref or contracts.UNDETERMINED,
        "notes": list(result.get("notes") or []),
    }


def effort_value_view(result: dict) -> dict:
    """effort value 하나의 probe 결과(실제 field path·실제 value는 관측값이다)."""
    return {
        "value": result.get("value"),
        "fieldPath": result.get("fieldPath"),
        "status": result.get("status"),
        "transmitted": bool(result.get("transmitted")),
        "http": bool(result.get("http")),
        "validOutput": bool(result.get("validOutput")),
        "terminalSuccess": bool(result.get("terminalSuccess")),
        "category": result.get("category"),
        "rejection": result.get("rejection"),
        "reason": result.get("reason"),
        "probeId": result.get("probeId"),
        "sanitizedSchema": result.get("sanitizedSchema") or {},
        "usage": result.get("usage", contracts.NOT_PROVIDED),
        "cost": result.get("cost", contracts.NOT_PROVIDED),
    }


def effort_view(label: str, summary: dict, *, evidence_ref: str = "") -> dict:
    """model·route별 effort probe summary(Requirement 11.10, 11.12)."""
    return {
        "candidateLabel": label,
        "modelId": summary.get("modelId") or contracts.UNDETERMINED,
        "routeKey": summary.get("routeKey"),
        "status": summary.get("status"),
        "fieldPath": summary.get("fieldPath"),
        "valueType": summary.get("valueType"),
        "domainKind": summary.get("domainKind"),
        "requiredValues": list(summary.get("requiredValues") or []),
        "verifiedValues": list(summary.get("verifiedValues") or []),
        "baselineSucceeded": bool(summary.get("baselineSucceeded")),
        "baselineSource": summary.get("baselineSource"),
        "baseRouteStatus": summary.get("baseRouteStatus"),
        "contractConfirmed": isinstance(summary.get("contract"), dict),
        "contractMissing": list(summary.get("contractMissing") or []),
        "transmissions": int(summary.get("transmissions") or 0),
        "productionPath": bool(summary.get("productionPath")),
        "evidenceEligible": bool(summary.get("evidenceEligible")),
        "reason": summary.get("reason"),
        "probeId": summary.get("probeId"),
        "evidenceRef": evidence_ref or contracts.UNDETERMINED,
        "notes": list(summary.get("notes") or []),
        "values": [
            effort_value_view(item)
            for item in (summary.get("results") or [])
            if isinstance(item, dict)
        ],
    }


def probe_views(context: dict) -> list[dict]:
    """probe별 Probe_ID·Sanitized_Schema 목록(raw prompt·raw body 없음 — 11.12).

    실제로 전송이 발생한 probe만 담는다. 전송 0건 결과(`NOT_ADVERTISED`·계약 결손·예산
    소진)는 probe가 아니므로 상태 절(`routes`·`effort`)에만 남는다.
    """
    state = context["state"]
    out: list[dict] = []
    for label, results in state["routeResults"].items():
        for item in results:
            if not isinstance(item, dict) or not int(item.get("transmissions") or 0):
                continue
            out.append(
                {
                    "probeId": item.get("probeId"),
                    "kind": PROBE_KIND_ROUTE,
                    "candidateLabel": label,
                    "modelId": item.get("modelId") or contracts.UNDETERMINED,
                    "routeKey": item.get("routeKey"),
                    "transmissions": int(item.get("transmissions") or 0),
                    "sanitizedSchema": item.get("sanitizedSchema") or {},
                }
            )
    for label, summaries in state["effortResults"].items():
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            for item in summary.get("results") or []:
                if not isinstance(item, dict) or not item.get("transmitted"):
                    continue
                out.append(
                    {
                        "probeId": item.get("probeId"),
                        "kind": PROBE_KIND_EFFORT_VALUE,
                        "candidateLabel": label,
                        "modelId": summary.get("modelId") or contracts.UNDETERMINED,
                        "routeKey": summary.get("routeKey"),
                        "transmissions": 1,
                        "sanitizedSchema": item.get("sanitizedSchema") or {},
                    }
                )
    return out


def evidence_views(context: dict) -> list[dict]:
    """이 실행이 만든 Verification_Record 요약(usage·cost는 Gateway 제공값만)."""
    state = context["state"]
    out: list[dict] = []
    for record in state["records"]:
        evidence_id = str(record.get("evidenceRecordId") or "")
        out.append(
            {
                "evidenceRecordId": evidence_id,
                "runId": record.get("runId"),
                "candidateLabel": record.get("candidateLabel"),
                "modelId": record.get("modelId") or contracts.UNDETERMINED,
                "provider": record.get("provider") or contracts.UNDETERMINED,
                "revision": record.get("revision"),
                "verifiedAt": record.get("verifiedAt"),
                "catalogFingerprint": record.get("catalogFingerprint"),
                "capabilityFingerprint": record.get("capabilityFingerprint"),
                "invocationModelIds": list(record.get("invocationModelIds") or []),
                "routeResultCount": len(record.get("routeResults") or []),
                "effortResultCount": len(record.get("effortResults") or []),
                "routeCompleteness": bool(record.get("routeCompleteness")),
                "usage": record.get("usage", contracts.NOT_PROVIDED),
                "cost": record.get("cost", contracts.NOT_PROVIDED),
                "path": state["recordPaths"].get(evidence_id),
            }
        )
    return out


def model_views(context: dict) -> dict:
    """Capability_Map entry별 Verification_Status·fingerprint·route·effort 상태(11.11).

    Malformed_Entry는 별도 목록으로 분리해 activation 입력에서 제외된 사실을 남긴다.
    """
    map_obj = context["state"].get("map")
    if not isinstance(map_obj, dict):
        return {"entries": [], "malformed": [], "updatedAt": None}

    hints = evidence_collector.route_mode_hints()
    valid, malformed = capability_map.valid_entries(map_obj)
    entries: list[dict] = []
    for entry in valid:
        routes = entry.get("routes") if isinstance(entry.get("routes"), dict) else {}
        effort = entry.get("effort") if isinstance(entry.get("effort"), dict) else {}
        recomputed = canonicalizer.capability_fingerprint(entry)
        entries.append(
            {
                "candidateLabel": entry.get("candidateLabel"),
                "modelId": entry.get("modelId") or contracts.UNDETERMINED,
                "provider": entry.get("provider") or contracts.UNDETERMINED,
                "sourceKind": entry.get("sourceKind"),
                "verificationStatus": entry.get("verificationStatus"),
                "staleReason": entry.get("staleReason"),
                "capabilityFingerprint": entry.get("capabilityFingerprint"),
                "recomputedFingerprint": recomputed,
                "fingerprintMatches": entry.get("capabilityFingerprint") == recomputed,
                "catalogFingerprint": entry.get("catalogFingerprint"),
                "revision": entry.get("revision"),
                "verifiedAt": entry.get("verifiedAt"),
                "evidence": list(entry.get("evidence") or []),
                "activeEvidenceRef": entry.get("activeEvidenceRef"),
                "invocationModelIds": list(entry.get("invocationModelIds") or []),
                "syncSupport": entry.get("syncSupport"),
                "asyncSupport": entry.get("asyncSupport"),
                "streamingSupport": entry.get("streamingSupport"),
                "completeRecord": capability_map.is_complete_record(entry, mode_hints=hints),
                "supportedRoutes": capability_map.supported_routes(entry),
                "routes": {
                    route: {
                        "status": (routes.get(route) or {}).get("status"),
                        "allowlist": (routes.get(route) or {}).get("allowlist"),
                        "evidenceRef": (routes.get(route) or {}).get("evidenceRef"),
                        "contractPresent": isinstance((routes.get(route) or {}).get("contract"), dict),
                    }
                    for route in contracts.KNOWN_ROUTES
                },
                "effort": {
                    route: {
                        "status": (effort.get(route) or {}).get("status"),
                        "evidenceRef": (effort.get(route) or {}).get("evidenceRef"),
                        "contractPresent": isinstance((effort.get(route) or {}).get("contract"), dict),
                        "verifiedValues": list(
                            ((effort.get(route) or {}).get("contract") or {}).get("verifiedValues") or []
                        ),
                    }
                    for route in contracts.KNOWN_ROUTES
                },
            }
        )

    return {
        "updatedAt": map_obj.get("updatedAt"),
        "entryCount": len(capability_map.entries_of(map_obj)),
        "entries": entries,
        "malformed": [
            {
                "candidateLabel": entry.get("candidateLabel") if isinstance(entry, dict) else None,
                "modelId": (entry.get("modelId") if isinstance(entry, dict) else None)
                or contracts.UNDETERMINED,
                "reasons": capability_map.entry_malformed_reasons(entry)[:8],
            }
            for entry in malformed
        ],
    }


# ─────────────────────────────────────────────────────────────────
# 예산 재확인 (Requirement 11.15, 11.16 — runner 수준 재확인)
# ─────────────────────────────────────────────────────────────────
def budget_reverification(context: dict) -> dict:
    """ProbeBudget 카운터를 다시 읽어 조합별 한도 준수를 재확인한다.

    한도는 :class:`evidence_collector.ProbeBudget`이 강제하는 값이며, 이 함수는 실행이
    끝난 뒤 **관측된 카운터**를 그 한도와 다시 비교한다(runner 수준 재확인). 위반이
    하나라도 있으면 `reverified: False`와 위반 목록을 보고서에 남긴다.
    """
    budget = context.get("budget")
    if not isinstance(budget, evidence_collector.ProbeBudget):
        return {
            "reverified": False,
            "violations": [{"scope": "budget", "reason": "BUDGET_UNAVAILABLE"}],
            "snapshot": {},
            "totalTransmissions": 0,
        }

    snapshot = budget.snapshot()
    limits = (
        ("successes", "maxSuccesses"),
        ("corrections", "maxCorrections"),
        ("prefixCorrections", "maxPrefixCorrections"),
        ("transmissions", "transmissionLimit"),
    )
    violations: list[dict] = []
    for scope, limit_key in limits:
        limit = int(snapshot.get(limit_key) or 0)
        for key, count in (snapshot.get(scope) or {}).items():
            if int(count) > limit:
                violations.append(
                    {
                        "scope": scope,
                        "combination": key,
                        "count": int(count),
                        "limit": limit,
                        "axes": list(COMBINATION_AXES),
                    }
                )
    return {
        "reverified": not violations,
        "violations": violations,
        "snapshot": snapshot,
        "totalTransmissions": int(snapshot.get("totalTransmissions") or 0),
        "maxSuccessesPerCombination": int(snapshot.get("maxSuccesses") or 0),
        "maxCorrectionsPerCombination": int(snapshot.get("maxCorrections") or 0),
        "maxPrefixCorrectionsPerRoute": int(snapshot.get("maxPrefixCorrections") or 0),
    }


# ─────────────────────────────────────────────────────────────────
# 미완료 조합의 `UNVERIFIED` 주장 (Requirement 11.24, 12.23, 12.24)
# ─────────────────────────────────────────────────────────────────
def unverified_claims(context: dict) -> list[dict]:
    """integration probe가 완료되지 않은 조합을 `UNVERIFIED`로 보고한다.

    Gateway 지원 주장은 production path integration probe로만 확정되므로
    (Requirement 12.22, 12.23), 다음은 모두 `UNVERIFIED`로 남는다.

      - Exact_Model_ID를 찾지 못한 Candidate_Label(model 주장)
      - 광고된 Known_Route 중 production probe가 `SUPPORTED`를 확정하지 못한 조합
      - catalog가 effort를 선언했지만 effort probe가 `SUPPORTED`를 확정하지 못한 조합
    """
    state = context["state"]
    discovery = state.get("discovery")
    if not isinstance(discovery, list):
        return []

    unverified = str(contracts.Verification_Status.UNVERIFIED)
    out: list[dict] = []
    for result in discovery:
        label = str(result.get("candidateLabel") or "")
        model_id = str(result.get("modelId") or "")
        if not result.get("found") or not model_id:
            reasons = list(result.get("reasons") or [])
            out.append(
                {
                    "kind": CLAIM_MODEL,
                    "candidateLabel": label,
                    "modelId": contracts.UNDETERMINED,
                    "routeKey": None,
                    "status": unverified,
                    "reason": reasons[0] if reasons else CLAIM_PROBE_INCOMPLETE,
                }
            )
            continue

        routes = {
            str(item.get("routeKey")): item
            for item in (state["routeResults"].get(label) or [])
            if isinstance(item, dict)
        }
        for route in result.get("advertisedRoutes") or []:
            item = routes.get(str(route))
            confirmed = bool(
                isinstance(item, dict)
                and item.get("productionPath")
                and item.get("evidenceEligible")
                and item.get("status") == str(contracts.Route_Support_Status.SUPPORTED)
            )
            if confirmed:
                continue
            out.append(
                {
                    "kind": CLAIM_ROUTE,
                    "candidateLabel": label,
                    "modelId": model_id,
                    "routeKey": str(route),
                    "status": str(contracts.Route_Support_Status.UNVERIFIED),
                    "reason": (item or {}).get("reason") or CLAIM_PROBE_INCOMPLETE,
                }
            )

        efforts = {
            str(item.get("routeKey")): item
            for item in (state["effortResults"].get(label) or [])
            if isinstance(item, dict)
        }
        for route in advertised_effort_routes(result):
            item = efforts.get(route)
            confirmed = bool(
                isinstance(item, dict)
                and item.get("productionPath")
                and item.get("evidenceEligible")
                and item.get("status") == str(contracts.Effort_Support_Status.SUPPORTED)
            )
            if confirmed:
                continue
            out.append(
                {
                    "kind": CLAIM_EFFORT,
                    "candidateLabel": label,
                    "modelId": model_id,
                    "routeKey": route,
                    "status": str(contracts.Effort_Support_Status.UNVERIFIED),
                    "reason": (item or {}).get("reason") or CLAIM_PROBE_INCOMPLETE,
                }
            )
    return out


# ─────────────────────────────────────────────────────────────────
# activation 결과 (Requirement 11.21~11.24)
# ─────────────────────────────────────────────────────────────────
def run_evidence_reasons(entry: dict, context: dict) -> list[str]:
    """이 실행이 만들거나 바꾼 Active_Model의 일치 검사 실패 이유 목록.

    검사 항목(전부 AND):
      - **Current_Evidence** — entry의 evidence reference가 이 Validation_Run이 만든
        Evidence_Record_ID를 가리킨다(11.21).
      - **revision** — entry의 revision이 Current_Revision과 같다(11.22).
      - **Capability_Fingerprint** — 저장값이 재계산값과 같다(11.23).

    빈 목록이 아니면 호출자는 그 entry를 activation 결과에서 제외한다(11.24).
    """
    reasons: list[str] = []
    run_ids = set(context["state"]["evidenceIds"])
    refs = {str(item) for item in (entry.get("evidence") or []) if isinstance(item, str)}
    active_ref = str(entry.get("activeEvidenceRef") or "")
    if active_ref:
        refs.add(active_ref)
    if not (refs & run_ids):
        reasons.append(RUN_EVIDENCE_MISMATCH)

    revision = str(context.get("revision") or "")
    if revision and str(entry.get("revision") or "") != revision:
        reasons.append(RUN_REVISION_MISMATCH)

    if str(entry.get("capabilityFingerprint") or "") != canonicalizer.capability_fingerprint(entry):
        reasons.append(RUN_FINGERPRINT_MISMATCH)
    return reasons


def touched_by_run(entry: dict, context: dict) -> bool:
    """이 실행이 record를 만든(신규·변경) entry인지 — 일치 검사 대상 판정."""
    label = str(entry.get("candidateLabel") or "")
    if label and label in context["state"]["touchedLabels"]:
        return True
    model_id = str(entry.get("modelId") or "")
    return bool(
        model_id
        and any(
            str(record.get("modelId") or "") == model_id
            for record in context["state"]["records"]
        )
    )


def activation_section(context: dict) -> dict:
    """Active_Model 목록·탈락 이유·미완료 주장·Managed_Segment 노출 집합.

    Active_Model 판정은 :func:`activation_gate.activation_report`가 하고, 이 함수는 그
    결과에 **이 실행의 evidence 일치 검사**를 더한다(Requirement 11.21~11.24).
    """
    map_obj = context["state"].get("map")
    if not isinstance(map_obj, dict):
        return {
            "active": [],
            "excluded": [],
            "counts": {"entries": 0, "active": 0, "excluded": 0, "malformed": 0},
            "evidenceChecks": [],
            "managedSegment": {"modelIds": [], "count": 0},
            "unverified": unverified_claims(context),
        }

    hints = evidence_collector.route_mode_hints()
    report = activation_gate.activation_report(map_obj, currency_ctx(context), mode_hints=hints)

    active: list[dict] = []
    excluded = list(report.get("excluded") or [])
    checks: list[dict] = []
    for entry in report.get("active") or []:
        if not touched_by_run(entry, context):
            active.append(entry)
            continue
        reasons = run_evidence_reasons(entry, context)
        checks.append(
            {
                "candidateLabel": entry.get("candidateLabel"),
                "modelId": entry.get("modelId") or contracts.UNDETERMINED,
                "capabilityFingerprint": entry.get("capabilityFingerprint"),
                "revision": entry.get("revision"),
                "ok": not reasons,
                "reasons": reasons,
            }
        )
        if reasons:
            excluded.append(
                {
                    "candidateLabel": entry.get("candidateLabel"),
                    "modelId": entry.get("modelId") or contracts.UNDETERMINED,
                    "provider": entry.get("provider") or contracts.UNDETERMINED,
                    "capabilityFingerprint": entry.get("capabilityFingerprint"),
                    "verifiedAt": entry.get("verifiedAt"),
                    "revision": entry.get("revision"),
                    "sourceKind": entry.get("sourceKind"),
                    "verificationStatus": entry.get("verificationStatus"),
                    "reason": reasons[0],
                }
            )
            continue
        active.append(entry)

    payload = capability_map.to_ui_payload(active)
    counts = dict(report.get("counts") or {})
    counts["active"] = len(active)
    counts["excluded"] = len(excluded)
    return {
        "active": [
            {
                "candidateLabel": entry.get("candidateLabel"),
                "modelId": entry.get("modelId") or contracts.UNDETERMINED,
                "provider": entry.get("provider") or contracts.UNDETERMINED,
                "capabilityFingerprint": entry.get("capabilityFingerprint"),
                "verificationStatus": entry.get("verificationStatus"),
                "verifiedAt": entry.get("verifiedAt"),
                "revision": entry.get("revision"),
                "supportedRoutes": capability_map.supported_routes(entry),
                "eligibleRoutes": activation_gate.eligible_route_keys(
                    entry, ctx=currency_ctx(context)
                ),
            }
            for entry in active
        ],
        "excluded": excluded,
        "counts": counts,
        "evidenceChecks": checks,
        # Managed_Segment 노출 집합에는 Exact_Model_ID만 들어간다(Candidate_Label 미노출).
        "managedSegment": {
            "modelIds": list(payload.get("modelIds") or []),
            "count": len(payload.get("modelIds") or []),
        },
        "unverified": unverified_claims(context),
    }


# ─────────────────────────────────────────────────────────────────
# PBT seed와 counterexample (Requirement 12.19, 12.20, 12.21)
# ─────────────────────────────────────────────────────────────────
def property_test_files(root: str | None = None) -> list[str]:
    """이 기능의 property test 파일 목록(Correctness Properties 1~10).

    design.md의 property ↔ 테스트 파일 매핑을 이 파일에 복제하지 않는다. 판별 기준은
    두 가지 구조적 신호뿐이다.

      - 파일명이 `test_*` + :data:`PBT_FILE_SUFFIX`
      - 이 기능의 공통 생성기 모듈(:data:`PBT_STRATEGY_MODULE`)을 참조

    두 번째 조건이 저장소의 다른 기능 PBT를 걸러낸다(테스트 파일 목록을 상수로 두지
    않으므로 property가 늘어도 runner를 고치지 않는다).
    """
    directory = os.path.join(_abspath(root or repo_root()), "scripts")
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []

    out: list[str] = []
    for name in names:
        if not name.startswith("test_") or not name.endswith(PBT_FILE_SUFFIX):
            continue
        try:
            with open(os.path.join(directory, name), "r", encoding="utf-8") as handle:
                source = handle.read(PBT_SCAN_MAX_BYTES)
        except OSError:
            continue
        if PBT_STRATEGY_MODULE in source:
            out.append(os.path.join("scripts", name))
    return out


def parse_counterexamples(output: str) -> list[dict]:
    """Hypothesis 출력에서 최소화 counterexample과 재현 blob을 뽑는다(Requirement 12.21).

    Hypothesis는 최소화된 예시를 `Falsifying example: test_x(` 뒤 여러 줄에 걸쳐 출력하고,
    재현 blob(`@reproduce_failure(...)`)을 같은 실패 블록 안에 덧붙인다. 두 값을 짝지어
    보고서에 남긴다(값은 property 생성기가 만든 무작위 심볼이므로 비민감하다).
    """
    lines = (output or "").splitlines()
    out: list[dict] = []
    index = 0
    while index < len(lines) and len(out) < PBT_MAX_COUNTEREXAMPLES:
        match = _PBT_FALSIFYING.match(lines[index])
        index += 1
        if not match:
            continue

        parts = [match.group("text").strip()]
        while index < len(lines) and len(parts) <= PBT_EXAMPLE_MAX_LINES:
            piece = _PBT_MARKER.sub("", lines[index]).strip()
            if not piece:
                break
            index += 1
            parts.append(piece)
            if piece.startswith(")"):
                break

        blob: str | None = None
        for probe in range(index, min(index + PBT_BLOB_SCAN_LINES, len(lines))):
            found = _PBT_REPRODUCE.search(lines[probe])
            if found:
                blob = _truncate(found.group(0))
                break
        out.append(
            {
                "minimizedExample": _truncate(" ".join(part for part in parts if part)),
                "reproduceBlob": blob,
            }
        )
    return out


def run_property_tests(context: dict) -> dict:
    """property test를 이 실행의 interpreter로 수행하고 결과를 요약한다(옵트인).

    :data:`ENV_PBT_RUN`이 켜졌을 때만 수행한다. 실행은 guard가 확인한
    `ai_engine/.venv/bin/python`으로만 하고(모든 Python 실행 규약), Gateway로는 아무
    것도 전송하지 않는다(property test는 외부 Gateway를 호출하지 않는다 —
    Requirement 12.22).
    """
    files = property_test_files(context.get("root"))
    if not files:
        return {"executed": False, "reason": "NO_PROPERTY_TEST_FILE", "files": []}

    interpreter = str((context.get("interpreter") or {}).get("interpreterPath") or "")
    command = [interpreter, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files]
    try:
        completed = subprocess.run(
            command,
            cwd=context.get("root"),
            capture_output=True,
            text=True,
            timeout=PBT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"executed": True, "reason": "TIMEOUT", "files": files, "passed": False,
                "counterexamples": []}
    except Exception as exc:
        return {
            "executed": False,
            "reason": _truncate(f"PBT_RUN_ERROR:{type(exc).__name__}:{exc}"),
            "files": files,
        }

    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    return {
        "executed": True,
        "reason": REASON_OK if completed.returncode == 0 else "PROPERTY_FAILED",
        "files": files,
        "exitCode": completed.returncode,
        "passed": completed.returncode == 0,
        "counterexamples": parse_counterexamples(output),
    }


def pbt_section(context: dict) -> dict:
    """PBT 실행 seed(항상)와 실패 시 최소화 counterexample(수행했을 때)."""
    seed = context.get("pbtSeed")
    section: dict[str, Any] = {
        "seed": seed,
        "seedEnvVar": "AE_PBT_SEED",
        "seedSourceRef": "scripts/_capability_strategies.py::AE_PBT_SEED",
        "executed": False,
        "reason": None,
        "counterexamples": [],
        "files": property_test_files(context.get("root")),
    }
    if context.get("dryRun"):
        section["reason"] = REASON_DRY_RUN
        return section
    if str((context.get("env") or {}).get(ENV_PBT_RUN) or "").strip().lower() not in ("1", "true", "yes"):
        section["reason"] = "NOT_REQUESTED"
        return section
    section.update(run_property_tests(context))
    return section


# ─────────────────────────────────────────────────────────────────
# stage 실행
# ─────────────────────────────────────────────────────────────────
def stage_baseline(context: dict, body: dict) -> dict:
    """baseline 검사 stage — 10개 Baseline_Category record 생성·기록(전송 0건)."""
    baseline = ensure_baseline(context)
    state = context["state"]
    reason = REASON_OK
    status = STAGE_COMPLETED

    store_obj = context.get("store")
    if store_obj is None:
        reason = REASON_STORE_UNAVAILABLE
    else:
        try:
            state["baselinePath"] = str(
                store_obj.write_baseline(baseline.get("revision") or contracts.UNDETERMINED, baseline)
            )
        except Exception as exc:
            status, reason = STAGE_FAILED, REASON_STORE_ERROR
            context_note(context, f"baseline-persist-error:{type(exc).__name__}:{exc}")

    body["baseline"] = baseline_section(
        baseline, source=state.get("baselineSource"), path=state.get("baselinePath")
    )
    if status == STAGE_COMPLETED and not baseline.get("evidenceEligible"):
        reason = REASON_BASELINE_INELIGIBLE
    return {"status": status, "reason": reason}


def stage_discover(context: dict, body: dict) -> dict:
    """catalog discovery stage — 라벨별 Exact_Model_ID·Provider_String 확인(전송 0건)."""
    collector = ensure_collector(context)
    results = ensure_discovery(context)
    state = context["state"]
    catalog = catalog_result(context)

    map_obj = ensure_map(context)
    map_obj, records = collector.apply_discovery(
        map_obj,
        results,
        ctx=currency_ctx(context),
        mode_hints=evidence_collector.route_mode_hints(),
    )
    state["map"] = map_obj

    by_label: dict[str, str] = {}
    for record in records:
        label = str(record.get("candidateLabel") or "")
        by_label[label] = remember_records(context, label, [record])
    state["discoveryRefs"].update(by_label)

    save_catalog_snapshot(context)
    persist_failed = False
    try:
        save_map(context)
    except Exception as exc:
        context_note(context, f"map-persist-error:{type(exc).__name__}:{exc}")
        persist_failed = True

    body["catalog"] = catalog_section(catalog, path=state.get("catalogPath"))
    body["discovery"] = [
        discovery_view(item, evidence_ref=by_label.get(str(item.get("candidateLabel") or ""), ""))
        for item in results
    ]
    if persist_failed:
        return {"status": STAGE_FAILED, "reason": REASON_STORE_ERROR}
    if not catalog.get("available"):
        # 근거로 인정된 catalog가 없다 → 모든 라벨 `UNVERIFIED` 유지 · probe 미생성.
        return {"status": STAGE_COMPLETED, "reason": REASON_CATALOG_UNAVAILABLE}
    if not any(item.get("probeEligible") for item in results):
        return {"status": STAGE_COMPLETED, "reason": REASON_NO_DISCOVERED_MODEL}
    return {"status": STAGE_COMPLETED, "reason": REASON_OK}


def probe_targets(context: dict) -> list[dict]:
    """probe 대상 discovery 결과(Exact_Model_ID가 확정된 것만 — Requirement 2.12)."""
    return [
        item
        for item in ensure_discovery(context)
        if isinstance(item, dict) and item.get("probeEligible") and item.get("modelId")
    ]


def stage_route(context: dict, body: dict) -> dict:
    """route probe stage — advertised Known_Route에만 production Minimal_Request 전송."""
    collector = ensure_collector(context)
    targets = probe_targets(context)
    state = context["state"]
    map_obj = ensure_map(context)
    ctx = currency_ctx(context)
    hints = evidence_collector.route_mode_hints()

    if not targets:
        body["routes"] = []
        return {"status": STAGE_COMPLETED, "reason": REASON_NO_DISCOVERED_MODEL}

    for discovery in targets:
        label = str(discovery.get("candidateLabel") or "")
        entry = capability_map.find_entry(
            map_obj, candidate_label=label, model_id=str(discovery.get("modelId") or "")
        )
        results = asyncio.run(
            collector.probe_routes(discovery, entry=entry, budget=context["budget"])
        )
        state["routeResults"][label] = results
        map_obj, records = collector.apply_route_probes(
            map_obj, discovery, results, ctx=ctx, mode_hints=hints
        )
        state["map"] = map_obj
        state["routeRefs"][label] = remember_records(context, label, records)

    body["routes"] = route_views(context)
    try:
        save_map(context)
    except Exception as exc:
        context_note(context, f"map-persist-error:{type(exc).__name__}:{exc}")
        return {"status": STAGE_FAILED, "reason": REASON_STORE_ERROR}
    return {"status": STAGE_COMPLETED, "reason": REASON_OK}


def stage_effort(context: dict, body: dict) -> dict:
    """effort probe stage — 무-effort baseline 성공 확인 후에만 effort 계약 검증."""
    collector = ensure_collector(context)
    targets = probe_targets(context)
    state = context["state"]
    map_obj = ensure_map(context)
    ctx = currency_ctx(context)
    hints = evidence_collector.route_mode_hints()

    if not targets:
        body["effort"] = []
        return {"status": STAGE_COMPLETED, "reason": REASON_NO_DISCOVERED_MODEL}

    for discovery in targets:
        label = str(discovery.get("candidateLabel") or "")
        entry = capability_map.find_entry(
            map_obj, candidate_label=label, model_id=str(discovery.get("modelId") or "")
        )
        route_results = list(state["routeResults"].get(label) or [])
        summaries = asyncio.run(
            collector.probe_efforts(
                discovery,
                route_results=route_results,
                entry=entry,
                budget=context["budget"],
            )
        )
        state["effortResults"][label] = summaries

        # route 결과 없이 effort stage를 단독 실행하면 collector가 무-effort baseline을
        # route probe로 확인한다(Requirement 5.7). 그 결과는 route 기록 경로로만 반영한다 —
        # route 상태·Probe_ID·Sanitized_Schema를 잃지 않기 위해 route 결과에 합친다.
        baseline_results = [
            summary["baselineResult"]
            for summary in summaries
            if isinstance(summary, dict) and isinstance(summary.get("baselineResult"), dict)
        ]
        if baseline_results:
            state["routeResults"][label] = route_results + baseline_results

        map_obj, records = collector.apply_effort_probes(
            map_obj,
            discovery,
            summaries,
            route_results=route_results + baseline_results,
            ctx=ctx,
            mode_hints=hints,
        )
        state["map"] = map_obj
        evidence_ref = remember_records(context, label, records)
        state["effortRefs"][label] = evidence_ref
        if baseline_results:
            state["routeRefs"].setdefault(label, evidence_ref)

    body["effort"] = effort_views(context)
    body["routes"] = route_views(context)
    try:
        save_map(context)
    except Exception as exc:
        context_note(context, f"map-persist-error:{type(exc).__name__}:{exc}")
        return {"status": STAGE_FAILED, "reason": REASON_STORE_ERROR}
    return {"status": STAGE_COMPLETED, "reason": REASON_OK}


def stage_map(context: dict, body: dict) -> dict:
    """Capability_Map 갱신 stage — STALE 전이 반영 · fingerprint 재계산 · 원자적 기록."""
    ensure_collector(context)
    ensure_discovery(context)
    map_obj = ensure_map(context)
    state = context["state"]

    # STALE 전이 트리거(catalog 제거·provider 변경·계약 변경·revision 불일치)는
    # capability_map이 판정한다 — 이 파일에 트리거 규칙을 복제하지 않는다. 계약 변경
    # 판정 입력(`routeContracts`·`effortContracts`)은 :func:`currency_ctx`가 catalog
    # 선언과 기존 구현이 아는 계약으로만 채운다(Requirement 6.17, 6.18).
    state["map"] = capability_map.apply_stale_triggers(map_obj, currency_ctx(context))

    reason = REASON_OK
    status = STAGE_COMPLETED
    if context.get("store") is None:
        reason = REASON_STORE_UNAVAILABLE
    else:
        try:
            save_map(context)
            save_catalog_snapshot(context)
        except Exception as exc:
            status, reason = STAGE_FAILED, REASON_STORE_ERROR
            context_note(context, f"map-persist-error:{type(exc).__name__}:{exc}")

    body["models"] = model_views(context)
    body["models"]["path"] = state.get("mapPath")
    body["evidence"] = {
        "count": len(state["records"]),
        "evidenceRecordIds": list(state["evidenceIds"]),
        "records": evidence_views(context),
    }
    return {"status": status, "reason": reason}


def stage_activate(context: dict, body: dict) -> dict:
    """Activation_Gate 확인 stage — Active_Model·탈락 이유·미완료 `UNVERIFIED` 주장."""
    ensure_collector(context)
    ensure_discovery(context)
    ensure_map(context)

    section = activation_section(context)
    body["activation"] = section
    if not body.get("models"):
        body["models"] = model_views(context)
    if not section["active"]:
        return {"status": STAGE_COMPLETED, "reason": REASON_NO_ACTIVE_MODEL}
    return {"status": STAGE_COMPLETED, "reason": REASON_OK}


#: stage 이름 → 실행 함수(닫힌 집합 — interpreter stage는 guard가 이미 판정했다).
STAGE_HANDLERS: dict[str, Callable[[dict, dict], dict]] = {
    STAGE_BASELINE: stage_baseline,
    STAGE_DISCOVER: stage_discover,
    STAGE_ROUTE: stage_route,
    STAGE_EFFORT: stage_effort,
    STAGE_MAP: stage_map,
    STAGE_ACTIVATE: stage_activate,
}


def route_views(context: dict) -> list[dict]:
    """모든 라벨의 route probe 결과 목록(evidence reference 포함)."""
    state = context["state"]
    refs = state.get("routeRefs") or {}
    out: list[dict] = []
    for label, results in state["routeResults"].items():
        for item in results:
            if isinstance(item, dict):
                out.append(route_view(label, item, evidence_ref=refs.get(label, "")))
    return out


def effort_views(context: dict) -> list[dict]:
    """모든 라벨의 effort probe summary 목록(evidence reference 포함)."""
    state = context["state"]
    refs = state.get("effortRefs") or {}
    out: list[dict] = []
    for label, summaries in state["effortResults"].items():
        for summary in summaries:
            if isinstance(summary, dict):
                out.append(effort_view(label, summary, evidence_ref=refs.get(label, "")))
    return out


def new_report_body(context: dict) -> dict:
    """보고서 본문 골격 — stage가 채우기 전에는 아무 지원 주장도 담지 않는다.

    키 이름은 `reportBody`다(:func:`build_report`). store 정제기는 `body` 키를 raw
    request/response body로 보고 Sanitized_Schema로 치환하므로 그 이름을 쓰지 않는다.
    """
    return {
        "status": STAGE_PENDING,
        "reason": REASON_STAGE_NOT_IMPLEMENTED,
        "stagesExecuted": [],
        "baseline": None,
        "catalog": None,
        "discovery": [],
        "routes": [],
        "effort": [],
        "models": None,
        "evidence": None,
        "activation": None,
        "probes": [],
        "usage": contracts.NOT_PROVIDED,
        "cost": contracts.NOT_PROVIDED,
        "budget": None,
        "pbt": None,
        "transmissions": 0,
        "notes": [],
    }


def gateway_usage_cost(context: dict) -> tuple[Any, Any]:
    """Gateway가 제공한 usage·cost만 집계한다(미제공은 `notProvided` — 11.17~11.20).

    집계는 :func:`evidence_collector.usage_from`·:func:`evidence_collector.cost_from`에
    위임하므로 값이 없으면 추정하지 않고 ``notProvided``가 된다.
    """
    state = context["state"]
    results: list[dict] = []
    for items in state["routeResults"].values():
        results.extend(item for item in items if isinstance(item, dict))
    for summaries in state["effortResults"].values():
        for summary in summaries:
            if not isinstance(summary, dict):
                continue
            results.extend(
                item for item in (summary.get("results") or []) if isinstance(item, dict)
            )
    return evidence_collector.usage_from(results), evidence_collector.cost_from(results)


def execute_stages(context: dict, results: list[dict], *, run_id: str) -> dict:
    """stage를 :data:`STAGE_ORDER` 순서로 실행하고 보고서 본문을 만든다.

    Args:
        context: :func:`new_run_context` 결과.
        results: :func:`stage_results`가 만든 stage 결과 목록(제자리에서 갱신한다).
        run_id: Validation_Run ID(Verification_Record에 기록된다).

    Returns:
        `reportBody`. 각 stage의 전송 건수는 :class:`evidence_collector.ProbeBudget`
        카운터 증가분으로만 셈하므로 보고서의 전송 계정이 예산 계정과 항상 일치한다.

    한 stage가 실패하면 이후 stage는 실행하지 않고 `blocked`으로 기록한다(기록되지 않을
    근거에 Gateway 예산을 쓰지 않는다). 실패는 프로세스를 죽이지 않고 보고서에 남는다.
    """
    context["runId"] = str(run_id)
    body = new_report_body(context)
    executed: list[str] = []
    failed = False
    skipped = 0

    for item in results:
        name = str(item.get("stage") or "")
        if name == STAGE_INTERPRETER:
            continue  # guard가 이미 판정했다(전송 0건).
        if item.get("status") in (STAGE_BLOCKED, STAGE_SKIPPED):
            skipped += 1
            continue
        if failed:
            # 앞 stage가 실패했으면 이후 stage를 실행하지 않는다 — 기록되지 않을 근거에
            # Gateway 예산을 쓰지 않기 위해서다(전송 0건).
            item["status"], item["reason"] = STAGE_BLOCKED, REASON_STAGE_ERROR
            continue
        handler = STAGE_HANDLERS.get(name)
        if handler is None:  # pragma: no cover - 닫힌 집합이므로 도달하지 않는다
            item["status"], item["reason"] = STAGE_PENDING, REASON_STAGE_NOT_IMPLEMENTED
            continue

        before = total_transmissions(context)
        try:
            outcome = handler(context, body)
        except Exception as exc:
            item["status"], item["reason"] = STAGE_FAILED, REASON_STAGE_ERROR
            item["transmissions"] = total_transmissions(context) - before
            body["notes"].append(_truncate(f"{name}-error:{type(exc).__name__}:{exc}"))
            failed = True
            continue
        item["status"] = str(outcome.get("status") or STAGE_COMPLETED)
        item["reason"] = str(outcome.get("reason") or REASON_OK)
        item["transmissions"] = total_transmissions(context) - before
        if item["status"] == STAGE_FAILED:
            failed = True
        else:
            executed.append(name)

    body["stagesExecuted"] = executed
    body["probes"] = probe_views(context)
    body["usage"], body["cost"] = gateway_usage_cost(context)
    body["budget"] = budget_reverification(context)
    body["pbt"] = pbt_section(context)
    body["transmissions"] = total_transmissions(context)
    if body.get("evidence") is None and context["state"]["records"]:
        body["evidence"] = {
            "count": len(context["state"]["records"]),
            "evidenceRecordIds": list(context["state"]["evidenceIds"]),
            "records": evidence_views(context),
        }
    if body.get("activation") is None and STAGE_ACTIVATE not in executed:
        # activation stage를 실행하지 않았으면 어떤 활성 주장도 하지 않는다.
        body["activation"] = {"evaluated": False, "unverified": unverified_claims(context)}

    if failed:
        body["status"], body["reason"] = STAGE_FAILED, REASON_STAGE_ERROR
    elif executed:
        body["status"] = STAGE_COMPLETED
        body["reason"] = (
            REASON_BUDGET_VIOLATION if not body["budget"]["reverified"] else REASON_OK
        )
    elif skipped:
        body["status"], body["reason"] = STAGE_SKIPPED, REASON_DRY_RUN
    return body


def build_report(
    *,
    labels: Sequence[str],
    stage: str,
    dry_run: bool,
    root: str | None = None,
    verdict: dict | None = None,
    started_at: str | None = None,
    env: dict | None = None,
    extra_notes: Sequence[str] | None = None,
    context: dict | None = None,
    execute: bool = True,
) -> dict:
    """보고서(헤더 + interpreter 판정 + stage 결과 + 예산 계획 + 본문)를 만든다.

    interpreter guard가 실패하면 **interpreter 환경 오류만** 보고한다 — 예산 계획과
    본문 자리는 만들지 않고 stage는 전부 `blocked`이며 전송 건수는 0이다
    (Requirement 11.2, 11.3).

    Args:
        context: :func:`new_run_context` 결과. 주지 않으면 guard 통과 시 이 함수가
            만든다(호출자가 store·client를 재사용하려면 미리 만들어 넘긴다).
        execute: 거짓이면 stage를 실행하지 않고 본문 골격만 남긴다(구조 점검용).
    """
    root_abs = _abspath(root or repo_root())
    guard = verdict if isinstance(verdict, dict) else interpreter_verdict(root_abs)
    stages = resolve_stages(stage)
    interpreter_ok = bool(guard.get("ok"))

    run_context = context
    if run_context is None and interpreter_ok:
        run_context = new_run_context(
            labels=labels,
            stage=stage,
            stages=stages,
            dry_run=dry_run,
            root=root_abs,
            verdict=guard,
            started_at=started_at,
            env=env,
            extra_notes=extra_notes,
        )

    header = report_header(
        labels=labels,
        stage=stage,
        stages=stages,
        dry_run=dry_run,
        root=root_abs,
        verdict=guard,
        started_at=started_at,
        env=env,
        extra_notes=extra_notes,
        context=run_context,
    )

    report: dict[str, Any] = {
        "schemaVersion": contracts.SCHEMA_VERSION,
        "runId": header["runId"],
        "header": header,
        "interpreter": guard,
        "gatewayProbeTransmissions": 0,
        "stages": stage_results(stages, interpreter_ok=interpreter_ok, dry_run=dry_run),
    }
    if not interpreter_ok:
        report["error"] = {
            "category": REASON_INTERPRETER_ENVIRONMENT_ERROR,
            "reasons": list(guard.get("reasons") or []),
            "message": interpreter_error_message(guard),
        }
        return report

    report["plan"] = transmission_plan(labels, dry_run=dry_run)
    # 본문 키 이름은 `reportBody`다 — store 정제기는 `body` 키를 raw request/response
    # body로 보고 Sanitized_Schema로 치환하므로 그 이름을 쓰지 않는다.
    if execute and run_context is not None:
        report["reportBody"] = execute_stages(
            run_context, report["stages"], run_id=str(header["runId"])
        )
        # 헤더 note는 stage 실행 중 추가된 것까지 반영한다(진단 문자열 200자 절단).
        header["notes"] = [str(note) for note in (run_context.get("notes") or [])]
    else:
        report["reportBody"] = {
            "status": STAGE_SKIPPED if dry_run else STAGE_PENDING,
            "reason": REASON_DRY_RUN if dry_run else REASON_STAGE_NOT_IMPLEMENTED,
        }
    report["gatewayProbeTransmissions"] = int(
        (report["reportBody"] or {}).get("transmissions") or 0
    )
    report["completedAt"] = contracts.utc_now_iso()
    return report


# ─────────────────────────────────────────────────────────────────
# 보고서 기록 (userData 하위 한정 · 원자적 쓰기)
# ─────────────────────────────────────────────────────────────────
def write_report(report: dict, *, report_path: str | None = None, store_obj=None):
    """보고서를 `userData` 하위에 원자적으로 기록한다.

    `--report`를 주지 않으면 `userData/capability/runs/{runId}.json`(store의 runs 경로
    빌더)을 쓴다. 경로는 store가 userData 루트 기준으로 해석하며 루트 밖 경로는
    :class:`store.PathOutsideRootError`로 거부된다(Requirement 10.15).

    Returns:
        기록된 절대 경로.
    """
    target_store = store_obj or store.CapabilityStore()
    run_id = report.get("runId") or (report.get("header") or {}).get("runId") or ""
    if report_path:
        return target_store.write_json(report_path, report, sanitize=True)
    return target_store.write_run_report(run_id, report, sanitize=True)


# ─────────────────────────────────────────────────────────────────
# 출력
# ─────────────────────────────────────────────────────────────────
def format_summary(report: dict, *, report_path: Any = None) -> str:
    """사람이 읽는 요약(전송 0건 계획 · 헤더 · stage 상태)."""
    header = report.get("header") or {}
    guard = report.get("interpreter") or {}
    lines = [
        "[Validation_Runner] 보고서 헤더",
        f"  runId          : {report.get('runId')}",
        f"  revision       : {header.get('revision') or '(미확정)'}",
        f"  interpreter    : {header.get('interpreterPath')}",
        f"  startedAt(UTC) : {header.get('startedAt')}",
        "  environment    : "
        f"envId={(header.get('environment') or {}).get('gatewayEnvironmentId') or '(미지정)'} "
        f"endpoint={(header.get('environment') or {}).get('endpointIdentity') or '(미확정)'} "
        f"region={(header.get('environment') or {}).get('region') or '(미확정)'}",
        f"  pbtSeed        : {header.get('pbtSeed')}",
        f"  stage          : {header.get('requestedStage')} → {', '.join(header.get('stages') or [])}",
        f"  labels         : {', '.join(header.get('candidateLabels') or [])}",
        f"  interpreter OK : {guard.get('ok')} reasons={','.join(guard.get('reasons') or []) or '-'}",
        f"  Gateway_Probe 전송: {report.get('gatewayProbeTransmissions')}건",
    ]
    if header.get("notes"):
        lines.append(f"  notes          : {', '.join(str(note) for note in header['notes'])}")

    plan = report.get("plan")
    if isinstance(plan, dict):
        budget = plan.get("budget") or {}
        probe = plan.get("probeInput") or {}
        lines.extend(
            [
                "",
                f"[예산 계획] dryRun={plan.get('dryRun')} 계획 전송={plan.get('plannedTransmissions')}건",
                f"  조합 축                 : ({', '.join(budget.get('combinationAxes') or [])})",
                f"  조합당 성공 generation  : {budget.get('maxSuccessesPerCombination')}회",
                f"  조합당 교정             : {budget.get('maxCorrectionsPerCombination')}회",
                f"  route당 prefix 교정     : {budget.get('maxPrefixCorrectionsPerRoute')}회",
                f"  조합당 전송 상한        : {budget.get('transmissionLimitPerCombination')}건",
                f"  고정 probe 입력         : probeId={probe.get('probeId')} "
                f"길이={probe.get('textLength')} systemPrompt비움={probe.get('systemPromptEmpty')}",
                "",
                "[Known_Route 최소 output/token bound]",
            ]
        )
        for route in plan.get("routes") or []:
            bounds = route.get("minOutputBound") or []
            rendered = (
                ", ".join(
                    f"{'.'.join(item.get('fieldPath') or [])}={item.get('value')}" for item in bounds
                )
                if bounds
                else "(bound field 없음 — baseline body 유지)"
            )
            lines.append(
                f"  {route.get('routeKey'):<22} {route.get('endpointRef')} "
                f"[{route.get('executionMode')}/{route.get('signingService')}] "
                f"effort주입={route.get('effortInjectable')} 전송={route.get('plannedTransmissions')}건"
            )
            lines.append(f"    최소 bound: {rendered} (source={route.get('minOutputBoundSourceRef')})")

    lines.append("")
    lines.append("[stage]")
    for item in report.get("stages") or []:
        lines.append(
            f"  {item.get('stage'):<12} {item.get('status'):<10} "
            f"reason={item.get('reason')} 전송={item.get('transmissions')}건"
        )

    lines.extend(format_body(report.get("reportBody")))
    if report_path:
        lines.append("")
        lines.append(f"[보고서] {report_path}")
    return "\n".join(lines)


def format_body(body: Any) -> list[str]:
    """보고서 본문 요약(사람이 읽는 형태 — 값은 전부 보고서에서 읽은 것이다)."""
    if not isinstance(body, dict) or "stagesExecuted" not in body:
        return []

    lines = [
        "",
        f"[본문] status={body.get('status')} reason={body.get('reason')} "
        f"실행 stage={', '.join(body.get('stagesExecuted') or []) or '-'} "
        f"전송={body.get('transmissions')}건",
    ]

    baseline = body.get("baseline")
    if isinstance(baseline, dict):
        lines.append(
            f"  baseline    : revision={baseline.get('revision') or '(미확정)'} "
            f"category={baseline.get('categoryCount')} record={baseline.get('recordCount')} "
            f"evidenceEligible={baseline.get('evidenceEligible')} "
            f"결손={','.join(baseline.get('missingCategories') or []) or '-'}"
        )
        if baseline.get("path"):
            lines.append(f"                {baseline['path']}")

    catalog = body.get("catalog")
    if isinstance(catalog, dict):
        lines.append(
            f"  catalog     : available={catalog.get('available')} "
            f"source={catalog.get('sourceKind') or '-'} record={catalog.get('recordCount')} "
            f"fp={catalog.get('catalogFingerprint') or '(미확정)'} "
            f"reasons={','.join(catalog.get('reasons') or []) or '-'}"
        )

    if body.get("discovery"):
        lines.append("  discovery   :")
        for item in body["discovery"]:
            lines.append(
                f"    {str(item.get('candidateLabel')):<10} found={item.get('found')} "
                f"status={item.get('verificationStatus')} "
                f"modelId={item.get('modelId') or '(미확정)'} "
                f"provider={item.get('provider') or '(미확정)'} "
                f"routes={','.join(item.get('advertisedRoutes') or []) or '-'} "
                f"probe가능={item.get('probeEligible')} "
                f"reasons={','.join(item.get('reasons') or []) or '-'}"
            )

    if body.get("routes"):
        lines.append("  route probe :")
        for item in body["routes"]:
            lines.append(
                f"    {str(item.get('candidateLabel')):<10} {str(item.get('routeKey')):<22} "
                f"status={item.get('status')} allowlist={item.get('allowlist')} "
                f"http={item.get('http')} valid={item.get('validOutput')} "
                f"terminal={item.get('terminalSuccess')} 교정={item.get('correctionUsed')} "
                f"전송={item.get('transmissions')}건 probeId={item.get('probeId')} "
                f"evidence={item.get('evidenceRef') or '-'} reason={item.get('reason') or '-'}"
            )

    if body.get("effort"):
        lines.append("  effort probe:")
        for item in body["effort"]:
            lines.append(
                f"    {str(item.get('candidateLabel')):<10} {str(item.get('routeKey')):<22} "
                f"status={item.get('status')} baseline성공={item.get('baselineSucceeded')} "
                f"fieldPath={item.get('fieldPath')} verified={item.get('verifiedValues')} "
                f"전송={item.get('transmissions')}건 evidence={item.get('evidenceRef') or '-'} "
                f"reason={item.get('reason') or '-'}"
            )

    models = body.get("models")
    if isinstance(models, dict) and models.get("entries"):
        lines.append("  capability_map:")
        for item in models["entries"]:
            lines.append(
                f"    {str(item.get('candidateLabel')):<10} status={item.get('verificationStatus')} "
                f"modelId={item.get('modelId') or '(미확정)'} "
                f"fp={item.get('capabilityFingerprint') or '(미확정)'} "
                f"fp일치={item.get('fingerprintMatches')} "
                f"complete={item.get('completeRecord')} "
                f"supported={','.join(item.get('supportedRoutes') or []) or '-'}"
            )
        if models.get("malformed"):
            lines.append(f"    malformed  : {len(models['malformed'])}건(activation 입력 제외)")

    evidence = body.get("evidence")
    if isinstance(evidence, dict):
        lines.append(
            f"  evidence    : {evidence.get('count')}건 "
            f"usage={body.get('usage')} cost={body.get('cost')}"
        )

    activation = body.get("activation")
    if isinstance(activation, dict) and activation.get("evaluated") is False:
        unverified = activation.get("unverified") or []
        lines.append(
            f"  activation  : (미평가 — activate stage 미실행) "
            f"UNVERIFIED 주장={len(unverified)}건"
        )
    elif isinstance(activation, dict):
        counts = activation.get("counts") or {}
        lines.append(
            f"  activation  : active={counts.get('active', 0)} "
            f"excluded={counts.get('excluded', 0)} "
            f"Managed_Segment={(activation.get('managedSegment') or {}).get('count', 0)}건"
        )
        for item in activation.get("active") or []:
            lines.append(
                f"    active     : modelId={item.get('modelId')} "
                f"routes={','.join(item.get('eligibleRoutes') or []) or '-'} "
                f"fp={item.get('capabilityFingerprint')}"
            )
        for item in activation.get("excluded") or []:
            lines.append(
                f"    excluded   : label={item.get('candidateLabel')} "
                f"modelId={item.get('modelId') or '(미확정)'} reason={item.get('reason')}"
            )
        unverified = activation.get("unverified") or []
        if unverified:
            lines.append(f"    UNVERIFIED : {len(unverified)}건(integration probe 미완료 주장)")
            for item in unverified[:12]:
                lines.append(
                    f"      {item.get('kind'):<7} label={item.get('candidateLabel')} "
                    f"route={item.get('routeKey') or '-'} reason={item.get('reason')}"
                )

    budget = body.get("budget")
    if isinstance(budget, dict):
        lines.append(
            f"  예산 재확인 : reverified={budget.get('reverified')} "
            f"성공/조합≤{budget.get('maxSuccessesPerCombination')} "
            f"교정/조합≤{budget.get('maxCorrectionsPerCombination')} "
            f"prefix교정/route≤{budget.get('maxPrefixCorrectionsPerRoute')} "
            f"총 전송={budget.get('totalTransmissions')}건 "
            f"위반={len(budget.get('violations') or [])}건"
        )

    pbt = body.get("pbt")
    if isinstance(pbt, dict):
        lines.append(
            f"  PBT         : seed={pbt.get('seed')} executed={pbt.get('executed')} "
            f"reason={pbt.get('reason')} counterexample={len(pbt.get('counterexamples') or [])}건"
        )
        for item in pbt.get("counterexamples") or []:
            lines.append(f"    counterexample: {item.get('minimizedExample')}")
            if item.get("reproduceBlob"):
                lines.append(f"      재현: {item['reproduceBlob']}")

    if body.get("probes"):
        lines.append(f"  probe 기록  : {len(body['probes'])}건(Probe_ID·Sanitized_Schema만)")
    if body.get("notes"):
        lines.append(f"  notes       : {', '.join(str(note) for note in body['notes'])}")
    return lines


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    """CLI 파서 — `--labels`, `--report`, `--dry-run`, `--stage`."""
    parser = argparse.ArgumentParser(
        prog="validate_gateway_model_capabilities",
        description=(
            "Validation_Runner — Candidate_Label discovery부터 Activation_Gate 확인까지의 "
            "validation run을 최소 비용으로 실행한다. 반드시 ai_engine/.venv/bin/python으로 "
            "실행해야 하며, --dry-run은 Gateway 전송 0건으로 예산 계획만 출력한다."
        ),
    )
    parser.add_argument(
        "--labels",
        nargs="*",
        metavar="LABEL",
        default=list(evidence_collector.CANDIDATE_LABELS),
        help=(
            "검색에 사용할 Candidate_Label 목록(검색 라벨일 뿐 model identity가 아니다). "
            "기본값은 evidence_collector.CANDIDATE_LABELS이며, 값 없이 --labels만 주면 "
            "기본 집합으로 되돌아간다(보고서에 LABELS_DEFAULTED note 기록)."
        ),
    )
    parser.add_argument(
        "--report",
        default=None,
        help=(
            "보고서 기록 경로. userData 루트 기준 상대 경로 또는 루트 하위 절대 경로만 "
            "허용한다. 미지정 시 userData/capability/runs/{runId}.json."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Gateway 전송을 0건으로 유지하고 예산 계획만 산출한다.",
    )
    parser.add_argument(
        "--stage",
        choices=list(STAGE_CHOICES),
        default=STAGE_ALL,
        help=(
            "실행할 stage(닫힌 집합). 개별 stage를 지정해도 interpreter 확인이 먼저 "
            "수행된다. 기본값 all."
        ),
    )
    return parser


def normalize_labels(labels: Iterable[str]) -> list[str]:
    """라벨 목록을 정규화한다(빈 값 제거 · 순서 보존 중복 제거)."""
    out: list[str] = []
    for label in labels or []:
        if not isinstance(label, str) or not label.strip():
            continue
        if label not in out:
            out.append(label)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 엔트리 — interpreter 확인 → stage 실행 → 보고서 기록 → 요약 출력.

    실행 순서가 중요하다. capability 모듈 import는 interpreter guard **다음**이다.
    구버전 interpreter로 실행하면 import가 먼저 깨져 traceback으로 끝나고 Requirement
    11.3(interpreter 환경 오류 보고)을 만족하지 못하기 때문이다. interpreter guard가
    실패하거나 `--dry-run`이면 Gateway_Probe를 한 건도 만들지 않는다.

    종료 코드: 0 성공, 1 interpreter 환경 오류, 2 인자 오류(argparse),
    3 보고서 기록 실패(userData 루트 밖 경로·I/O). stage 실행 실패는 종료 코드를
    바꾸지 않고 보고서와 stderr로 보고한다(부분 실행 결과를 잃지 않기 위해서다).
    """
    root = repo_root()
    verdict = interpreter_verdict(root)

    loaded, load_error = load_capability()
    if not loaded:
        # capability 모듈을 이 interpreter로 불러올 수 없다 → 전송 0건 유지 +
        # interpreter 환경 오류만 보고하고 이후 stage를 진행하지 않는다.
        print(
            f"[Validation_Runner] {interpreter_error_message(with_import_failure(verdict, load_error))}",
            file=sys.stderr,
        )
        return EXIT_INTERPRETER_ERROR

    parser = build_parser()
    args = parser.parse_args(argv)

    labels = normalize_labels(args.labels)
    extra_notes: list[str] = []
    if not labels:
        # 값 없는 `--labels`는 기본 Candidate_Label 집합으로 되돌린다(라벨을 만들지 않는다).
        labels = list(evidence_collector.CANDIDATE_LABELS)
        extra_notes.append(NOTE_LABELS_DEFAULTED)

    stages = resolve_stages(args.stage)
    run_context = (
        new_run_context(
            labels=labels,
            stage=args.stage,
            stages=stages,
            dry_run=bool(args.dry_run),
            root=root,
            verdict=verdict,
            extra_notes=extra_notes,
        )
        if verdict["ok"]
        else None
    )

    report = build_report(
        labels=labels,
        stage=args.stage,
        dry_run=bool(args.dry_run),
        root=root,
        verdict=verdict,
        extra_notes=extra_notes,
        context=run_context,
    )

    try:
        path = write_report(
            report,
            report_path=args.report,
            store_obj=(run_context or {}).get("store"),
        )
    except store.StoreError as exc:
        print(f"[Validation_Runner] 보고서 기록 실패: {_truncate(exc)}", file=sys.stderr)
        return EXIT_REPORT_ERROR

    print(format_summary(report, report_path=path))

    if not verdict["ok"]:
        # Gateway_Probe 전송 0건 유지 + interpreter 환경 오류만 보고(Requirement 11.2, 11.3).
        print(f"[Validation_Runner] {interpreter_error_message(verdict)}", file=sys.stderr)
        return EXIT_INTERPRETER_ERROR

    for item in report.get("stages") or []:
        if item.get("status") == STAGE_FAILED:
            print(
                f"[Validation_Runner] stage 실패: {item.get('stage')} reason={item.get('reason')}",
                file=sys.stderr,
            )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
