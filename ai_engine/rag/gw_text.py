"""게이트웨이 텍스트 헬퍼 — 인라인 LLM 호출(검증자/리랭커/확장/교차검증)용.

라이브 실측으로 확인: 동기 `/converse`는 이 게이트웨이에서 비동기(S3 job 폴링) 경로로
라우팅돼 수십~수백 초 걸리고, blocking urllib이 executor 스레드를 물고 있어 asyncio
타임아웃으로도 깔끔히 취소되지 않는다. 반면 스트리밍(Lambda Function URL, httpx 비동기)은
빠르고 `asyncio.wait_for`로 취소 가능하다.

따라서 인라인 저지연 LLM 호출은 스트리밍으로 텍스트를 수집하고, 스트리밍이 없거나 실패하면
동기 `/converse`로 폴백한다. 반환은 순수 텍스트(문자열).

Requirements: 3.x/5.x/6.x/9.x 라이브 경로 저지연화 (비차단 폴백 유지)
"""
import asyncio


async def _collect_stream(gw, model_id, messages, system_prompt) -> str:
    """stream_sse_realtime 이벤트에서 텍스트 델타만 모아 반환."""
    parts = []
    async for evt in gw.stream_sse_realtime(model_id=model_id, messages=messages,
                                            system_prompt=system_prompt):
        ty = evt.get("type", "") if isinstance(evt, dict) else ""
        if ty == "content_block_delta":
            d = evt.get("delta", {})
            if isinstance(d, dict) and "text" in d:
                parts.append(d["text"])
        elif ty == "error":
            raise RuntimeError(str(evt.get("message", "stream error"))[:200])
        elif ty in ("message_stop",):
            break
    return "".join(parts)


def _extract_text(resp) -> str:
    """동기 converse 응답에서 텍스트 추출(방어적)."""
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


async def converse_text(gw, model_id, messages, system_prompt="", timeout: float = 12.0,
                        prefer_stream: bool = True) -> str:
    """저지연 텍스트 응답. 스트리밍 우선(빠름·취소가능), 실패 시 동기 converse 폴백.

    예외/타임아웃은 상위(각 모듈)의 비차단 폴백에서 처리하도록 전파한다.
    반환 텍스트가 비어도 상위 파서가 default로 처리한다.
    """
    # 1) 스트리밍 우선 (가능하고 옵트인일 때)
    if prefer_stream and hasattr(gw, "stream_sse_realtime"):
        try:
            return await asyncio.wait_for(
                _collect_stream(gw, model_id, messages, system_prompt), timeout=timeout)
        except asyncio.TimeoutError:
            raise
        except Exception:
            pass  # 스트리밍 불가/에러 → 동기 폴백 시도

    # 2) 동기 converse 폴백
    resp = await asyncio.wait_for(
        gw.converse(model_id=model_id, messages=messages, system_prompt=system_prompt),
        timeout=timeout)
    if isinstance(resp, dict) and resp.get("decision") not in (None, "ALLOW"):
        raise RuntimeError(f"gateway {resp.get('decision')}: "
                           f"{str(resp.get('error') or resp.get('denial_reason') or '')[:200]}")
    return _extract_text(resp)
