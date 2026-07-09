"""retrieve 노드 — RAG 근거를 GraphState.evidence 에 적재(프롬프트 주입 대체).

Task 5.1 산출물 (Phase 3). design.md 섹션 4(retrieve 노드) + API_NOTES.md(CRITICAL 2)를
근거로 한다.

핵심 사항:
- **기존 RAG 자산 재사용(재구현 금지 — 요구사항 7.5):** `context_builder.build_system_prompt`
  하나만 호출한다. 이 함수는 내부적으로 `ProjectIndexer`(TF-IDF) / `FastEmbedProvider`(384dim
  임베딩) / `VectorStore` 를 `build_context → get_indexer / get_searcher` 경로로 재사용하므로,
  이 노드는 그 자산들을 직접 재조립하지 않는다.
- **타임아웃 (요구사항 6.3 / API_NOTES CRITICAL 2):** `build_system_prompt` 는 스트림이 아니라
  **단발(blocking) 함수**이므로 `asyncio.to_thread(...)` 로 오프로딩한 뒤 그 **개별 awaitable
  하나만** `asyncio.wait_for(..., RETRIEVE_NODE_TIMEOUT)` 로 감싸는 것이 안전하다(스트림 소비
  루프를 wait_for 로 감싸면 Python 3.14 에서 취소 시 hang — 하지만 여기선 스트림이 없다).
- **비차단 (요구사항 3.2 / 6.3 — 가용성 우선):** project_path 가 없거나 domain=="chat" 이면
  검색을 건너뛰고 `{"evidence": None}` 을 반환한다. 타임아웃/예외도 `{"evidence": None}` 으로
  삼켜 그래프 진행을 막지 않는다.
- **return_evidence 방어:** 현재 `build_system_prompt` 는 `return_evidence` 를 지원하며
  `(prompt, {"context", "chunks"})` 튜플을 반환한다(실측 확정). 만약 향후 시그니처가 바뀌어
  이 인자를 지원하지 않으면, prompt 문자열만 받아 chunks 없는 evidence 를 최대한 구성한다.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from typing import Any

# RETRIEVE_NODE_TIMEOUT — RAG 검색 상한(초). 기본 30 — indexer TF-IDF 검색은 빠름.
# env override: AE_RETRIEVE_TIMEOUT (요구사항 6.3).
try:
    RETRIEVE_NODE_TIMEOUT: float = float(os.environ.get("AE_RETRIEVE_TIMEOUT", "30"))
except (TypeError, ValueError):
    RETRIEVE_NODE_TIMEOUT = 30.0


def _supports_return_evidence(func: Any) -> bool:
    """build_system_prompt 가 return_evidence 키워드를 지원하는지 시그니처로 판단.

    지원하면 (prompt, evidence) 튜플을 받을 수 있고, 미지원이면 prompt 문자열만 받는다.
    시그니처 introspection 실패 시(내장/C 확장 등) 보수적으로 False 를 반환한다.
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return False
    params = sig.parameters
    if "return_evidence" in params:
        return True
    # **kwargs 를 받으면 지원 가능성이 있으나, 안전하게 False(문자열 경로)로 처리.
    return False


def make_retrieve_node(deps: Any, domain: str = "coding"):
    """retrieve 노드 팩토리 → `async def retrieve_node(state) -> dict`.

    Args:
        deps:   GraphDeps (gateway / model_coding / checkpointer 등). gateway 는
                build_system_prompt 의 gateway_client 로 전달된다.
        domain: 서브그래프 도메인. "chat" 이면 RAG 를 수행하지 않는다(도구 불필요 경량 대화).

    Postcondition (요구사항 3.1 / 3.2):
        - project_path 가 있고 domain != "chat" 이면 build_system_prompt 로 근거를 조회해
          {"system_prompt": <근거 포함 프롬프트>, "evidence": {"context", "chunks"}} 반환.
        - project_path 가 없거나 domain == "chat" 이면 {"evidence": None} 반환(비차단).
    Invariant (요구사항 6.3):
        - 검색은 asyncio.wait_for(RETRIEVE_NODE_TIMEOUT) 로 감싸고, 타임아웃/예외 시
          {"evidence": None} 을 반환하여 그래프 진행을 막지 않는다.
    """

    async def retrieve_node(state: Any) -> dict:
        # ── 비차단 스킵 조건 (요구사항 3.2) ──
        project_path = state.get("project_path")
        if not project_path or domain == "chat":
            return {"evidence": None}

        try:
            # 기존 RAG 자산 재사용 — build_system_prompt 내부가 indexer/embedder/vectorstore 를
            # 재사용한다(재구현 금지, 요구사항 7.5).
            from ai_engine.rag.context_builder import build_system_prompt

            gateway = getattr(deps, "gateway", None)
            supports_evidence = _supports_return_evidence(build_system_prompt)

            def _call():
                # 단발(blocking) 호출. return_evidence 지원 여부에 따라 반환 형태가 다르다.
                kwargs = dict(
                    project_path=project_path,
                    query=state.get("prompt", ""),
                    open_file=state.get("open_file"),
                    open_file_content=state.get("open_file_content"),
                    base_system_prompt=state.get("system_prompt", "") or "",
                    aws_profile=state.get("aws_profile", "") or "",
                    bedrock_user=state.get("bedrock_user", "") or "",
                    gateway_client=gateway,
                )
                if supports_evidence:
                    kwargs["return_evidence"] = True
                return build_system_prompt(**kwargs)

            # 단발 함수이므로 개별 awaitable 하나만 wait_for 로 감싼다(API_NOTES CRITICAL 2 안전).
            result = await asyncio.wait_for(
                asyncio.to_thread(_call), timeout=RETRIEVE_NODE_TIMEOUT
            )

            if supports_evidence and isinstance(result, tuple) and len(result) == 2:
                # (system_prompt, evidence) — evidence = {"context", "chunks"}
                system_prompt, evidence = result
                if not isinstance(evidence, dict):
                    # 방어: 예상 밖 형태면 최소 evidence 구성.
                    evidence = {"context": "", "chunks": []}
                else:
                    evidence.setdefault("context", "")
                    evidence.setdefault("chunks", [])
                return {"system_prompt": system_prompt, "evidence": evidence}

            # 미지원(또는 예상 밖 반환) — prompt 문자열만 받았다고 보고 chunks 없이 최대 구성.
            system_prompt = result if isinstance(result, str) else str(result)
            return {
                "system_prompt": system_prompt,
                "evidence": {"context": system_prompt, "chunks": []},
            }
        except asyncio.TimeoutError:
            # 타임아웃 — 비차단(요구사항 6.3, 가용성 우선).
            return {"evidence": None}
        except Exception:
            # RAG 실패는 비차단(요구사항 3.2 / 6.3).
            return {"evidence": None}

    return retrieve_node
