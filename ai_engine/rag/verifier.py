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
        return VerifyResult(score=None, degraded=True, feedback="empty answer/context")
    try:
        from ai_engine.rag.gw_text import converse_text
        messages = build_verify_prompt(answer, context)
        # 스트리밍 우선(저지연·취소가능), 실패 시 동기 converse 폴백.
        text = await converse_text(gw, model_id, messages, timeout=timeout)
        return VerifyResult(
            score=parse_faithfulness(text),
            degraded=False,
            feedback=parse_feedback(text),
        )
    except Exception as e:
        return VerifyResult(score=None, degraded=True, feedback=f"verify failed: {e}")


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
