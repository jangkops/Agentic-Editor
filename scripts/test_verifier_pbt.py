"""충실도 파싱 속성 테스트 (Req 3.5, 3.6) + rerank/verify 폴백(Property 5).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_verifier_pbt.py -p no:cacheprovider -q
"""
import asyncio
from hypothesis import given, strategies as st
from ai_engine.rag.verifier import (
    parse_faithfulness, parse_feedback, build_verify_prompt, verify_faithfulness,
)
from ai_engine.rag.reranker import rerank


def test_parse_score_basic():
    assert parse_faithfulness("SCORE: 0.8\nFEEDBACK: OK") == 0.8


def test_parse_score_missing_default():
    assert parse_faithfulness("no score here") == 0.5
    assert parse_faithfulness("", default=0.3) == 0.3


@given(st.floats(min_value=-5, max_value=5))
def test_score_clamped_0_1(v):
    txt = f"SCORE: {v}"
    out = parse_faithfulness(txt)
    assert 0.0 <= out <= 1.0


@given(st.text(max_size=200))
def test_parse_never_crashes(txt):
    assert 0.0 <= parse_faithfulness(txt) <= 1.0
    assert isinstance(parse_feedback(txt), str)


def test_feedback_extract():
    assert parse_feedback("SCORE: 0.2\nFEEDBACK: 근거 없음") == "근거 없음"


def test_build_verify_prompt_shape():
    msgs = build_verify_prompt("ans", "ctx")
    assert msgs[0]["role"] == "user"
    assert "SCORE:" in msgs[0]["content"][0]["text"]


# ── Property 5: 폴백 불변식 ──
class _BoomGateway:
    async def converse(self, **kwargs):
        raise RuntimeError("gateway down")


def test_verify_fallback_on_error():
    gw = _BoomGateway()
    res = asyncio.run(verify_faithfulness(gw, "m", "answer", "context", timeout=1.0))
    assert res.degraded is True and res.score is None


def test_rerank_fallback_on_error():
    gw = _BoomGateway()
    cands = ["a", "b", "c"]
    out = asyncio.run(rerank(gw, "m", "q", cands, timeout=1.0))
    assert out == [0, 1, 2]  # 원순서 폴백


def test_rerank_success_parses_order():
    class _GW:
        async def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "[2,0,1]"}]}}}
    out = asyncio.run(rerank(_GW(), "m", "q", ["a", "b", "c"], timeout=2.0))
    assert out == [2, 0, 1]
