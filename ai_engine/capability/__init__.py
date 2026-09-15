"""capability 패키지 — Gateway 모델 활성화 게이트와 effort capability 지원.

이 패키지는 근거(Authoritative_Evidence) 기반 모델 활성화와 exact model·route별
effort 계약을 다룬다. 기존 Gateway 통합(`ai_engine/gateway_module.py`,
`ai_engine/openai_adapter.py`, `ai_engine/server.py`)은 재사용하며 복제하지 않는다.

**import 부작용 없음** — 이 진입점은 어떤 하위 모듈도 import하지 않는다.
서버·CLI·테스트는 필요한 모듈만 명시적으로 import한다.

    from ai_engine.capability import contracts
    from ai_engine.capability import canonicalizer, capability_map, activation_gate

하위 모듈(구현 순서와 무관한 목록):
    contracts          enum·계약 스키마·검증기·Malformed_Entry 판정기
    store              userData 하위 영속 경로 해석과 원자적 쓰기
    canonicalizer      canonical 정규화·직렬화·fingerprint
    baseline_inspector Repository_Baseline_Inspector
    capability_map     Capability_Map 로드·갱신·상태 전이
    activation_gate    Activation_Gate(순수 함수)
    request_builder    Request_Router + EffortBoundClient
    effort_settings    Effort_Settings 정규화·무효화
    failure_handler    Failure_Precedence 분류·전이·복구
    evidence_collector probe 오케스트레이션·정제

참조: .kiro/specs/gateway-models-effort-support/design.md
"""

# 하위 모듈은 여기서 import하지 않는다(부작용 금지). 이름만 문서화 목적으로 남긴다.
SUBMODULES: tuple[str, ...] = (
    "contracts",
    "store",
    "canonicalizer",
    "baseline_inspector",
    "capability_map",
    "activation_gate",
    "request_builder",
    "effort_settings",
    "failure_handler",
    "evidence_collector",
)

__all__ = ["SUBMODULES"]
