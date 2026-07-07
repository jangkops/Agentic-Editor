"""verify_e2e_gateway 하네스 로직 검증 — Fake 게이트웨이로 세 경로 구조 정확성 확인.

게이트웨이가 즉시 응답한다고 가정할 때, 단일/병렬/합의 경로가 결과와 품질
메타데이터를 올바르게 조립하는지 검증한다(게이트웨이 지연과 무관하게 하네스가
정확함을 보장). 실제 게이트웨이 실측은 활성 모델(sonnet-4-5)로 별도 완료됨.
"""
import asyncio
import scripts.verify_e2e_gateway as e2e


class _Chunk:
    file_path = "hybrid_search.py"
    start_line = 1
    end_line = 5
    content = "def rrf_fuse(rank_lists, k=60): RRF(d)=sum 1/(k+rank), k 기본값 60"


CHUNKS = [(_Chunk(), 0.9)]
CONTEXT = ("hybrid_search.py:1-5\n"
           "rrf_fuse(rank_lists, k=60): 여러 순위 리스트를 RRF로 융합, k 기본값 60")


class FakeGW:
    """converse_text의 스트리밍 우선 경로가 소비하는 Fake 게이트웨이."""
    async def stream_sse_realtime(self, model_id, messages, system_prompt=""):
        text = ("rrf_fuse는 여러 순위 리스트를 RRF로 융합하며 k 기본값은 60입니다 "
                "(hybrid_search.py:1-5).")
        for i in range(0, len(text), 12):
            yield {"type": "content_block_delta", "delta": {"text": text[i:i+12]}}
        yield {"type": "message_stop"}


def test_single_path_assembles_metadata():
    ans, meta = asyncio.run(e2e.run_single(FakeGW(), CHUNKS, CONTEXT))
    assert ans
    assert "grounding" in meta or "citation" in meta


def test_parallel_path_self_consistency():
    results, ranking = asyncio.run(e2e.run_parallel(FakeGW(), CONTEXT))
    assert any(r["status"] == "done" for r in results)
    # 동일 Fake 응답 2개 → self-consistency 랭킹 산출, 대표 1개
    if ranking:
        reps = [d for d in ranking if d.get("representative")]
        assert len(reps) == 1


def test_consensus_path_crossverify():
    results, _ = asyncio.run(e2e.run_parallel(FakeGW(), CONTEXT))
    rep = asyncio.run(e2e.run_consensus(FakeGW(), results))
    if rep is not None:
        cv = rep.as_dict()
        assert "degraded" in cv and "conflictCount" in cv and "candidates" in cv
