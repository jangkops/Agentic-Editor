"""LLM 리랭커 — 별도 cross-encoder 모델 없이 경량 LLM으로 후보 재정렬.

순수 함수(프롬프트 구성/응답 파싱)와 async 호출부(폴백 포함)를 분리한다.
파싱은 리랭커가 범위 밖/중복/누락 인덱스를 반환해도 [0, n) 유효 인덱스의
순열을 보장한다(누락은 원순서로 뒤에 append).

Requirements: 5.1, 5.2, 5.3, 5.5  /  Property 4
"""
import re
from typing import List, Sequence


def build_rerank_prompt(query: str, candidates: Sequence[str]) -> List[dict]:
    """후보 목록을 관련성 순으로 재정렬하도록 요청하는 messages(순수).

    candidates[i]는 사람이 읽을 수 있는 요약(예: "파일:라인 + 첫 몇 줄").
    응답은 관련성 높은 순의 0-based 인덱스 목록(예: "[3, 0, 1, 2]")을 기대한다.
    """
    lines = [f"[{i}] {c}" for i, c in enumerate(candidates)]
    body = "\n".join(lines)
    instruction = (
        "다음 후보들을 질의와의 관련성이 높은 순서로 재정렬하세요.\n"
        f"질의: {query}\n\n"
        f"후보:\n{body}\n\n"
        "가장 관련 있는 것부터 순서대로 0-based 인덱스만 JSON 배열로 출력하세요. "
        "예: [2, 0, 1]. 다른 설명은 쓰지 마세요."
    )
    return [{"role": "user", "content": [{"text": instruction}]}]


def parse_rerank_order(text: str, n_candidates: int) -> List[int]:
    """리랭커 응답에서 인덱스 순서를 파싱. 항상 [0, n) 유효 인덱스의 순열 반환.

    - 범위 밖/비정수/중복 인덱스는 무시.
    - 응답에 누락된 유효 인덱스는 원래 순서(0..n-1)대로 뒤에 append.
    """
    if n_candidates <= 0:
        return []
    order: List[int] = []
    seen = set()
    for tok in re.findall(r'-?\d+', text or ""):
        try:
            idx = int(tok)
        except ValueError:
            continue
        if 0 <= idx < n_candidates and idx not in seen:
            order.append(idx)
            seen.add(idx)
    # 누락 인덱스를 원순서로 보강 → 항상 완전한 순열
    for i in range(n_candidates):
        if i not in seen:
            order.append(i)
    return order


async def rerank(gw, model_id: str, query: str, candidates: Sequence[str],
                 timeout: float = 8.0) -> List[int]:
    """경량 LLM으로 후보 재정렬. 실패/타임아웃 시 원순서(0..n-1) 반환(비차단).

    gw: GatewayClient 유사 객체 (converse(model_id, messages, ...) 제공 가정).
    """
    n = len(candidates)
    if n <= 1:
        return list(range(n))
    identity = list(range(n))
    try:
        import asyncio
        messages = build_rerank_prompt(query, candidates)
        coro = gw.converse(model_id=model_id, messages=messages)
        resp = await asyncio.wait_for(coro, timeout=timeout)
        if isinstance(resp, dict) and resp.get("decision") not in (None, "ALLOW"):
            return identity  # 에러/거부 → 원 순위 유지(비차단)
        text = _extract_text(resp)
        return parse_rerank_order(text, n)
    except Exception:
        return identity


def _extract_text(resp) -> str:
    """Converse 응답에서 텍스트 추출(방어적)."""
    try:
        if isinstance(resp, str):
            return resp
        if isinstance(resp, dict):
            # {"output":{"message":{"content":[{"text":...}]}}} 형태 대응
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
