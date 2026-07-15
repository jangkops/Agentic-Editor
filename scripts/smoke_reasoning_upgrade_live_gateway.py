#!/usr/bin/env python
"""실제 Bedrock Gateway e2e 스모크 — LangGraph 추론 고도화(langgraph-reasoning-upgrade).

⚠️ 이 스크립트는 **실제 Gateway 를 호출**한다(mock 아님). 네트워크 + AWS SSO 자격증명 필요.
   pytest 자동 수집 대상이 아니다(파일명이 test_ 로 시작하지 않음). 수동 실행 전용.

전제(반드시 선행):
  1) aws sso login --profile bedrock-gw   (또는 조직 SSO 프로파일)
  2) BedrockUser-{name} assume-role 권한 보유

목적(mock e2e 가 못 잡는 것 검증):
  - 실제 Opus/Sonnet 응답으로 planner(select_plan) / evaluator(submit_evaluation) toolChoice
    강제 스키마가 실제로 파싱 가능한 형태로 돌아오는지
  - 다중 Wave DAG + evaluator 재계획 루프가 실제 지연/응답에서 유한 종료하는지
  - 자격증명이 최종 state 에 새지 않는지

실행:
  # 1) 먼저 로그인
  aws sso login --profile bedrock-gw
  # 2) 프로파일/유저 지정(환경변수) 후 실행
  AE_BEDROCK_PROFILE=bedrock-gw AE_BEDROCK_USER=<yourname> \
    ai_engine/.venv/bin/python scripts/smoke_reasoning_upgrade_live_gateway.py

  # 다중 도메인 프롬프트로 병렬/재계획을 강하게 유도하려면:
  AE_SMOKE_PROMPT="이 저장소 구조를 조사하고, 그 결과를 요약한 간단한 설명을 작성해줘" ...

옵션 env:
  AE_MAX_REFINE(기본 1로 낮춰 라이브 비용/시간 절감), AE_MAX_PARALLEL_TASKS,
  AE_EVALUATOR_TIMEOUT, AE_AGGREGATE_TIMEOUT, AE_ROUTER_TIMEOUT.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 라이브 호출이므로 신규 기능 전부 on. 비용/시간 절감을 위해 refine cap 은 낮게 기본 1.
os.environ.setdefault("AE_ENABLE_EVALUATOR", "on")
os.environ.setdefault("AE_ENABLE_DAG_PLANNER", "on")
os.environ.setdefault("AE_MAX_REFINE", "1")


def _fail(msg: str, code: int = 2):
    print(f"\n❌ {msg}")
    raise SystemExit(code)


def _build_gateway():
    """gateway_module.GatewayClient 를 프로파일/유저로 구성. 실패 시 명확히 안내."""
    profile = os.environ.get("AE_BEDROCK_PROFILE") or os.environ.get("AWS_PROFILE")
    bedrock_user = os.environ.get("AE_BEDROCK_USER", "")
    if not profile:
        _fail("AE_BEDROCK_PROFILE(또는 AWS_PROFILE) 미설정. 예: AE_BEDROCK_PROFILE=bedrock-gw")

    try:
        from ai_engine.gateway_module import GatewayClient
    except Exception as e:  # noqa: BLE001
        _fail(f"GatewayClient import 실패: {e}")

    # GatewayClient 시그니처는 배포본 기준. 실패 시 사용자가 인자를 조정하도록 원인을 노출.
    try:
        gw = GatewayClient(profile_name=profile, bedrock_user=bedrock_user or None)
    except TypeError:
        # 시그니처가 다르면 인자 없이 시도(환경변수 기반 구성).
        from ai_engine.gateway_module import GatewayClient as _GC
        gw = _GC()
    return gw, profile, bedrock_user


async def main():
    from ai_engine.agent_system.deps import GraphDeps
    from ai_engine.agent_system.supervisor import build_parallel_top_graph, AE_MAX_REFINE
    from langchain_core.messages import HumanMessage

    gw, profile, bedrock_user = _build_gateway()
    deps = GraphDeps(gateway=gw)  # 모델 역할 기본값(Planner/Evaluator=Opus, Generator=Sonnet)

    prompt = os.environ.get(
        "AE_SMOKE_PROMPT",
        "이 프로젝트가 무엇을 하는지 간단히 조사해서 3문장으로 요약해줘.",
    )
    print("=" * 70)
    print("실제 Gateway e2e 스모크 — LangGraph 추론 고도화")
    print(f"  profile={profile} bedrock_user={bedrock_user or '(자동감지)'}")
    print(f"  AE_MAX_REFINE={AE_MAX_REFINE} prompt={prompt!r}")
    print("=" * 70)

    graph = build_parallel_top_graph(deps)
    state = {"prompt": prompt, "session_id": "live-smoke", "messages": [HumanMessage(content=prompt)]}

    t0 = time.time()
    try:
        final = await asyncio.wait_for(
            graph.ainvoke(state, config={"recursion_limit": 60}),
            timeout=float(os.environ.get("AE_SMOKE_TOTAL_TIMEOUT", "900")),
        )
    except asyncio.TimeoutError:
        _fail("전체 실행 타임아웃(AE_SMOKE_TOTAL_TIMEOUT). 게이트웨이 응답 지연 확인.")
    except Exception as e:  # noqa: BLE001
        _fail(f"그래프 실행 예외(무한루프면 GraphRecursionError): {type(e).__name__}: {str(e)[:300]}")
    elapsed = time.time() - t0

    final_text = final.get("final_text") or ""
    refine_count = final.get("refine_count", 0) or 0
    evaluation = final.get("evaluation") or {}

    print(f"\n⏱  총 소요: {elapsed:.1f}s")
    print(f"final_text 길이: {len(final_text)}")
    print(f"refine_count: {refine_count} (cap={AE_MAX_REFINE})")
    print(f"evaluation.achieved: {evaluation.get('achieved')}")
    print(f"메시지 수: {len(final.get('messages', []))}")
    print("\n--- 최종 답변(앞 500자) ---")
    print(final_text[:500] if final_text else "(final_text 비어있음 — 단일 워커 스킵일 수 있음)")

    # 검증(라이브)
    problems = []
    if refine_count > AE_MAX_REFINE:
        problems.append(f"refine_count({refine_count}) > cap({AE_MAX_REFINE}) — 유한 종료 위반")
    blob = repr(final)
    if re.search(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", blob) or "secretAccessKey" in blob or "sessionToken" in blob:
        problems.append("최종 state 에 자격증명 지표 발견 — 유출 위험")

    if problems:
        for p in problems:
            print(f"  ❌ {p}")
        raise SystemExit(1)

    print("\n✅ 라이브 게이트웨이 e2e 스모크 통과: 유한 종료 + 자격증명 미유출 + 실제 응답 처리 정상")


if __name__ == "__main__":
    asyncio.run(main())
