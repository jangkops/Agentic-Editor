"""충실도 검증 — 응답이 제공된 근거와 모순되지 않는지 경량 LLM으로 채점.

순수 함수(프롬프트 구성/점수 파싱)와 async 호출부(타임아웃/폴백)를 분리한다.
검증 실패/타임아웃 시 degraded=True로 비차단 폴백한다(가용성 우선).

Requirements: 3.1, 3.2, 3.5, 3.6  /  Property 5
"""
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class VerifyResult:
    score: Optional[float]   # None이면 검증 불가(degraded)
    degraded: bool
    feedback: str = ""
    latency_ms: Optional[float] = None   # 검증 LLM 호출 소요(관측/진단용)
    reason: str = ""                     # degraded 사유 코드: "timeout"|"error"|"empty"|""


def build_verify_prompt(answer: str, context: str) -> List[dict]:
    """응답과 근거를 대조해 충실도를 채점하도록 요청하는 messages(순수)."""
    instruction = (
        "당신은 엄격한 사실 검증자입니다. 아래 [근거] 범위 안에서만 [응답]이 "
        "뒷받침되는지 평가하세요. 근거에 없는 서술, 근거와의 모순, 과장된 주장은 "
        "감점 요인입니다.\n\n"
        f"[근거]\n{context[:12000]}\n\n"
        f"[응답]\n{answer[:8000]}\n\n"
        "충실도를 0.0~1.0로 채점하세요. 형식(반드시 준수):\n"
        "SCORE: X.X\nFEEDBACK: <근거 없는 주장이 있으면 지적, 없으면 'OK'>"
    )
    return [{"role": "user", "content": [{"text": instruction}]}]


def parse_faithfulness(text: str, default: float = 0.5) -> float:
    """응답에서 `SCORE: X.X`를 파싱. 없으면 default. 0.0~1.0로 클램프."""
    if not text:
        return default
    m = re.search(r'SCORE:\s*([0-1](?:\.\d+)?|\.\d+|\d+(?:\.\d+)?)', text, re.IGNORECASE)
    if not m:
        return default
    try:
        v = float(m.group(1))
    except ValueError:
        return default
    return max(0.0, min(1.0, v))


def parse_feedback(text: str) -> str:
    """`FEEDBACK:` 이후 텍스트 추출(없으면 빈 문자열)."""
    if not text: 
        return ""
    m = re.search(r'FEEDBACK:\s*(.+)', text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


async def verify_faithfulness(gw, model_id: str, answer: str, context: str,
                              timeout: float = 10.0) -> VerifyResult:
    """경량 LLM으로 충실도 채점. 실패/타임아웃 시 degraded 폴백(score=None)."""
    if not answer or not context:
        return VerifyResult(score=None, degraded=True,
                            feedback="empty answer/context", reason="empty")
    import time
    _t0 = time.perf_counter()

    def _ms():
        return round((time.perf_counter() - _t0) * 1000, 1)

    try:
        import asyncio as _asyncio
        from ai_engine.rag.gw_text import converse_text
        messages = build_verify_prompt(answer, context)
        # 스트리밍 우선(저지연·취소가능), 실패 시 동기 converse 폴백.
        text = await converse_text(gw, model_id, messages, timeout=timeout)
        return VerifyResult(
            score=parse_faithfulness(text),
            degraded=False,
            feedback=parse_feedback(text),
            latency_ms=_ms(),
            reason="",
        )
    except _asyncio.TimeoutError:
        # 빈 메시지 대신 명시적 사유 — 운영 진단(게이트웨이 지연) 가능.
        return VerifyResult(score=None, degraded=True,
                            feedback=f"verify timeout after {timeout:.0f}s",
                            latency_ms=_ms(), reason="timeout")
    except Exception as e:
        return VerifyResult(score=None, degraded=True,
                            feedback=f"verify failed: {str(e) or type(e).__name__}",
                            latency_ms=_ms(), reason="error")


def _extract_text(resp) -> str:
    try:
        if isinstance(resp, str):
            return resp
        if isinstance(resp, dict):
            out = resp.get("output") or resp
            msg = out.get("message") if isinstance(out, dict) else None
            content = (msg or {}).get("content") if isinstance(msg, dict) else None
            if isinstance(content, list):
                return "".join(c.get("text", "") for c in content if isinstance(c, dict))
            if isinstance(resp.get("content"), list):
                return "".join(c.get("text", "") for c in resp["content"] if isinstance(c, dict))
            return str(resp.get("text", ""))
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────
# 로컬 grounding 점수 — 게이트웨이 LLM 없이 근거-기반성을 근사 (한계 개선)
#
# LLM 충실도 검증은 게이트웨이 응답 지연에 의존해 무점수(degraded)로 빠지기 쉽다.
# 이를 보완하기 위해, 로컬 임베딩(FastEmbed 등)으로 답변 각 문장이 제공된 근거와
# 얼마나 의미적으로 겹치는지를 코사인 유사도로 근사한다. LLM/게이트웨이 불필요 →
# 게이트웨이가 느리거나 없어도 항상 점수가 나온다.
#
# 정직한 한계 표기: 이는 어휘/의미 겹침 근사이며 진짜 함의(entailment)·모순 탐지가
# 아니다. LLM 충실도의 대체가 아니라 항상 제공되는 하한 신호로 사용한다.
# ─────────────────────────────────────────────────────────────────────────
import re as _re


def _split_sentences(text: str, max_sents: int = 40) -> List[str]:
    """답변을 문장/줄 단위로 분리(한/영). 너무 짧은 조각 제외, 상한 적용."""
    if not text:
        return []
    parts = _re.split(r'(?<=[.!?。])\s+|\n+', text)
    out = [p.strip() for p in parts if len(p.strip()) >= 5]
    return out[:max_sents]


def local_grounding_score(answer: str, context_chunks: List[str], embedder) -> Optional[float]:
    """LLM 없이 로컬 임베딩으로 답변의 근거-기반성 근사(0.0~1.0). 불가 시 None.

    답변 문장별로 근거 청크와의 최대 코사인 유사도를 구해 평균한다. embedder는
    L2 정규화 벡터를 반환한다고 가정(FastEmbed/LSA 등) → 내적이 코사인과 일치.
    """
    if not answer or not context_chunks or embedder is None:
        return None
    if not getattr(embedder, "is_ready", False):
        # TF-IDF류는 fit 전 is_ready=False. 코퍼스 fit 시도(있으면).
        try:
            if hasattr(embedder, "fit_corpus"):
                embedder.fit_corpus(list(context_chunks))
        except Exception:
            pass
        if not getattr(embedder, "is_ready", False):
            return None
    sents = _split_sentences(answer)
    if not sents:
        return None
    try:
        import numpy as _np
        ctx_vecs = [v for v in embedder.embed_batch(list(context_chunks)) if v is not None]
        if not ctx_vecs:
            return None
        ctx_mat = _np.vstack([_np.asarray(v, dtype=_np.float32) for v in ctx_vecs])
        # 안전 정규화(제공자가 정규화 안 해도 코사인 성립)
        ctx_mat = ctx_mat / (_np.linalg.norm(ctx_mat, axis=1, keepdims=True) + 1e-10)
        sent_vecs = embedder.embed_batch(sents)
        scores = []
        for v in sent_vecs:
            if v is None:
                continue
            a = _np.asarray(v, dtype=_np.float32)
            a = a / (_np.linalg.norm(a) + 1e-10)
            sims = ctx_mat @ a
            scores.append(float(_np.max(sims)))
        if not scores:
            return None
        return max(0.0, min(1.0, sum(scores) / len(scores)))
    except Exception as e:
        print(f"[Verifier] local_grounding_score 실패(비차단): {e}")
        return None
