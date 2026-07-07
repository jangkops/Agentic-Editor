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
    """근거 품질 검증 활성 여부. **기본 자동 ON**(사용자 공수 0).

    끄려면 AE_ANSWER_QUALITY=0/false 명시. 미설정/빈값이면 활성.
    기본 모드는 deferred(비차단)라 답변 UX에 지연을 주지 않는다.
    """
    env = env if env is not None else os.environ
    val = env.get("AE_ANSWER_QUALITY")
    if val is None or str(val).strip() == "":
        return True
    return _truthy(val)


_LOCAL_EMBEDDER = None


def _get_local_embedder():
    """로컬 grounding용 임베딩 provider(프로세스 싱글턴). 게이트웨이 불필요.

    실패해도 None 반환(비차단). 기본 fastembed, 미가용 시 내부 폴백.
    """
    global _LOCAL_EMBEDDER
    if _LOCAL_EMBEDDER is None:
        try:
            from ai_engine.rag.embedder import get_embedding_provider
            env = dict(os.environ)
            env.setdefault("AE_EMBED_PROVIDER", "fastembed")
            _LOCAL_EMBEDDER = get_embedding_provider(env)
        except Exception as e:
            print(f"[AnswerQuality] 로컬 임베더 초기화 실패(비차단): {e}")
            _LOCAL_EMBEDDER = False  # 재시도 방지 sentinel
    return _LOCAL_EMBEDDER or None


def _chunk_texts(chunks) -> List[str]:
    """검색 청크(또는 (chunk,score)) → grounding용 텍스트 목록(방어적)."""
    out = []
    for item in chunks or []:
        c = item[0] if isinstance(item, (tuple, list)) and item else item
        txt = getattr(c, "content", None)
        if isinstance(txt, str) and txt.strip():
            out.append(txt)
    return out


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

    # 1.5) 로컬 grounding 점수 (LLM/게이트웨이 불필요 — 항상 계산 시도).
    #      게이트웨이가 느리거나 없어도 근거-기반성 하한 신호를 제공(한계 개선).
    try:
        from ai_engine.rag.verifier import local_grounding_score
        chunk_texts = _chunk_texts(retrieved_chunks) or (
            [context_text] if context_text else [])
        if chunk_texts:
            gs = local_grounding_score(answer, chunk_texts, _get_local_embedder())
            if gs is not None:
                metadata["grounding"] = {"score": round(gs, 4),
                                         "method": "local-embedding-cosine"}
    except Exception as e:
        metadata["grounding_error"] = str(e)[:200]

    # 2) 충실도 검증 (기본 자동 ON — 끄려면 AE_VERIFY=0). gw 없으면 자동 skip.
    _v = env.get("AE_VERIFY")
    _verify_on = True if (_v is None or str(_v).strip() == "") else _truthy(_v)
    if _verify_on and gw is not None and context_text:
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
    # 기본 deferred — 자동 ON이어도 응답을 막지 않도록(비차단). 빠른 게이트웨이면 inline 선택 가능.
    mode = str(env.get("AE_VERIFY_MODE") or "deferred").strip().lower()
    return mode if mode in ("inline", "deferred", "off") else "deferred"


async def run_deferred_verification(answer: str, context_text: str, retrieved_chunks,
                                    gw, session_id: str, message_id: str,
                                    env: Optional[dict] = None) -> dict:
    """비차단 지연 검증 — 응답 경로와 분리해 실행 후 품질 저장소에 기록.

    긴 타임아웃 허용(응답을 막지 않으므로). 예외는 삼켜 저장 실패로만 남긴다(비차단).
    반환은 저장한 metadata(관측/테스트용).
    """
    from ai_engine.rag.quality_store import save_quality
    # deferred는 비차단이므로 충실도 타임아웃을 넉넉히(기본 120s) — 응답 UX 영향 없음.
    _env = dict(env if env is not None else os.environ)
    _env.setdefault("AE_VERIFY_TIMEOUT_MS", "120000")
    try:
        res = await enhance_answer(answer, context_text=context_text,
                                   retrieved_chunks=retrieved_chunks, gw=gw, env=_env)
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
