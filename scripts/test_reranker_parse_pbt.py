"""리랭크 파싱 속성 테스트 — Property 4 (항상 [0,n) 유효 인덱스 순열).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_reranker_parse_pbt.py -p no:cacheprovider -q
"""
from hypothesis import given, strategies as st
from ai_engine.rag.reranker import parse_rerank_order, build_rerank_prompt


@given(st.integers(min_value=0, max_value=30), st.text(max_size=300))
def test_always_valid_permutation(n, text):
    """임의 텍스트 입력에도 출력은 [0,n) 유효 인덱스의 완전 순열."""
    out = parse_rerank_order(text, n)
    assert sorted(out) == list(range(n))  # 완전 순열
    assert len(out) == len(set(out))       # 중복 없음


@given(st.integers(min_value=1, max_value=20))
def test_respects_given_order(n):
    """유효 순서를 주면 그 순서가 앞쪽에 보존된다."""
    order = list(range(n))[::-1]
    text = str(order)
    out = parse_rerank_order(text, n)
    assert out == order


def test_out_of_range_ignored_and_backfilled():
    out = parse_rerank_order("[5, 1, 999, -3, 0]", 3)
    # 유효: 1, 0 (순서 유지) → 누락 2는 원순서로 뒤에
    assert out == [1, 0, 2]


def test_duplicates_ignored():
    out = parse_rerank_order("[2, 2, 2, 0]", 3)
    assert out == [2, 0, 1]


def test_empty_text_backfills_identity():
    assert parse_rerank_order("", 4) == [0, 1, 2, 3]


def test_zero_candidates():
    assert parse_rerank_order("[0,1]", 0) == []


def test_build_prompt_shape():
    msgs = build_rerank_prompt("q", ["a", "b"])
    assert isinstance(msgs, list) and msgs[0]["role"] == "user"
    txt = msgs[0]["content"][0]["text"]
    assert "[0]" in txt and "[1]" in txt and "q" in txt
