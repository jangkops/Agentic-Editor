"""합의 경로 교차 검증 — 병합 전 검증자 모델이 각 후보의 충실도를 채점·충돌 표기.

멀티에이전트 합의(병렬 후보 → merger 병합)에서, 병합 전에 검증자 모델이 각 후보 응답이
사용자 요청·근거에 충실한지 채점하고 후보 간 충돌을 표기한다. 결과는 additive 메타데이터로,
merger 병합을 차단하지 않는다(가용성 우선). 실패/타임아웃 시 degraded 폴백.

기존 verifier.py/reranker.py와 동일하게 순수 함수(프롬프트/파싱)와 async 호출부를 분리한다.
LLM 호출은 게이트웨이(gw.converse)만 사용한다. 플래그: AE_CONSENSUS_CROSSVERIFY.

Requirements: 9.1, 9.2, 9.3, 9.4  /  Property 5(폴백)
"""
import asyncio
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CandidateVerdict:
    index: int
    score: float                 # 0.0~1.0 충실도
    conflict: bool               # 다른 후보와 사실 충돌 여부
    note: str = ""


@dataclass
class CrossVerifyReport:
    verdicts: List[CandidateVerdict] = field(default_factory=list)
    degraded: bool = False       # 검증 불가(폴백) 시 True
    conflict_count: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "degraded": self.degraded,
            "conflictCount": self.conflict_count,
            "candidates": [
                {"index": v.index, "score": v.score,
                 "conflict": v.conflict, "note": v.note}
                for v in self.verdicts
            ],
            **({"error": self.error} if self.error else {}),
        }


def _candidate_text(agent: dict, limit: int = 1200) -> str:
    role = agent.get("role") or agent.get("taskId") or "?"
    title = agent.get("title") or ""
    summary = (agent.get("summary") or "")[:limit]
    return f"[{role}] {title}\n{summary}".strip()


def build_crossverify_prompt(user_prompt: str, agents: List[dict]) -> List[dict]:
    """각 후보의 충실도·충돌을 채점하도록 요청하는 messages(순수)."""
    blocks = []
    for i, a in enumerate(agents):
        blocks.append(f"### 후보 {i}\n{_candidate_text(a)}")
    joined = "\n\n".join(blocks)
    instruction = (
        "당신은 엄격한 교차 검증자입니다. 아래 여러 후보 답변이 [사용자 요청]에 대해 "
        "사실적으로 충실한지, 그리고 후보들 사이에 사실 충돌(같은 사실을 다르게 주장)이 "
        "있는지 평가하세요. 근거 없는 단정·과장은 감점입니다.\n\n"
        f"[사용자 요청]\n{user_prompt[:1500]}\n\n"
        f"[후보들]\n{joined}\n\n"
        "각 후보를 한 줄씩, 반드시 아래 형식으로만 출력하세요:\n"
        "AGENT <번호>: SCORE=<0.0~1.0> CONFLICT=<yes|no> NOTE=<간단한 근거 또는 OK>"
    )
    return [{"role": "user", "content": [{"text": instruction}]}]


def parse_crossverify(text: str, n: int, default: float = 0.5) -> List[CandidateVerdict]:
    """`AGENT i: SCORE=X CONFLICT=yes NOTE=...` 라인들을 파싱(순수, 방어적).

    - 범위 밖([0,n) 아님)·중복 인덱스는 무시
    - 누락된 인덱스는 default 점수/충돌 false로 채움
    - 점수는 0.0~1.0 클램프
    결과는 index 오름차순 길이 n의 리스트(결정론).
    """
    found = {}
    if text:
        pattern = re.compile(
            r'AGENT\s*(\d+)\s*:\s*SCORE\s*=\s*([0-1](?:\.\d+)?|\.\d+|\d+(?:\.\d+)?)'
            r'(?:\s+CONFLICT\s*=\s*(yes|no|true|false))?'
            r'(?:\s+NOTE\s*=\s*(.*))?',
            re.IGNORECASE,
        )
        for m in pattern.finditer(text):
            idx = int(m.group(1))
            if idx < 0 or idx >= n or idx in found:
                continue
            try:
                score = max(0.0, min(1.0, float(m.group(2))))
            except (TypeError, ValueError):
                score = default
            conflict = str(m.group(3) or "").lower() in ("yes", "true")
            note = (m.group(4) or "").strip()
            found[idx] = CandidateVerdict(index=idx, score=score, conflict=conflict, note=note)
    verdicts = []
    for i in range(n):
        verdicts.append(found.get(i, CandidateVerdict(index=i, score=default,
                                                       conflict=False, note="")))
    return verdicts


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


async def cross_verify_consensus(gw, model_id: str, user_prompt: str,
                                 agent_results: List[dict],
                                 timeout: float = 12.0) -> CrossVerifyReport:
    """합의 후보들을 검증자 모델로 교차 채점. 실패/타임아웃 시 degraded 폴백(비차단)."""
    agents = list(agent_results or [])
    n = len(agents)
    if n == 0 or gw is None:
        return CrossVerifyReport(degraded=True, error="no candidates or gateway")
    try:
        from ai_engine.rag.gw_text import converse_text
        messages = build_crossverify_prompt(user_prompt, agents)
        text = await converse_text(gw, model_id, messages, timeout=timeout)
        verdicts = parse_crossverify(text, n)
        conflicts = sum(1 for v in verdicts if v.conflict)
        return CrossVerifyReport(verdicts=verdicts, degraded=False, conflict_count=conflicts)
    except asyncio.TimeoutError:
        return CrossVerifyReport(degraded=True, error=f"cross-verify timeout after {timeout:.0f}s")
    except Exception as e:
        # 폴백: 검증 불가 → degraded, merger 병합은 그대로 진행(비차단)
        return CrossVerifyReport(degraded=True,
                                 error=f"cross-verify failed: {str(e) or type(e).__name__}")
