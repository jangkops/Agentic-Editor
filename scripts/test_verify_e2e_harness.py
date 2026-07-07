"""verify_e2e_gateway 하네스 로직 검증 — Fake 게이트웨이로 세 경로 구조 정확성 확인.

게이트웨이가 즉시 응답한다고 가정할 때, 단일/병렬/합의 경로가 결과와 품질
메타데이터를 올바르게 조립하는지 검증한다(게이트웨이 지연과 무관하게 하네스가
정확함을 보장).
"""
import asyncio
import pytest

import scripts.verify_e2e_gateway as e2e


class FakeGW:
    """converse_text의 스트리밍 우선 경로가 소비하는 Fake 게이트웨이."""
    async def stream_sse_realtime(self, model_id, messages, system_prompt=""):
        # 근거에 부합하는 답변을 델타로 스트리밍
        text = "rrf_fuse는 여러 순위 리스트를 Reciprocal Rank Fusion으로 융합하며 k 기본값은 60입니다."
        for ch in [text[i:i+12] for i in range(0, len(text), 12)]:
            yield {"type": "content_block_delta", "delta": {"text": ch}}
        yield {"type": "message_stop"}


def test_single_path_assembles_metadata():
    r = asyncio.run(e2e.verify_single(FakeGW()))
    assert r["ok"] is True
    assert r["answer"]
    md = r["metadata"] or {}
    # 인용/grounding 메타가 조립되어야 함(게이트웨이 없이도 계산되는 부분)
    assert "grounding" in md or "citation" in md


def test_parallel_path_self_consistency():
    r = asyncio.run(e2e.verify_parallel(FakeGW()))
    assert r["ok"] is True
    assert isinstance(r["selfConsistency"], list) and len(r["selfConsistency"]) >= 1
    # 대표 후보가 정확히 1개 표시
    reps = [d for d in r["selfConsistency"] if d.get("representative")]
    assert len(reps) == 1


def test_consensus_path_crossverify():
    r = asyncio.run(e2e.verify_consensus(FakeGW()))
    cv = r["crossVerify"]
    assert "degraded" in cv and "conflictCount" in cv and "candidates" in cv
    # Fake는 응답을 주므로 degraded=False, 후보 수만큼 verdict
    assert cv["degraded"] is False
    assert len(cv["candidates"]) == len(e2e.PARALLEL_MODELS)
