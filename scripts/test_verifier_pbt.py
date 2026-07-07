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


# ── degraded 사유·latency 계측 (라이브 진단 개선) ──────────────────
def test_verify_timeout_has_explicit_reason():
    """게이트웨이 지연 → 빈 메시지가 아니라 'timeout' 사유·명시 피드백."""
    import asyncio

    class _HangGW:
        async def converse(self, model_id, messages, system_prompt="", tool_config=None):
            await asyncio.sleep(10)
            return {"decision": "ALLOW", "output": {"message": {"content": [{"text": "x"}]}}}

    res = asyncio.run(verify_faithfulness(_HangGW(), "m", "답변", "근거", timeout=0.2))
    assert res.degraded and res.score is None
    assert res.reason == "timeout"
    assert "timeout" in res.feedback.lower() and res.feedback.strip() != ""
    assert res.latency_ms is not None and res.latency_ms >= 0


def test_verify_success_records_latency_and_empty_reason():
    import asyncio

    class _OkGW:
        async def converse(self, model_id, messages, system_prompt="", tool_config=None):
            return {"decision": "ALLOW",
                    "output": {"message": {"content": [{"text": "SCORE: 0.9\nFEEDBACK: OK"}]}}}

    res = asyncio.run(verify_faithfulness(_OkGW(), "m", "답변", "근거", timeout=5))
    assert not res.degraded and res.score == 0.9
    assert res.reason == "" and res.latency_ms is not None


def test_verify_error_reason_never_empty_feedback():
    """예외 str이 비어도 feedback은 타입명으로 채워져 진단 가능."""
    import asyncio

    class _BoomGW:
        async def converse(self, model_id, messages, system_prompt="", tool_config=None):
            raise RuntimeError("")  # 빈 메시지 예외

    res = asyncio.run(verify_faithfulness(_BoomGW(), "m", "답변", "근거", timeout=5))
    assert res.degraded and res.reason == "error"
    assert res.feedback.strip() != "" and "RuntimeError" in res.feedback
