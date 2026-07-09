"""verify 노드 — 산출물/답변 검증 (기존 검증 자산 재사용, 재구현 금지).

Task 5.2 산출물. design.md 섹션 4의 verify 노드 정의를 반영한다.

책임 (요구사항 3.3 / 3.4 / 3.5 / 3.6 / 3.8 / 7.5):
- final_text = 마지막 AIMessage 텍스트 확정 (요구사항 3.3).
- evidence.chunks 가 있으면 citation.parse_citations + verify_citations 로 인용을
  verified / unverified 로 분류해 citations 에 기록 (요구사항 3.4).
  **unverified 가 있어도 절대 raise/차단하지 않는다** (요구사항 3.5 / Property 7 — 가용성 우선).
- answer_quality: verify_mode 가 off 가 아니고 모듈이 사용 가능하면 enhance_answer 로
  metadata 를 구성한다. 비차단 — 실패/타임아웃은 무시하고 answer_quality 를 생략한다.
- 파일 생성 의도가 있었으나(state prompt 로 _infer_file_intent_from_prompt 판정)
  verified_files 가 비어있으면 server.py `_force_generate_from_text` 를 호출해 결과를
  verified_files 에 병합한다 (요구사항 3.6 / 3.8 / 7.5). 이 호출도 timeout 으로 감싼다.

Invariant:
- answer 는 절대 차단하지 않는다(가용성 우선). 모든 검증 실패는 비차단.
- verify_node 자체는 LLM 을 직접 호출하지 않는다. 콘텐츠 보강/파일 변환은 전부
  server.py `_force_generate_from_text` 내부에서 처리한다(순환참조 방지 위해 지연 import).
- 강제 생성 결과는 디스크 실측(os.path.isfile & size>0) 후에만 verified_files 로 병합한다.

기존 자산 재사용(재구현 금지 — 요구사항 7.5):
- citation.parse_citations / verify_citations / RetrievedRange (`ai_engine/rag/citation.py`)
- answer_quality.enhance_answer / verify_mode (`ai_engine/rag/answer_quality.py`)
- server._infer_file_intent_from_prompt / _force_generate_from_text (`ai_engine/server.py`)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, List

from langchain_core.messages import AIMessage, BaseMessage


# ─────────────────────────────────────────────────────────────────────────────
# 타임아웃 상수 (env override — 요구사항 6.x, 모든 외부 호출 유한 시간 종료)
# ─────────────────────────────────────────────────────────────────────────────
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# answer_quality.enhance_answer 상한(초). enhance_answer 내부 충실도 검증이 gateway 를
# 쓸 수 있으므로 여유를 둔다. 초과/실패해도 answer_quality 를 생략할 뿐 비차단.
ANSWER_QUALITY_TIMEOUT: float = _env_float("AE_VERIFY_AQ_TIMEOUT", 30.0)
# _force_generate_from_text 상한(초). 이미지 생성/HTML 렌더가 포함될 수 있어 넉넉히.
FORCE_GENERATE_TIMEOUT: float = _env_float("AE_FORCE_GENERATE_TIMEOUT", 180.0)


# ─────────────────────────────────────────────────────────────────────────────
# 순수 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
def _last_ai_text(messages: List[BaseMessage]) -> str:
    """마지막 AIMessage 의 텍스트를 추출(없으면 빈 문자열).

    content 가 str 이면 그대로, list(멀티모달 블록)면 text 조각을 이어붙인다.
    """
    for msg in reversed(messages or []):
        if isinstance(msg, AIMessage):
            content = msg.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                ]
                return "".join(parts)
    return ""


def _classify_citations(final_text: str, evidence: Any) -> dict:
    """evidence.chunks 를 근거로 final_text 의 인용을 verified / unverified 로 분류.

    Precondition:  evidence 는 dict 이고 evidence["chunks"] 는 [(chunk, score), ...]
                   형태. 각 chunk 는 file_path / start_line / end_line 속성을 갖는다.
    Postcondition: {"verified": [raw...], "unverified": [raw...]} 반환. 인용이 없거나
                   evidence 가 없으면 빈 리스트 2개.
    Invariant:     어떤 예외도 밖으로 전파하지 않는다(요구사항 3.5 / Property 7 — 비차단).
    """
    report = {"verified": [], "unverified": []}
    if not isinstance(evidence, dict):
        return report
    chunks = evidence.get("chunks")
    if not chunks:
        return report
    try:
        from ai_engine.rag.citation import (
            RetrievedRange,
            parse_citations,
            verify_citations,
        )

        ranges: List[Any] = []
        for item in chunks:
            # item = (chunk, score) 또는 chunk (방어적).
            c = item[0] if isinstance(item, (tuple, list)) and item else item
            fp = getattr(c, "file_path", None)
            s = getattr(c, "start_line", None)
            e = getattr(c, "end_line", None)
            if fp and isinstance(s, int) and isinstance(e, int):
                ranges.append(RetrievedRange(file=fp, start_line=s, end_line=e))

        result = verify_citations(parse_citations(final_text), ranges)
        report = {
            "verified": [c.raw for c in result.verified],
            "unverified": [c.raw for c in result.unverified],
        }
    except Exception as e:  # noqa: BLE001 — 검증 실패는 비차단(가용성 우선).
        print(f"[verify] citation 분류 실패(비차단): {e}")
    return report


async def _build_answer_quality(final_text: str, evidence: Any, deps: Any) -> dict:
    """answer_quality metadata 를 구성(비차단). verify_mode 가 off 이거나 모듈이 없으면 {}.

    Invariant: 실패/타임아웃은 무시하고 {} 를 반환한다(요구사항 3.5 — answer 를 막지 않음).
    """
    try:
        from ai_engine.rag.answer_quality import enhance_answer, verify_mode
    except Exception:
        # answer_quality 모듈 미가용 → answer_quality 생략.
        return {}

    try:
        if verify_mode() == "off":
            return {}
    except Exception:
        return {}

    context_text = ""
    chunks = None
    if isinstance(evidence, dict):
        ctx = evidence.get("context")
        if isinstance(ctx, str):
            context_text = ctx
        chunks = evidence.get("chunks")

    gw = getattr(deps, "gateway", None)
    try:
        res = await asyncio.wait_for(
            enhance_answer(
                final_text,
                context_text=context_text,
                retrieved_chunks=chunks,
                gw=gw,
            ),
            timeout=ANSWER_QUALITY_TIMEOUT,
        )
        return (res or {}).get("metadata") or {}
    except asyncio.TimeoutError:
        print(f"[verify] answer_quality 타임아웃(비차단, {ANSWER_QUALITY_TIMEOUT}s)")
        return {}
    except Exception as e:  # noqa: BLE001 — 비차단.
        print(f"[verify] answer_quality 실패(비차단): {e}")
        return {}


async def _invoke_force_generate(state: Any, deps: Any) -> List[dict]:
    """파일 생성 의도가 있으나 산출물 0건일 때 server.py 강제 생성 폴백을 호출.

    server.py 함수는 순환참조 방지를 위해 함수 내부에서 지연 import 한다.
    _force_generate_from_text 는 async 이면 await, 혹시 동기라면 asyncio.to_thread 로
    실행한다(방어적). 모든 호출은 FORCE_GENERATE_TIMEOUT 으로 감싼다.

    Postcondition: 디스크 실측(os.path.isfile & size>0)을 통과한 파일만 VerifiedFile
                   dict({path, absPath, tool}) 리스트로 반환. 실패/타임아웃/의도 없음이면 [].
    Invariant:     어떤 예외도 전파하지 않는다(비차단 — 요구사항 3.5).
    """
    prompt = state.get("prompt") or ""
    final_text = state.get("final_text") or _last_ai_text(state.get("messages") or [])

    try:
        from ai_engine.server import (
            _force_generate_from_text,
            _infer_file_intent_from_prompt,
        )
    except Exception as e:  # noqa: BLE001 — server 모듈 미가용 시 비차단.
        print(f"[verify] server 강제 생성 자산 import 실패(비차단): {e}")
        return []

    try:
        primary_tool, wanted, target_files = _infer_file_intent_from_prompt(
            prompt, "", final_text
        )
    except Exception as e:  # noqa: BLE001
        print(f"[verify] 파일 의도 판정 실패(비차단): {e}")
        return []

    if not wanted:
        return []

    project_path = state.get("project_path") or ""
    aws_profile = state.get("aws_profile") or ""
    bedrock_user = state.get("bedrock_user") or ""
    template_id = state.get("template_id") or ""
    title = prompt[:80]

    async def _run() -> Any:
        call = _force_generate_from_text(
            primary_tool=primary_tool,
            target_files=target_files,
            title=title,
            description=prompt,
            final_text=final_text or prompt,
            project_path=project_path,
            aws_profile=aws_profile,
            bedrock_user=bedrock_user,
            template_id=template_id,
        )
        if asyncio.iscoroutine(call):
            return await call
        # 방어적: 동기 반환(향후 시그니처 변경 대비)이면 스레드로 감싼다.
        return await asyncio.to_thread(lambda: call)

    try:
        forced = await asyncio.wait_for(_run(), timeout=FORCE_GENERATE_TIMEOUT)
    except asyncio.TimeoutError:
        print(f"[verify] 강제 생성 타임아웃(비차단, {FORCE_GENERATE_TIMEOUT}s)")
        return []
    except Exception as e:  # noqa: BLE001
        print(f"[verify] 강제 생성 실패(비차단): {e}")
        return []

    # 반환: [(rel_path, finfo), ...]. 디스크 실측 후 VerifiedFile 로 정규화.
    verified: List[dict] = []
    for item in forced or []:
        try:
            finfo = item[1] if isinstance(item, (tuple, list)) and len(item) > 1 else item
            if not isinstance(finfo, dict):
                continue
            abs_path = finfo.get("absPath") or ""
            if not abs_path or not os.path.isfile(abs_path) or os.path.getsize(abs_path) <= 0:
                print(f"[verify] 강제 생성 결과 디스크 실측 실패 — 스킵: {abs_path}")
                continue
            verified.append(
                {
                    "path": finfo.get("path", ""),
                    "absPath": abs_path,
                    "tool": finfo.get("tool", "deterministic-converter"),
                }
            )
        except Exception as e:  # noqa: BLE001
            print(f"[verify] 강제 생성 결과 정규화 실패(비차단): {e}")
            continue
    return verified


# ─────────────────────────────────────────────────────────────────────────────
# 노드 팩토리
# ─────────────────────────────────────────────────────────────────────────────
def make_verify_node(deps: Any):
    """verify 노드 팩토리 → async verify_node(state) -> dict.

    Postcondition (design.md 섹션 4):
      - final_text = 마지막 AIMessage 텍스트 (요구사항 3.3).
      - citations = {"verified": [...], "unverified": [...]} (evidence.chunks 기반, 요구사항 3.4).
      - answer_quality = enhance_answer metadata (verify_mode != off 이고 가용 시에만).
      - verified_files = 파일 의도 있으나 기존 verified_files 0건이면 강제 생성 병합분.
    Invariant: answer 는 절대 차단하지 않는다. 모든 검증 실패는 비차단(요구사항 3.5 / Property 7).
    """

    async def verify_node(state: Any) -> dict:
        messages = state.get("messages") or []
        final_text = _last_ai_text(messages)

        # (1) citation 분류 — 비차단(unverified 있어도 예외 없이 진행).
        evidence = state.get("evidence")
        citations = _classify_citations(final_text, evidence)

        out: dict = {"final_text": final_text, "citations": citations}

        # (2) answer_quality metadata — verify_mode off 가 아니고 가용 시에만.
        answer_quality = await _build_answer_quality(final_text, evidence, deps)
        if answer_quality:
            out["answer_quality"] = answer_quality

        # (3) 강제 생성 폴백 — 파일 의도 있으나 산출물 0건일 때만.
        #     final_text 를 헬퍼가 참조하므로 state 에 반영해 전달.
        if not state.get("verified_files"):
            forced = await _invoke_force_generate({**state, "final_text": final_text}, deps)
            if forced:
                out["verified_files"] = forced

        return out

    return verify_node
