"""deep-research-engine: Deep_Research_Pipeline (멀티에이전트 오케스트레이션).

design.md "Components and Interfaces / 6) deep_research.py" 명세 구현. 딥리서치는
**신규 그래프를 만들지 않고** 기존 LangGraph 오케스트레이션 프리미티브(planner /
DAG 웨이브 / aggregate / evaluator / checkpoint / store)를 재사용한다(요구사항 6.6 / 16.3).

이 파일은 축 B(딥리서치) 전체를 담는다 — **Planner(Task 12.1), Wave 실행(Task 12.2),
Generator 종합(Task 12.3), Evaluator/심화 판정(Task 12.4), Coordinator 진입점·체크포인트/
스토어 재사용·동기 실행 seam(Task 12.6)**:

- ``plan_subqueries(query, deps_or_gw, config)`` — 원 조사 질의를 하위 조사 질의(subtask)로
  분해한다(≤ ``AE_RESEARCH_MAX_SUBQUERIES``). 각 하위 질의는 ``depends_on`` 을 가진다.
  분해는 ``supervisor._make_plan`` 과 **동형의 Gateway 호출 패턴**(GatewayChatModel +
  toolChoice 강제 스키마 + ``asyncio.wait_for``)을 차용한다. 모든 LLM 호출은 Bedrock
  Gateway 경유만 사용하며 boto3/SDK 를 직접 호출하지 않는다(steering project.md / gateway.md).
- ``plan_waves(subqueries)`` — 하위 질의의 ``depends_on`` 을 위상정렬해 실행 웨이브로
  분할한다. 웨이브 분할 로직은 **재구현하지 않고** ``dag.sanitize_depends_on`` +
  ``dag.topological_waves`` 를 **그대로 재사용**한다(요구사항 6.6 / 16.3).
- ``run_wave(wave, deps, config, prior_context)`` — 한 실행 웨이브 내 각 하위질의에 대해
  다중 소스 검색(``backend.search_web_with_fallback``/``search_academic_with_fallback`` —
  웹/논문 각 축의 제공자 폴백 체인 1차→보조→폴백을 시도해 첫 성공 제공자의 원시 응답을
  얻는다. 옵트인 게이트·질의 검증·개별 타임아웃·비차단 폴백은 backend 가 보장) → 정규화
  (``normalize.parse_search_result``/``parse_paper_result``) → 융합·중복제거·재랭킹
  (``rank.merge_and_rerank`` — RRF) → 상위 K(``AE_RESEARCH_FETCH_PER_SUBQUERY``) 본문 수집
  (``backend.fetch_url_raw``)을 수행해 ``EvidenceSource`` 근거를 구성한다(요구사항 5.2~5.5).
  선행 Wave 근거(``prior_context``)의 source_id 를 후속 질의의 "이미 확보한 소스"로 취급해
  중복 수집을 회피한다(커버리지 단조성 P5 정합). 외부 네트워크 egress 는 ``backend`` 단일
  모듈만 경유하며(요구사항 10.4), 이 파일에서 직접 HTTP 호출을 하지 않는다.
- ``_synthesize_report(query, evidence, deps_or_gw, config)`` — Generator. 재랭킹된 근거
  (``EvidenceSource`` 목록)로 **인용 포함 Research_Report** 를 종합한다. research 도메인
  model 노드 패턴(``_common.make_model_node`` 과 동형의 GatewayChatModel 1턴 호출 +
  ``asyncio.wait_for`` 개별 await 래핑)과 ``supervisor.aggregate_node`` 의 "여러 산출물을
  하나의 일관된 답변으로 종합" 로직을 차용한다. 모든 LLM 호출은 Bedrock Gateway 경유만
  사용한다(요구사항 10.1). 각 사실 주장이 근거 집합의 source_id 를 대괄호로 최소 1개 인용
  하도록 프롬프트로 강제하고, 생성물에서 인용된 source_id 를 근거 집합과 대조해
  ``citations={"verified":[...], "unverified":[...]}`` 로 분류한다(요구사항 5.6/6.3/8.1).
  Gateway 부재/실패/타임아웃/빈 응답 시 근거 목록을 인용과 함께 나열하는 결정적 폴백
  리포트를 생성하고, 근거가 전무하면 "외부 근거 미확보" 리포트로 비차단 종료한다
  (요구사항 13.3). 인용 검증기(``rag/citation.verify_citations``)의 소스 id 대조 정식
  통합은 Task 13.1 이 완료했다(``_verify_citations`` 가 소스 식별자 집합 대조 모드로
  재사용하며, rag 부재 시 동일 규약 폴백). 미검증 비율 확정 + 근거성 메타데이터 병합
  (``rag/answer_quality.enhance_answer`` 의 local_grounding_score+faithfulness ·
  ``agent_system/grounding_gate`` 재사용)은 Task 13.2 가 완료해, answerQuality 형태
  메타데이터를 ``ResearchReport.answer_quality`` / ``ResearchMetrics.grounding_score`` 로
  반영한다(요구사항 9.5/8.4).

비차단(무회귀) 원칙:
- Gateway 부재/실패/타임아웃/빈 응답 → 단일 하위 질의(원 질의) 폴백. 예외를 전파하지 않는다.
- 상한(``max_subqueries``) 초과 시 절단한다.
- 순수 스케줄링(``plan_waves``)은 LLM 스택(langchain/게이트웨이) 없이도 import·동작하도록
  LLM 빌딩블록은 ``plan_subqueries`` 내부에서 지연 import 한다(Gateway 없이 순수 부분 검증 가능).
- Wave 실행(``run_wave``)은 옵트인/동의가 off 면 ``backend.web_research_enabled()``가 False
  이므로 외부 호출 없이 빈 근거를 반환한다(무회귀 P15). 제공자·본문 조회 실패는 해당 소스를
  제외하고 진행한다(비차단 P8 / 요구사항 3.5·13).

Requirements: 5.1~5.8, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8.1, 9.6, 10.1, 12.2, 16.3
Design: "6) deep_research.py — Deep_Research_Pipeline (멀티에이전트)"
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, List, Optional
from urllib.parse import quote

# 웨이브 분할은 순수 DAG 함수를 그대로 재사용한다(재구현 금지 — 요구사항 6.6 / 16.3).
# dag 는 부작용·네트워크·무거운 의존이 전혀 없는 순수 모듈이라 최상위 import 가 안전하다.
from ai_engine.agent_system.dag import sanitize_depends_on, topological_waves
from ai_engine.research import backend, normalize, rank
from ai_engine.research.config import DeepResearchConfig
from ai_engine.research.models import EvidenceSource, ResearchMetrics, ResearchReport

# Planner 기본 모델(steering: Planner=Opus 의도이나, 스트리밍 신뢰 경로 기준 Sonnet 4.5 기본).
# deps._DEFAULT_PLANNER_MODEL 와 동일 값으로 정렬한다(주입 시 deps.model_planner 우선).
_DEFAULT_PLANNER_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"


# ─────────────────────────────────────────────────────────────────────────────
# Planner 도구 스키마 / 시스템 프롬프트 (supervisor._make_plan 의 select_plan 과 동형)
# ─────────────────────────────────────────────────────────────────────────────
# 도메인 라우팅용 supervisor._PLAN_TOOL 과 달리, 딥리서치 하위 질의는 "도메인" 개념이 없고
# 모두 조사 질의(subtask)이다. 따라서 schema 는 {id, subtask, depends_on} 만 갖는다. dag 의
# sanitize_depends_on/topological_waves 는 id/depends_on 만 사용하고 나머지 키는 보존하므로,
# 이 형태는 _make_plan 산출물과 동형이며 웨이브 분할에 그대로 투입된다.
_SUBQUERY_PLAN_TOOL: dict = {
    "name": "plan_subqueries",
    "description": (
        "원 조사 질의를 서로 보완적인 하위 조사 질의들로 분해한다. 각 하위 질의는 고유 id, "
        "구체적 조사 질의문(subtask), 선행 완료가 필요한 하위 질의 id 목록(depends_on)을 가진다. "
        "서로 독립적으로 조사 가능한 질의는 depends_on 을 비워 병렬 조사되게 하고, 앞 질의의 "
        "결과가 있어야 조사 가능한 질의는 그 앞 질의 id 를 depends_on 에 넣는다(예: 개요 조사 → "
        "세부 쟁점 심화). 질의가 단순하면 하위 질의를 1개만 만든다."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "subqueries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "하위 질의 고유 식별자(예: q1)",
                            },
                            "subtask": {
                                "type": "string",
                                "description": "구체적 하위 조사 질의문(한국어 또는 원 질의 언어)",
                            },
                            "depends_on": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "이 하위 질의 조사 전 완료되어야 할 선행 하위 질의 id 목록",
                            },
                        },
                        "required": ["id", "subtask"],
                    },
                }
            },
            "required": ["subqueries"],
        }
    },
}

_SUBQUERY_PLANNER_SYSTEM_PROMPT = (
    "너는 심층 조사(deep research) 플래너다. 사용자의 원 조사 질의를 서로 보완적인 하위 조사 "
    "질의들로 분해해 plan_subqueries 도구를 호출한다. 각 하위 질의에는 고유 id(q1, q2 …), "
    "구체적 조사 질의문(subtask), 그리고 선행 하위 질의 id 목록(depends_on)을 부여한다. 서로 "
    "독립적으로 조사 가능한 질의는 depends_on 을 비워 병렬 조사되게 하고, 앞 질의 결과에 "
    "의존하는 질의는 그 앞 질의 id 를 depends_on 에 넣는다. 질의가 단순하면 하위 질의를 1개로 "
    "둔다. 하위 질의 개수는 제시된 상한을 초과하지 않는다."
)


def _planner_timeout() -> float:
    """Planner LLM 개별 호출 상한(초).

    supervisor.PLANNER_TIMEOUT 와 동일한 env(``AE_PLANNER_TIMEOUT``)·기본값(300s)을 사용한다.
    게이트웨이 /converse 의 toolConfig 비동기 S3 잡 폴링(최대 300s)과 정렬해, 게이트웨이가
    스스로 종결하기 전에 조기 폴백(분해 무력화)되지 않도록 한다.
    """
    try:
        return float(os.environ.get("AE_PLANNER_TIMEOUT", "300.0"))
    except (TypeError, ValueError):
        return 300.0


def _subquery_cap(config: Optional[DeepResearchConfig]) -> int:
    """하위 질의 상한(``AE_RESEARCH_MAX_SUBQUERIES``). 최소 1 보장(요구사항 5.1: 1개 이상)."""
    try:
        cap = int(getattr(config, "max_subqueries", 8))
    except (TypeError, ValueError):
        cap = 8
    return cap if cap >= 1 else 1


def _single_subquery(query: str) -> List[dict]:
    """단일 하위 질의(원 질의) 폴백 — 항상 ≥1개를 보장하는 비차단 결과(요구사항 5.1)."""
    return [{"id": "q0", "subtask": query, "depends_on": []}]


def _resolve_gateway_and_model(deps_or_gw: Any) -> tuple[Any, str]:
    """``deps_or_gw`` 에서 (gateway, model_id) 를 해석한다.

    유연 시그니처: 호출자는 GraphDeps 유사 객체(``.gateway`` / ``.model_planner`` 보유) 또는
    게이트웨이 클라이언트 자체를 넘길 수 있다.

    - GraphDeps 유사(``model_planner`` 필드 보유): ``.gateway`` 와 ``.model_planner``
      (미주입 시 ``.model_coding`` → 기본값)를 추출한다. gateway 가 None 이면 (None, model)
      을 반환해 호출자가 폴백하도록 한다.
    - 그 외(게이트웨이 클라이언트 직접 전달): 객체 자체를 gateway 로 간주하고 기본 모델 사용.
    - None: (None, 기본 모델).
    """
    if deps_or_gw is None:
        return None, _DEFAULT_PLANNER_MODEL
    # GraphDeps 유사 판별: dataclass 필드 model_planner 존재로 구분(게이트웨이 클라이언트는
    # 이 필드를 갖지 않는다). 역할 모델 배분(요구사항 9.2)과 정합.
    if hasattr(deps_or_gw, "model_planner") or hasattr(deps_or_gw, "gateway"):
        gateway = getattr(deps_or_gw, "gateway", None)
        model_id = (
            getattr(deps_or_gw, "model_planner", None)
            or getattr(deps_or_gw, "model_coding", None)
            or _DEFAULT_PLANNER_MODEL
        )
        return gateway, (model_id or _DEFAULT_PLANNER_MODEL)
    # 게이트웨이 클라이언트를 직접 받은 경우.
    return deps_or_gw, _DEFAULT_PLANNER_MODEL


def _build_planner_prompt(query: str, cap: int) -> str:
    """원 질의 + 상한을 Planner 입력 프롬프트로 구성."""
    return (
        f"[원 조사 질의]\n{query}\n\n"
        f"위 질의를 최대 {cap}개의 상호 보완적인 하위 조사 질의로 분해하라. 각 하위 질의에 "
        f"고유 id 와 depends_on(선행 하위 질의 id 목록)을 부여하고, 독립 조사 가능한 질의는 "
        f"depends_on 을 비워라. 질의가 단순하면 하위 질의 1개로 충분하다."
    )


def _parse_subqueries(ai: Any, cap: int) -> List[dict]:
    """Planner LLM 응답(tool_calls 우선)에서 하위 질의 리스트를 파싱한다(방어적·순수).

    - tool_calls 의 ``plan_subqueries`` args.subqueries(list)에서 항목을 추출한다.
    - 각 항목: subtask(비어있지 않은 str)만 채택, id 누락/무효는 인덱스 기반 ``q{i}`` 보정,
      depends_on 은 문자열 원소만 남긴다(누락/무효 → []). (supervisor._make_plan 과 동형)
    - 상한(cap) 초과 시 절단한다.
    - 유효 항목이 없거나 파싱 불가 → 빈 리스트 반환(호출자가 단일 폴백으로 대체).

    Note: 절단으로 인해 depends_on 이 절단된 id 를 참조할 수 있으나, 웨이브 분할 시
    ``plan_waves`` → ``sanitize_depends_on`` 이 존재하지 않는 참조를 제거하므로 안전하다.
    """
    tool_calls = getattr(ai, "tool_calls", None) or []
    if not isinstance(tool_calls, (list, tuple)):
        return []
    for tc in tool_calls:
        args = tc.get("args") if isinstance(tc, dict) else None
        if not isinstance(args, dict) or not isinstance(args.get("subqueries"), list):
            continue
        out: List[dict] = []
        for i, it in enumerate(args["subqueries"]):
            if not isinstance(it, dict):
                continue
            sub = it.get("subtask")
            if not isinstance(sub, str) or not sub.strip():
                continue
            raw_id = it.get("id")
            item_id = (
                raw_id.strip()
                if isinstance(raw_id, str) and raw_id.strip()
                else f"q{i}"
            )
            raw_deps = it.get("depends_on")
            deps_list = (
                [d for d in raw_deps if isinstance(d, str)]
                if isinstance(raw_deps, (list, tuple))
                else []
            )
            out.append({"id": item_id, "subtask": sub.strip(), "depends_on": deps_list})
        if out:
            return out[:cap]
    return []


async def plan_subqueries(
    query: str, deps_or_gw: Any, config: Optional[DeepResearchConfig] = None
) -> List[dict]:
    """원 조사 질의를 하위 조사 질의(subtask)로 분해한다 — Planner (요구사항 5.1 / 6.2).

    ``supervisor._make_plan`` 과 **동형의 Gateway 호출 패턴**을 차용한다: GatewayChatModel 에
    toolChoice 로 단일 스키마(``plan_subqueries``)를 강제하고, 개별 ``ainvoke`` 를
    ``asyncio.wait_for`` 로 감싼다. 모든 LLM 호출은 Bedrock Gateway 경유만 사용한다.

    Args:
        query: 원 조사 질의. ``strip()`` 후 사용한다.
        deps_or_gw: GraphDeps 유사 객체(``.gateway``/``.model_planner``) 또는 게이트웨이
            클라이언트. gateway 가 없으면 단일 폴백을 반환한다(비차단).
        config: ``DeepResearchConfig``. None 이면 ``from_env()`` 로 로딩한다(상한
            ``max_subqueries`` 사용).

    Returns:
        하위 질의 dict 리스트 ``[{"id": str, "subtask": str, "depends_on": list[str]}, ...]``.
        항상 1개 이상이며 ``max_subqueries`` 이하이다(요구사항 5.1). Gateway 실패/빈 응답 시
        단일 하위 질의(원 질의)로 폴백한다.

    Invariant:
        - 예외를 전파하지 않는다(모든 실패는 단일 폴백으로 비차단 처리).
        - LLM 빌딩블록(GatewayChatModel 등)은 지연 import 하여, 순수 스케줄링(``plan_waves``)이
          LLM 스택 없이도 동작하도록 한다.
    """
    if config is None:
        config = DeepResearchConfig.from_env()
    cap = _subquery_cap(config)

    q = query.strip() if isinstance(query, str) else ""
    fallback = _single_subquery(q)

    # 빈 질의: 외부 상태 없이 단일(빈) 하위 질의로 비차단 반환(≥1개 불변식 유지).
    if not q:
        return fallback

    gateway, model_id = _resolve_gateway_and_model(deps_or_gw)
    if gateway is None:
        # Gateway 부재 → 네트워크/LLM 미호출, 단일 폴백(무회귀).
        return fallback

    # LLM 빌딩블록 지연 import(supervisor 와 동일 자산). import 실패 시 폴백(비차단).
    try:
        from ai_engine.agent_system.chat_model_adapter import (
            GatewayChatModel,
            GatewayModelError,
        )
        from langchain_core.messages import HumanMessage, SystemMessage
    except Exception:  # noqa: BLE001 — LLM 스택 부재는 비차단 폴백.
        return fallback

    try:
        # prefer_streaming=True: reasoning 메타 노드 지연 최적화(supervisor planner 와 동형).
        llm = GatewayChatModel(
            gateway=gateway, model_id=model_id, prefer_streaming=True
        ).bind_tools([_SUBQUERY_PLAN_TOOL], tool_choice="plan_subqueries")
        messages = [
            SystemMessage(content=_SUBQUERY_PLANNER_SYSTEM_PROMPT),
            HumanMessage(content=_build_planner_prompt(q, cap)),
        ]
        ai = await asyncio.wait_for(llm.ainvoke(messages), timeout=_planner_timeout())
    except (asyncio.TimeoutError, GatewayModelError, Exception):  # noqa: BLE001
        # 타임아웃/게이트웨이 오류/기타 → 단일 폴백(비차단, 요구사항 13 정합).
        return fallback

    parsed = _parse_subqueries(ai, cap)
    return parsed if parsed else fallback


def plan_waves(subqueries: List[dict]) -> List[List[dict]]:
    """하위 질의를 ``depends_on`` 위상 순서 실행 웨이브로 분할한다 (요구사항 6.6 / 16.3).

    웨이브 분할은 **재구현하지 않고** 순수 DAG 함수를 그대로 재사용한다:
    ``sanitize_depends_on`` 으로 존재하지 않는 id 참조를 제거한 뒤 ``topological_waves`` 로
    위상 웨이브를 만든다. 두 함수는 임의 입력에 대해 방어적으로 동작하고(예외 없음), 순환
    감지 시 단일 웨이브로 폴백하며, 웨이브 수 ≤ 하위 질의 수를 보장한다(dag.py 불변식 계승).

    Args:
        subqueries: ``plan_subqueries`` 산출물(또는 동형의 {id, depends_on} 보유 dict 리스트).

    Returns:
        웨이브 리스트(각 웨이브는 dict 리스트). 각 웨이브의 하위 질의는 선행 ``depends_on`` 이
        모두 이전 웨이브에 존재한다. 입력이 비면 ``[]``.
    """
    return topological_waves(sanitize_depends_on(subqueries))


# =============================================================================
# [12.2] Wave 실행 — 다중 소스 검색 → 정규화 → 융합·재랭킹 → 본문 수집
# =============================================================================
# run_wave 는 ``plan_waves`` 산출물의 한 원소(= 하위질의 dict 리스트)를 받아, 웨이브 내 각
# 하위질의에 대해 다음을 수행한다(요구사항 5.2~5.5):
#   1. backend.search_web_with_fallback + backend.search_academic_with_fallback 로 웹/논문 각
#      축의 제공자 폴백 체인(1차→보조→폴백)을 시도해 첫 성공 제공자의 원시 응답을 얻는다
#      (옵트인 게이트/질의 검증/개별 타임아웃/비차단 폴백은 backend 가 보장 — egress 는
#      backend 단일 모듈, 요구사항 10.4/13).
#   2. normalize.parse_search_result / parse_paper_result 로 정규 결과(SearchResult /
#      PaperResult)로 변환한다. 제공자별 필드 매핑은 normalize→providers 어댑터에 위임하며
#      여기서 복제하지 않는다(재구현 금지). 이 모듈은 배열 컨테이너 위치만 라우팅한다.
#   3. rank.merge_and_rerank 로 RRF 융합 + dedup + (신뢰도 기반) 재랭킹(재사용 자산 조합).
#   4. 상위 K(config.fetch_per_subquery) 소스를 backend.fetch_url_raw 로 본문 수집해
#      EvidenceSource(models)로 구성한다. 조회 실패 소스는 제외한다(비차단 — 요구사항 3.5).
#
# 선행 Wave 근거(prior_context)의 source_id 는 후속 질의의 "이미 확보한 소스"로 취급해 중복
# 수집을 회피한다(선행 근거를 후속 컨텍스트로 사용 — 5.2, 커버리지 단조성 P5 정합). 옵트인/
# 동의가 off 면 backend.web_research_enabled()가 False 이므로 외부 호출 없이 빈 근거를 반환
# 한다(무회귀 P15). 순수 스케줄링(plan_waves)과 달리 backend egress 를 수행하지만, 모든
# 네트워크 호출은 backend 단일 모듈을 경유한다(요구사항 10.4). 예외는 전파하지 않는다(P8).

# backend 원시 응답의 "결과 항목 배열" 컨테이너 경로(backend.py 가 만든 응답 구조와 정합).
# 여기서는 배열 위치만 라우팅하고, 개별 항목의 필드 매핑은 normalize.parse_* 에 위임한다
# (providers 어댑터를 직접 import·사용하지 않으며 필드 매핑을 복제하지 않는다 — 재구현 금지).
_WEB_ITEM_PATHS: dict = {
    "tavily": (("results",),),
    "exa": (("results",),),
    "brave": (("web", "results"), ("results",)),
}
_ACADEMIC_ITEM_PATHS: dict = {
    "semantic_scholar": (("data",), ("results",)),
    "openalex": (("results",), ("data",)),
    "arxiv": (("feed", "entry"), ("entries",), ("entry",)),
    "pubmed": (("PubmedArticleSet", "PubmedArticle"), ("PubmedArticle",), ("articles",)),
}
_DEFAULT_ITEM_PATHS: tuple = (("results",), ("data",))


def _field(s, name: str, default: str = ""):
    """dataclass(getattr) 또는 dict(.get) 양쪽에서 필드를 안전하게 읽는다(순수)."""
    if s is None:
        return default
    v = s.get(name, default) if isinstance(s, dict) else getattr(s, name, default)
    return v if v is not None else default


def _extract_raw_items(raw, paths) -> List[dict]:
    """backend 원시 응답(dict)에서 결과 항목(dict) 배열을 추출한다(순수·예외 없음).

    ``paths`` 후보를 순서대로 따라가 첫 list(항목 배열)를 반환한다. 항목이 단일 dict 로
    온 경우(단일 결과 XML→dict)는 ``[dict]`` 로 감싼다. backend 가 구조화 오류 dict
    (``{"error": ...}``)를 반환했거나, 예외 객체이거나, 어떤 경로도 배열을 못 찾으면 빈
    리스트를 반환한다(비차단 — P8). 필드 매핑은 하지 않는다(normalize.parse_* 위임).
    """
    if not isinstance(raw, dict) or raw.get("error"):
        return []
    for path in paths:
        node = raw
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, list):
            return [it for it in node if isinstance(it, dict)]
        if isinstance(node, dict):
            return [node]
    return []


def _wave_subqueries(wave) -> List[str]:
    """웨이브(하위질의 dict 리스트)에서 조사 질의문(subtask) 리스트를 추출한다(순수).

    ``plan_waves`` 산출물의 한 원소를 받는다. 각 항목은 ``{"subtask": ...}`` dict(또는
    질의 문자열)이며, 앞뒤 공백을 제거한 비어있지 않은 subtask 만 순서대로 취한다.
    """
    if not isinstance(wave, (list, tuple)):
        return []
    out: List[str] = []
    for sq in wave:
        if isinstance(sq, dict):
            sub = sq.get("subtask")
        elif isinstance(sq, str):
            sub = sq
        else:
            sub = None
        if isinstance(sub, str) and sub.strip():
            out.append(sub.strip())
    return out


def _prior_source_ids(prior_context) -> set:
    """선행 Wave 근거의 source_id 집합(이미 확보한 소스 — 중복 수집 회피용, P5)."""
    ids: set = set()
    if not prior_context:
        return ids
    for es in prior_context:
        sid = _field(es, "source_id", "")
        if isinstance(sid, str) and sid:
            ids.add(sid)
    return ids


def _fetch_target(source) -> str:
    """정규 결과에서 본문 조회 대상 URL 을 파생한다(웹=url, 논문=doi_or_url)."""
    return (
        _field(source, "url", "")
        or _field(source, "doi_or_url", "")
        or _field(source, "url_or_doi", "")
    )


def _published_of(source) -> str:
    """정규 결과의 발행일 문자열을 파생한다(웹=published_date, 논문=year>0 → 문자열)."""
    published = _field(source, "published_date", "")
    if isinstance(published, str) and published.strip():
        return published
    try:
        y = int(_field(source, "year", 0))
    except (TypeError, ValueError):
        y = 0
    return str(y) if y > 0 else ""


def _to_evidence(source, fetch_result) -> EvidenceSource:
    """정규 결과 + 조회 본문 → 근거 소스(EvidenceSource)를 구성한다(순수, models 정합).

    ``authority`` 는 ``rank.source_authority`` 로 산출하고(요구사항 9.4), 본문은 조회
    결과 텍스트를 담는다(backend 가 이미 크기 상한을 적용). 자격증명은 담지 않는다(P9).
    """
    return EvidenceSource(
        source_id=_field(source, "source_id", ""),
        title=_field(source, "title", ""),
        url_or_doi=_fetch_target(source),
        provider=_field(source, "provider", ""),
        content=getattr(fetch_result, "text", "") or "",
        published_date=_published_of(source),
        authority=rank.source_authority(source),
    )


def _dedup_evidence(evidences: List[EvidenceSource]) -> List[EvidenceSource]:
    """근거 소스를 source_id 기준 first-wins 로 중복 제거한다(순서 보존, 순수).

    source_id 는 providers 가 canonical_url/canonical_doi 로 생성한 정규 키라 문자열
    동등 비교로 안전하다(dedup 키 규약 정합). 빈 source_id 는 유일 키가 없어 그대로
    보존한다(조회 성공 소스는 URL 이 있어 통상 비어있지 않다).
    """
    seen: set = set()
    out: List[EvidenceSource] = []
    for es in evidences:
        sid = es.source_id
        if sid and sid in seen:
            continue
        if sid:
            seen.add(sid)
        out.append(es)
    return out


def _fallback_raw_and_provider(res) -> tuple:
    """폴백 체인 결과에서 ``(provider, raw)`` 를 안전 추출한다(순수·예외 없음).

    ``backend.search_web_with_fallback``/``search_academic_with_fallback`` 는 성공 시
    ``{"ok": True, "provider": name, "raw": {...}, ...}`` 를, 실패 시 ``ok=False`` 구조화
    신호(옵트인 off/불량 질의/전 제공자 실패)를 반환한다. ``asyncio.gather`` 의 예외
    포획으로 dict 가 아닌 값(예외 객체)이 올 수도 있다. 어느 경우든 성공(``ok`` +
    유효 provider/raw)이 아니면 ``(None, None)`` 으로 비차단 폴백한다(P8).
    """
    if not isinstance(res, dict) or not res.get("ok"):
        return None, None
    provider = res.get("provider")
    raw = res.get("raw")
    if not isinstance(provider, str) or not provider or not isinstance(raw, dict):
        return None, None
    return provider, raw


def _normalize_web_fallback(res) -> List:
    """웹 폴백 체인 결과 → 정규 ``SearchResult`` 리스트(실패/빈 응답 → ``[]``).

    첫 성공 웹 제공자의 원시 응답에서 결과 배열을 추출(``_extract_raw_items``)해
    ``normalize.parse_search_result`` 로 정규화한다. 제공자별 필드 매핑은
    normalize→providers 어댑터에 위임하며 여기서 복제하지 않는다(재구현 금지).
    """
    provider, raw = _fallback_raw_and_provider(res)
    if provider is None:
        return []
    items = _extract_raw_items(raw, _WEB_ITEM_PATHS.get(provider, _DEFAULT_ITEM_PATHS))
    return [normalize.parse_search_result(provider, it) for it in items]


def _normalize_academic_fallback(res) -> List:
    """논문 폴백 체인 결과 → 정규 ``PaperResult`` 리스트(실패/빈 응답 → ``[]``).

    첫 성공 논문 제공자의 원시 응답에서 결과 배열을 추출해
    ``normalize.parse_paper_result`` 로 정규화한다(필드 매핑은 어댑터에 위임).
    """
    provider, raw = _fallback_raw_and_provider(res)
    if provider is None:
        return []
    items = _extract_raw_items(
        raw, _ACADEMIC_ITEM_PATHS.get(provider, _DEFAULT_ITEM_PATHS)
    )
    return [normalize.parse_paper_result(provider, it) for it in items]


async def _search_subquery_sources(query: str, config: DeepResearchConfig):
    """하위질의 1건에 대해 웹/논문 폴백 체인 검색 → 정규화한다(축별 순위 리스트 + 합집합).

    ``backend.search_web_with_fallback`` / ``search_academic_with_fallback`` 로 웹·논문 각
    축의 제공자 폴백 체인(1차→보조→폴백)을 **병렬 시도**해 첫 성공 제공자의 원시 응답을
    받는다. 옵트인 게이트·질의 검증·개별 타임아웃·비차단 폴백은 모두 backend 가 보장하며
    (egress 는 backend 단일 모듈 — 요구사항 10.4), 각 성공 응답을 ``normalize.parse_*`` 로
    정규 결과 리스트로 변환한다. 실패 축(구조화 오류/예외/빈 응답)은 빈 결과로 건너뛴다
    (비차단 — P8/요구사항 13.2).

    Returns:
        ``(per_provider_ranklists, all_sources)`` — 축별(웹/논문) 정규 결과 순위 리스트의
        리스트와 전체 합집합. ``merge_and_rerank`` 의 RRF 순위 입력/순열 풀로 쓰인다
        (다중 소스 융합 — 요구사항 5.4).
    """
    # 웹/논문 폴백 체인을 병렬 시도한다(각 축은 첫 성공 제공자만 반환 — 폴백 계약).
    # egress 는 backend 단일 모듈만 경유하며, gather 예외 포획으로 비차단을 보장한다(P8).
    web_res, academic_res = await asyncio.gather(
        backend.search_web_with_fallback(
            query, top_k=config.top_k, timeout=config.search_timeout
        ),
        backend.search_academic_with_fallback(
            query, top_k=config.top_k, timeout=config.search_timeout
        ),
        return_exceptions=True,
    )

    per_provider_ranklists: list = []
    all_sources: list = []

    web_results = _normalize_web_fallback(web_res)
    if web_results:
        per_provider_ranklists.append(web_results)
        all_sources.extend(web_results)

    academic_results = _normalize_academic_fallback(academic_res)
    if academic_results:
        per_provider_ranklists.append(academic_results)
        all_sources.extend(academic_results)

    return per_provider_ranklists, all_sources


async def _run_subquery(
    query: str, config: DeepResearchConfig, prior_ids: set
) -> List[EvidenceSource]:
    """하위질의 1건: 검색 → 정규화 → 융합·재랭킹 → 상위 K 본문 수집 → 근거 구성.

    조회 실패 소스는 제외하고(요구사항 3.5), 선행 Wave 에서 이미 확보한 source_id 는
    건너뛴다(중복 수집 회피 — P5). 예외를 전파하지 않는다(비차단).
    """
    per_provider_ranklists, all_sources = await _search_subquery_sources(query, config)
    if not all_sources:
        return []

    # RRF 융합 + dedup + (신뢰도 기반) 재랭킹 — 결과는 dedup 된 소스의 순열(P4).
    ranked = rank.merge_and_rerank(query, per_provider_ranklists, all_sources)

    # 선행 Wave 근거와 중복인 소스는 제외(커버리지 단조성 — P5).
    if prior_ids:
        ranked = [s for s in ranked if _field(s, "source_id", "") not in prior_ids]

    # 상위 K 소스만 본문 수집(요구사항 5.3, config.fetch_per_subquery).
    k = config.fetch_per_subquery if config.fetch_per_subquery > 0 else 0
    top = ranked[:k]
    if not top:
        return []

    fetch_tasks = [
        backend.fetch_url_raw(
            _fetch_target(s),
            timeout=config.fetch_timeout,
            max_chars=config.fetch_max_chars,
        )
        for s in top
    ]
    fetched = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    evidence: List[EvidenceSource] = []
    for source, fr in zip(top, fetched):
        # 조회 예외/실패/타임아웃/비 http(s) 소스는 ok=False(또는 예외) → 제외(요구사항 3.5).
        if not getattr(fr, "ok", False):
            continue
        evidence.append(_to_evidence(source, fr))
    return evidence


async def run_wave(
    wave,
    deps=None,
    config: Optional[DeepResearchConfig] = None,
    prior_context: Optional[List[EvidenceSource]] = None,
) -> List[EvidenceSource]:
    """실행 웨이브 1개를 수행해 근거 소스(EvidenceSource) 목록을 반환한다 (요구사항 5.2~5.5).

    웨이브 내 각 하위질의(``plan_subqueries``→``plan_waves`` 산출물)에 대해 다중 제공자
    검색(backend) → 정규화(normalize) → 융합·재랭킹(``rank.merge_and_rerank``) → 상위 K
    (``AE_RESEARCH_FETCH_PER_SUBQUERY``) 본문 수집(``backend.fetch_url_raw``)을 수행하고,
    조회 성공 소스만 ``EvidenceSource`` 로 구성한다. 하위질의는 같은 웨이브라 서로 독립
    (``depends_on`` 충족)이므로 병렬 실행하고, 결과는 source_id 기준으로 중복 제거해 반환한다.

    Args:
        wave: 한 실행 웨이브 — 하위질의 dict(``{"id","subtask","depends_on"}``) 리스트.
            ``plan_waves`` 산출물의 한 원소를 그대로 받는다(질의 문자열 리스트도 허용).
        deps: GraphDeps 유사 객체/게이트웨이(12.3 Generator·12.6 Coordinator 배선용).
            Wave 의 검색·수집 단계는 LLM 을 호출하지 않으며(재랭킹은 결정적 RRF+신뢰도),
            현재 이 인자를 사용하지 않는다(향후 동기 재랭킹 어댑터 주입 지점).
        config: ``DeepResearchConfig``. None 이면 ``from_env()`` 로 로딩한다.
        prior_context: 선행 Wave 에서 확보한 근거 목록. 그 source_id 는 이미 확보한 소스로
            취급해 중복 수집을 회피한다(선행 근거를 후속 컨텍스트로 사용 — 5.2, P5).

    Returns:
        이 웨이브에서 수집·중복제거된 ``EvidenceSource`` 목록. 옵트인/동의가 off 이거나
        (무회귀 P15) 하위질의가 없거나 외부 근거를 확보하지 못하면 빈 리스트(비차단).

    Invariant:
        - 예외를 전파하지 않는다(모든 제공자·조회 실패는 비차단 폴백 — P8/요구사항 13).
        - 외부 네트워크 egress 는 backend 단일 모듈만 경유한다(요구사항 10.4).
    """
    if config is None:
        config = DeepResearchConfig.from_env()

    # 옵트인/동의 off → 외부 호출 없이 빈 근거(무회귀, P15). backend 도 함수별로 게이트하나
    # 여기서 선제 확인해 불필요한 태스크 구성을 피한다(외부 미도입 상태와 동등).
    if not backend.web_research_enabled():
        return []

    subqueries = _wave_subqueries(wave)
    if not subqueries:
        return []

    prior_ids = _prior_source_ids(prior_context)

    # 같은 웨이브의 하위질의는 depends_on 이 충족된 독립 질의이므로 병렬 실행한다.
    results = await asyncio.gather(
        *[_run_subquery(q, config, prior_ids) for q in subqueries],
        return_exceptions=True,
    )

    merged: List[EvidenceSource] = []
    for r in results:
        if isinstance(r, list):  # 하위질의 실행 예외/취소는 list 가 아니므로 건너뛴다(비차단)
            merged.extend(r)
    return _dedup_evidence(merged)


# =============================================================================
# [12.3] Generator — 인용 포함 Research_Report 종합 (요구사항 5.6 / 6.3 / 8.1 / 10.1)
# =============================================================================
# _synthesize_report 는 재랭킹된 근거(run_wave 산출 EvidenceSource 목록)를 받아 인용 포함
# Research_Report 를 종합한다. 두 기존 자산을 **동형으로 차용**한다(재구현 금지):
#   1. research 도메인 model 노드(_common.make_model_node): GatewayChatModel 을 1턴
#      호출하고, ``llm.ainvoke(...)`` 라는 **개별 await 하나만** ``asyncio.wait_for`` 로
#      감싼다(스트림 루프는 감싸지 않음 — API_NOTES CRITICAL 2). 모델은 deps.model_generator
#      (기본 Sonnet 4.5)를 사용한다.
#   2. supervisor.aggregate_node: "여러 산출물(여기선 근거 소스들)을 하나의 일관된 답변으로
#      종합"하는 편집자 프롬프트 + SystemMessage/HumanMessage 구성 + 실패/타임아웃 비차단
#      폴백 패턴.
# 모든 LLM 호출은 Bedrock Gateway 경유만 사용하며 boto3/anthropic/openai 를 직접 호출하지
# 않는다(요구사항 10.1 / steering). 각 사실 주장이 근거 집합의 source_id 를 대괄호로 최소 1개
# 인용하도록 프롬프트로 강제하고(요구사항 8.1), 생성물에서 인용된 source_id 를 추출해 근거
# 집합과 대조하여 verified/unverified 로 분류한다(요구사항 5.6). 이 대조는 Task 13.1 에서
# rag/citation.verify_citations 재사용(소스 식별자 집합 대조 모드, _verify_citations)으로
# 정식 통합됐다. 근거성 메타데이터 병합(answer_quality/grounding_gate 재사용)은 Task 13.2 가
# 완료해, answerQuality 형태 메타데이터를 ResearchReport.answer_quality 로 반영한다(요구사항 9.5/8.4).

# Generator 기본 모델(deps._DEFAULT_GENERATOR_MODEL 와 동일 — Generator=Sonnet, 요구사항 9.4).
_DEFAULT_GENERATOR_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# 종합 프롬프트에 실을 소스당 본문 발췌 상한(문자). 근거 본문은 backend 가 이미 fetch_max_chars
# (기본 100k)로 잘랐으나, 다수 소스를 종합 프롬프트에 실으면 과대해지므로 소스당 발췌를 추가로
# 제한한다(aggregate_node 의 _collect_worker_texts limit 철학 계승). env 로 조정 가능.
def _generator_source_excerpt_chars() -> int:
    """종합 프롬프트의 소스당 본문 발췌 상한(문자). 형식 오류/미설정 → 기본 2000."""
    try:
        v = int(os.environ.get("AE_RESEARCH_GENERATOR_SOURCE_CHARS", "2000"))
    except (TypeError, ValueError):
        return 2000
    return v if v > 0 else 2000


def _generator_timeout() -> float:
    """Generator LLM 개별 호출 상한(초).

    supervisor.AE_AGGREGATE_TIMEOUT 와 동일한 기본값(300s)을 사용한다(게이트웨이 /converse
    read timeout 및 비동기 S3 잡 폴링 상한과 정렬 — 조기 폴백 방지). ``AE_GENERATOR_TIMEOUT``
    로 조정 가능. 값은 호출 시점에 읽어 테스트 토글을 허용한다.
    """
    try:
        return float(os.environ.get("AE_GENERATOR_TIMEOUT", "300.0"))
    except (TypeError, ValueError):
        return 300.0


# 인용 토큰 추출용 정규식 — source_id 스킴(web:<canonical_url> / doi:<canonical_doi>)에 정합.
# 대괄호 인용([...])이든 본문 내 노출이든 무관하게 "web:" 또는 "doi:" 로 시작하는 토큰을
# 공백·대괄호 경계까지 포착한다(canonical_url 은 공백을 %-인코딩하므로 URL 내부에 공백 없음).
_SOURCE_ID_TOKEN_RE = re.compile(r"(?:web|doi):[^\s\[\]]+")
# 인용 토큰 말미에 붙기 쉬운 문장부호(닫는 소괄호·쉼표·마침표 등)를 제거한다. ':' 는 제외해
# 스킴 프리픽스(web:/doi:)를 훼손하지 않는다.
_CITATION_TRAILING_PUNCT = ".,;)"


_GENERATOR_SYSTEM_PROMPT = (
    "너는 심층 조사(deep research) 종합가다. 아래에 제공된 근거 소스들만 사용해 사용자의 원 "
    "조사 질의에 대한 인용 포함 종합 리포트를 한국어 마크다운으로 작성한다.\n"
    "규칙:\n"
    "- 모든 사실 주장에는 그 근거가 된 소스의 식별자(source_id)를 대괄호로 최소 1개 인용한다 "
    "(예: [web:https://example.com/a] 또는 [doi:10.1000/xyz]).\n"
    "- 인용 식별자는 제공된 근거 목록에 있는 source_id 를 그 문자열 그대로 사용한다. 목록에 "
    "없는 식별자를 지어내지 않는다.\n"
    "- 근거에서 확인되지 않는 내용은 추측하거나 덧붙이지 않는다.\n"
    "- 근거들이 서로 상충하면 그 사실을 명시하고 각 출처를 함께 인용한다.\n"
    "- 간결한 제목과 문단으로 구성하고, 필요하면 핵심 요점을 목록으로 정리한다."
)


def _content_to_text(content: Any) -> str:
    """AIMessage.content(문자열 또는 멀티모달 list)를 평문 텍스트로 평탄화한다(순수).

    supervisor._content_to_text 와 동형. content 가 list(멀티모달 파트)면 각 파트의
    text 만 이어 붙이고, None 은 ""로, 그 외는 str()로 환원한다.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )
    if content is None:
        return ""
    return str(content)


def _resolve_gateway_and_generator_model(deps_or_gw: Any) -> tuple[Any, str]:
    """``deps_or_gw`` 에서 (gateway, generator_model_id) 를 해석한다(_resolve_gateway_and_model 동형).

    - GraphDeps 유사(``model_generator``/``gateway`` 보유): ``.gateway`` 와 ``.model_generator``
      (미주입 시 ``.model_coding`` → 기본값)를 추출한다. Generator 역할 모델 배분(요구사항 9.4)
      과 정합한다. gateway 가 None 이면 (None, model) 을 반환해 호출자가 폴백하도록 한다.
    - 그 외(게이트웨이 클라이언트 직접 전달): 객체 자체를 gateway 로 간주하고 기본 모델 사용.
    - None: (None, 기본 모델).
    """
    if deps_or_gw is None:
        return None, _DEFAULT_GENERATOR_MODEL
    if hasattr(deps_or_gw, "model_generator") or hasattr(deps_or_gw, "gateway"):
        gateway = getattr(deps_or_gw, "gateway", None)
        model_id = (
            getattr(deps_or_gw, "model_generator", None)
            or getattr(deps_or_gw, "model_coding", None)
            or _DEFAULT_GENERATOR_MODEL
        )
        return gateway, (model_id or _DEFAULT_GENERATOR_MODEL)
    return deps_or_gw, _DEFAULT_GENERATOR_MODEL


def _evidence_source_ids(evidence: List[EvidenceSource]) -> List[str]:
    """근거 목록의 source_id 를 순서 보존·중복 제거해 반환한다(순수).

    빈 source_id 는 유일 인용 키가 될 수 없으므로 제외한다.
    """
    out: List[str] = []
    seen: set = set()
    for es in evidence or []:
        sid = _field(es, "source_id", "")
        if isinstance(sid, str) and sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _build_generator_prompt(query: str, evidence: List[EvidenceSource]) -> str:
    """원 질의 + 근거 소스 목록(source_id/제목/본문 발췌)을 Generator 입력 프롬프트로 구성(순수).

    각 근거를 ``source_id`` 와 함께 나열해, 모델이 그 source_id 를 대괄호 인용에 그대로
    사용하도록 유도한다(요구사항 8.1). 소스당 본문은 발췌 상한으로 잘라 프롬프트 과대화를
    막는다. 자격증명은 프롬프트에 포함하지 않는다(P9 — EvidenceSource 는 애초에 자격증명을
    담지 않는다).
    """
    excerpt_cap = _generator_source_excerpt_chars()
    parts: List[str] = [f"[원 조사 질의]\n{query}"]

    lines: List[str] = [
        "[근거 소스 목록] — 각 항목의 source_id 를 인용에 그대로 사용하라(목록에 없는 식별자 생성 금지)."
    ]
    for i, es in enumerate(evidence or [], 1):
        sid = _field(es, "source_id", "")
        title = _field(es, "title", "") or _field(es, "url_or_doi", "")
        content = _field(es, "content", "")
        block = [f"[{i}] source_id: {sid}" if sid else f"[{i}] (source_id 없음)"]
        if title:
            block.append(f"    제목: {title}")
        if isinstance(content, str) and content.strip():
            block.append(f"    본문 발췌: {content.strip()[:excerpt_cap]}")
        lines.append("\n".join(block))
    parts.append("\n\n".join(lines))

    parts.append(
        "위 근거만 사용해 원 조사 질의에 대한 종합 리포트를 한국어 마크다운으로 작성하라. "
        "모든 사실 주장에는 해당 근거의 source_id 를 대괄호로 최소 1개 인용하라 "
        "(예: [web:https://example.com/a])."
    )
    return "\n\n".join(parts)


def _fallback_report_markdown(query: str, evidence: List[EvidenceSource]) -> str:
    """Gateway 부재/실패/타임아웃 시 결정적 폴백 리포트(순수·비차단 — 요구사항 13.3).

    근거가 있으면 각 근거를 그 source_id 인용과 함께 나열해 "각 항목이 최소 1개 source_id 를
    인용"하는 형태를 유지한다(요구사항 8.1 의 degraded 준수). 근거가 전무하면 "외부 근거
    미확보" 표시를 담은 리포트로 비차단 종료한다(요구사항 13.3).
    """
    q = query or ""
    if not evidence:
        return (
            f"# 조사 결과: {q}\n\n"
            "외부 근거를 확보하지 못했습니다(외부 근거 미확보). 확보된 근거가 없어 "
            "인용 가능한 사실 주장을 생성하지 않았습니다."
        )
    lines = [f"# 조사 결과: {q}", "", "확보된 근거 요약:"]
    for es in evidence:
        sid = _field(es, "source_id", "")
        title = _field(es, "title", "") or _field(es, "url_or_doi", "") or sid or "(제목 미상)"
        cite = f" [{sid}]" if sid else ""
        lines.append(f"- {title}{cite}")
    return "\n".join(lines)


def _extract_cited_source_ids(report_markdown: str) -> List[str]:
    """리포트 마크다운에서 인용된 source_id 토큰을 순서 보존·중복 제거 추출한다(순수).

    ``web:``/``doi:`` 로 시작하는 토큰을 공백·대괄호 경계까지 포착하고, 말미 문장부호를
    제거한다. 대괄호 인용 여부와 무관하게 동작하므로 모델이 인용 형식을 다소 달리해도
    견고하다(비차단 산출).
    """
    if not isinstance(report_markdown, str) or not report_markdown:
        return []
    out: List[str] = []
    seen: set = set()
    for m in _SOURCE_ID_TOKEN_RE.finditer(report_markdown):
        sid = m.group(0).rstrip(_CITATION_TRAILING_PUNCT)
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def _classify_citations(cited_ids: List[str], evidence_ids: set) -> dict:
    """인용된 source_id 를 근거 집합 존재성으로 verified/unverified 분류한다(순수 폴백).

    근거 집합에 존재하는 인용은 verified, 존재하지 않는 인용(dangling)은 unverified 로
    분류한다(요구사항 8.2/8.3 정합 — 미검증도 차단하지 않음). 순서는 보존한다.

    Task 13.1 이후로 정식 경로는 ``rag/citation.verify_citations`` 재사용
    (``_verify_citations_via_rag``)이며, 이 함수는 rag 자산을 import 할 수 없을 때만
    쓰이는 **비차단 폴백**이다(동일 판정 규약: 인용 소스 id ∈ 근거 소스 id 집합 = verified,
    P6 참조 무결성 방향 보존).
    """
    verified: List[str] = []
    unverified: List[str] = []
    for sid in cited_ids:
        (verified if sid in evidence_ids else unverified).append(sid)
    return {"verified": verified, "unverified": unverified}


def _encode_source_id(sid: Any) -> str:
    """source_id 를 슬래시·역슬래시 없는 토큰으로 인코딩한다(순수·단사).

    ``rag/citation.verify_citations`` 의 파일 매칭은 완전 일치뿐 아니라 **경로 접미
    일치**(``rf.endswith("/"+cf)`` / ``cf.endswith("/"+rf)``)와 라인 범위 겹침까지 허용
    한다(코드 인용의 ``파일경로:시작-끝`` 규약용). 딥리서치 인용은 라인 개념이 없고
    "인용 소스 id ∈ 근거 소스 id 집합"의 **정확 멤버십**으로 판정해야 하므로(P6), 접미
    일치가 오검증(exact 아님에도 verified)을 일으키지 않도록 source_id 를 슬래시가 전혀
    없는 토큰으로 인코딩한다.

    ``urllib.parse.quote(sid, safe="")`` 는 ``/``→``%2F``·``\\``→``%5C`` 를 포함해 영숫자
    와 ``_.-~`` 외 모든 문자를 percent-encoding 하므로, 결과 토큰에는 ``/``·``\\`` 가
    없고(따라서 ``citation._norm`` 의 ``\\``→``/`` 치환·선행 ``./`` 제거가 항등이 되고,
    ``endswith("/"+x)`` 접미 검사가 성립할 수 없다) 서로 다른 source_id 는 서로 다른
    토큰이 된다(percent-encoding 은 가역·단사). 결과적으로 ``verify_citations`` 의 파일
    매칭이 **정확 문자열 동등**으로 환원되어 verified ⊆ 근거 집합(P6)이 보장된다.

    Args:
        sid: 인코딩할 source_id(``web:<canonical_url>`` / ``doi:<canonical_doi>`` 등).
            ``None``/비문자열은 안전하게 문자열화한다(예외 없음 — P8).

    Returns:
        슬래시 없는 percent-encoded 토큰(단사). 동일 입력 → 동일 출력(결정적).
    """
    return quote(str(sid) if sid is not None else "", safe="")


def _verify_citations_via_rag(
    cited_ids: List[str], evidence_ids_ordered: List[str]
) -> Optional[dict]:
    """리포트 인용을 ``rag/citation.verify_citations`` 로 근거 소스 id 집합과 대조한다(재사용).

    design.md "7) 인용·근거 검증" 의 **소스 식별자 집합 대조 모드**를 구현한다: 인용/근거
    source_id 를 각각 ``Citation``/``RetrievedRange`` 로 승격해(파일=인코딩된 source_id,
    라인=센티넬 1..1) ``verify_citations`` 에 넘기고, 그 verified/unverified 분할을 그대로
    사용한다(분류 로직 재구현 금지 — 요구사항 8.4/16.2).

    센티넬 라인(1..1)은 모든 인용·근거의 라인 범위를 항상 겹치게 만들어 판정이 오직 파일
    (=source_id) 일치로만 결정되게 한다. ``_encode_source_id`` 가 파일 토큰에서 슬래시를
    제거하므로 ``verify_citations`` 의 접미 일치가 성립할 수 없어, 판정은 **정확한 source_id
    동등**으로 환원된다(참조 무결성 = 인용 소스 id ∈ 근거 소스 id 집합, P6).

    원본 source_id 는 ``Citation.raw`` 에 실어 ``verify_citations`` 결과에서 그대로 복원한다
    (인코딩 토큰이 아니라 원본 문자열을 반환). 입력 ``cited_ids`` 는 순서 보존·중복 제거된
    상태이므로(``_extract_cited_source_ids``) 결과 verified/unverified 도 순서 보존·중복 없이
    상호 배타적이며 합집합이 전체 인용과 같다(분류 누락 없음).

    Args:
        cited_ids: 리포트에서 추출된 인용 source_id 목록(순서 보존·중복 제거).
        evidence_ids_ordered: 근거 집합의 source_id 목록(순서 보존·중복 제거).

    Returns:
        ``{"verified": [source_id...], "unverified": [source_id...]}``. ``rag/citation``
        자산을 import 할 수 없으면 ``None`` 을 반환해 호출자가 폴백(``_classify_citations``)
        하도록 한다(비차단 — P8).
    """
    try:
        from ai_engine.rag.citation import (
            Citation,
            RetrievedRange,
            verify_citations,
        )
    except Exception:  # noqa: BLE001 — rag 자산 부재는 비차단 폴백 신호.
        return None

    # 인용/근거 source_id → Citation/RetrievedRange 승격(파일=인코딩 source_id, 라인=1..1).
    # raw 에 원본 source_id 를 보존해 결과에서 인코딩 아닌 원본 문자열을 복원한다.
    citations = [
        Citation(file=_encode_source_id(s), start_line=1, end_line=1, raw=s)
        for s in cited_ids
    ]
    retrieved = [
        RetrievedRange(file=_encode_source_id(e), start_line=1, end_line=1)
        for e in evidence_ids_ordered
    ]
    report = verify_citations(citations, retrieved)
    return {
        "verified": [c.raw for c in report.verified],
        "unverified": [c.raw for c in report.unverified],
    }


def _verify_citations(cited_ids: List[str], evidence_ids_ordered: List[str]) -> dict:
    """리포트 인용을 근거 소스 id 집합과 대조해 verified/unverified 로 분류한다 (요구사항 8.2/8.3).

    정식 경로는 ``rag/citation.verify_citations`` 재사용(``_verify_citations_via_rag`` —
    소스 식별자 집합 대조 모드)이다. rag 자산을 import 할 수 없으면 동일 판정 규약의 순수
    폴백(``_classify_citations``)으로 비차단 대체한다(요구사항 8.4/16.2 재사용 우선, P8).

    dangling citation(근거 집합에 없는 인용)은 unverified 로 표기하되 **차단하지 않는다**
    (요구사항 8.3). verified 는 항상 근거 집합 source_id 의 부분집합이다(참조 무결성 방향 —
    P6). 순서 보존·상호 배타적이며 verified∪unverified 는 전체 인용과 같다(분류 누락 없음).
    """
    via_rag = _verify_citations_via_rag(cited_ids, evidence_ids_ordered)
    if via_rag is not None:
        return via_rag
    return _classify_citations(cited_ids, set(evidence_ids_ordered))


def _unverified_ratio(citations: dict) -> float:
    """미검증 인용 비율 [0,1] 산출(전체 인용 0이면 0.0 — 요구사항 9.5).

    Task 13.2 가 이 값을 응답 메타데이터(``ResearchReport.answer_quality`` 및
    ``ResearchMetrics.citation_accuracy = 1 − 이 값``)에 근거성 메타데이터와 함께 정식
    반영한다. 순수·무예외(빈/누락 citations 도 안전 처리).
    """
    verified = citations.get("verified") or []
    unverified = citations.get("unverified") or []
    total = len(verified) + len(unverified)
    if total <= 0:
        return 0.0
    return len(unverified) / total


async def _generate_via_gateway(
    query: str, evidence: List[EvidenceSource], gateway: Any, model_id: str
) -> Optional[str]:
    """GatewayChatModel 1턴 호출로 인용 포함 종합 마크다운을 생성한다(실패/빈 응답 → None).

    research 도메인 model 노드(_common.make_model_node)와 동형: GatewayChatModel 을 도구
    바인딩 없이 1턴 호출하고 ``ainvoke`` 개별 await 하나만 ``asyncio.wait_for`` 로 감싼다
    (스트림 루프 미포함). LLM 스택(langchain/게이트웨이 어댑터)은 지연 import 하여, 순수
    부분(프롬프트/인용 추출)이 LLM 스택 없이도 동작하도록 한다. 예외/타임아웃/빈 응답은
    None 으로 환원하고(비차단), 호출자가 결정적 폴백으로 대체한다.
    """
    try:
        from ai_engine.agent_system.chat_model_adapter import (
            GatewayChatModel,
            GatewayModelError,
        )
        from langchain_core.messages import HumanMessage, SystemMessage
    except Exception:  # noqa: BLE001 — LLM 스택 부재는 비차단 폴백.
        return None

    try:
        llm = GatewayChatModel(
            gateway=gateway, model_id=model_id, prefer_streaming=True
        )
        messages = [
            SystemMessage(content=_GENERATOR_SYSTEM_PROMPT),
            HumanMessage(content=_build_generator_prompt(query, evidence)),
        ]
        ai = await asyncio.wait_for(llm.ainvoke(messages), timeout=_generator_timeout())
    except (asyncio.TimeoutError, GatewayModelError, Exception):  # noqa: BLE001
        return None

    text = _content_to_text(getattr(ai, "content", "")).strip()
    return text or None


# =============================================================================
# [13.2] 미검증 인용 비율 확정 + 근거성 메타데이터 병합 (요구사항 9.5 / 8.4)
# =============================================================================
# _synthesize_report 가 산출한 인용 분류(verified/unverified, source_id 스킴, Task 13.1)로부터:
#   (1) 미검증 인용 비율 [0,1](_unverified_ratio — 전체 인용 0이면 0.0)을 확정하고(요구사항 9.5),
#   (2) 기존 근거성 자산을 **재사용**해(재구현 금지 — 요구사항 8.4/16.2):
#       - rag/answer_quality.enhance_answer 의 local_grounding_score(로컬 임베딩, 게이트웨이 불필요)
#         + faithfulness(gw 있을 때만; 없으면 자동 skip),
#       - agent_system/grounding_gate.grounding_below(순수 근거 미달 판정),
#   (3) 프론트 answerQuality SSE 규약과 **동일한 형태**의 응답 메타데이터로 병합한다:
#       {citation:{citations_total, verified(개수), unverified(raw 목록)}, unverified_ratio,
#        grounding?:{score,...}, faithfulness?:{...}, grounding_gate?:{below_threshold,enabled}}.
#
# 이 메타데이터는 ResearchReport.answer_quality 에 실려 downstream(프론트 research-panel 의
# answerQuality 소비, 품질 하네스)이 그대로 소비할 수 있다. grounding score 는
# ResearchMetrics.grounding_score 로도 반영한다. 실제 graph-stream SSE 방출(answerQuality
# 이벤트) 배선은 Task 18.1 범위이며, 여기서는 신규 SSE 채널을 만들지 않고 데이터만 노출한다.
#
# citation 형태 주의: 딥리서치 인용은 source_id(web:/doi:) 스킴이라 enhance_answer 내부의
# citation(rag/citation.parse_citations — 코드 `경로:라인` 전용)으로는 인식되지 않는다. 따라서
# citation 은 이미 검증된 리포트 citations(Task 13.1 의 _verify_citations 결과)를 answerQuality
# 규약 형태로 환원해 쓰고(재검증 아님), enhance_answer 에서는 grounding·faithfulness 신호만 취한다.
#
# 비차단(P8): enhance_answer/grounding_gate 는 지연 import 하며, import·호출 실패나 옵트아웃
# (AE_ANSWER_QUALITY=0 → enhance_answer 가 빈 metadata 반환)이어도 최소 {citation,
# unverified_ratio} 는 항상 산출한다. 자격증명은 담지 않는다(P9 — EvidenceSource·citation 은
# 애초에 자격증명을 포함하지 않는다).

# faithfulness 컨텍스트 결합 상한(문자). verifier.build_verify_prompt 가 내부적으로 12k 로
# 자르므로 그에 맞춰 과대 결합을 미연에 방지한다(로컬 grounding 은 개별 청크를 임베딩).
_ANSWER_QUALITY_CONTEXT_CHARS = 12000


def _evidence_context_text(
    evidence: List[EvidenceSource], cap_chars: int = _ANSWER_QUALITY_CONTEXT_CHARS
) -> str:
    """근거 본문을 faithfulness 컨텍스트 텍스트로 결합한다(순수·자격증명 없음).

    각 근거의 제목+본문을 이어붙이되 총 길이를 상한(``cap_chars``)으로 자른다. 자격증명은
    EvidenceSource 에 애초에 없으므로 결과 텍스트에 포함되지 않는다(P9).
    """
    parts: List[str] = []
    used = 0
    for es in evidence or []:
        content = _field(es, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        title = _field(es, "title", "")
        block = (f"{title}\n{content}" if title else content).strip()
        remaining = cap_chars - used
        if remaining <= 0:
            break
        chunk = block[:remaining]
        parts.append(chunk)
        used += len(chunk)
    return "\n\n".join(parts)


def _citation_answer_quality(citations: dict, unverified_ratio: float) -> dict:
    """리포트 인용 분류(source_id 스킴)를 answerQuality.citation 형태로 환원한다(순수).

    ``rag/answer_quality.build_citation_metadata`` 와 동일한 형태
    ({citations_total, verified=개수(int), unverified=raw 목록})로 맞춰, 프론트 research-panel 이
    로컬 RAG 인용 경로와 동일하게 소비하게 한다(요구사항 9.5). 이미 검증된 리포트 citations
    (Task 13.1)를 형태 변환할 뿐 재검증하지 않는다. ``unverified_ratio`` 도 함께 실어 downstream
    이 별도 계산 없이 미검증 비율을 표기할 수 있게 한다.
    """
    verified = list((citations or {}).get("verified") or [])
    unverified = list((citations or {}).get("unverified") or [])
    return {
        "citations_total": len(verified) + len(unverified),
        "verified": len(verified),        # answerQuality 규약: 검증 인용 "개수"(int)
        "unverified": unverified,          # answerQuality 규약: 미검증 인용 raw 목록
        "unverified_ratio": unverified_ratio,
    }


async def _enhance_grounding_metadata(
    report_markdown: str,
    evidence: List[EvidenceSource],
    gateway: Any,
    env: Optional[dict],
) -> dict:
    """``rag/answer_quality.enhance_answer`` 재사용 → grounding+faithfulness 신호만 추출(비차단).

    enhance_answer 는 citation(코드 스킴)·grounding(local_grounding_score)·faithfulness(gw 제공
    시)를 산출하나, 딥리서치 citation 은 source_id 스킴이라 정합하지 않으므로 **grounding·
    faithfulness 만** 취한다(citation 은 _citation_answer_quality 로 별도 구성). enhance_answer 는
    EvidenceSource.content 를 grounding 청크로 사용하고(``retrieved_chunks``), 결합 컨텍스트를
    faithfulness 근거로 사용한다. import·호출 실패나 옵트아웃(빈 metadata)이면 빈 dict 를
    반환한다(비차단 — P8).
    """
    if not report_markdown:
        return {}
    try:
        from ai_engine.rag.answer_quality import enhance_answer
    except Exception:  # noqa: BLE001 — rag 자산 부재는 비차단 폴백.
        return {}

    context_text = _evidence_context_text(evidence)
    try:
        res = await enhance_answer(
            report_markdown,
            context_text,
            retrieved_chunks=list(evidence or []),
            gw=gateway,
            env=env,
        )
    except Exception:  # noqa: BLE001 — 근거성 산출 실패는 비차단.
        return {}

    meta = (res or {}).get("metadata") or {}
    out: dict = {}
    grounding = meta.get("grounding")
    if isinstance(grounding, dict):
        out["grounding"] = grounding
    faithfulness = meta.get("faithfulness")
    if isinstance(faithfulness, dict):
        out["faithfulness"] = faithfulness
    return out


def _apply_grounding_gate(answer_quality: dict, env: Optional[dict]) -> None:
    """``grounding_gate.grounding_below`` 재사용 → 근거 미달 신호를 메타데이터에 주입(비차단·in-place).

    grounding_gate 의 순수 판정(faithfulness 우선, 없으면 로컬 grounding.score 임계 비교)을
    그대로 재사용해 근거 미달 여부를 기록한다(재구현 금지 — 요구사항 8.4). import·판정 실패는
    무시한다(비차단 — P8). langchain 의존이 있어 지연 import 한다(순수 부분 import 경량 유지).
    """
    try:
        from ai_engine.agent_system.grounding_gate import (
            grounding_below,
            grounding_gate_enabled,
        )
    except Exception:  # noqa: BLE001 — grounding_gate 부재는 비차단 폴백.
        return
    try:
        answer_quality["grounding_gate"] = {
            "below_threshold": bool(grounding_below(answer_quality, env)),
            "enabled": bool(grounding_gate_enabled(env)),
        }
    except Exception:  # noqa: BLE001 — 판정 실패는 비차단.
        pass


async def _build_answer_quality_metadata(
    report_markdown: str,
    evidence: List[EvidenceSource],
    citations: dict,
    unverified_ratio: float,
    deps_or_gw: Any,
    env: Optional[dict] = None,
) -> dict:
    """응답 근거성 메타데이터를 answerQuality 형태로 병합한다 (요구사항 9.5 / 8.4).

    반환 형태(프론트 answerQuality SSE 규약 정합):
        ``{"citation": {"citations_total", "verified", "unverified", "unverified_ratio"},
           "unverified_ratio": float, "grounding"?: {...}, "faithfulness"?: {...},
           "grounding_gate"?: {"below_threshold", "enabled"}}``.

    citation 은 리포트의 source_id 인용 분류(Task 13.1)를 answerQuality 규약 형태로 환원한
    것이고, grounding·faithfulness 는 ``enhance_answer`` 재사용 산출을 병합한다(가능할 때).
    ``grounding_gate.grounding_below`` 로 근거 미달 신호를 덧붙인다. 어떤 실패에도 최소
    ``{citation, unverified_ratio}`` 는 항상 반환한다(비차단 — P8). 자격증명 미포함(P9).
    """
    merged: dict = {
        "citation": _citation_answer_quality(citations, unverified_ratio),
        "unverified_ratio": unverified_ratio,
    }
    # Generator 와 동일 규약으로 gateway 를 해석(주입 없으면 None → faithfulness 자동 skip).
    gateway, _ = _resolve_gateway_and_generator_model(deps_or_gw)
    grounding_meta = await _enhance_grounding_metadata(
        report_markdown, evidence, gateway, env
    )
    merged.update(grounding_meta)
    _apply_grounding_gate(merged, env)
    return merged


def _grounding_score_of(answer_quality: dict) -> float:
    """answer_quality 메타데이터에서 로컬 grounding score [0,1] 을 추출한다(없으면 0.0).

    ``ResearchMetrics.grounding_score`` 반영용. grounding 신호가 없으면(임베더 미가용/옵트아웃)
    0.0 을 반환한다(정렬 가능한 기본값 — P8 정합).
    """
    grounding = (answer_quality or {}).get("grounding")
    if isinstance(grounding, dict):
        return _clamp01(_coerce_float(grounding.get("score"), 0.0))
    return 0.0


async def _synthesize_report(
    query: str,
    evidence: List[EvidenceSource],
    deps_or_gw: Any,
    config: Optional[DeepResearchConfig] = None,
    *,
    deepening_count: int = 0,
) -> ResearchReport:
    """Generator — 재랭킹된 근거로 인용 포함 Research_Report 를 종합한다 (요구사항 5.6/6.3/8.1).

    research 도메인 model 노드 패턴(GatewayChatModel 1턴 호출)과 ``aggregate_node`` 종합
    로직을 동형으로 차용한다. 모든 LLM 호출은 Bedrock Gateway 경유만 사용한다(요구사항 10.1).
    각 사실 주장이 근거 집합의 source_id 를 대괄호로 최소 1개 인용하도록 프롬프트로 강제하고,
    생성물에서 인용된 source_id 를 근거 집합과 대조해 verified/unverified 로 분류한다.

    Args:
        query: 원 조사 질의. ``strip()`` 후 사용한다.
        evidence: run_wave 산출 ``EvidenceSource`` 목록(재랭킹·중복제거된 근거).
        deps_or_gw: GraphDeps 유사 객체(``.gateway``/``.model_generator``) 또는 게이트웨이
            클라이언트. gateway 가 없으면 결정적 폴백 리포트를 생성한다(비차단).
        config: ``DeepResearchConfig``. None 이면 ``from_env()`` 로 로딩한다(현재 종합 자체는
            config 상한을 직접 쓰지 않으나, 시그니처 일관성/향후 확장을 위해 받는다).
        deepening_count: Coordinator(12.6)가 수행한 심화 반복 횟수를 전달한다(≤ Deepening_Cap,
            P13). 기본 0.

    Returns:
        ``ResearchReport`` — report_markdown(인용 포함 종합 본문), citations(verified/
        unverified 분류), evidence_snapshot(근거 스냅샷), metrics(인용 정확도·커버리지·
        grounding_score), deepening_count, unverified_ratio, created_at, answer_quality
        (answerQuality 형태 근거성 메타데이터 — Task 13.2: citation/grounding/faithfulness/
        grounding_gate 병합).

    Invariant:
        - 예외를 전파하지 않는다(Gateway 실패/타임아웃/빈 응답 → 결정적 폴백, 근거 전무 →
          "외부 근거 미확보" 리포트로 비차단 종료 — 요구사항 13.3 / P8).
        - LLM 호출은 GatewayChatModel(gateway 경유)만 사용한다(요구사항 10.1).
        - citations 의 verified 는 근거 집합 source_id 의 부분집합이다(참조 무결성 방향 P6 —
          ``_verify_citations`` 가 ``rag/citation.verify_citations`` 재사용으로 판정, Task 13.1).
    """
    if config is None:
        config = DeepResearchConfig.from_env()

    q = query.strip() if isinstance(query, str) else ""
    ev: List[EvidenceSource] = [e for e in (evidence or []) if e is not None]

    ordered_ids = _evidence_source_ids(ev)
    providers = {
        _field(e, "provider", "") for e in ev if _field(e, "provider", "")
    }
    created_at = datetime.now(timezone.utc).isoformat()

    # LLM 종합 시도(근거가 있고 gateway 가 있을 때만). 실패/부재 → 결정적 폴백(비차단).
    gateway, model_id = _resolve_gateway_and_generator_model(deps_or_gw)
    report_md: Optional[str] = None
    if ev and gateway is not None:
        report_md = await _generate_via_gateway(q, ev, gateway, model_id)
    if not report_md:
        report_md = _fallback_report_markdown(q, ev)

    # 생성물에서 인용된 source_id 추출 → 근거 집합 대조 분류(요구사항 5.6/8.2/8.3).
    # 대조는 rag/citation.verify_citations 재사용(소스 식별자 집합 대조 모드, P6). 파싱은
    # rag/citation.parse_citations 가 코드 `경로:라인` 규약 전용이라 web:/doi: source_id 를
    # 인식하지 못하므로, source_id 토큰 추출기(_extract_cited_source_ids)를 파서로 쓴다.
    cited = _extract_cited_source_ids(report_md)
    citations = _verify_citations(cited, ordered_ids)
    unverified_ratio = _unverified_ratio(citations)

    # 근거성 메타데이터 병합(Task 13.2 — 요구사항 9.5/8.4): enhance_answer(local_grounding_score
    # + faithfulness)·grounding_gate 재사용으로 answerQuality 형태 메타데이터를 구성한다. 비차단
    # (실패해도 최소 {citation, unverified_ratio}). 자격증명 미포함(P9). env=None → os.environ
    # (사용자 answer_quality/verify 플래그를 존중; 미설정 시 로컬 grounding 은 기본 산출).
    answer_quality = await _build_answer_quality_metadata(
        report_md, ev, citations, unverified_ratio, deps_or_gw, env=None
    )
    grounding_score = _grounding_score_of(answer_quality)

    # 인용 정확도·커버리지·근거성 점수는 종합의 직접 산물이라 여기서 산출한다(요구사항 9.5/8.4).
    # precision@k·MRR·최신성·중복제거 등 나머지 지표는 평가 하네스(Task 16)가 채운다.
    metrics = ResearchMetrics(
        citation_accuracy=1.0 - unverified_ratio,
        coverage_sources=len(ordered_ids),
        coverage_providers=len(providers),
        grounding_score=grounding_score,
    )

    return ResearchReport(
        query=q,
        report_markdown=report_md,
        citations=citations,
        evidence_snapshot=ev,
        metrics=metrics,
        deepening_count=deepening_count,
        unverified_ratio=unverified_ratio,
        created_at=created_at,
        answer_quality=answer_quality,
    )


# =============================================================================
# [12.4] Evaluator + 심화 루프 — 결정적 커버리지 판정 (요구사항 5.7/5.8/6.4/9.6 / P13)
# =============================================================================
# should_deepen 은 Generator 가 산출한 품질 지표(ResearchMetrics)와 지금까지 수행한 심화 반복
# 횟수(deepening_count)를 받아, 추가 심화 조사(재계획)를 1회 더 수행할지 여부를 **결정적으로**
# 판정한다. design.md 멀티에이전트 매핑의 Evaluator 역할(make_evaluator_node/refine_count
# monotonic MAX)과 동형이나, 판정 근거가 LLM 종합 판정이 아니라 **결정적 커버리지 지표**라는
# 점이 다르다(design.md "심화 루프 = evaluator→planner 재계획 루프").
#
# 판정 규칙(요구사항 5.7):
#   심화 후보 = (고유 소스 수 < Min_Sources) OR (고유 제공자 수 < Min_Providers)
#               OR (미검증 인용 비율 > Unverified_Threshold)
#   최종 판정 = 심화 후보 AND (deepening_count < Deepening_Cap)
#
# 유한 종료(P13 / 요구사항 5.8): deepening_count 는 Coordinator(12.6)가 심화 1회마다 +1 하는
# monotonic 카운터다. deepening_count 가 Deepening_Cap(AE_MAX_DEEPENING)에 도달하면
# should_deepen 은 **커버리지 지표와 무관하게 항상 False** 를 반환한다(아래 조기 반환). 따라서
# 심화 루프는 최대 Deepening_Cap 회 후 반드시 종료한다(supervisor evaluator 의
# refine_count>=cap 조기 종료와 동형). 이 조기 반환이 P13 유한 종료의 핵심 보증이다.
#
# 순수·결정적·무예외: 외부 상태·네트워크·부작용이 없어 동일 입력 → 동일 출력이며, 임의/불량
# 입력(None metrics, 형식 오류 수치, 음수 카운트)에도 예외를 던지지 않고 안전 기본값으로
# 폴백한다(P8 정합). 인용 검증기/근거성 통합(Task 13)과 Coordinator 배선(Task 12.6)은 이
# should_deepen 을 그대로 호출한다(중복 구현 금지).


def _coerce_int(v: Any, default: int) -> int:
    """임의 값을 int 로 안전 변환한다(형식 오류/None → default). 순수·무예외."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _coerce_float(v: Any, default: float) -> float:
    """임의 값을 float 로 안전 변환한다(형식 오류/None → default). 순수·무예외."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp01(x: float) -> float:
    """[0,1] 구간으로 클램프한다(미검증 인용 비율 등 확률형 지표 정규화)."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _metrics_unverified_ratio(metrics: Any) -> float:
    """metrics 에서 미검증 인용 비율 [0,1] 을 유도한다(순수·무예외).

    ``ResearchMetrics`` 는 ``citation_accuracy``(= 1 − 미검증비율)만 보유하므로
    ``1 − citation_accuracy`` 로 환산한다(_synthesize_report 가 그 규약으로 산출). 방어적으로,
    ``unverified_ratio`` 를 직접 보유한 객체(예: ``ResearchReport``)를 넘기면 그 값을 우선
    사용한다. 결과는 항상 [0,1] 로 클램프한다.
    """
    direct = _field(metrics, "unverified_ratio", None)
    if isinstance(direct, (int, float)) and not isinstance(direct, bool):
        return _clamp01(float(direct))
    citation_accuracy = _coerce_float(_field(metrics, "citation_accuracy", 1.0), 1.0)
    return _clamp01(1.0 - citation_accuracy)


def _deepening_cap(config: Any) -> int:
    """Deepening_Cap(``AE_MAX_DEEPENING``) 을 config 에서 읽는다. 음수는 0 으로 클램프.

    cap=0 이면 어떤 커버리지에서도 심화하지 않는다(deepening_count 0 은 0 < 0 == False).
    """
    cap = _coerce_int(getattr(config, "max_deepening", 3), 3)
    return cap if cap >= 0 else 0


def should_deepen(
    metrics: Any,
    deepening_count: int,
    config: Optional[DeepResearchConfig] = None,
) -> bool:
    """딥리서치 심화(재계획) 필요 여부를 결정적으로 판정한다 — Evaluator (요구사항 5.7/5.8/6.4/9.6).

    커버리지 지표(고유 소스 수/고유 제공자 수/미검증 인용 비율)를 임계값과 대조해 심화 후보
    여부를 정하고, 심화 반복 횟수가 Deepening_Cap 미만일 때만 True 를 반환한다. deepening_count
    가 Deepening_Cap 에 도달하면 커버리지와 무관하게 항상 False 를 반환해 유한 종료를 보장한다
    (P13). LLM·네트워크·파일 I/O 가 전혀 없는 순수·결정적 함수다.

    Args:
        metrics: ``ResearchMetrics``(또는 ``coverage_sources``/``coverage_providers``/
            ``citation_accuracy`` 를 노출하는 유사 객체). Generator(``_synthesize_report``)가
            산출한 지표를 그대로 받는다. ``None`` 이어도 예외 없이 안전 기본값으로 판정한다.
        deepening_count: Coordinator(12.6)가 지금까지 수행한 심화 반복 횟수(monotonic). 음수는
            0 으로 정규화한다.
        config: 임계값 출처(``min_sources``/``min_providers``/``unverified_threshold``/
            ``max_deepening``). ``None`` 이면 ``DeepResearchConfig.from_env()`` 로 로딩한다.

    Returns:
        추가 심화 조사를 수행해야 하면 ``True``, 아니면 ``False``.
        ``True`` ⟺ ``deepening_count < Deepening_Cap`` **그리고** (고유 소스 수 < Min_Sources
        또는 고유 제공자 수 < Min_Providers 또는 미검증 인용 비율 > Unverified_Threshold).

    Invariant:
        - 예외를 전파하지 않는다(임의/불량 입력에도 bool 반환 — P8 정합).
        - ``deepening_count >= Deepening_Cap`` 이면 metrics 와 무관하게 항상 ``False``
          (유한 종료 P13 / 요구사항 5.8).
        - 외부 상태·부작용이 없어 동일 입력에 대해 항상 동일 결과(결정적).
    """
    if config is None:
        config = DeepResearchConfig.from_env()

    # ── 유한 종료 게이트(P13 / 요구사항 5.8) ──────────────────────────────────
    # monotonic deepening_count 가 Deepening_Cap 에 도달하면 커버리지 지표를 평가하지 않고
    # 항상 False 를 반환한다(심화 루프의 유한 종료 핵심 보증 — supervisor evaluator 의
    # refine_count>=cap 조기 종료와 동형). 음수 카운트는 0 으로 정규화한다.
    count = _coerce_int(deepening_count, 0)
    if count < 0:
        count = 0
    cap = _deepening_cap(config)
    if count >= cap:
        return False

    # ── 결정적 커버리지 부족 신호(요구사항 5.7) ─────────────────────────────────
    # 하나라도 참이면 심화 후보다: 고유 소스 부족 / 고유 제공자 부족 / 미검증 인용 과다.
    min_sources = _coerce_int(getattr(config, "min_sources", 5), 5)
    min_providers = _coerce_int(getattr(config, "min_providers", 2), 2)
    unverified_threshold = _coerce_float(
        getattr(config, "unverified_threshold", 0.2), 0.2
    )

    coverage_sources = _coerce_int(_field(metrics, "coverage_sources", 0), 0)
    coverage_providers = _coerce_int(_field(metrics, "coverage_providers", 0), 0)
    unverified_ratio = _metrics_unverified_ratio(metrics)

    return (
        coverage_sources < min_sources
        or coverage_providers < min_providers
        or unverified_ratio > unverified_threshold
    )


# =============================================================================
# [12.6] Coordinator 진입점 — run_deep_research + 체크포인트/스토어 + 실행 seam
# =============================================================================
# run_deep_research 는 딥리서치 세션을 조율하는 Coordinator 진입점이다(요구사항 6.1). 위에서
# 구현한 Planner(plan_subqueries/plan_waves)·Wave 실행(run_wave)·Generator(_synthesize_report)·
# Evaluator(should_deepen)를 **재사용**해 다음 흐름을 조율한다(중복 구현 금지):
#
#   Planner(분해·웨이브) → Wave 실행(검색·정규화·융합·수집) → Generator(인용 포함 종합)
#     → Evaluator(should_deepen: 커버리지 미달 & deepening_count < Cap 이면 재계획·+1)
#
# 심화 루프는 should_deepen 이 False 를 반환할 때까지 재진입하며 deepening_count 를 monotonic
# 하게 +1 한다. should_deepen 은 deepening_count 가 Deepening_Cap(AE_MAX_DEEPENING)에 도달하면
# 커버리지와 무관하게 항상 False 이므로 루프는 최대 Deepening_Cap 회 후 반드시 종료한다
# (유한 종료 P13 / 요구사항 5.8). Planner 는 무조건 1회 실행되고 Evaluator(should_deepen)는
# while 조건에서 최소 1회 평가되므로, 모든 실행 경로가 Planner·Evaluator 를 각각 ≥1회 거친다
# (요구사항 6.5).
#
# 체크포인트/스토어 재사용(요구사항 6.6 / 12): 기존 오케스트레이션 자산 JsonFileCheckpointSaver
# (langgraph BaseCheckpointSaver)·JsonFileStore(langgraph BaseStore)를 **그대로 재사용**한다.
# deps 에 주입된 것이 있으면 우선 사용하고(서버가 이미 userData 하위로 구성), 없으면 userData
# 하위 base_dir 로 직접 생성한다. 심화 단계마다 경량 상태 스냅샷을 체크포인트로 남기고, 세션
# 요약을 세션 간 store 에 남긴다. 자격증명은 어디에도 담지 않는다(P9).
#
# userData 영속(요구사항 12.2 / P10): 리포트(JSON+MD)와 근거 스냅샷은 userData 루트 하위
# research/{session} 에만 기록한다. session_id 는 경로 안전 컴포넌트로 정규화해 경로 이스케이프
# (.. / 절대경로 / 특수문자)를 차단한다.
#
# 실행 seam(요구사항 6.1): _execute_tool 은 동기 함수이므로 딥리서치 도구 디스패치(Task 14.1)는
# run_deep_research_sync 를 호출한다. 이 seam 은 rag.retrieval_pipeline.retrieve_evidence_sync /
# research 서브그래프 _run_coro_sync 와 동일하게 "실행 중 이벤트 루프면 별도 스레드+독립 루프"
# 로 async 파이프라인을 실행해, 이벤트 루프 내 asyncio.run 안티패턴을 피한다.
#
# 비차단(P8): 체크포인트/스토어/파일 저장의 모든 부작용은 try/except 로 감싸 실패해도 파이프
# 라인 진행·리턴을 막지 않는다. 파이프라인 자체도 Gateway 부재/외부 실패/옵트인 off 에 대해
# 부분/빈 ResearchReport 로 비차단 종료하며(요구사항 13), 예외를 전파하지 않는다.


def _userdata_root() -> str:
    """userData 루트 경로를 해석한다(server.py 배선과 정합 — P10).

    우선순위: ``AE_GENERATED_ROOT``(Electron 이 주입한 userData 경로) → ``~/.agentic-editor``
    (로컬 사용자별 격리 폴더, server.py ``_resolve_local_root`` 폴백과 동일). 딥리서치의
    모든 산출물·체크포인트·스토어는 이 루트 하위에만 기록된다(요구사항 12).
    """
    env_root = os.environ.get("AE_GENERATED_ROOT", "").strip()
    if env_root:
        return env_root
    return os.path.expanduser("~/.agentic-editor")


def _checkpoint_base_dir() -> str:
    """체크포인트 base_dir(userData 하위) — server.py 배선과 동일 레이아웃."""
    return os.path.join(_userdata_root(), "checkpoints", "langgraph")


def _store_base_dir() -> str:
    """세션 간 store base_dir(userData 하위) — server.py 배선과 동일 레이아웃."""
    return os.path.join(_userdata_root(), "store", "langgraph")


def _research_output_dir() -> str:
    """리서치 산출물(리포트/근거 스냅샷) 루트(userData 하위 — 요구사항 12.2 / P10)."""
    return os.path.join(_userdata_root(), "research")


# session_id 를 파일 경로 안전 컴포넌트로 정규화(P10: .. / 절대경로 / 특수문자 차단).
_SESSION_ID_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_session_component(session_id: str) -> str:
    """session_id 를 단일 경로 컴포넌트로 정규화한다(경로 이스케이프 차단 — P10).

    허용 문자([A-Za-z0-9_.-]) 외에는 ``_`` 로 치환하고, 앞뒤 ``.``/``_`` 를 제거해 ``..``·숨김
    파일·빈 이름을 방지한다. 비면 ``"session"`` 으로 폴백하고 길이를 128자로 제한한다. 결과는
    디렉터리 구분자·상위 참조를 포함하지 않으므로 userData 루트 하위 단일 폴더명이 된다.
    """
    s = _SESSION_ID_SANITIZE_RE.sub("_", session_id or "")
    s = s.strip("._") or "session"
    return s[:128]


def deep_research_report_path(session_id: str, *, root: Optional[str] = None) -> str:
    """세션의 구조화 리포트(JSON) 산출 경로를 결정적으로 반환한다(userData 하위 — 요구사항 12.2).

    딥리서치 도구 디스패치(Task 14.1)가 리포트 파일 경로를 재구성해 verified_files·도구 출력
    (``{path, ...}``)에 사용할 수 있도록 공개한다. ``root`` 미지정 시 userData 하위 research
    폴더를 사용한다. 반환 경로는 항상 userData(또는 주입 root) 루트 하위이다(P10).
    """
    base = root or _research_output_dir()
    return os.path.join(base, _safe_session_component(session_id), "report.json")


def _resolve_checkpointer(deps: Any):
    """체크포인트 세이버를 해석한다 — 주입 우선 재사용, 없으면 userData 하위 생성(비차단).

    ``deps.checkpointer`` 가 있으면 그대로 재사용한다(서버가 이미 userData 하위로 구성한
    ``JsonFileCheckpointSaver``). 없으면 userData 하위 base_dir 로 새로 생성한다(요구사항 6.6 /
    12). langgraph/파일시스템 문제로 생성이 실패하면 ``None`` 을 반환해 체크포인트를 건너뛴다
    (비차단 — 파이프라인 진행을 막지 않는다).
    """
    existing = getattr(deps, "checkpointer", None) if deps is not None else None
    if existing is not None:
        return existing
    try:
        from ai_engine.agent_system.checkpoint_store import JsonFileCheckpointSaver

        return JsonFileCheckpointSaver(_checkpoint_base_dir())
    except Exception:  # noqa: BLE001 — 체크포인트 부재는 비차단.
        return None


def _resolve_store(deps: Any):
    """세션 간 store 를 해석한다 — 주입 우선 재사용, 없으면 userData 하위 생성(비차단).

    ``deps.store`` 가 있으면 그대로 재사용한다(서버가 이미 userData 하위로 구성한
    ``JsonFileStore``). 없으면 userData 하위 base_dir 로 새로 생성한다(요구사항 6.6 / 12).
    실패 시 ``None`` 을 반환해 장기 메모리 기록을 건너뛴다(비차단).
    """
    existing = getattr(deps, "store", None) if deps is not None else None
    if existing is not None:
        return existing
    try:
        from ai_engine.agent_system.store import JsonFileStore

        return JsonFileStore(_store_base_dir())
    except Exception:  # noqa: BLE001 — 장기 메모리 부재는 비차단.
        return None


def _state_snapshot(
    query: str,
    deepening_count: int,
    evidence: List[EvidenceSource],
    report: Optional[ResearchReport],
) -> dict:
    """체크포인트용 경량 상태 스냅샷(JSON-safe·자격증명 미포함 — P9).

    질의·심화 횟수·확보 소스 id·커버리지 지표 등 평문 요약만 담는다. 근거 본문 전체나
    자격증명은 담지 않는다(체크포인트 최소화 + P9). ``ResearchReport``/``ResearchMetrics``
    부재에도 안전 기본값으로 폴백한다.
    """
    metrics = getattr(report, "metrics", None)
    return {
        "query": query,
        "deepening_count": int(deepening_count),
        "source_ids": _evidence_source_ids(evidence),
        "coverage_sources": _coerce_int(_field(metrics, "coverage_sources", 0), 0),
        "coverage_providers": _coerce_int(_field(metrics, "coverage_providers", 0), 0),
        "unverified_ratio": _coerce_float(getattr(report, "unverified_ratio", 0.0), 0.0),
    }


def _checkpoint_state(checkpointer: Any, session_id: str, state: dict) -> None:
    """딥리서치 심화 상태를 체크포인트로 영속한다(비차단·userData 하위 — 요구사항 6.6).

    기존 ``JsonFileCheckpointSaver``(langgraph ``BaseCheckpointSaver``)를 그대로 재사용한다.
    langgraph 체크포인트 규약(``empty_checkpoint`` + 고유 id)에 상태를 ``channel_values`` 로
    담아 ``put`` 한다. 심화 단계마다 고유 id 를 부여해 단계별 스냅샷을 남긴다. 자격증명은
    담지 않으며(state 는 평문 요약뿐 — P9), langgraph 부재/직렬화/파일 문제 등 어떤 실패도
    전파하지 않는다(비차단 P8).
    """
    if checkpointer is None:
        return
    try:
        from langgraph.checkpoint.base import empty_checkpoint

        ck = empty_checkpoint()
        ck["id"] = uuid.uuid4().hex  # 심화 단계마다 고유 체크포인트 id
        ck["channel_values"] = {"deep_research": state}
        cfg = {
            "configurable": {
                "thread_id": f"deep_research:{session_id}",
                "checkpoint_ns": "",
            }
        }
        metadata = {
            "source": "deep_research",
            "step": int(state.get("deepening_count", 0)),
        }
        checkpointer.put(cfg, ck, metadata, {})
    except Exception:  # noqa: BLE001 — 체크포인트 실패는 비차단(P8).
        pass


def _remember_session(
    store: Any,
    session_id: str,
    query: str,
    report: ResearchReport,
    report_path: str,
) -> None:
    """세션 간 store 에 리서치 세션 요약을 남긴다(비차단·userData 하위 — 요구사항 6.6).

    기존 ``JsonFileStore``(langgraph ``BaseStore``)를 재사용한다. namespace ``("research",
    session_id)`` 에 마지막 리포트 요약(질의·경로·커버리지·미검증 비율·생성시각)을 저장한다.
    자격증명은 포함하지 않는다(P9). 저장 실패는 전파하지 않는다(비차단 P8).
    """
    if store is None:
        return
    try:
        metrics = getattr(report, "metrics", None)
        store.put(
            ("research", session_id),
            "last_report",
            {
                "query": query,
                "report_path": report_path or "",
                "deepening_count": _coerce_int(
                    getattr(report, "deepening_count", 0), 0
                ),
                "unverified_ratio": _coerce_float(
                    getattr(report, "unverified_ratio", 0.0), 0.0
                ),
                "coverage_sources": _coerce_int(
                    _field(metrics, "coverage_sources", 0), 0
                ),
                "coverage_providers": _coerce_int(
                    _field(metrics, "coverage_providers", 0), 0
                ),
                "created_at": getattr(report, "created_at", "") or "",
            },
        )
    except Exception:  # noqa: BLE001 — 장기 메모리 기록 실패는 비차단(P8).
        pass


def _report_to_dict(report: ResearchReport) -> dict:
    """``ResearchReport`` 를 JSON 직렬화 가능한 dict 로 변환한다(근거 스냅샷 포함).

    표준 ``dataclasses.asdict`` 로 중첩 ``ResearchMetrics``/``EvidenceSource`` 목록까지
    재귀 변환한다. 실패 시(비표준 입력) 핵심 필드만 담은 방어적 dict 로 폴백한다(비차단).
    """
    try:
        return asdict(report)
    except Exception:  # noqa: BLE001 — 방어적 수동 직렬화.
        return {
            "query": getattr(report, "query", "") or "",
            "report_markdown": getattr(report, "report_markdown", "") or "",
            "citations": getattr(report, "citations", {}) or {},
            "deepening_count": _coerce_int(getattr(report, "deepening_count", 0), 0),
            "unverified_ratio": _coerce_float(
                getattr(report, "unverified_ratio", 0.0), 0.0
            ),
            "created_at": getattr(report, "created_at", "") or "",
        }


def _save_report_snapshot(report: ResearchReport, session_id: str) -> str:
    """리포트(JSON+MD)와 근거 스냅샷을 userData 하위에 저장한다(비차단 — 요구사항 12.2 / P10).

    ``userData/research/{session}/`` 에 ``report.json``(구조화 리포트 — 근거 스냅샷 포함)과
    ``report.md``(사람이 읽는 종합 본문)를 원자적으로 기록한다. 경로는 정규화된 session
    컴포넌트로 구성되어 항상 userData 루트 하위이다(P10). 파일 I/O 실패는 전파하지 않고
    빈 문자열을 반환한다(비차단 P8).

    Returns:
        저장한 ``report.json`` 절대/구성 경로(성공 시), 실패 시 ``""``.
    """
    try:
        session_dir = os.path.join(
            _research_output_dir(), _safe_session_component(session_id)
        )
        os.makedirs(session_dir, exist_ok=True)

        report_json = os.path.join(session_dir, "report.json")
        payload = _report_to_dict(report)
        tmp = report_json + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, report_json)  # 원자적 교체(부분 파일 방지)

        # 사람이 읽는 마크다운 본문도 함께 저장(리포트). 실패는 비차단.
        try:
            report_md = os.path.join(session_dir, "report.md")
            md_tmp = report_md + ".tmp"
            with open(md_tmp, "w", encoding="utf-8") as f:
                f.write(getattr(report, "report_markdown", "") or "")
            os.replace(md_tmp, report_md)
        except OSError:
            pass

        return report_json
    except Exception:  # noqa: BLE001 — 산출물 저장 실패는 비차단(P8).
        return ""


def _deepening_query(query: str, deepening_count: int) -> str:
    """심화 라운드용 Planner 입력 질의(원 질의 + 보완 조사 힌트).

    원 질의를 보존하되, 아직 다루지 않은 상호 보완적 하위 주제·최신 자료·다른 관점을
    탐색하도록 힌트를 덧붙인다. Planner LLM 부재 시엔 단일 폴백(원 질의)로 귀결되나,
    ``run_wave`` 가 선행 근거 source_id 를 제외하므로 심화마다 새 소스를 확보한다(커버리지
    단조성 P5 정합).
    """
    q = query.strip() if isinstance(query, str) else ""
    return (
        f"{q}\n\n(심화 조사 {deepening_count}회차: 위 원 질의에 대해 아직 다루지 않은 상호 "
        f"보완적 하위 주제, 최신 자료, 다른 관점을 중심으로 추가 조사 질의를 생성하라.)"
    )


async def _execute_waves(
    waves: List[List[dict]],
    deps: Any,
    config: DeepResearchConfig,
    prior_context: List[EvidenceSource],
) -> List[EvidenceSource]:
    """위상 순서 웨이브를 순차 실행하며 근거를 누적한다(웨이브 간 dedup·prior 전파 — 5.2~5.5).

    ``plan_waves`` 산출물(웨이브 리스트)을 위상 순서대로 실행한다. 각 웨이브의 ``run_wave`` 에
    누적 근거(``acc``)를 ``prior_context`` 로 전달해, 선행 웨이브에서 확보한 소스를 후속 수집에서
    제외한다(중복 회피·커버리지 단조성 P5). 웨이브 내 하위질의 병렬성은 ``run_wave`` 가 담당한다.

    Returns:
        ``prior_context`` + 전 웨이브 근거를 source_id 기준으로 중복 제거한 누적 근거 목록.
        예외를 전파하지 않는다(``run_wave`` 가 비차단 — P8).
    """
    acc: List[EvidenceSource] = list(prior_context or [])
    for wave in waves or []:
        wave_ev = await run_wave(wave, deps, config, prior_context=acc)
        if wave_ev:
            acc = _dedup_evidence(acc + wave_ev)
    return _dedup_evidence(acc)


async def run_deep_research(
    query: str,
    deps: Any = None,
    *,
    session_id: str = "",
    config: Optional[DeepResearchConfig] = None,
) -> ResearchReport:
    """Coordinator 진입점 — 딥리서치 멀티에이전트 파이프라인을 조율한다 (요구사항 6.1/6.5/6.6/12.2).

    위에서 구현한 4역할을 재사용해 조율한다(중복 구현 금지):
    Planner(``plan_subqueries``/``plan_waves``) → Wave 실행(``run_wave``) →
    Generator(``_synthesize_report``) → Evaluator(``should_deepen``). Evaluator 가 심화를
    지시하면 추가 하위질의로 재계획해 웨이브를 더 실행하고 ``deepening_count`` 를 +1 한 뒤
    재종합한다. ``should_deepen`` 은 ``deepening_count`` 가 ``Deepening_Cap`` 에 도달하면 항상
    False 이므로 심화 루프는 최대 ``Deepening_Cap`` 회 후 반드시 종료한다(유한 종료 P13 /
    요구사항 5.8). Planner 는 무조건 1회, Evaluator 는 while 조건에서 최소 1회 평가되어 모든
    실행 경로가 두 역할을 각각 ≥1회 거친다(요구사항 6.5).

    체크포인트/스토어는 기존 ``JsonFileCheckpointSaver``/``JsonFileStore`` 를 재사용하며
    (``deps`` 주입 우선, 없으면 userData 하위 생성), base_dir 는 userData 하위로 한정한다
    (요구사항 6.6 / 12). 리포트와 근거 스냅샷은 ``userData/research/{session}/`` 에만 저장한다
    (요구사항 12.2 / P10). 모든 LLM 호출은 재사용 스테이지를 통해 Bedrock Gateway 경유만
    사용한다(요구사항 10.1).

    Args:
        query: 원 조사 질의. ``strip()`` 후 사용한다.
        deps: GraphDeps 유사 객체(``.gateway``/``.model_*``/``.checkpointer``/``.store``) 또는
            게이트웨이 클라이언트, 또는 ``None``. 하위 스테이지(plan/synthesize)에 그대로
            전달되며, 체크포인트/스토어는 ``deps.checkpointer``/``deps.store`` 를 우선 재사용한다.
        session_id: 세션 식별자(체크포인트 thread·산출물 폴더명). 비면 무작위 id 를 생성한다.
            경로 안전 컴포넌트로 정규화되어 경로 이스케이프를 차단한다(P10).
        config: ``DeepResearchConfig``. ``None`` 이면 ``from_env()`` 로 로딩한다.

    Returns:
        ``ResearchReport`` — 인용 포함 종합 본문·검증/미검증 인용 분류·근거 스냅샷·품질 지표·
        심화 횟수(≤ Deepening_Cap)·미검증 비율. 외부 근거 미확보/옵트인 off/Gateway 부재 등은
        부분/빈 리포트로 비차단 종료한다(요구사항 13).

    Invariant:
        - 예외를 전파하지 않는다(모든 부작용·외부 실패는 비차단 폴백 — P8/요구사항 13).
        - 심화 반복 횟수는 ``Deepening_Cap`` 이하로 유한 종료한다(P13 / 요구사항 5.8).
        - 기록하는 모든 산출물 경로는 userData 루트 하위이다(P10 / 요구사항 12).
        - 모든 실행 경로가 Planner·Evaluator 를 각각 ≥1회 거친다(요구사항 6.5).
    """
    if config is None:
        config = DeepResearchConfig.from_env()

    q = query.strip() if isinstance(query, str) else ""
    session_id = (
        _safe_session_component(session_id) if session_id else uuid.uuid4().hex
    )

    # 체크포인트/스토어 재사용(주입 우선, 없으면 userData 하위 생성 — 요구사항 6.6 / 12).
    checkpointer = _resolve_checkpointer(deps)
    store = _resolve_store(deps)

    deepening_count = 0

    # ── Planner: 질의 분해 + 웨이브 스케줄 (모든 실행 경로에서 최소 1회 — 요구사항 6.5) ──
    subqueries = await plan_subqueries(q, deps, config)
    waves = plan_waves(subqueries)

    # ── Wave 실행: 다중 소스 검색 → 정규화 → 융합·재랭킹 → 상위 K 본문 수집 (5.2~5.5) ──
    evidence = await _execute_waves(waves, deps, config, prior_context=[])

    # ── Generator: 인용 포함 Research_Report 종합 (5.6/6.3/8.1) ──
    report = await _synthesize_report(
        q, evidence, deps, config, deepening_count=deepening_count
    )
    _checkpoint_state(
        checkpointer,
        session_id,
        _state_snapshot(q, deepening_count, evidence, report),
    )

    # ── Evaluator + 심화 루프: should_deepen (요구사항 5.7/5.8/6.4/9.6, P13) ──
    # while 조건에서 should_deepen(Evaluator)을 최소 1회 평가한다(요구사항 6.5). should_deepen
    # 은 deepening_count >= Deepening_Cap 이면 항상 False → 루프는 최대 Deepening_Cap 회 후
    # 반드시 종료한다(유한 종료 P13). deepening_count 는 monotonic 하게 +1 한다.
    while should_deepen(report.metrics, deepening_count, config):
        deepening_count += 1
        deepen_q = _deepening_query(q, deepening_count)
        # 재계획(Planner) — 추가 하위질의로 보완 조사(요구사항 5.7).
        more_subqueries = await plan_subqueries(deepen_q, deps, config)
        more_waves = plan_waves(more_subqueries)
        # 선행 근거를 prior_context 로 전달 → 이미 확보한 소스 제외(커버리지 단조 P5).
        evidence = await _execute_waves(
            more_waves, deps, config, prior_context=evidence
        )
        # 확장된 근거로 재종합(Generator) — deepening_count 반영.
        report = await _synthesize_report(
            q, evidence, deps, config, deepening_count=deepening_count
        )
        _checkpoint_state(
            checkpointer,
            session_id,
            _state_snapshot(q, deepening_count, evidence, report),
        )

    # ── 리포트/근거 스냅샷 userData 하위 저장 (요구사항 12.2 / P10) ──
    report_path = _save_report_snapshot(report, session_id)
    _remember_session(store, session_id, q, report, report_path)

    return report


def run_deep_research_sync(
    query: str,
    deps: Any = None,
    *,
    session_id: str = "",
    config: Optional[DeepResearchConfig] = None,
) -> ResearchReport:
    """``run_deep_research`` 의 동기 실행 seam — 실행 중 루프면 별도 스레드+독립 루프(요구사항 6.1).

    ``rag.retrieval_pipeline.retrieve_evidence_sync`` / research 서브그래프 ``_run_coro_sync``
    와 **동일한 seam 패턴**이다: 서버 이벤트 루프 안에서 ``asyncio.run`` 을 호출하는 안티패턴을
    피하기 위해, 실행 중 이벤트 루프가 있으면 별도 스레드의 독립 루프에서 async 파이프라인을
    돌린다. 실행 중 루프가 없으면 ``asyncio.run`` 으로 직행한다.

    ``_execute_tool`` 은 동기 함수이고 ``GatewayToolNode`` 가 ``asyncio.to_thread`` 로 분리
    실행하므로 대개 실행 중 루프가 없지만(그 경우 ``asyncio.run`` 직행), 양쪽을 모두 방어한다.
    딥리서치 도구 디스패치(Task 14.1)는 이 함수를 호출한다(동기 호출 지점).

    Args/Returns: ``run_deep_research`` 와 동일.
    """

    async def _coro() -> ResearchReport:
        return await run_deep_research(
            query, deps, session_id=session_id, config=config
        )

    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False

    if not running:
        return asyncio.run(_coro())

    # 실행 중 루프 내부 → 별도 스레드 + 독립 루프(이벤트 루프 내 asyncio.run 회피).
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(_coro())).result()
