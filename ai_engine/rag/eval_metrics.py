"""RAG 품질 평가 지표 — 순수 함수 (LLM 호출 없음, 결정론적).

검색 품질(recall@k, MRR)과 응답 충실도(인용 커버리지/미검증 비율)를 계산한다.
평가 하네스(scripts/eval_rag_quality.py)와 속성 테스트에서 사용한다.

Requirements: 8.2, 8.3  /  Property 7 (recall@k 단조성)
"""
from typing import Iterable, Sequence


def recall_at_k(relevant: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    """recall@k = (상위 k개 검색결과 중 relevant에 속한 고유 항목 수) / (relevant 총 수).

    - relevant/retrieved 항목은 비교 가능한 식별자(예: "path:start-end" 또는 파일경로).
    - k<=0 이면 0.0. relevant가 비어있으면 1.0(관련 항목이 없으면 완전 재현으로 간주).
    - retrieved 중복은 집합으로 정규화되어 중복 카운트되지 않는다.
    """
    rel = set(relevant)
    if not rel:
        return 1.0
    if k <= 0:
        return 0.0
    topk = set(retrieved[:k])
    hit = len(rel & topk)
    return hit / len(rel)


def mrr(relevant: Iterable[str], retrieved: Sequence[str]) -> float:
    """Mean Reciprocal Rank(단일 쿼리) = 1/(첫 relevant 항목의 1-based 순위).

    상위에 relevant가 하나도 없으면 0.0.
    """
    rel = set(relevant)
    if not rel:
        return 0.0
    for i, item in enumerate(retrieved):
        if item in rel:
            return 1.0 / (i + 1)
    return 0.0


def citation_coverage(n_verified: int, n_total_claims: int) -> float:
    """검증된 인용 수 / 사실 주장(근사) 수. 분모 0이면 1.0(주장 없음)."""
    if n_total_claims <= 0:
        return 1.0
    return max(0.0, min(1.0, n_verified / n_total_claims))


def unverified_ratio(n_unverified: int, n_citations: int) -> float:
    """미검증 인용 / 전체 인용. 인용이 없으면 0.0."""
    if n_citations <= 0:
        return 0.0
    return max(0.0, min(1.0, n_unverified / n_citations))


# ─────────────────────────────────────────────────────────────────────────
# 확장 지표 — 폐루프 평가(계측)용 (검색-근거-생성-검증 분리 진단)
# RAGChecker류 방향: retrieval과 generation을 분리해 세부 지표로 진단.
# ─────────────────────────────────────────────────────────────────────────
def context_precision(relevant: Iterable[str], retrieved: Sequence[str], k: int) -> float:
    """context precision@k = 상위 k개 중 relevant 비율 (근거의 순도).

    recall이 "정답을 얼마나 담았나"라면 precision은 "가져온 게 얼마나 관련있나".
    k<=0 또는 상위 k가 비면 0.0.
    """
    if k <= 0:
        return 0.0
    topk = list(retrieved[:k])
    if not topk:
        return 0.0
    rel = set(relevant)
    hit = sum(1 for x in topk if x in rel)
    return hit / len(topk)


def unsupported_claim_rate(n_unsupported: int, n_claims: int) -> float:
    """근거 없는 주장 비율 = 미지원 주장 / 전체 주장. 주장 0이면 0.0."""
    if n_claims <= 0:
        return 0.0
    return max(0.0, min(1.0, n_unsupported / n_claims))


def abstention_accuracy(decisions: Sequence[bool], should_abstain: Sequence[bool]) -> float:
    """거절 정확도 = (근거 부족 시 거절 + 근거 충분 시 답변)이 맞은 비율.

    decisions[i]=True면 '거절함', should_abstain[i]=True면 '거절해야 함'.
    두 리스트 길이가 다르면 짧은 쪽 기준. 항목 없으면 1.0.
    """
    n = min(len(decisions), len(should_abstain))
    if n == 0:
        return 1.0
    correct = sum(1 for i in range(n) if bool(decisions[i]) == bool(should_abstain[i]))
    return correct / n


def groundedness(n_supported: int, n_claims: int) -> float:
    """groundedness = 근거 지원 주장 / 전체 주장 (충실도 근사). 주장 0이면 1.0."""
    if n_claims <= 0:
        return 1.0
    return max(0.0, min(1.0, n_supported / n_claims))
