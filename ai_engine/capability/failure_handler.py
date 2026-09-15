"""Failure_Handler — 결정론적 실패 분류·상태 전이·복구 계획·User_Notification.

이 모듈은 Gateway 호출 실패를 **결정론적으로 하나의 Failure_Category로 분류**하고
(:func:`classify`), 그 범주에 대응하는 **capability 상태 전이만** 적용하며
(:func:`apply`), 허용된 복구 수단을 계획하고(:func:`plan_recovery`), 사용자에게
표시할 최소 필드 집합을 만든다(:func:`notification`).

순수 로직 모듈이다. 네트워크·디스크·시각·난수에 접근하지 않으며 재시도를 직접
수행하지 않는다. "무엇을 해도 되는가"만 계산하고 실행은 호출자(server seam ·
Evidence_Collector)가 기존 transport 구현으로 한다.

**기존 심볼 재사용(신규 판정 규칙 복제 금지)** — 판정 신호는 이미 존재하는 구현에
위임한다. 순환 import를 피하기 위해 :class:`FailureHooks`가 지연 연결한다.

    ai_engine/gateway_module.py
      GatewayClient._is_expired_error         인증·credential 만료 신호
      GatewayClient._looks_unsupported_model  unknown model / unsupported route 신호
      _is_prefix_form_error                   교정 가능한 model ID 형태 오류 신호
      QuotaExceededError · SyncTimeout · JobTimeout · OpenAISurfaceError ·
      OpenAIModelUnsupported                  예외 타입 신호(MRO 이름으로 판정)
      mask_token                              원인 문자열 토큰 마스킹(store 경유)

    ai_engine/server.py
      _maybe_record_denied_from_error         explicit allowlist denial 문자열 신호 공유
      _extract_denied_model_from_error        거부된 model ID 추출
      _record_denied_model                    denylist 등록(전이 시 호출)
      _normalize_model_key                    Editor_Model_Catalog 제거 시 ID 비교

서버는 이 모듈을 import하지만 이 모듈은 서버를 **import 시점에 참조하지 않는다**.
``ai_engine.server``는 이미 로드된 module 객체(``sys.modules``)로만 연결하고, 없으면
등록을 건너뛰고 결과에 그 사실을 남긴다(``deniedRecorded``). 테스트·CLI는
:class:`FailureHooks`로 콜백을 직접 주입할 수 있다.

Failure_Precedence(:data:`PRECEDENCE`) — 신호가 겹치면 **첫 일치 범주**를 선택한다
(Requirement 9.1). design.md "Error Handling" 분류표와 1:1 대응한다.

    1 authentication            상태 불변 · 기존 강제 갱신 정책에 위임 · fallback 없음
    2 allowlist                 entry REJECTED · route allowlist REJECTED ·
                                _record_denied_model 호출 · Editor_Model_Catalog 제거
    3 effort-mismatch           해당 Effort_Contract만 STALE · 무-effort 재시도 1회
    4 route-capability-mismatch 해당 route UNSUPPORTED · 해당 route effort STALE ·
                                첫 Eligible_Contract fallback
    5 quota                     상태 불변 · retry·fallback 없음
    6 transient                 상태 불변(강등 없음) · 기존 retry 한도 · 소진 후 fallback
    7 request-validation        상태 불변 · 동일 조합 교정 1회
    8 unknown                   상태 불변(entry canonical serialization 불변)

상태 보존 불변식(design.md "상태 보존 불변식" 표): ``transient``·``unknown``·인증
실패·``quota``·``request-validation``은 Verification_Status·Route_Support_Status·
Effort_Support_Status·Editor_Model_Catalog를 **모두 불변**으로 둔다. 이 모듈은 해당
범주에서 entry 사본을 그대로 반환하므로 canonical serialization이 변하지 않는다.

verified fallback 규칙(design.md "verified fallback 규칙"):
  1. 후보는 Eligible_Contract(현재 `SUPPORTED` + Current_Evidence + 요청 목적 충족 +
     allowlist `ALLOWED`)로만 구성한다.
  2. Fallback_Order(`fallbackRank` 오름차순)의 **첫 항목만** 사용한다(연쇄 이동 금지).
  3. 후보가 없으면 fallback 전송을 만들지 않고 오류 상태로 종료한다(``terminate``).
  4. 미지원 route에 대한 전송 건수는 항상 0이다(후보 판정이 `SUPPORTED`만 통과시킨다).

참조: .kiro/specs/gateway-models-effort-support/design.md
  - "Components and Interfaces" 7절 (Failure_Handler / User_Notification)
  - "Error Handling" 전 절(분류표 · 무-effort 단일 재시도 · 상태 보존 불변식 ·
    verified fallback 규칙 · User_Notification 필드)
Requirements: 9.1~9.20, 10.16, 10.17, 10.18
"""
from __future__ import annotations

import copy
import re
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import canonicalizer, capability_map, contracts, store

# ─────────────────────────────────────────────────────────────────
# Failure_Category 상수와 Failure_Precedence
# ─────────────────────────────────────────────────────────────────
AUTHENTICATION = str(contracts.Failure_Category.AUTHENTICATION)
ALLOWLIST = str(contracts.Failure_Category.ALLOWLIST)
EFFORT_MISMATCH = str(contracts.Failure_Category.EFFORT_MISMATCH)
ROUTE_CAPABILITY_MISMATCH = str(contracts.Failure_Category.ROUTE_CAPABILITY_MISMATCH)
QUOTA = str(contracts.Failure_Category.QUOTA)
TRANSIENT = str(contracts.Failure_Category.TRANSIENT)
REQUEST_VALIDATION = str(contracts.Failure_Category.REQUEST_VALIDATION)
UNKNOWN = str(contracts.Failure_Category.UNKNOWN)

#: Failure_Precedence — 여러 신호가 겹치면 이 순서의 첫 일치 범주를 선택한다(9.1).
PRECEDENCE: tuple[str, ...] = (
    AUTHENTICATION,
    ALLOWLIST,
    EFFORT_MISMATCH,
    ROUTE_CAPABILITY_MISMATCH,
    QUOTA,
    TRANSIENT,
    REQUEST_VALIDATION,
    UNKNOWN,
)

#: PRECEDENCE가 Failure_Category 닫힌 집합을 정확히 덮는지(테스트·보고서 확인용).
PRECEDENCE_COVERS_ENUM: bool = set(PRECEDENCE) == set(contracts.Failure_Category.values())

#: 상태를 변경하지 않는 범주(design.md "상태 보존 불변식" 표 + 분류표 "변경 없음").
STATE_PRESERVING_CATEGORIES: tuple[str, ...] = (
    AUTHENTICATION,
    QUOTA,
    TRANSIENT,
    REQUEST_VALIDATION,
    UNKNOWN,
)

# ─────────────────────────────────────────────────────────────────
# 전이 코드(닫힌 집합) — 보고서·로그·테스트가 참조한다
# ─────────────────────────────────────────────────────────────────
TRANSITION_ENTRY_REJECTED = "ENTRY_REJECTED"
TRANSITION_ROUTE_ALLOWLIST_REJECTED = "ROUTE_ALLOWLIST_REJECTED"
TRANSITION_ROUTE_UNSUPPORTED = "ROUTE_UNSUPPORTED"
TRANSITION_EFFORT_CONTRACT_STALE = "EFFORT_CONTRACT_STALE"
TRANSITION_CATALOG_MODEL_REMOVED = "CATALOG_MODEL_REMOVED"

TRANSITIONS: tuple[str, ...] = (
    TRANSITION_ENTRY_REJECTED,
    TRANSITION_ROUTE_ALLOWLIST_REJECTED,
    TRANSITION_ROUTE_UNSUPPORTED,
    TRANSITION_EFFORT_CONTRACT_STALE,
    TRANSITION_CATALOG_MODEL_REMOVED,
)

# ─────────────────────────────────────────────────────────────────
# 복구 계획 이유 코드(닫힌 집합)
# ─────────────────────────────────────────────────────────────────
RECOVERY_AUTH_REFRESH_DELEGATED = "AUTH_REFRESH_DELEGATED"
RECOVERY_AUTH_REFRESH_EXHAUSTED = "AUTH_REFRESH_EXHAUSTED"
RECOVERY_EFFORT_BASELINE_RETRY = "EFFORT_BASELINE_RETRY"
RECOVERY_EFFORT_BASELINE_USED = "EFFORT_BASELINE_RETRY_USED"
RECOVERY_EFFORT_BASE_ROUTE_NOT_SUPPORTED = "EFFORT_BASE_ROUTE_NOT_SUPPORTED"
RECOVERY_TRANSIENT_RETRY = "TRANSIENT_RETRY"
RECOVERY_CORRECTION_RETRY = "CORRECTION_RETRY"
RECOVERY_CORRECTION_USED = "CORRECTION_RETRY_USED"
RECOVERY_FALLBACK_SELECTED = "FALLBACK_CONTRACT_SELECTED"
RECOVERY_NO_ELIGIBLE_CONTRACT = "NO_ELIGIBLE_CONTRACT"
RECOVERY_NO_RECOVERY = "NO_RECOVERY_AVAILABLE"
RECOVERY_ROUTE_UNKNOWN = "ROUTE_UNKNOWN"

RECOVERY_REASONS: tuple[str, ...] = (
    RECOVERY_AUTH_REFRESH_DELEGATED,
    RECOVERY_AUTH_REFRESH_EXHAUSTED,
    RECOVERY_EFFORT_BASELINE_RETRY,
    RECOVERY_EFFORT_BASELINE_USED,
    RECOVERY_EFFORT_BASE_ROUTE_NOT_SUPPORTED,
    RECOVERY_TRANSIENT_RETRY,
    RECOVERY_CORRECTION_RETRY,
    RECOVERY_CORRECTION_USED,
    RECOVERY_FALLBACK_SELECTED,
    RECOVERY_NO_ELIGIBLE_CONTRACT,
    RECOVERY_NO_RECOVERY,
    RECOVERY_ROUTE_UNKNOWN,
)

# ─────────────────────────────────────────────────────────────────
# 예산·한도 — 전부 Existing_Gateway_Integration의 기존 값을 그대로 반영한다
# (신규 정책을 만들지 않는다. gateway_module의 실제 구현이 근거다.)
# ─────────────────────────────────────────────────────────────────

#: transient 최대 시도 횟수 — `_openai_post_with_retry`의 ``for attempt in range(3)``.
TRANSIENT_MAX_ATTEMPTS = 3

#: transient 최대 재시도 횟수(첫 시도 제외) — 위 루프의 ``attempt < 2`` 게이트.
TRANSIENT_MAX_RETRIES = TRANSIENT_MAX_ATTEMPTS - 1

#: transient 백오프(초) — `_openai_post_with_retry`의 ``backoff = [1, 2, 4]``.
TRANSIENT_BACKOFF_SECONDS: tuple[float, ...] = (1.0, 2.0, 4.0)

#: 인증 만료 강제 갱신 최대 시도 횟수 — `converse`/`_openai_post_with_retry` 재시도 한도.
AUTH_MAX_REFRESH_ATTEMPTS = 3

#: effort-only mismatch의 무-effort baseline 재시도 한도(Requirement 9.7 — 최대 1회).
EFFORT_BASELINE_MAX_RETRIES = 1

#: 명시적·교정 가능 request validation의 동일 조합 교정 한도(design 분류표 rank 7).
CORRECTION_MAX_RETRIES = 1

#: SSE max_tokens 축소 재시도 한도 — `stream_sse_realtime`의 기존 한도(참조용).
STREAM_MAX_TOKENS_REDUCTIONS = 2

# ─────────────────────────────────────────────────────────────────
# 문자열 신호
# ─────────────────────────────────────────────────────────────────

#: explicit allowlist denial 신호 — ``server._maybe_record_denied_from_error``가
#: 사용하는 문자열과 동일하다(신호 공유 — 판정이 갈리지 않게 같은 값을 쓴다).
ALLOWLIST_DENIAL_SIGNALS: tuple[str, ...] = ("model_denied", "not in allowed list")

#: effort field 자체를 모른다고 거부한 신호(field 이름은 evidence만 알므로 문구만 본다).
EFFORT_UNKNOWN_FIELD_SIGNALS: tuple[str, ...] = (
    "unknown field",
    "unknown parameter",
    "unknown argument",
    "unexpected field",
    "unexpected parameter",
    "unexpected keyword",
    "unrecognized field",
    "unrecognized parameter",
    "unsupported field",
    "unsupported parameter",
    "extra fields not permitted",
    "additional properties",
    "not permitted",
)

#: effort value를 invalid/out-of-range로 거부한 신호.
EFFORT_INVALID_VALUE_SIGNALS: tuple[str, ...] = (
    "invalid value",
    "invalid enum",
    "invalid parameter value",
    "unsupported value",
    "not a valid",
    "must be one of",
    "out of range",
    "outside the allowed range",
    "less than",
    "greater than",
)

#: Transient_Error 문자열 신호(timeout·연결 실패·throttling·empty/partial output).
TRANSIENT_SIGNALS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "connection refused",
    "connection error",
    "temporarily unavailable",
    "service unavailable",
    "too many requests",
    "throttl",
    "try again",
    "empty output",
    "empty response",
    "partial output",
    "incomplete response",
)

#: 명시적이고 교정 가능한 request validation 신호.
REQUEST_VALIDATION_SIGNALS: tuple[str, ...] = (
    "validationexception",
    "invalid request",
    "malformed",
    "bad request",
    "is required",
    "missing required",
)

# ─────────────────────────────────────────────────────────────────
# 예외 타입 신호 — MRO 이름으로 판정하므로 하위 클래스도 포함되고
# gateway_module을 import하지 않아도 동작한다.
# ─────────────────────────────────────────────────────────────────
AUTH_ERROR_NAMES: frozenset[str] = frozenset({
    "NoCredentialsError",
    "PartialCredentialsError",
    "CredentialRetrievalError",
    "ExpiredTokenError",
    "TokenRetrievalError",
    "UnauthorizedSSOTokenError",
    "SSOTokenLoadError",
})
ALLOWLIST_ERROR_NAMES: frozenset[str] = frozenset({"OpenAIModelUnsupported"})
ROUTE_MISMATCH_ERROR_NAMES: frozenset[str] = frozenset({"OpenAIModelUnsupported"})
QUOTA_ERROR_NAMES: frozenset[str] = frozenset({"QuotaExceededError"})
TRANSIENT_ERROR_NAMES: frozenset[str] = frozenset({
    "SyncTimeout",
    "JobTimeout",
    "TimeoutError",
    "TimeoutException",
    "ReadTimeout",
    "WriteTimeout",
    "ConnectTimeout",
    "PoolTimeout",
    "ConnectError",
    "ConnectionError",
    "ConnectionResetError",
    "RemoteProtocolError",
    "ReadError",
    "NetworkError",
})
REQUEST_VALIDATION_ERROR_NAMES: frozenset[str] = frozenset({"OpenAISurfaceError"})

# ─────────────────────────────────────────────────────────────────
# signals / ctx 키 문서
# ─────────────────────────────────────────────────────────────────

#: :func:`classify`가 읽는 signals 키(모두 선택 — 없는 신호는 판정하지 않는다).
SIGNAL_KEYS: tuple[str, ...] = (
    "error",                 # 발생한 예외 객체 또는 예외 클래스
    "errorType",             # 예외 타입 이름 문자열(객체가 없을 때)
    "errorText",             # 오류 문자열(응답 본문·메시지 — 분류 입력 전용)
    "message", "detail", "bodyText",   # 추가 오류 문자열
    "httpStatus", "status", "statusCode",  # HTTP 상태(-1은 네트워크 오류 sentinel)
    "credentialFailure",     # credential 획득·인증 실패(True → authentication)
    "allowlistDenied",       # explicit allowlist denial 확정 신호
    "effortInjected",        # 이 요청에 effort가 주입되었는지
    "effortFieldPath",       # 주입한 Effort_Contract field path(계약에서 옴)
    "effortValue",           # 주입한 effort value
    "effortFieldRejected",   # Gateway가 effort field를 모른다고 거부
    "effortValueRejected",   # Gateway가 effort value를 invalid/out-of-range로 거부
    "routeRejected",         # 이전 검증 route를 명시적으로 거부(unknown model/route)
    "quotaExceeded",         # 403 권한·쿼터 확정 신호
    "timeout", "connectionFailed", "emptyOutput", "partialOutput",  # transient 신호
    "requestValidation",     # 명시적·교정 가능 request validation 확정 신호
)

#: :func:`apply`·:func:`plan_recovery`·:func:`notification`이 읽는 ctx 키.
CTX_KEYS: tuple[str, ...] = (
    "modelId",               # 원 Exact_Model_ID(알림·denylist 등록용)
    "invocationModelId",     # 실제 전송된 ID(prefix 교정 결과)
    "route",                 # 실패한 Known_Route
    "purpose",               # 요청 목적(Eligible_Contract 판정)
    "category",              # notification용 Failure_Category
    "errorText",             # 원인 문자열(200자 절단·마스킹 후 사용)
    "catalogModelIds",       # 현재 Editor_Model_Catalog의 model ID 목록
    "modeHints",             # {routeKey: Execution_Mode} — 파생 모드 재계산 보조
    "revision", "catalogFingerprint",  # Current_Evidence 현재성 검사(선택)
    "retryCount",            # 지금까지 수행한 전체 retry 횟수(무-effort 재시도 포함)
    "transientRetryCount", "correctionRetryCount",  # 범주별 retry 횟수
    "retryWithoutEffortUsed",  # 무-effort 재시도를 이미 사용했는지
    "transientMaxRetries",   # transient 한도 override(기본 TRANSIENT_MAX_RETRIES)
    "authRetryCount",        # credential 강제 갱신 시도 횟수
    "fallback", "fallbackContract", "fallbackModelId",  # notification fallback 표시
)

#: User_Notification 필드 화이트리스트(Requirement 9.14~9.19).
NOTIFICATION_FIELDS: tuple[str, ...] = ("modelId", "route", "category", "retryCount", "fallback")

#: fallback을 수행하지 않았음을 명시하는 값(Requirement 9.18).
NO_FALLBACK = "none"

#: 원인 문자열 최대 길이(기존 프로젝트 규칙과 동일하게 200자 절단).
CAUSE_MAX_LENGTH = 200

#: ``key=value`` / ``key: value`` 형태에서 비밀 값을 찾아 마스킹하기 위한 패턴.
_ASSIGNMENT_RE = re.compile(r"([A-Za-z][A-Za-z0-9_\-]{2,40})(\s*[:=]\s*\"?)([^\s\"',;}\]]+)")

#: 지연 import 결과 캐시 sentinel.
_UNSET = object()
_GATEWAY_MODULE: Any = _UNSET

#: ``ai_engine.server``를 찾을 때 확인하는 module 이름(이미 로드된 것만 사용한다).
_SERVER_MODULE_NAMES: tuple[str, ...] = ("ai_engine.server", "server", "__main__")


# ─────────────────────────────────────────────────────────────────
# 기존 구현 연결 — 지연 import / 콜백 주입 (순환 import 회피)
# ─────────────────────────────────────────────────────────────────
def _gateway_module():
    """``ai_engine.gateway_module``을 지연 import한다(불가하면 ``None``).

    capability 패키지는 transport 의존을 import 시점에 만들지 않는다. import가
    불가한 환경에서는 예외 타입 이름·문자열 신호만으로 판정한다.
    """
    global _GATEWAY_MODULE
    if _GATEWAY_MODULE is _UNSET:
        try:
            from ai_engine import gateway_module as module  # 지연 import
        except Exception:  # pragma: no cover - transport 의존 부재 환경
            module = None
        _GATEWAY_MODULE = module
    return _GATEWAY_MODULE


def _server_module():
    """이미 로드된 ``ai_engine.server`` module 객체를 반환한다(없으면 ``None``).

    **새로 import하지 않는다.** server는 이 모듈을 import하는 쪽이며, denylist는
    실행 중 프로세스의 in-memory 레지스트리이므로 로드되지 않은 상태에서 새로
    import하는 것은 의미가 없고 무거운 부작용만 만든다.
    """
    for name in _SERVER_MODULE_NAMES:
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "_record_denied_model"):
            return module
    return None


class FailureHooks:
    """판정·기록을 기존 구현에 연결하는 훅 모음(주입 가능).

    각 훅은 명시 주입값 → 기존 구현(지연 연결) → 보수적 폴백 순으로 해석된다.
    폴백은 "신호 없음"으로 판정해 상태를 바꾸지 않는 쪽을 택한다(오분류로 인한
    잘못된 강등을 만들지 않는다).

    Args:
        is_expired_error: ``(text) -> bool``. 기본은
            ``GatewayClient._is_expired_error``(인스턴스 상태를 쓰지 않는 순수 판정).
        looks_unsupported_model: ``(status, text) -> bool``. 기본은
            ``GatewayClient._looks_unsupported_model``.
        is_prefix_form_error: ``(text) -> bool``. 기본은 ``_is_prefix_form_error``.
        record_denied_model: ``(model_id) -> None``. 기본은
            ``server._record_denied_model``(이미 로드된 module에서만).
        extract_denied_model: ``(text) -> str``. 기본은
            ``server._extract_denied_model_from_error``.
        normalize_model_key: ``(model_id) -> str``. 기본은
            ``server._normalize_model_key``, 없으면 gateway의 prefix 제거 + 소문자.
        select_contract: ``(entry, purpose, ctx) -> dict|None``. 주면 Request_Router의
            Fallback_Order 판정을 그 구현에 위임한다(작업 7.2 연결점).
    """

    def __init__(
        self,
        *,
        is_expired_error: Callable[[str], bool] | None = None,
        looks_unsupported_model: Callable[[Any, str], bool] | None = None,
        is_prefix_form_error: Callable[[str], bool] | None = None,
        record_denied_model: Callable[[str], Any] | None = None,
        extract_denied_model: Callable[[str], str] | None = None,
        normalize_model_key: Callable[[str], str] | None = None,
        select_contract: Callable[[Mapping[str, Any], str | None, Mapping[str, Any]], Any] | None = None,
    ) -> None:
        self._is_expired_error = is_expired_error
        self._looks_unsupported_model = looks_unsupported_model
        self._is_prefix_form_error = is_prefix_form_error
        self._record_denied_model = record_denied_model
        self._extract_denied_model = extract_denied_model
        self._normalize_model_key = normalize_model_key
        self.select_contract = select_contract

    # -- 판정 훅 ---------------------------------------------------------
    def is_expired_error(self, text: str) -> bool:
        """credential 만료·인증 실패 신호인지(기존 ``_is_expired_error`` 위임)."""
        if self._is_expired_error is not None:
            return bool(self._is_expired_error(text))
        module = _gateway_module()
        if module is None or not text:
            return False
        try:
            # 인스턴스 상태를 사용하지 않는 판정이므로 unbound 호출로 재사용한다.
            return bool(module.GatewayClient._is_expired_error(None, text))
        except Exception:  # pragma: no cover - 시그니처 변경 방어
            return False

    def looks_unsupported_model(self, status: Any, text: str) -> bool:
        """unknown model / unsupported route 거부 신호인지(기존 판정 위임)."""
        if self._looks_unsupported_model is not None:
            return bool(self._looks_unsupported_model(status, text))
        module = _gateway_module()
        if module is None or not text:
            return False
        try:
            return bool(module.GatewayClient._looks_unsupported_model(None, status, text))
        except Exception:  # pragma: no cover - 시그니처 변경 방어
            return False

    def is_prefix_form_error(self, text: str) -> bool:
        """교정 가능한 model ID 형태 오류인지(기존 ``_is_prefix_form_error`` 위임)."""
        if self._is_prefix_form_error is not None:
            return bool(self._is_prefix_form_error(text))
        module = _gateway_module()
        if module is None or not text:
            return False
        try:
            return bool(module._is_prefix_form_error(text))
        except Exception:  # pragma: no cover - 시그니처 변경 방어
            return False

    # -- 기록·정규화 훅 --------------------------------------------------
    def record_denied_model(self, model_id: str) -> bool:
        """기존 ``_record_denied_model``로 denylist에 등록한다(성공 여부 반환)."""
        if not isinstance(model_id, str) or not model_id:
            return False
        if self._record_denied_model is not None:
            self._record_denied_model(model_id)
            return True
        module = _server_module()
        if module is None:
            return False
        try:
            module._record_denied_model(model_id)
        except Exception:  # pragma: no cover - 기존 구현 변경 방어
            return False
        return True

    def extract_denied_model(self, text: str) -> str:
        """오류 문자열에서 거부된 model ID를 추출한다(없으면 빈 문자열)."""
        if not isinstance(text, str) or not text:
            return ""
        if self._extract_denied_model is not None:
            found = self._extract_denied_model(text)
            return found if isinstance(found, str) else ""
        module = _server_module()
        if module is None or not hasattr(module, "_extract_denied_model_from_error"):
            return ""
        try:
            found = module._extract_denied_model_from_error(text)
        except Exception:  # pragma: no cover - 기존 구현 변경 방어
            return ""
        return found if isinstance(found, str) else ""

    def normalize_model_key(self, model_id: str) -> str:
        """model ID 비교 키(prefix 제거 + 소문자) — 기존 정규화 규칙 위임."""
        if not isinstance(model_id, str) or not model_id:
            return ""
        if self._normalize_model_key is not None:
            key = self._normalize_model_key(model_id)
            return key if isinstance(key, str) else ""
        module = _server_module()
        if module is not None and hasattr(module, "_normalize_model_key"):
            try:
                key = module._normalize_model_key(model_id)
                if isinstance(key, str):
                    return key
            except Exception:  # pragma: no cover - 기존 구현 변경 방어
                pass
        gateway = _gateway_module()
        if gateway is not None:
            try:
                return str(gateway._strip_region_prefix(model_id)).lower()
            except Exception:  # pragma: no cover - 기존 구현 변경 방어
                pass
        return model_id.lower()


#: 기본 훅(무상태 — 지연 연결만 하므로 공유해도 안전하다).
_DEFAULT_HOOKS = FailureHooks()


def default_hooks() -> FailureHooks:
    """기본 :class:`FailureHooks`(기존 구현 지연 연결)."""
    return _DEFAULT_HOOKS


# ─────────────────────────────────────────────────────────────────
# 작은 술어·유틸
# ─────────────────────────────────────────────────────────────────
def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else contracts.UNDETERMINED


def _flag(signals: Mapping[str, Any], *keys: str) -> bool:
    """주어진 키 중 하나라도 참인지(명시 신호 우선 판정용)."""
    return any(bool(signals.get(key)) for key in keys)


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    number = int(value)
    return number if number > 0 else 0


def _haystack(signals: Mapping[str, Any]) -> str:
    """분류에 사용할 오류 문자열을 하나로 모아 소문자화한다(메모리 내 판정 전용)."""
    parts: list[str] = []
    for key in ("errorText", "message", "detail", "bodyText", "reason"):
        value = signals.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    error = signals.get("error")
    if error is not None and not isinstance(error, type):
        try:
            rendered = str(error)
        except Exception:  # pragma: no cover - __str__ 실패 방어
            rendered = ""
        if rendered:
            parts.append(rendered)
    return " ".join(parts).lower()


def _error_names(signals: Mapping[str, Any]) -> frozenset[str]:
    """예외 타입 이름 집합(MRO 포함)을 만든다 — 하위 클래스도 신호로 잡힌다."""
    names: set[str] = set()
    declared = signals.get("errorType")
    if isinstance(declared, str) and declared:
        names.add(declared)
    error = signals.get("error")
    if error is not None:
        cls = error if isinstance(error, type) else type(error)
        mro = getattr(cls, "__mro__", None)
        if mro:
            names.update(getattr(item, "__name__", "") for item in mro)
        else:  # pragma: no cover - 비정상 타입 방어
            names.add(getattr(cls, "__name__", ""))
    names.discard("")
    return frozenset(names)


def _http_status(signals: Mapping[str, Any]) -> int | None:
    for key in ("httpStatus", "status", "statusCode"):
        value = signals.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return int(value)
    return None


def _has_signal(text: str, needles: Sequence[str]) -> bool:
    return any(needle in text for needle in needles)


def safe_cause(text: Any, *, limit: int = CAUSE_MAX_LENGTH) -> str:
    """원인 문자열을 사용자·로그에 안전한 형태로 만든다(마스킹 후 200자 절단).

    ``key=value``/``key: value`` 형태에서 키가 비밀 분류(``store.classify_key`` →
    ``DROP``)인 값만 기존 ``mask_token`` 규칙으로 마스킹한다(신규 마스킹 규칙을
    만들지 않는다). 그 다음 :data:`CAUSE_MAX_LENGTH`로 절단한다.
    """
    if text is None:
        return ""
    raw = text if isinstance(text, str) else str(text)
    if not raw:
        return ""

    def _mask(match: re.Match) -> str:
        key, separator, value = match.group(1), match.group(2), match.group(3)
        if store.classify_key(key) == "DROP":
            return f"{key}{separator}{store.mask_token_for_log(value)}"
        return match.group(0)

    return _ASSIGNMENT_RE.sub(_mask, raw)[: max(0, int(limit))]


def normalized_category(category: Any) -> str:
    """닫힌 집합 밖의 범주 값을 ``unknown``으로 환원한다(상태 불변이 안전측)."""
    return str(category) if contracts.Failure_Category.has(category) else UNKNOWN


def _route_key(value: Any) -> str | None:
    return str(value) if contracts.Known_Route.has(value) else None


def _route_of(ctx: Mapping[str, Any]) -> str | None:
    """ctx에서 실패한 Known_Route를 읽는다(닫힌 집합 밖이면 ``None``)."""
    for key in ("route", "routeKey"):
        route = _route_key(ctx.get(key))
        if route is not None:
            return route
    return None


# ─────────────────────────────────────────────────────────────────
# 1) classify — Failure_Precedence 첫 일치 (Requirement 9.1)
# ─────────────────────────────────────────────────────────────────
def _effort_present(signals: Mapping[str, Any]) -> bool:
    """이 요청에 effort가 실제로 주입되었는지(effort 범주의 전제조건)."""
    if signals.get("effortInjected") is True:
        return True
    field_path = signals.get("effortFieldPath")
    if isinstance(field_path, (list, tuple)) and any(
        isinstance(segment, str) and segment for segment in field_path
    ):
        return True
    return signals.get("effortValue") is not None


def _mentions_effort(signals: Mapping[str, Any], text: str) -> bool:
    """오류 문자열이 주입한 effort field 또는 value를 지목하는지.

    field 이름·허용값은 계약(evidence)에서만 오며 이 모듈에 상수로 두지 않는다.
    """
    field_path = signals.get("effortFieldPath")
    if isinstance(field_path, (list, tuple)):
        for segment in field_path:
            if isinstance(segment, str) and len(segment) >= 2 and segment.lower() in text:
                return True
    value = signals.get("effortValue")
    if value is not None and not isinstance(value, bool):
        rendered = str(value).lower()
        if len(rendered) >= 2 and rendered in text:
            return True
    return False


def _match_authentication(signals, text, status, names, hooks) -> bool:
    if _flag(signals, "credentialFailure", "authFailure", "authenticationFailure"):
        return True
    if names & AUTH_ERROR_NAMES:
        return True
    if status == 401:
        return True
    return hooks.is_expired_error(text)


def _match_allowlist(signals, text, status, names, hooks) -> bool:
    if _flag(signals, "allowlistDenied"):
        return True
    # 기존 `_maybe_record_denied_from_error`와 동일한 문자열 신호만 사용한다.
    # `OpenAIModelUnsupported`의 메시지도 이 문자열을 담고 있으면 여기서 걸린다.
    return _has_signal(text, ALLOWLIST_DENIAL_SIGNALS)


def _match_effort_mismatch(signals, text, status, names, hooks) -> bool:
    if not _effort_present(signals):
        return False
    if _flag(signals, "effortFieldRejected", "effortValueRejected", "effortMismatch"):
        return True
    if not _mentions_effort(signals, text):
        return False
    return _has_signal(text, EFFORT_UNKNOWN_FIELD_SIGNALS) or _has_signal(
        text, EFFORT_INVALID_VALUE_SIGNALS
    )


def _match_route_capability_mismatch(signals, text, status, names, hooks) -> bool:
    if _flag(signals, "routeRejected", "routeCapabilityMismatch", "unsupportedRoute"):
        return True
    if names & ROUTE_MISMATCH_ERROR_NAMES:
        return True
    return hooks.looks_unsupported_model(status, text)


def _match_quota(signals, text, status, names, hooks) -> bool:
    if _flag(signals, "quotaExceeded"):
        return True
    if names & QUOTA_ERROR_NAMES:
        return True
    return status == 403


def _match_transient(signals, text, status, names, hooks) -> bool:
    if _flag(signals, "timeout", "connectionFailed", "emptyOutput", "partialOutput", "transient"):
        return True
    if names & TRANSIENT_ERROR_NAMES:
        return True
    if status is not None and (status == 429 or status >= 500 or status == -1):
        return True
    return _has_signal(text, TRANSIENT_SIGNALS)


def _match_request_validation(signals, text, status, names, hooks) -> bool:
    if _flag(signals, "requestValidation"):
        return True
    if names & REQUEST_VALIDATION_ERROR_NAMES:
        return True
    if status in (400, 422):
        return True
    if hooks.is_prefix_form_error(text):
        return True
    return _has_signal(text, REQUEST_VALIDATION_SIGNALS)


def _match_unknown(signals, text, status, names, hooks) -> bool:
    return True  # 종단 범주 — 위 어느 신호에도 맞지 않음


#: 범주별 판정기(PRECEDENCE 순서로 평가된다).
_MATCHERS: dict[str, Callable[..., bool]] = {
    AUTHENTICATION: _match_authentication,
    ALLOWLIST: _match_allowlist,
    EFFORT_MISMATCH: _match_effort_mismatch,
    ROUTE_CAPABILITY_MISMATCH: _match_route_capability_mismatch,
    QUOTA: _match_quota,
    TRANSIENT: _match_transient,
    REQUEST_VALIDATION: _match_request_validation,
    UNKNOWN: _match_unknown,
}


def classify(signals: Mapping[str, Any] | None = None, *, hooks: FailureHooks | None = None) -> str:
    """신호 집합을 Failure_Precedence 첫 일치 범주로 분류한다(Requirement 9.1).

    여러 신호가 겹쳐도 결과는 :data:`PRECEDENCE` 순서의 첫 일치 범주로 유일하게
    결정된다(결정론적). 입력이 비면 ``unknown``이다(상태 불변이 안전측).

    Args:
        signals: :data:`SIGNAL_KEYS`의 부분집합. 명시 boolean 신호가 문자열 추론보다
            우선한다.
        hooks: 판정 훅. 기본은 기존 구현 지연 연결(:func:`default_hooks`).

    Returns:
        :data:`PRECEDENCE`의 값 중 하나.
    """
    sig = _mapping(signals)
    hooks = hooks or default_hooks()
    text = _haystack(sig)
    status = _http_status(sig)
    names = _error_names(sig)
    for category in PRECEDENCE:
        if _MATCHERS[category](sig, text, status, names, hooks):
            return category
    return UNKNOWN  # pragma: no cover - _match_unknown이 항상 참이다


# ─────────────────────────────────────────────────────────────────
# Eligible_Contract / Fallback_Order (design "verified fallback 규칙")
# ─────────────────────────────────────────────────────────────────
def is_eligible_contract(
    entry: Mapping[str, Any],
    route_key: str,
    *,
    purpose: str | None = None,
    ctx: Mapping[str, Any] | None = None,
) -> bool:
    """해당 route가 Eligible_Contract인지 판정한다.

    조건(전부 AND): route 상태 `SUPPORTED` · allowlist `ALLOWED` · 완전한
    Route_Contract · Current_Evidence reference 보유 · 요청 목적(`purposes`) 충족.
    ctx에 `revision`·`catalogFingerprint`가 주어지면 entry의 값과 일치해야 한다
    (Current_Evidence 현재성). Malformed 판정과 Active_Model 노출은 Activation_Gate가
    담당하며 여기서는 fallback 후보 자격만 본다.
    """
    entry_map = _mapping(entry)
    ctx = _mapping(ctx)
    routes = entry_map.get("routes")
    route_entry = routes.get(route_key) if isinstance(routes, Mapping) else None
    if not isinstance(route_entry, Mapping):
        return False
    if route_entry.get("status") != contracts.Route_Support_Status.SUPPORTED:
        return False
    if route_entry.get("allowlist") != contracts.Allowlist_Result.ALLOWED:
        return False

    contract = route_entry.get("contract")
    if not isinstance(contract, Mapping) or not contracts.route_contract_is_complete(contract):
        return False
    if not (_text(route_entry.get("evidenceRef")) or _text(contract.get("evidenceRef"))):
        return False
    if purpose is not None:
        purposes = contract.get("purposes")
        if not isinstance(purposes, (list, tuple)) or purpose not in purposes:
            return False

    for field in ("revision", "catalogFingerprint"):
        expected = _text(ctx.get(field))
        if expected and _text(entry_map.get(field)) != expected:
            return False
    return True


def eligible_contracts(
    entry: Mapping[str, Any],
    *,
    purpose: str | None = None,
    ctx: Mapping[str, Any] | None = None,
    exclude_routes: Iterable[str] = (),
) -> list[dict]:
    """Eligible_Contract를 Fallback_Order(`fallbackRank` 오름차순)로 반환한다.

    동일 rank는 Known_Route 선언 순서로 안정 정렬하므로 결과는 입력 순서와
    무관하게 결정론적이다. ``exclude_routes``에 든 route는 후보에서 제외한다
    (실패한 route로의 "fallback"은 fallback이 아니다).
    """
    entry_map = _mapping(entry)
    excluded = {route for route in exclude_routes if isinstance(route, str)}
    found: list[tuple[int, int, dict]] = []
    for index, route_key in enumerate(contracts.KNOWN_ROUTES):
        if route_key in excluded:
            continue
        if not is_eligible_contract(entry_map, route_key, purpose=purpose, ctx=ctx):
            continue
        contract = entry_map["routes"][route_key]["contract"]
        rank = contract.get("fallbackRank")
        rank = int(rank) if isinstance(rank, int) and not isinstance(rank, bool) else 0
        found.append((rank, index, copy.deepcopy(dict(contract))))
    found.sort(key=lambda item: (item[0], item[1]))
    return [contract for _, _, contract in found]


def first_eligible_contract(
    entry: Mapping[str, Any],
    *,
    purpose: str | None = None,
    ctx: Mapping[str, Any] | None = None,
    exclude_routes: Iterable[str] = (),
    hooks: FailureHooks | None = None,
) -> dict | None:
    """Fallback_Order의 **첫** Eligible_Contract만 반환한다(연쇄 이동 금지).

    ``hooks.select_contract``가 주어지면 Request_Router 구현에 위임하고, 그 결과가
    제외 대상 route이거나 Eligible_Contract가 아니면 채택하지 않는다.
    """
    hooks = hooks or default_hooks()
    entry_map = _mapping(entry)
    excluded = {route for route in exclude_routes if isinstance(route, str)}

    if hooks.select_contract is not None:
        try:
            selected = hooks.select_contract(entry_map, purpose, dict(_mapping(ctx)))
        except Exception:  # pragma: no cover - 외부 구현 방어
            selected = None
        if isinstance(selected, Mapping):
            route_key = _route_key(selected.get("routeKey"))
            if (
                route_key is not None
                and route_key not in excluded
                and is_eligible_contract(entry_map, route_key, purpose=purpose, ctx=ctx)
            ):
                return copy.deepcopy(dict(selected))
            return None

    candidates = eligible_contracts(
        entry_map, purpose=purpose, ctx=ctx, exclude_routes=excluded
    )
    return candidates[0] if candidates else None


# ─────────────────────────────────────────────────────────────────
# 2) apply — 범주별 상태 전이 (design "Error Handling" 표와 1:1)
# ─────────────────────────────────────────────────────────────────
def _mark_effort_stale(entry: dict, route_key: str) -> bool:
    """해당 route의 Effort_Contract만 `STALE`로 전이한다(전이 발생 시 ``True``).

    `UNSUPPORTED`(evidence로 확정된 더 강한 부정)는 그대로 둔다. 이미 `STALE`이면
    변화가 없다. route·Verification 상태는 건드리지 않는다(Requirement 9.6).
    """
    effort = entry.get("effort")
    if not isinstance(effort, dict):
        return False
    effort_entry = effort.get(route_key)
    if not isinstance(effort_entry, dict):
        return False
    status = effort_entry.get("status")
    if status in (
        str(contracts.Effort_Support_Status.UNSUPPORTED),
        str(contracts.Effort_Support_Status.STALE),
    ):
        return False
    effort_entry["status"] = str(contracts.Effort_Support_Status.STALE)
    return True


def _filter_catalog(
    catalog_model_ids: Any, model_ids: Sequence[str], hooks: FailureHooks
) -> list[str] | None:
    """Editor_Model_Catalog 목록에서 대상 model ID를 제거한 새 목록을 만든다.

    ctx가 목록을 주지 않으면 ``None``(호출자가 자기 목록에서 제거한다).
    비교는 기존 `_normalize_model_key` 규칙(prefix 제거 + 소문자)으로 한다.
    """
    if not isinstance(catalog_model_ids, (list, tuple, set, frozenset)):
        return None
    denied_keys = {hooks.normalize_model_key(model_id) for model_id in model_ids}
    denied_keys.discard("")
    return [
        model_id
        for model_id in catalog_model_ids
        if not (
            isinstance(model_id, str) and hooks.normalize_model_key(model_id) in denied_keys
        )
    ]


def apply(
    entry: Mapping[str, Any] | None,
    category: str,
    ctx: Mapping[str, Any] | None = None,
    *,
    hooks: FailureHooks | None = None,
) -> dict:
    """범주별 capability 상태 전이를 적용한 결과를 반환한다(입력 entry는 변경하지 않음).

    design.md "Error Handling" 분류표·"상태 보존 불변식" 표와 1:1로 대응한다.

    - ``transient``·``unknown``·``authentication``·``quota``·``request-validation``
      → Verification / Route / Effort / Editor_Model_Catalog **모두 불변**
      (Requirement 9.8, 9.9, 9.20, 10.16~10.18). 반환 entry는 입력의 사본이며
      canonical serialization이 동일하다.
    - ``allowlist`` → entry `REJECTED`(9.2) · 해당 route `allowlist = REJECTED` ·
      기존 ``_record_denied_model`` 호출 · Editor_Model_Catalog에서 Exact_Model_ID
      제거(9.3).
    - ``effort-mismatch`` → **해당 Effort_Contract만** `STALE`(9.4, 9.5). base
      Route_Support_Status와 Verification_Status는 유지(9.6).
    - ``route-capability-mismatch`` → 해당 route `UNSUPPORTED` · 그 route의 effort는
      참조 무효이므로 `STALE` · 다른 `SUPPORTED` route가 있으면 entry 상태 유지 ·
      Eligible_Contract가 없으면 Editor_Model_Catalog에서 제외.

    상태를 바꾼 경우에는 파생 모드 상태(`syncSupport`/`asyncSupport`/
    `streamingSupport`)와 `capabilityFingerprint`를 재계산한다. 재계산하지 않으면
    entry가 Malformed_Entry(FINGERPRINT_MISMATCH)로 판정되어 Activation_Gate가
    통째로 탈락시키므로, "다른 `SUPPORTED` route가 있으면 유지"가 성립하지 않는다.

    Args:
        entry: Capability_Map entry(없거나 dict가 아니면 전이 없음).
        category: Failure_Category. 닫힌 집합 밖이면 ``unknown``으로 환원한다.
        ctx: :data:`CTX_KEYS` 참조. ``route``가 없으면 route 범위 전이는 생략한다.
        hooks: 기록·정규화 훅.

    Returns:
        ``{"category", "entry", "changed", "transitions", "removeFromCatalog",
        "catalogModelIds", "deniedModelIds", "deniedRecorded",
        "fingerprintRecomputed", "reason"}``
    """
    ctx = _mapping(ctx)
    hooks = hooks or default_hooks()
    category = normalized_category(category)
    source = _mapping(entry)
    updated = copy.deepcopy(dict(source))
    route = _route_of(ctx)

    transitions: list[str] = []
    denied_model_ids: list[str] = []
    denied_recorded = False
    remove_from_catalog = False
    catalog_model_ids: list[str] | None = None

    if category == ALLOWLIST and source:
        updated["verificationStatus"] = str(contracts.Verification_Status.REJECTED)
        transitions.append(TRANSITION_ENTRY_REJECTED)

        routes = updated.get("routes")
        if route is not None and isinstance(routes, dict) and isinstance(routes.get(route), dict):
            routes[route]["allowlist"] = str(contracts.Allowlist_Result.REJECTED)
            transitions.append(TRANSITION_ROUTE_ALLOWLIST_REJECTED)

        # denylist 등록 대상: 오류가 지목한 ID → 실제 전송 ID → entry의 Exact_Model_ID.
        seen: set[str] = set()
        for candidate in (
            hooks.extract_denied_model(_text(ctx.get("errorText"))),
            _text(ctx.get("invocationModelId")),
            _text(ctx.get("modelId")),
            _text(source.get("modelId")),
        ):
            key = hooks.normalize_model_key(candidate)
            if candidate and key and key not in seen:
                seen.add(key)
                denied_model_ids.append(candidate)
        for model_id in denied_model_ids:
            denied_recorded = hooks.record_denied_model(model_id) or denied_recorded

        remove_from_catalog = True
        transitions.append(TRANSITION_CATALOG_MODEL_REMOVED)
        catalog_model_ids = _filter_catalog(
            ctx.get("catalogModelIds"), denied_model_ids, hooks
        )

    elif category == EFFORT_MISMATCH and source and route is not None:
        if _mark_effort_stale(updated, route):
            transitions.append(TRANSITION_EFFORT_CONTRACT_STALE)

    elif category == ROUTE_CAPABILITY_MISMATCH and source and route is not None:
        routes = updated.get("routes")
        if isinstance(routes, dict) and isinstance(routes.get(route), dict):
            routes[route]["status"] = str(contracts.Route_Support_Status.UNSUPPORTED)
            transitions.append(TRANSITION_ROUTE_UNSUPPORTED)
        if _mark_effort_stale(updated, route):
            transitions.append(TRANSITION_EFFORT_CONTRACT_STALE)

    fingerprint_recomputed = False
    if transitions:
        # 파생 모드 상태 → fingerprint 순서로 재계산한다(Complete_Record 유지).
        try:
            capability_map.apply_mode_support(updated, mode_hints=ctx.get("modeHints"))
            capability_map.recompute_fingerprint(updated)
            fingerprint_recomputed = True
        except Exception:  # 정규화 불가 entry — 저장값을 그대로 두고 사실만 보고한다
            fingerprint_recomputed = False

    if category == ROUTE_CAPABILITY_MISMATCH and TRANSITION_ROUTE_UNSUPPORTED in transitions:
        # "Eligible_Contract 없으면 제외" — 남은 후보가 없을 때만 목록에서 제거한다.
        if not eligible_contracts(updated, purpose=ctx.get("purpose"), ctx=ctx):
            remove_from_catalog = True
            transitions.append(TRANSITION_CATALOG_MODEL_REMOVED)
            catalog_model_ids = _filter_catalog(
                ctx.get("catalogModelIds"), [_text(updated.get("modelId"))], hooks
            )

    return {
        "category": category,
        "entry": updated,
        "changed": not canonicalizer.canonical_equal(source, updated),
        "transitions": transitions,
        "removeFromCatalog": remove_from_catalog,
        "catalogModelIds": catalog_model_ids,
        "deniedModelIds": denied_model_ids,
        "deniedRecorded": denied_recorded,
        "fingerprintRecomputed": fingerprint_recomputed,
        "reason": safe_cause(ctx.get("errorText")),
    }


# ─────────────────────────────────────────────────────────────────
# 3) plan_recovery — 허용된 복구 수단만 계획한다
# ─────────────────────────────────────────────────────────────────
def _route_is_supported(entry: Mapping[str, Any], route_key: str | None) -> bool:
    if route_key is None:
        return False
    routes = _mapping(entry).get("routes")
    route_entry = routes.get(route_key) if isinstance(routes, Mapping) else None
    return (
        isinstance(route_entry, Mapping)
        and route_entry.get("status") == contracts.Route_Support_Status.SUPPORTED
    )


def _plan(
    *,
    retry_without_effort: bool = False,
    retry_transient: bool = False,
    retry_with_correction: bool = False,
    retry_delay: float | None = None,
    fallback: dict | None = None,
    terminate: bool = False,
    reason: str = RECOVERY_NO_RECOVERY,
    retry_count: int = 0,
    retry_limit: int = 0,
) -> dict:
    return {
        "retryWithoutEffort": retry_without_effort,
        "fallbackContract": fallback,
        "terminate": terminate,
        "retryTransient": retry_transient,
        "retryWithCorrection": retry_with_correction,
        "retryDelaySeconds": retry_delay,
        "retryCount": retry_count,
        "retryLimit": retry_limit,
        "reasonCode": reason,
    }


def plan_recovery(
    entry: Mapping[str, Any] | None,
    category: str,
    ctx: Mapping[str, Any] | None = None,
    *,
    hooks: FailureHooks | None = None,
) -> dict:
    """범주별로 허용된 복구 수단을 계획한다(재시도를 직접 수행하지는 않는다).

    :func:`apply`가 반환한 **전이 후 entry**로 호출해야 한다. 그래야 방금
    `UNSUPPORTED`가 된 route나 `REJECTED`가 된 allowlist가 fallback 후보에서
    구조적으로 제외된다(미지원 route 전송 0건).

    범주별 규칙(design.md 분류표 retry·fallback 컬럼):

    - ``effort-mismatch`` — base route가 `SUPPORTED`이고 아직 사용하지 않았다면
      무-effort Baseline_Request_Body 재시도를 **정확히 1회** 허용한다(9.7).
      fallback은 사용하지 않으며, 한도를 소진했으면 오류 종료한다.
    - ``transient`` — 기존 retry 한도(:data:`TRANSIENT_MAX_RETRIES`, 백오프
      :data:`TRANSIENT_BACKOFF_SECONDS`)를 그대로 적용하고(9.10), 소진 후에만
      Eligible_Contract 첫 항목으로 fallback한다(9.11, 9.12).
    - ``allowlist`` / ``route-capability-mismatch`` — retry 없이 Eligible_Contract
      첫 항목으로 fallback한다. 후보가 없으면 오류 종료한다(9.13).
    - ``authentication`` — 기존 강제 갱신 정책에 위임한다(한도 소진 시 종료).
    - ``request-validation`` — 동일 조합 교정 요청 최대 1회.
    - ``quota`` / ``unknown`` — retry·fallback 없이 종료.

    Returns:
        ``{"retryWithoutEffort", "fallbackContract", "terminate", "retryTransient",
        "retryWithCorrection", "retryDelaySeconds", "retryCount", "retryLimit",
        "reasonCode"}``
    """
    ctx = _mapping(ctx)
    hooks = hooks or default_hooks()
    category = normalized_category(category)
    entry_map = _mapping(entry)
    route = _route_of(ctx)
    purpose = ctx.get("purpose")

    def _fallback() -> dict | None:
        excluded = (route,) if route is not None else ()
        return first_eligible_contract(
            entry_map, purpose=purpose, ctx=ctx, exclude_routes=excluded, hooks=hooks
        )

    if category == EFFORT_MISMATCH:
        used = _non_negative_int(ctx.get("effortRetryCount")) + (
            1 if ctx.get("retryWithoutEffortUsed") else 0
        )
        if not _route_is_supported(entry_map, route):
            return _plan(
                terminate=True,
                reason=RECOVERY_EFFORT_BASE_ROUTE_NOT_SUPPORTED,
                retry_count=used,
                retry_limit=EFFORT_BASELINE_MAX_RETRIES,
            )
        if used >= EFFORT_BASELINE_MAX_RETRIES:
            return _plan(
                terminate=True,
                reason=RECOVERY_EFFORT_BASELINE_USED,
                retry_count=used,
                retry_limit=EFFORT_BASELINE_MAX_RETRIES,
            )
        return _plan(
            retry_without_effort=True,
            reason=RECOVERY_EFFORT_BASELINE_RETRY,
            retry_count=used,
            retry_limit=EFFORT_BASELINE_MAX_RETRIES,
        )

    if category == TRANSIENT:
        limit = ctx.get("transientMaxRetries")
        limit = (
            int(limit)
            if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0
            else TRANSIENT_MAX_RETRIES
        )
        used = _non_negative_int(ctx.get("transientRetryCount") or ctx.get("retryCount"))
        if used < limit:
            index = min(used, len(TRANSIENT_BACKOFF_SECONDS) - 1)
            return _plan(
                retry_transient=True,
                retry_delay=TRANSIENT_BACKOFF_SECONDS[index],
                reason=RECOVERY_TRANSIENT_RETRY,
                retry_count=used,
                retry_limit=limit,
            )
        fallback = _fallback()
        return _plan(
            fallback=fallback,
            terminate=fallback is None,
            reason=RECOVERY_FALLBACK_SELECTED if fallback else RECOVERY_NO_ELIGIBLE_CONTRACT,
            retry_count=used,
            retry_limit=limit,
        )

    if category in (ALLOWLIST, ROUTE_CAPABILITY_MISMATCH):
        fallback = _fallback()
        return _plan(
            fallback=fallback,
            terminate=fallback is None,
            reason=RECOVERY_FALLBACK_SELECTED if fallback else RECOVERY_NO_ELIGIBLE_CONTRACT,
            retry_count=_non_negative_int(ctx.get("retryCount")),
        )

    if category == AUTHENTICATION:
        used = _non_negative_int(ctx.get("authRetryCount"))
        exhausted = used >= AUTH_MAX_REFRESH_ATTEMPTS
        return _plan(
            terminate=exhausted,
            reason=RECOVERY_AUTH_REFRESH_EXHAUSTED if exhausted else RECOVERY_AUTH_REFRESH_DELEGATED,
            retry_count=used,
            retry_limit=AUTH_MAX_REFRESH_ATTEMPTS,
        )

    if category == REQUEST_VALIDATION:
        used = _non_negative_int(ctx.get("correctionRetryCount"))
        if used < CORRECTION_MAX_RETRIES:
            return _plan(
                retry_with_correction=True,
                reason=RECOVERY_CORRECTION_RETRY,
                retry_count=used,
                retry_limit=CORRECTION_MAX_RETRIES,
            )
        return _plan(
            terminate=True,
            reason=RECOVERY_CORRECTION_USED,
            retry_count=used,
            retry_limit=CORRECTION_MAX_RETRIES,
        )

    # quota · unknown — retry·fallback 없음
    return _plan(
        terminate=True,
        reason=RECOVERY_NO_RECOVERY,
        retry_count=_non_negative_int(ctx.get("retryCount")),
    )


# ─────────────────────────────────────────────────────────────────
# 4) notification — User_Notification 필드 화이트리스트
# ─────────────────────────────────────────────────────────────────
def _fallback_view(ctx: Mapping[str, Any]) -> dict | str:
    """fallback 표시값을 ``{"modelId","route"}`` 또는 :data:`NO_FALLBACK`으로 만든다."""
    explicit = ctx.get("fallback")
    if isinstance(explicit, str):
        return NO_FALLBACK
    default_model = _text(ctx.get("fallbackModelId")) or _text(ctx.get("modelId"))

    if isinstance(explicit, Mapping):
        route = _route_key(explicit.get("route")) or _route_key(explicit.get("routeKey"))
        model_id = _text(explicit.get("modelId")) or default_model
        if route is not None or model_id:
            return {"modelId": model_id, "route": route or contracts.UNDETERMINED}

    contract = ctx.get("fallbackContract")
    if isinstance(contract, Mapping):
        route = _route_key(contract.get("routeKey"))
        if route is not None:
            return {"modelId": default_model, "route": route}

    return NO_FALLBACK


def _retry_count(ctx: Mapping[str, Any]) -> int:
    """표시할 retry 횟수 — 무-effort 재시도를 포함한다(design User_Notification 표).

    ``retryCount``가 주어지면 그 값을 신뢰하고(합산 책임은 호출자), 없으면 범주별
    카운터와 무-effort 재시도 사용 여부를 합산한다.
    """
    if ctx.get("retryCount") is not None:
        return _non_negative_int(ctx.get("retryCount"))
    total = (
        _non_negative_int(ctx.get("transientRetryCount"))
        + _non_negative_int(ctx.get("correctionRetryCount"))
        + _non_negative_int(ctx.get("effortRetryCount"))
    )
    if ctx.get("retryWithoutEffortUsed") and not _non_negative_int(ctx.get("effortRetryCount")):
        total += 1
    return total


def notification(
    ctx: Mapping[str, Any] | None = None, *, include_cause: bool = False
) -> dict:
    """사용자에게 표시할 User_Notification을 만든다(Requirement 9.14~9.19).

    :data:`NOTIFICATION_FIELDS`(``modelId``·``route``·``category``·``retryCount``·
    ``fallback``)만 포함한다. credential·authorization·cookie·signature·raw
    request/response body는 구조적으로 포함될 수 없다(화이트리스트 구성).

    Args:
        ctx: :data:`CTX_KEYS` 참조. ``modelId``는 라벨이 아니라 원 Exact_Model_ID다.
        include_cause: ``True``면 ``reason``(마스킹 + 200자 절단) 필드를 덧붙인다.
            기본은 ``False``로 화이트리스트를 엄격히 유지한다.

    Returns:
        ``{"modelId", "route", "category", "retryCount", "fallback"}``
        (``include_cause``면 ``"reason"`` 추가). ``fallback``은
        ``{"modelId","route"}`` 또는 :data:`NO_FALLBACK`이다.
    """
    ctx = _mapping(ctx)
    payload = {
        "modelId": _text(ctx.get("modelId"))[:CAUSE_MAX_LENGTH],
        "route": _route_key(ctx.get("route")) or _route_key(ctx.get("routeKey")) or contracts.UNDETERMINED,
        "category": normalized_category(ctx.get("category")),
        "retryCount": _retry_count(ctx),
        "fallback": _fallback_view(ctx),
    }
    if include_cause:
        payload["reason"] = safe_cause(ctx.get("errorText") or ctx.get("reason"))
    return payload


__all__ = [
    "ALLOWLIST",
    "ALLOWLIST_DENIAL_SIGNALS",
    "ALLOWLIST_ERROR_NAMES",
    "AUTHENTICATION",
    "AUTH_ERROR_NAMES",
    "AUTH_MAX_REFRESH_ATTEMPTS",
    "CAUSE_MAX_LENGTH",
    "CORRECTION_MAX_RETRIES",
    "CTX_KEYS",
    "EFFORT_BASELINE_MAX_RETRIES",
    "EFFORT_INVALID_VALUE_SIGNALS",
    "EFFORT_MISMATCH",
    "EFFORT_UNKNOWN_FIELD_SIGNALS",
    "FailureHooks",
    "NOTIFICATION_FIELDS",
    "NO_FALLBACK",
    "PRECEDENCE",
    "PRECEDENCE_COVERS_ENUM",
    "QUOTA",
    "QUOTA_ERROR_NAMES",
    "RECOVERY_REASONS",
    "REQUEST_VALIDATION",
    "REQUEST_VALIDATION_ERROR_NAMES",
    "REQUEST_VALIDATION_SIGNALS",
    "ROUTE_CAPABILITY_MISMATCH",
    "ROUTE_MISMATCH_ERROR_NAMES",
    "STATE_PRESERVING_CATEGORIES",
    "STREAM_MAX_TOKENS_REDUCTIONS",
    "SIGNAL_KEYS",
    "TRANSIENT",
    "TRANSIENT_BACKOFF_SECONDS",
    "TRANSIENT_ERROR_NAMES",
    "TRANSIENT_MAX_ATTEMPTS",
    "TRANSIENT_MAX_RETRIES",
    "TRANSIENT_SIGNALS",
    "TRANSITIONS",
    "TRANSITION_CATALOG_MODEL_REMOVED",
    "TRANSITION_EFFORT_CONTRACT_STALE",
    "TRANSITION_ENTRY_REJECTED",
    "TRANSITION_ROUTE_ALLOWLIST_REJECTED",
    "TRANSITION_ROUTE_UNSUPPORTED",
    "UNKNOWN",
    "apply",
    "classify",
    "default_hooks",
    "eligible_contracts",
    "first_eligible_contract",
    "is_eligible_contract",
    "normalized_category",
    "notification",
    "plan_recovery",
    "safe_cause",
]
