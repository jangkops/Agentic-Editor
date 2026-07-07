"""병렬 후보 self-consistency 랭킹 단위/속성 테스트.

결정적 Mock 임베더로 순위 성질을 고정하고, 방어 케이스를 검증한다.
"""
import numpy as np
from ai_engine.rag.consensus_select import rank_by_self_consistency


class MockEmb:
    """토큰 집합을 2D 방향 벡터로 매핑하는 결정적 임베더.
    'yes' 계열은 [1,0] 근방, 'no' 계열은 [0,1] 근방."""
    is_ready = True

    def embed_batch(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            yes = tl.count("yes")
            no = tl.count("no")
            v = np.array([yes + 0.01, no + 0.01], dtype="float32")
            out.append(v)
        return out


def test_empty_returns_none():
    assert rank_by_self_consistency([], MockEmb()) is None
    assert rank_by_self_consistency(["", "  "], MockEmb()) is None


def test_single_is_representative():
    r = rank_by_self_consistency(["only answer"], MockEmb())
    assert r == [{"index": 0, "consistency": 1.0, "representative": True}]


def test_no_embedder_returns_none_for_multi():
    assert rank_by_self_consistency(["a", "b"], None) is None


def test_majority_gets_higher_consistency():
    # 3개 중 2개는 'yes'(합의), 1개는 'no'(이상치)
    answers = ["yes yes definitely", "yes yes agreed", "no no disagree"]
    r = rank_by_self_consistency(answers, MockEmb())
    assert r is not None and len(r) == 3
    # 결과는 consistency 내림차순
    scores = [d["consistency"] for d in r]
    assert scores == sorted(scores, reverse=True)
    # 대표(representative)는 최상위 1개
    reps = [d for d in r if d["representative"]]
    assert len(reps) == 1
    # 이상치(index 2, 'no')는 대표가 아니어야 함
    rep_idx = reps[0]["index"]
    assert rep_idx in (0, 1)
    # 이상치의 consistency가 합의 후보보다 낮아야 함
    by_idx = {d["index"]: d["consistency"] for d in r}
    assert by_idx[2] < by_idx[0]
    assert by_idx[2] < by_idx[1]


def test_scores_in_range():
    r = rank_by_self_consistency(["yes", "yes", "no"], MockEmb())
    for d in r:
        assert 0.0 <= d["consistency"] <= 1.0
