"""쿼리 확장 — 짧거나 모호한 질의의 검색 recall을 높인다 (opt-in, 기본 off).

순수 판단 함수(should_expand)와 async 확장 생성(expand_query, 폴백 포함)을 분리한다.
확장 쿼리 결과와 원 쿼리 결과는 RRF로 융합해 사용한다(context_builder에서).

Requirements: 6.1, 6.2, 6.3, 6.4  /  Property 5 (폴백)
"""
import re
from typing import List

_TOKEN_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*|[가-힣]+')

# 모호성 신호 — 지시대명사/광범위 요청 등 (짧은 질의 외 추가 트리거)
_VAGUE_HINTS = ("이거", "그거", "저거", "어떻게", "왜", "뭐", "설명", "알려",
                "how", "why", "what", "explain", "tell me")


def should_expand(query: str, min_tokens: int = 4) -> bool:
    """질의가 짧거나(토큰 수 < min_tokens) 모호하면 True."""
    q = (query or "").strip()
    if not q:
        return False
    tokens = _TOKEN_RE.findall(q.lower())
    if len(tokens) < min_tokens:
        return True
    low = q.lower()
    return any(h in low for h in _VAGUE_HINTS) and len(tokens) < min_tokens * 3


def build_expand_prompt(query: str) -> List[dict]:
    """HyDE + 동의 키워드 확장을 요청하는 messages(순수)."""
    instruction = (
        "다음 질의에 대해 코드베이스 검색 recall을 높이기 위한 확장을 생성하세요.\n"
        f"질의: {query}\n\n"
        "1) 이 질의에 대한 가상의 이상적 답변을 1~2문장으로.\n"
        "2) 관련 기술 키워드/동의어 5개 이내.\n"
        "간결하게, 위 두 가지만 출력하세요."
    )
    return [{"role": "user", "content": [{"text": instruction}]}]


def parse_expansions(text: str, original: str) -> List[str]:
    """확장 응답을 검색용 쿼리 문자열 리스트로 변환(순수).

    원 쿼리 + 확장 텍스트(가상 답변/키워드)를 개별 검색 쿼리로 반환.
    """
    out = [original]
    if text:
        # 줄 단위로 의미 있는 조각만 추가(너무 짧은 것 제외)
        for line in text.splitlines():
            s = line.strip(" \t-*0123456789.)")
            if len(s) >= 4:
                out.append(s)
    # 중복 제거(순서 보존)
    seen = set()
    uniq = []
    for q in out:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq[:5]  # 과도한 확장 방지


async def expand_query(gw, model_id: str, query: str, timeout: float = 6.0) -> List[str]:
    """경량 LLM으로 확장 쿼리 생성. 실패/타임아웃 시 [원쿼리]만 반환(비차단)."""
    if not query:
        return []
    try:
        import asyncio
        messages = build_expand_prompt(query)
        coro = gw.converse(model_id=model_id, messages=messages,
                           inference_config={"maxTokens": 200})
        resp = await asyncio.wait_for(coro, timeout=timeout)
        text = _extract_text(resp)
        return parse_expansions(text, query)
    except Exception:
        return [query]


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
