"""확장 지표 + evidence trace 계약 테스트 (계측 우선).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_trace_and_metrics_pbt.py -p no:cacheprovider -q
"""
import json
from dataclasses import dataclass
from hypothesis import given, strategies as st
from ai_engine.rag.eval_metrics import (
    context_precision, unsupported_claim_rate, abstention_accuracy, groundedness,
)
from ai_engine.rag.trace import EvidenceTrace, RetrievalHit, Stopwatch, hits_from_results


# ── 확장 지표 ──
def test_context_precision_basic():
    assert context_precision({"a"}, ["a", "b", "c"], 3) == 1 / 3
    assert context_precision({"a", "b"}, ["a", "b", "c"], 3) == 2 / 3
    assert context_precision({"a"}, [], 3) == 0.0
    assert context_precision({"a"}, ["a"], 0) == 0.0


@given(st.lists(st.integers(0, 9).map(str), max_size=20),
       st.lists(st.integers(0, 9).map(str), max_size=20),
       st.integers(1, 20))
def test_context_precision_bounds(rel, ret, k):
    assert 0.0 <= context_precision(rel, ret, k) <= 1.0


def test_unsupported_claim_rate():
    assert unsupported_claim_rate(1, 4) == 0.25
    assert unsupported_claim_rate(0, 0) == 0.0
    assert unsupported_claim_rate(5, 2) == 1.0  # 클램프


def test_abstention_accuracy():
    assert abstention_accuracy([True, False], [True, False]) == 1.0
    assert abstention_accuracy([True, True], [True, False]) == 0.5
    assert abstention_accuracy([], []) == 1.0


def test_groundedness():
    assert groundedness(3, 4) == 0.75
    assert groundedness(0, 0) == 1.0


# ── evidence trace ──
@dataclass
class _Chunk:
    file_path: str
    start_line: int
    end_line: int


def test_trace_serializes_without_error():
    t = EvidenceTrace(user_query="q", rewritten_query="q2")
    t.bm25 = hits_from_results([(_Chunk("a.py", 1, 10), 0.9)], "bm25")
    t.final_context_ids = ["a.py:1-10"]
    t.citations = ["a.py:1-10"]
    t.verifier = {"score": 0.9, "action": "ok"}
    s = t.to_json()
    parsed = json.loads(s)
    assert parsed["user_query"] == "q"
    assert parsed["bm25"][0]["source_id"] == "a.py:1-10"
    summ = t.summary()
    assert summ["n_bm25"] == 1 and summ["n_citations"] == 1


def test_hits_from_results_defensive():
    hits = hits_from_results([(_Chunk("x.py", 5, 8), 0.5)], "dense")
    assert hits[0].stage == "dense" and hits[0].source_id == "x.py:5-8"
    # 빈 입력·비정상 입력 안전
    assert hits_from_results([], "bm25") == []
    assert hits_from_results(None, "rrf") == []


def test_stopwatch_records_latency():
    t = EvidenceTrace()
    with Stopwatch(t, "retrieve"):
        _ = sum(range(1000))
    assert "retrieve" in t.latency_ms and t.latency_ms["retrieve"] >= 0
