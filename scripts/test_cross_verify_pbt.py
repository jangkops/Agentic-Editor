"""합의 교차 검증 파싱·폴백 속성 테스트 (Req 9.1~9.4 / Property 5).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_cross_verify_pbt.py -p no:cacheprovider -q
"""
import asyncio
from hypothesis import given, strategies as st
from ai_engine.rag.cross_verify import (
    build_crossverify_prompt, parse_crossverify, cross_verify_consensus,
    CrossVerifyReport,
)

AGENTS = [
    {"taskId": "t1", "role": "planner", "title": "설계", "summary": "A안 제시"},
    {"taskId": "t2", "role": "coder", "title": "구현", "summary": "B안 구현"},
    {"taskId": "t3", "role": "reviewer", "title": "검토", "summary": "충돌 지적"},
]


def test_parse_basic():
    text = (
        "AGENT 0: SCORE=0.9 CONFLICT=no NOTE=OK\n"
        "AGENT 1: SCORE=0.4 CONFLICT=yes NOTE=근거부족\n"
        "AGENT 2: SCORE=0.7 CONFLICT=no NOTE=OK"
    )
    v = parse_crossverify(text, 3)
    assert len(v) == 3
    assert v[0].score == 0.9 and not v[0].conflict
    assert v[1].conflict and v[1].note == "근거부족"


def test_parse_out_of_range_and_dup_ignored():
    text = "AGENT 5: SCORE=0.9\nAGENT 0: SCORE=0.8\nAGENT 0: SCORE=0.1"
    v = parse_crossverify(text, 2)
    assert len(v) == 2
    assert v[0].score == 0.8   # 첫 등장만
    assert v[1].score == 0.5   # 누락 → default


def test_parse_missing_filled_with_default():
    v = parse_crossverify("", 3)
    assert len(v) == 3
    assert all(x.score == 0.5 and not x.conflict for x in v)


@given(st.integers(min_value=0, max_value=6))
def test_parse_always_len_n_and_clamped(n):
    text = "\n".join(f"AGENT {i}: SCORE=9.9 CONFLICT=yes" for i in range(n))
    v = parse_crossverify(text, n)
    assert len(v) == n
    assert all(0.0 <= x.score <= 1.0 for x in v)
    assert [x.index for x in v] == list(range(n))


def test_prompt_includes_all_candidates():
    msgs = build_crossverify_prompt("요청", AGENTS)
    txt = msgs[0]["content"][0]["text"]
    assert "후보 0" in txt and "후보 1" in txt and "후보 2" in txt
    assert "planner" in txt


def test_async_success():
    class _GW:
        async def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text":
                "AGENT 0: SCORE=0.9 CONFLICT=no\n"
                "AGENT 1: SCORE=0.3 CONFLICT=yes NOTE=모순\n"
                "AGENT 2: SCORE=0.8 CONFLICT=no"}]}}}
    rep = asyncio.run(cross_verify_consensus(_GW(), "m", "요청", AGENTS))
    assert not rep.degraded
    assert rep.conflict_count == 1
    assert rep.as_dict()["candidates"][1]["conflict"] is True


def test_async_fallback_on_error():
    class _Boom:
        async def converse(self, **kwargs):
            raise RuntimeError("gateway down")
    rep = asyncio.run(cross_verify_consensus(_Boom(), "m", "요청", AGENTS))
    assert rep.degraded and rep.error
    assert rep.as_dict()["degraded"] is True


def test_async_no_gateway_or_empty():
    assert asyncio.run(cross_verify_consensus(None, "m", "q", AGENTS)).degraded
    class _GW:
        async def converse(self, **kwargs):
            return {}
    assert asyncio.run(cross_verify_consensus(_GW(), "m", "q", [])).degraded
