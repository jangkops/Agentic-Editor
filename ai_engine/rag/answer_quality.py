"""응답 품질 오케스트레이터 — 근거 검증/충실도 검증을 플래그 게이트로 묶는 단일 seam.

server.py 채팅 경로가 최종 응답 텍스트 + 검색 컨텍스트를 넘기면, 활성화된 단계만
수행하고 `{answer, metadata}` 를 반환한다. 모든 단계는 환경변수로 on/off 되며,
전부 off(기본)이면 응답을 그대로 통과시킨다(무회귀). 어떤 단계 실패도 비차단 폴백.

플래그(env):
  AE_ANSWER_QUALITY   : 마스터 스위치("1"이어야 하위 단계 동작). 기본 off.
  AE_VERIFY           : 충실도 검증 활성("1"). 마스터가 on일 때만.
  AE_VERIFY_THRESHOLD : 충실도 임계값(기본 0.7).
  AE_VERIFY_MODEL     : 검증에 쓸 경량 모델 id.
  AE_VERIFY_TIMEOUT_MS: 검증 타임아웃(ms, 기본 10000).

Requirements: 2.2, 3.2, 3.3, 3.4, 3.5, 10.1, 10.2, 10.4
"""
import os
from typing import List, Optional

from ai_engine.rag.citation import (
    parse_citations, verify_citations, RetrievedRange,
)
from ai_engine.rag.verifier import verify_faithfulness


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def quality_enabled(env: Optional[dict] = None) -> bool:
    env = env if env is not None else os.environ
    return _truthy(env.get("AE_ANSWER_QUALITY"))


def _ranges_from_chunks(chunks) -> List[RetrievedRange]:
    """검색 청크(또는 (chunk,score) 튜플) → RetrievedRange 목록(방어적)."""
    out = []
    for item in chunks or []:
        c = item[0] if isinstance(item, (tuple, list)) and item else item
        fp = getattr(c, "file_path", None)
        s = getattr(c, "start_line", None)
        e = getattr(c, "end_line", None)
        if fp and isinstance(s, int) and isinstance(e, int):
            out.append(RetrievedRange(file=fp, start_line=s, end_line=e))
    return out


def build_citation_metadata(answer: str, retrieved_chunks) -> dict:
    """응답 인용을 검색 근거와 대조한 메타데이터(순수, 게이트웨이 불필요).

    반환: {citations_total, verified, unverified: [raw...]}
    """
    cites = parse_citations(answer or "")
    ranges = _ranges_from_chunks(retrieved_chunks)
    report = verify_citations(cites, ranges)
    return {
        "citations_total": report.total,
        "verified": len(report.verified),
        "unverified": [c.raw for c in report.unverified],
    }


async def enhance_answer(answer: str, context_text: str, retrieved_chunks=None,
                         gw=None, env: Optional[dict] = None) -> dict:
    """응답 품질 후처리(오케스트레이션). 반환: {answer, metadata}.

    - 마스터 플래그 off → 원 응답 그대로, metadata={}(무회귀).
    - 인용 검증(순수)은 마스터 on이면 항상 수행(비용 0, 게이트웨이 불필요).
    - 충실도 검증은 AE_VERIFY on + gw 제공 시에만. 실패/타임아웃 → degraded 표기, 비차단.
    """
    env = env if env is not None else os.environ
    metadata: dict = {}
    if not quality_enabled(env):
        return {"answer": answer, "metadata": metadata}

    # 1) 인용 검증 (순수, 항상)
    try:
        metadata["citation"] = build_citation_metadata(answer, retrieved_chunks)
    except Exception as e:
        metadata["citation_error"] = str(e)

    # 2) 충실도 검증 (opt-in + gateway)
    if _truthy(env.get("AE_VERIFY")) and gw is not None and context_text:
        model = env.get("AE_VERIFY_MODEL") or "anthropic.claude-3-5-sonnet-20241022-v2:0"
        try:
            timeout = float(env.get("AE_VERIFY_TIMEOUT_MS", "10000")) / 1000.0
        except (TypeError, ValueError):
            timeout = 10.0
        try:
            res = await verify_faithfulness(gw, model, answer, context_text, timeout=timeout)
            metadata["faithfulness"] = {
                "score": res.score,
                "degraded": res.degraded,
                "feedback": (res.feedback or "")[:300],
                "latency_ms": res.latency_ms,
                "reason": res.reason,
            }
        except Exception as e:
            metadata["faithfulness"] = {"score": None, "degraded": True,
                                        "feedback": str(e)[:200] or type(e).__name__,
                                        "reason": "error"}

    return {"answer": answer, "metadata": metadata}


def verify_mode(env: Optional[dict] = None) -> str:
    """검증 실행 모드 결정: "off"(기본) | "inline" | "deferred".

    - off: 검증 안 함(무회귀 기본). AE_ANSWER_QUALITY 미설정 시.
    - inline: 응답 최종 이벤트 전에 동기 대기(빠른 게이트웨이용). AE_VERIFY_MODE=inline.
    - deferred: [DONE] 이후 백그라운드 실행 후 저장(느린 게이트웨이용). AE_VERIFY_MODE=deferred.

    라이브 실측상 게이트웨이 모델 호출이 매우 느리면(수십~수백 초) deferred가 최적이다.
    마스터 플래그(AE_ANSWER_QUALITY)가 off면 항상 off.
    """
    env = env if env is not None else os.environ
    if not quality_enabled(env):
        return "off"
    mode = str(env.get("AE_VERIFY_MODE", "inline")).strip().lower()
    return mode if mode in ("inline", "deferred", "off") else "inline"


async def run_deferred_verification(answer: str, context_text: str, retrieved_chunks,
                                    gw, session_id: str, message_id: str,
                                    env: Optional[dict] = None) -> dict:
    """비차단 지연 검증 — 응답 경로와 분리해 실행 후 품질 저장소에 기록.

    긴 타임아웃 허용(응답을 막지 않으므로). 예외는 삼켜 저장 실패로만 남긴다(비차단).
    반환은 저장한 metadata(관측/테스트용).
    """
    from ai_engine.rag.quality_store import save_quality
    try:
        res = await enhance_answer(answer, context_text=context_text,
                                   retrieved_chunks=retrieved_chunks, gw=gw, env=env)
        meta = res.get("metadata") or {}
    except Exception as e:  # noqa: BLE001
        meta = {"error": str(e) or type(e).__name__, "reason": "error"}
    meta["mode"] = "deferred"
    try:
        save_quality(session_id, message_id, meta, env=env)
    except Exception as e:  # noqa: BLE001
        print(f"[AnswerQuality] deferred 저장 실패(비차단): {e}")
    return meta


def faithfulness_below_threshold(metadata: dict, env: Optional[dict] = None) -> bool:
    """교정 재생성 트리거 판단(순수). degraded이거나 점수 없으면 재생성하지 않음(비차단)."""
    env = env if env is not None else os.environ
    f = (metadata or {}).get("faithfulness") or {}
    score = f.get("score")
    if score is None or f.get("degraded"):
        return False
    try:
        thr = float(env.get("AE_VERIFY_THRESHOLD", "0.7"))
    except (TypeError, ValueError):
        thr = 0.7
    return score < thr
