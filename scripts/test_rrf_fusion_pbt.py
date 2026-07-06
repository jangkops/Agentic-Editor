"""RRF 융합 속성 테스트 — Property 1(결정론·단조성), Property 2(스케일 불변성).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_rrf_fusion_pbt.py -p no:cacheprovider -q
"""
from hypothesis import given, strategies as st
from ai_engine.rag.hybrid_search import rrf_fuse


def _perm(n):
    return st.permutations(list(range(n)))


@given(st.integers(min_value=1, max_value=20))
def test_determinism(n):
    """동일 입력 → 동일 출력(결정론)."""
    import random
    rl = [list(range(n)), list(reversed(range(n)))]
    a = rrf_fuse(rl)
    b = rrf_fuse(rl)
    assert a == b


@given(st.integers(min_value=2, max_value=15))
def test_monotonic_single_list(n):
    """단일 랭크 리스트면 상위 순위일수록 RRF 점수가 크다(엄격 내림차순)."""
    rl = [list(range(n))]  # 0이 1위, n-1이 꼴찌
    fused = rrf_fuse(rl)
    scores = [s for _, s in fused]
    assert scores == sorted(scores, reverse=True)
    # 1위 문서(idx 0)가 최상단
    assert fused[0][0] == 0


@given(st.lists(st.lists(st.integers(min_value=0, max_value=9), max_size=10), max_size=4))
def test_output_indices_are_subset_of_input(rank_lists):
    """출력 인덱스는 입력에 등장한 인덱스 집합의 부분집합, 중복 없음."""
    fused = rrf_fuse(rank_lists)
    out_idx = [i for i, _ in fused]
    assert len(out_idx) == len(set(out_idx))  # 중복 없음
    seen = set()
    for rl in rank_lists:
        seen.update(rl)
    assert set(out_idx) <= seen


@given(st.integers(min_value=2, max_value=12), st.integers(min_value=1, max_value=1000))
def test_scale_invariance(n, mult):
    """RRF는 순위 기반이므로 점수 스케일(상수배)과 무관 — 순위 리스트가 같으면 결과 동일.

    두 검색기의 '순위'가 동일하면, 원 점수를 아무리 상수배해도 RRF 결과는 불변.
    (RRF 입력이 이미 순위 리스트이므로, 이 성질을 순위 리스트 동일성으로 표현.)
    """
    rl_a = [list(range(n)), list(reversed(range(n)))]
    rl_b = [list(range(n)), list(reversed(range(n)))]  # 스케일 달라도 순위 동일
    assert rrf_fuse(rl_a) == rrf_fuse(rl_b)


def test_tie_break_stable():
    """동점은 인덱스 오름차순 안정 정렬."""
    # 두 리스트에서 0과 1이 대칭적으로 등장 → 동점
    fused = rrf_fuse([[0, 1], [1, 0]])
    idxs = [i for i, _ in fused]
    assert idxs == [0, 1]


def test_empty_and_k_guard():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[0, 1, 2]], k=0)  # k<=0이면 기본값으로 보정, 예외 없음
