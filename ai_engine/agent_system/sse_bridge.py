"""SSE 브리지 — LangGraph `astream_events(v2)` → 기존 프론트 SSE 이벤트 계약.

Task 5.4 산출물. design.md 섹션 6(SSE 이벤트 매핑 표) + API_NOTES.md 항목 5
(astream_events v2 이벤트 스키마 실측) + API_NOTES.md CRITICAL 2(스트림 소비 루프를
`asyncio.wait_for` 로 감싸지 말 것 — deadline 기반 수동 중단)를 근거로 한다.

핵심 계약 (요구사항 5.1~5.7, 6.7, 6.8):
- 반환은 async generator 이며, `data: {json}\n\n` 문자열을 yield 하고 마지막에
  `data: [DONE]\n\n` 를 yield 한다(요구사항 5.6).
- emit 하는 이벤트 키는 요구사항 5.5의 허용 집합
  `{text, thinking, tool, status, verifiedFiles, type, taskId, heartbeat,
    answerQuality, qualityPending, error}` 에 deep-research-engine 요구사항 18.4의
  `searchStatus` 를 순수 확장한 집합의 부분집합만 사용한다(Property 6 / P14 정합).

이벤트 매핑 (API_NOTES 항목 5 실측 data 키):
| astream_events 이벤트          | data 키          | SSE emit                                        |
|--------------------------------|------------------|-------------------------------------------------|
| on_chat_model_stream           | chunk(AIMessage) | {"text": chunk.content} (빈 content 스킵)       |
| on_tool_start                  | input            | {"tool", "status":"running"}                    |
| on_tool_end                    | output           | {"tool", "status":"done"}                       |
| on_chain_start (서브그래프명)  | input            | {"type":"agent_start", "taskId": name}          |
| on_chain_end   (서브그래프명)  | output           | {"verifiedFiles":[path...]} → {"type":"agent_done"} |
| on_custom_event (search_status)| payload          | {"searchStatus": payload} (요구사항 18.4)       |

⚠️ 설계 정합(사용자 결정): 요구사항 5.5의 허용 키 집합에는 `input`/`output` 이 없다.
태스크 초안은 tool 이벤트에 `input`/`output` 최상위 키를 포함했으나, Property 6(허용 키
부분집합)을 엄격히 준수하기 위해 tool 이벤트는 `{tool, status}` 만 emit 한다(프론트
`onTool` 핸들러는 tool/status 만 소비하므로 무회귀).

무한대기 차단 (API_NOTES CRITICAL 2 — Python 3.14 hang 이력):
- 스트림 소비 루프 **전체를 `asyncio.wait_for` 로 감싸지 않는다.** 대신 async iterator 를
  `aiter()` 로 얻어 **개별 `__anext__()` 만** heartbeat_interval 로 wait 한다. 이때
  `__anext__` 를 지속 task 로 만들고 `asyncio.shield` 로 감싸 타임아웃(heartbeat) 시에도
  in-flight next 가 취소되지 않게 한다 — 이벤트 유실 및 제너레이터 취소 hang 방지(CRITICAL 2).
  TimeoutError 시 heartbeat 를 emit 하고 동일 task 를 유지한 채 계속 대기한다(요구사항 6.8).
- 전체 상한(GRAPH_TOTAL_TIMEOUT)은 `loop.time()` deadline 을 각 반복에서 검사해 수동으로
  중단한다(요구사항 6.6/6.7).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Optional

# ── 서브그래프 노드 이름 집합 — on_chain_start/on_chain_end 에서 서브그래프 진입/종료 식별 ──
# design.md 계층 구조: Top Supervisor 아래 5개 도메인 서브그래프가 노드로 add 된다.
SUBGRAPH_NAMES = frozenset({"coding", "media", "research", "ops", "chat"})

# ── heartbeat / 전체 타임아웃 기본값 (요구사항 6.8 / 6.6) ──
HEARTBEAT_INTERVAL = float(os.environ.get("AE_HEARTBEAT_INTERVAL", "20"))
GRAPH_TOTAL_TIMEOUT = float(os.environ.get("AE_GRAPH_TOTAL_TIMEOUT", "1800"))

# ── 요구사항 5.5의 허용 이벤트 키 집합(부분집합만 emit — Property 6) ──
# deep-research-engine 요구사항 18.4: 검색 진행 표시를 위해 `searchStatus` 를 순수 확장으로
# 추가한다(기존 키 보존 — 무회귀, 신규 SSE 채널·CSP 변경 없음). design.md "9-3) 중계" 정합.
ALLOWED_EVENT_KEYS = frozenset(
    {
        "text",
        "thinking",
        "tool",
        "status",
        "verifiedFiles",
        "type",
        "taskId",
        "heartbeat",
        "answerQuality",
        "qualityPending",
        "error",
        "searchStatus",
        # 실행 계약 검사 결과(effect_ledger). [DONE] 직전 1회 방출되는 순수 확장 키다.
        # 기존 키·CSP·SSE 채널은 무변경(무회귀).
        "effectSummary",
    }
)

def _sse(payload: dict) -> str:
    """dict → `data: {json}\\n\\n` SSE 라인. ensure_ascii=False 로 한글 보존."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _extract_text(chunk: Any) -> str:
    """AIMessageChunk.content → 텍스트. 멀티모달 리스트 content 를 방어적으로 평탄화.

    API_NOTES 항목 5: on_chat_model_stream 의 data.chunk 는 AIMessageChunk 이며 content
    가 토큰 텍스트다. 다만 멀티모달 응답에서는 content 가 리스트(dict 블록)일 수 있어
    text 조각만 이어붙인다.
    """
    if chunk is None:
        return ""
    content = getattr(chunk, "content", "")
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(p.get("text", ""))
            else:
                parts.append(str(p))
        return "".join(parts)
    return content if isinstance(content, str) else str(content)


def _verified_paths_from_output(output: Any) -> list:
    """서브그래프 종료(on_chain_end) output 에서 verified_files 의 path 만 추출.

    요구사항 5.4: 디스크 실측된 path 만 `{verifiedFiles}` 로 emit 한다. 디스크 검증은
    이미 GatewayToolNode / verify 노드에서 수행되어 state.verified_files 에 반영되므로,
    여기서는 그 결과의 path 필드만 노출한다.
    """
    if not isinstance(output, dict):
        return []
    vfs = output.get("verified_files")
    if not isinstance(vfs, list):
        return []
    paths = []
    for vf in vfs:
        if isinstance(vf, dict):
            p = vf.get("path")
            if p:
                paths.append(p)
    return paths


async def graph_events_to_sse(
    compiled_graph: Any,
    state: Any,
    config: Optional[dict] = None,
    *,
    heartbeat_interval: Optional[float] = None,
    total_timeout: Optional[float] = None,
) -> AsyncIterator[str]:
    """compiled LangGraph 의 astream_events(v2) 를 기존 SSE 이벤트 계약으로 중계.

    Args:
        compiled_graph: `astream_events(input, config, version="v2")` 를 제공하는
                        compiled LangGraph(또는 그를 흉내내는 async generator 소스).
        state:          초기 GraphState(dict).
        config:         astream_events config(thread_id / recursion_limit 등). None 허용.
        heartbeat_interval: 이벤트 무수신 시 heartbeat emit 주기(초). None → HEARTBEAT_INTERVAL.
        total_timeout:  그래프 전체 시간 상한(초). None → GRAPH_TOTAL_TIMEOUT.

    Yields:
        `data: {json}\\n\\n` 형식의 SSE 문자열. 마지막에 `data: [DONE]\\n\\n`.

    무한대기 차단 (API_NOTES CRITICAL 2):
        - 스트림 소비 루프 전체를 `asyncio.wait_for` 로 감싸지 않는다.
        - async iterator 를 `aiter()` 로 얻고, 개별 `__anext__()` 를 **지속 task 로 만들어
          `asyncio.shield` 로 감싼 뒤** heartbeat_interval 로만 wait 한다. TimeoutError 시
          heartbeat 를 emit 하고 **동일 task 를 유지한 채** 계속 대기한다.
        - ⚠️ 왜 shield 인가: `wait_for(it.__anext__(), hb)` 를 직접 쓰면 타임아웃 시 진행
          중이던 `__anext__` 가 **취소**되어 (1) 지연 후 도착할 이벤트가 유실되고 (2) 취소가
          스트림 제너레이터로 전파돼 CRITICAL 2 가 경고한 hang/오염을 유발한다. shield 는
          바깥 wait_for 취소가 내부 task 로 전파되지 않게 해 이벤트 보존 + 무취소를 보장한다
          (기존 server.py `_stream_with_heartbeat` 의 producer 비취소 철학과 동일).
        - total_timeout 은 loop.time() deadline 으로 각 반복에서 수동 검사(초과 시 error+종료).
    """
    hb = heartbeat_interval if heartbeat_interval is not None else HEARTBEAT_INTERVAL
    tt = total_timeout if total_timeout is not None else GRAPH_TOTAL_TIMEOUT

    loop = asyncio.get_event_loop()
    deadline = loop.time() + tt

    # astream_events 는 async generator 를 반환. 이를 async iterator 로 얻는다.
    # ⚠️ CRITICAL 2: 이 async iterator 를 통째로 wait_for 로 감싸지 않는다.
    events = compiled_graph.astream_events(state, config=config, version="v2")
    it = aiter(events)

    # ── 실행 계약 검사(effect_ledger) ──────────────────────────────────────
    # "선언(설정·의도)" 과 "관측(도구 호출·제공자·결과 수)" 을 대조해 불일치를 [DONE]
    # 직전에 1회 방출한다. 이 리포에서 반복된 결함은 모두 "기능이 켜져 있는데 조용히
    # 아무것도 안 한다" 형태였고(라우팅 오분류·게이트 미적용·키 없어 0건 등), 개별 수정만
    # 으로는 재발했다. 중계 계약은 그대로 두고 같은 이벤트를 수집기에도 흘려보낸다.
    # 수집·판정은 전부 예외를 삼키므로 실패해도 스트림을 막지 않는다.
    _ledger = None
    _declared: dict = {}
    try:
        from ai_engine.agent_system.effect_ledger import (
            EffectCollector,
            build_effect_summary,
            declared_from_env,
            should_emit_summary,
        )

        _ledger = EffectCollector()
        _prompt = state.get("prompt", "") if isinstance(state, dict) else ""
        _declared = declared_from_env(_prompt)
    except Exception:  # noqa: BLE001 — 관측 장치 실패는 본체를 막지 않는다
        _ledger = None

    pending: Optional[asyncio.Task] = None
    try:
        while True:
            # 전체 시간 상한 초과 검사 — wait_for 미사용(수동 deadline, 요구사항 6.6/6.7).
            if loop.time() > deadline:
                yield _sse({"error": "graph_total_timeout"})
                break

            # 다음 이벤트를 지속 task 로 확보(이미 진행 중이면 재사용 — 이벤트 유실 방지).
            if pending is None:
                pending = asyncio.ensure_future(anext(it))

            # 개별 next 만 heartbeat_interval 로 wait(루프 전체 wait_for 금지 — CRITICAL 2).
            # shield 로 감싸 타임아웃 시에도 pending task 가 취소되지 않게 한다.
            try:
                event = await asyncio.wait_for(asyncio.shield(pending), hb)
            except asyncio.TimeoutError:
                # 이벤트가 heartbeat_interval 동안 없었음 → keep-alive heartbeat emit 후
                # 동일 pending 을 유지한 채 계속 대기(이벤트 보존).
                yield _sse({"heartbeat": True})
                continue
            except StopAsyncIteration:
                # 스트림 자연 종료.
                pending = None
                break

            # 이벤트 수신 완료 → 다음 반복에서 새 task 를 만들도록 초기화.
            pending = None

            etype = event.get("event", "")
            name = event.get("name", "")
            data = event.get("data") or {}

            if etype == "on_chat_model_stream":
                # 토큰 스트림 → {text}. 빈 content 는 스킵(요구사항 5.1).
                text = _extract_text(data.get("chunk"))
                if text:
                    yield _sse({"text": text})

            elif etype == "on_tool_start":
                # 도구 실행 시작 → {tool, status:"running"}(요구사항 5.2 + 5.5 허용 키만).
                if _ledger is not None:
                    _ledger.on_tool_start(name)
                yield _sse({"tool": name, "status": "running"})

            elif etype == "on_tool_end":
                # 도구 실행 종료 → {tool, status:"done"}(요구사항 5.2 + 5.5 허용 키만).
                yield _sse({"tool": name, "status": "done"})

            elif etype == "on_chain_start" and name in SUBGRAPH_NAMES:
                # 서브그래프 진입 → agent_start(요구사항 5.3).
                if _ledger is not None:
                    _ledger.on_agent_start(name)
                yield _sse({"type": "agent_start", "taskId": name})

            elif etype == "on_chain_end" and name in SUBGRAPH_NAMES:
                # 서브그래프 종료 → (verified_files path 만 emit) → agent_done(요구사항 5.3/5.4).
                paths = _verified_paths_from_output(data.get("output"))
                if paths:
                    if _ledger is not None:
                        _ledger.on_verified_files(paths)
                    yield _sse({"verifiedFiles": paths})
                yield _sse({"type": "agent_done", "taskId": name})

            elif etype == "on_custom_event" and name == "search_status":
                # 검색 진행 이벤트(deep-research-engine 요구사항 18.4) → {searchStatus: payload}.
                # `adispatch_custom_event("search_status", payload)` 가 astream_events(v2) 에서
                # on_custom_event(name="search_status") 로 표면화되며, 이때 data 는 payload
                # 자체다(phase/kind/providers/query_summary/status). 순수 확장이라 기존 이벤트
                # 계약은 그대로 보존(무회귀), 신규 SSE 채널·CSP 변경 없음. payload 에는
                # Provider_Credential 원문이 포함되지 않는다(P9 — 방출 측 GatewayToolNode 보장).
                if _ledger is not None:
                    _ledger.on_search_status(data)
                yield _sse({"searchStatus": data})

            # 그 외 이벤트(on_chain_stream / on_chat_model_start 등)는 무시.

    except Exception as exc:
        # 노드 예외 / GatewayModelError 등 → {error} 후 종료(요구사항 5.7).
        yield _sse({"error": str(exc)})
    finally:
        # 조기 종료(total_timeout / error) 시 남은 next task 를 best-effort 취소.
        # await 로 회수하지 않는다(제너레이터 취소가 hang 될 수 있음 — CRITICAL 2). 그래프
        # 실행 자체는 recursion_limit / per-node 타임아웃으로 유한 종료가 이미 보장된다.
        if pending is not None and not pending.done():
            pending.cancel()

    # 실행 계약 검사 결과를 [DONE] 직전 **최대 1회** 방출한다(정상/에러/타임아웃 무관).
    #
    # 방출 여부는 should_emit_summary 가 정한다 — 불일치가 있을 때, 또는 불일치가 없어도
    # 외부 조회 활동이 관측됐을 때만이다. 처음 구현은 무조건 방출했는데 순수 코딩 질문과
    # 빈 스트림에도 페이로드가 실렸고, 페이로드 목록을 정확 동일로 단정하던 SSE 계약
    # 테스트 6건이 깨졌다(test_langgraph_sse_contract_pbt.py). 계약을 느슨하게 푸는 대신
    # 방출을 좁혔다 — 그 단정은 "약속하지 않은 이벤트를 흘리지 않는다" 를 지키는 장치라
    # 느슨하게 만들면 다른 유출도 같이 통과한다.
    if _ledger is not None:
        try:
            _summary = build_effect_summary(_declared, _ledger.observed())
            if should_emit_summary(_summary):
                yield _sse({"effectSummary": _summary})
        except Exception:  # noqa: BLE001 — 요약 방출 실패는 [DONE] 을 막지 않는다
            pass

    # 스트림 종료(정상/에러 무관) → [DONE](요구사항 5.6).
    yield "data: [DONE]\n\n"
