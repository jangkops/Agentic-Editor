"""쿼리 확장 판단·파싱·폴백 테스트 (Req 6.1, 6.3, 6.4 / Property 5).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_query_expand_pbt.py -p no:cacheprovider -q
"""
import asyncio
from hypothesis import given, strategies as st
from ai_engine.rag.query_expand import (
    should_expand, parse_expansions, build_expand_prompt, expand_query,
)


def test_short_query_expands():
    assert should_expand("cache") is True
    assert should_expand("이거 왜") is True


def test_long_specific_query_no_expand():
    assert should_expand(
        "how does the hybrid search fuse bm25 and vector scores in context builder"
    ) is False


def test_empty_no_expand():
    assert should_expand("") is False
    assert should_expand("   ") is False


@given(st.text(max_size=200))
def test_should_expand_never_crashes(q):
    assert isinstance(should_expand(q), bool)


def test_parse_includes_original_first():
    out = parse_expansions("가상 답변 문장입니다.\n- keyword1\n- keyword2", "원쿼리")
    assert out[0] == "원쿼리"
    assert len(out) <= 5


def test_parse_dedup_and_filter_short():
    out = parse_expansions("ab\n원쿼리\n의미있는 확장 문장", "원쿼리")
    # "ab"(너무 짧음) 제외, 중복 "원쿼리" 하나만
    assert out.count("원쿼리") == 1
    assert "ab" not in out


@given(st.text(max_size=300), st.text(min_size=1, max_size=50))
def test_parse_always_list_with_original(text, original):
    out = parse_expansions(text, original)
    assert isinstance(out, list)
    assert out[0] == original
    assert len(out) <= 5


def test_expand_prompt_shape():
    msgs = build_expand_prompt("q")
    assert msgs[0]["role"] == "user" and "q" in msgs[0]["content"][0]["text"]


def test_expand_query_fallback_on_error():
    class _Boom:
        async def converse(self, **kwargs):
            raise RuntimeError("down")
    out = asyncio.run(expand_query(_Boom(), "m", "short q", timeout=1.0))
    assert out == ["short q"]  # 폴백: 원쿼리만


def test_expand_query_success():
    class _GW:
        async def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "가상 답변 문장.\nkeyword_a"}]}}}
    out = asyncio.run(expand_query(_GW(), "m", "원쿼리", timeout=2.0))
    assert out[0] == "원쿼리" and len(out) >= 2
