# Feature: reasoning-perf-reliability, Property-adjacent: SSE 스트리밍 계약 스냅샷 (Requirements 5.4, 10.3, 10.4)
"""Integration test: SSE 스트리밍 계약 스냅샷 (Fast_Path ⊆ Full_Graph).

Feature: reasoning-perf-reliability, Property-adjacent: SSE 스트리밍 계약 스냅샷
_Requirements: 5.4, 10.3, 10.4_

설계 분류(design.md Testing Strategy 표): SSE 스트리밍 계약은 **INTEGRATION (Property 인접)**
으로, `graph_events_to_sse` 가 emit 하는 이벤트 키/타입 집합을 예시 기반으로 검증한다(hypothesis
불필요). 이 테스트는 신규 플래그가 모두 off(baseline)인 상태에서, Fast_Path 그래프가 방출하는
SSE 이벤트 계열 집합이 Full_Graph 가 방출하는 집합의 **부분집합**임을 실증한다 — 즉 Fast_Path 로
경로를 바꿔도 기존 프론트가 소비하는 스트리밍 계약(토큰 스트림 + 노드 생명주기)이 보존된다
(요구사항 5.4 / 10.3 / 10.4).

대상 코드(실측):
- ai_engine/agent_system/sse_bridge.graph_events_to_sse(compiled, state, config, ...):
    astream_events(v2) → `data: {json}\n\n` SSE 문자열을 yield. 마지막에 `data: [DONE]\n\n`.
    emit payload 최상위 키(sse_bridge.py 실측):
      · on_chat_model_stream            → {"text": ...}
      · on_chain_start (SUBGRAPH_NAMES) → {"type":"agent_start", "taskId": name}
      · on_chain_end   (SUBGRAPH_NAMES) → ({"verifiedFiles":[...]} 있으면) → {"type":"agent_done", "taskId": name}
      · on_tool_start/on_tool_end        → {"tool": name, "status": ...}
      · heartbeat/에러                    → {"heartbeat": True} / {"error": ...}
- ai_engine/agent_system/depth_router.build_fast_path_graph(deps, 'chat'):
    START → chat(단일 도메인 서브그래프) → END. 노드명이 도메인 라벨이라 on_chain_start/
    on_chain_end 이 Full_Graph 워커와 동일한 이름(SUBGRAPH_NAMES 원소)을 실는다.
- ai_engine/agent_system/supervisor.build_parallel_top_graph(deps):
    planner → (fan-out) → 도메인 워커 → aggregate → [evaluator] → END (Full_Graph).

방법:
- scripts/eval_reasoning_perf.MockGateway(결정론·오프라인·자격증명/네트워크 없음)를 GraphDeps 에
  주입해 두 그래프를 컴파일한다.
- 각 그래프에 대해 `graph_events_to_sse` async generator 를 `async for` 로 소비하며(⚠️ async for
  루프 전체를 wait_for 로 감싸지 않음 — API_NOTES CRITICAL 2), emit 된 SSE 라인을 파싱해
  **이벤트 계열(event family)** 집합으로 수집한다. 계열은 payload 의 의미 키로 정규화한다
  (text / agent_start / agent_done / tool / verifiedFiles / heartbeat / error / DONE).
- Fast_Path 는 완주까지 소비한다. Full_Graph 는 heavy 할 수 있어 이벤트 상한(안전장치)과
  "Fast_Path 계열을 모두 관측하면 조기 종료" 조건으로 bound 한다.

단언:
- Fast_Path 이벤트 계열 집합은 비어있지 않다.
- Fast_Path 이벤트 계열 집합 ⊆ Full_Graph 이벤트 계열 집합 (스트리밍 계약 보존 — 요구사항 5.4).
- 두 그래프 모두 노드 생명주기 계열(agent_start/agent_done)과 종료 sentinel(DONE)을 공유한다.

실행: ai_engine/.venv/bin/python -m pytest scripts/test_sse_contract_snapshot.py -q
Stack: Python 3.11+, pytest (example-based integration test).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

# repo 루트 + scripts 를 import 경로에 추가한다(test_fast_path_finite_pbt.py 패턴 미러).
# repo 루트: ai_engine 패키지 로드용. scripts: eval_reasoning_perf.MockGateway 로드용.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
sys.path.insert(0, _HERE)

# 무회귀 baseline: 신규 플래그를 모두 off 로 고정한다(import 전에 설정 — 조립 시점 판독 대비).
# adaptive depth off → 그래프 선택은 기존과 동등, grounding gate off → verify→END 기존 구조.
os.environ["AE_ENABLE_ADAPTIVE_DEPTH"] = "0"
os.environ["AE_ENABLE_GROUNDING_GATE"] = "0"
# answer_quality 는 verify 가 gateway 를 추가 호출할 수 있어 SSE 계약과 무관하지만, 결정론·
# 종료 안정성을 위해 명시적으로 비활성(미설정과 동일 취지).
os.environ.pop("AE_ANSWER_QUALITY", None)

from langchain_core.messages import HumanMessage  # noqa: E402

from eval_reasoning_perf import MockGateway  # noqa: E402
from ai_engine.agent_system.deps import GraphDeps  # noqa: E402
from ai_engine.agent_system.depth_router import build_fast_path_graph  # noqa: E402
from ai_engine.agent_system.supervisor import build_parallel_top_graph  # noqa: E402
from ai_engine.agent_system.sse_bridge import graph_events_to_sse  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────
# SSE 라인 → 이벤트 계열(event family) 정규화
# ─────────────────────────────────────────────────────────────────────────
def _classify_sse_line(line: str) -> str | None:
    """단일 SSE 라인(`data: ...\n\n`)을 이벤트 계열 라벨로 정규화한다.

    sse_bridge.py 가 emit 하는 payload 최상위 키를 기준으로, 프론트가 소비하는 의미 단위로
    계열을 부여한다. agent_start/agent_done 은 taskId(도메인명)에 무관하게 동일 계열이므로
    Fast_Path(chat)와 Full_Graph(임의 도메인)가 라벨만으로 비교 가능하다.
    """
    if not line.startswith("data:"):
        return None
    payload_str = line[len("data:") :].strip()
    if not payload_str:
        return None
    if payload_str == "[DONE]":
        return "DONE"
    try:
        payload = json.loads(payload_str)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    # 노드 생명주기(agent_start/agent_done)는 type 값으로 계열 구분.
    if "type" in payload:
        return str(payload["type"])
    if "text" in payload:
        return "text"
    if "verifiedFiles" in payload:
        return "verifiedFiles"
    if "tool" in payload:
        return "tool"
    if "heartbeat" in payload:
        return "heartbeat"
    if "error" in payload:
        return "error"
    if "answerQuality" in payload:
        return "answerQuality"
    if "qualityPending" in payload:
        return "qualityPending"
    return None


def _initial_state(prompt: str, session_id: str) -> dict:
    """server.py graph-stream 배선과 정합한 최소 초기 GraphState."""
    return {
        "prompt": prompt,
        "session_id": session_id,
        "project_path": "",
        "open_file": "",
        "open_file_content": "",
        "aws_profile": "",
        "bedrock_user": "",
        "template_id": "",
        "system_prompt": "",
        "messages": [HumanMessage(content=prompt)],
        "visited_routes": [],
    }


async def _collect_event_families(
    compiled,
    prompt: str,
    session_id: str,
    *,
    max_events: int | None = None,
    stop_when: set[str] | None = None,
) -> set[str]:
    """graph_events_to_sse 를 async for 로 소비해 이벤트 계열 집합을 수집한다.

    ⚠️ API_NOTES CRITICAL 2: async for 루프 **전체를 asyncio.wait_for 로 감싸지 않는다.**
    heartbeat 는 sse_bridge 내부에서 개별 __anext__ 만 wait 하므로, 여기서는 큰 heartbeat_
    interval 을 줘 keep-alive 노이즈를 배제하고 total_timeout 으로 유한 종료만 보장한다.

    Args:
        max_events: 안전 상한(Full_Graph runaway 방지). None 이면 무제한.
        stop_when:  이 집합이 수집된 계열의 부분집합이 되면 조기 종료(Full_Graph bound).
    """
    config = {
        "configurable": {"thread_id": f"sse-contract-{session_id}"},
        "recursion_limit": 60,
    }
    families: set[str] = set()
    seen = 0
    agen = graph_events_to_sse(
        compiled,
        _initial_state(prompt, session_id),
        config,
        heartbeat_interval=1000.0,  # heartbeat 노이즈 배제(개별 next 만 감쌈 — 루프 아님)
        total_timeout=120.0,
    )
    async for line in agen:  # noqa: ASYNC — 루프 전체를 wait_for 로 감싸지 않는다(CRITICAL 2)
        fam = _classify_sse_line(line)
        if fam is not None:
            families.add(fam)
        seen += 1
        # Full_Graph 조기 종료: Fast_Path 계열을 모두 관측했고 종료 sentinel 도 봤으면 충분.
        if stop_when is not None and stop_when.issubset(families) and "DONE" in families:
            break
        if max_events is not None and seen >= max_events:
            break
    return families


def test_sse_contract_fast_path_subset_of_full_graph():
    """Fast_Path SSE 이벤트 계열 ⊆ Full_Graph SSE 이벤트 계열 (스트리밍 계약 보존)."""
    prompt = "이 프로젝트에 대해 간단히 설명해줘"

    # ── Fast_Path(chat 단일 도메인) — 완주까지 소비 ──
    fast_deps = GraphDeps(gateway=MockGateway())
    fast_graph = build_fast_path_graph(fast_deps, "chat")
    fast_families = asyncio.run(
        _collect_event_families(
            fast_graph, prompt, "fast", max_events=5000
        )
    )

    # ── Full_Graph(planner→workers→aggregate→[evaluator]) — bound 소비 ──
    # Fast_Path 계열을 모두 관측하면 조기 종료(+ 안전 상한)로 heavy 실행을 제한한다.
    full_deps = GraphDeps(gateway=MockGateway())
    full_graph = build_parallel_top_graph(full_deps)
    full_families = asyncio.run(
        _collect_event_families(
            full_graph,
            prompt,
            "full",
            max_events=20000,
            stop_when=fast_families,
        )
    )

    # (1) Fast_Path 는 최소한 하나 이상의 SSE 이벤트 계열을 방출한다(비어있지 않음).
    assert fast_families, "Fast_Path 가 어떤 SSE 이벤트도 방출하지 않음"

    # (2) 스트리밍 계약 보존(요구사항 5.4): Fast_Path 계열 ⊆ Full_Graph 계열.
    missing = fast_families - full_families
    assert not missing, (
        "Fast_Path 가 Full_Graph 에 없는 SSE 이벤트 계열을 방출함(계약 위반): "
        f"missing={sorted(missing)!r}\n"
        f"fast={sorted(fast_families)!r}\nfull={sorted(full_families)!r}"
    )

    # (3) 두 경로 모두 노드 생명주기 계열과 종료 sentinel 을 공유한다(핵심 계약 계열).
    for family in ("agent_start", "agent_done", "DONE"):
        assert family in fast_families, (
            f"Fast_Path 에 핵심 계약 계열 부재: {family!r} (fast={sorted(fast_families)!r})"
        )
        assert family in full_families, (
            f"Full_Graph 에 핵심 계약 계열 부재: {family!r} (full={sorted(full_families)!r})"
        )


if __name__ == "__main__":
    test_sse_contract_fast_path_subset_of_full_graph()
    print("OK")
