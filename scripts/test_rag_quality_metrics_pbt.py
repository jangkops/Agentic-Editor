"""평가 지표 속성 테스트 — Property 7 (recall@k 단조성) + 경계.

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_rag_quality_metrics_pbt.py -p no:cacheprovider -q
"""
from hypothesis import given, strategies as st
from ai_engine.rag.eval_metrics import (
    recall_at_k, mrr, citation_coverage, unverified_ratio,
)

_ids = st.lists(st.integers(min_value=0, max_value=30).map(str), max_size=30, unique=True)


@given(_ids, _ids, st.integers(min_value=0, max_value=40))
def test_recall_bounds(rel, ret, k):
    r = recall_at_k(rel, ret, k)
    assert 0.0 <= r <= 1.0


@given(_ids, _ids, st.integers(min_value=1, max_value=20))
def test_recall_monotonic_in_k(rel, ret, k):
    """k가 커지면 recall@k는 비감소 (Property 7)."""
    r_k = recall_at_k(rel, ret, k)
    r_k1 = recall_at_k(rel, ret, k + 1)
    assert r_k1 >= r_k - 1e-9


def test_recall_empty_relevant_is_one():
    assert recall_at_k([], ["a", "b"], 5) == 1.0


def test_recall_k_zero():
    assert recall_at_k(["a"], ["a", "b"], 0) == 0.0


def test_mrr_basic():
    assert mrr(["x"], ["a", "b", "x", "c"]) == 1.0 / 3
    assert mrr(["x"], ["x"]) == 1.0
    assert mrr(["x"], ["a", "b"]) == 0.0


@given(_ids, _ids)
def test_mrr_bounds(rel, ret):
    assert 0.0 <= mrr(rel, ret) <= 1.0


def test_citation_coverage():
    assert citation_coverage(3, 4) == 0.75
    assert citation_coverage(0, 0) == 1.0  # 주장 없음
    assert citation_coverage(10, 2) == 1.0  # 클램프


def test_unverified_ratio():
    assert unverified_ratio(1, 4) == 0.25
    assert unverified_ratio(0, 0) == 0.0
