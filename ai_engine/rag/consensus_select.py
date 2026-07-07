"""병렬 후보 self-consistency 랭킹 — 게이트웨이 없이 로컬 임베딩으로 최선 답 선별.

병렬 모델 호출은 여러 모델의 답변을 나열만 했다. 품질을 높이려면 "어느 답이 다수
의견(합의)에 가까운가"를 알려줘야 한다. self-consistency는 검증된 기법으로, 후보
답변들을 임베딩해 centroid(평균)에 가장 가까운 답을 대표로 고른다. 이상치(혼자 다른
주장을 하는 답)는 낮은 점수를 받는다.

LLM/게이트웨이 불필요(로컬 임베딩) → 느린 게이트웨이 환경에서도 즉시 동작하고 실측
가능하다. 실패 시 None/빈 결과로 비차단 폴백.

정직한 한계: 이는 '의미적 다수 근접도'이지 정답 보장이 아니다. 모든 후보가 함께
틀리면 대표도 틀릴 수 있다. 최종 판단 보조 신호로 사용한다.
"""
from typing import List, Optional


def rank_by_self_consistency(answers: List[str], embedder) -> Optional[List[dict]]:
    """후보 답변들을 self-consistency로 랭킹. 불가 시 None(비차단).

    반환: [{"index": i, "consistency": 0~1, "representative": bool}] — consistency 내림차순.
    consistency는 후보 임베딩 centroid와의 코사인 유사도(다수의견 근접도).
    """
    texts = [(a or "").strip() for a in (answers or [])]
    valid_idx = [i for i, t in enumerate(texts) if t]
    if not valid_idx:
        return None
    if len(valid_idx) == 1:
        return [{"index": valid_idx[0], "consistency": 1.0, "representative": True}]
    if embedder is None or not getattr(embedder, "is_ready", False):
        return None
    try:
        import numpy as np
        vecs_raw = embedder.embed_batch([texts[i] for i in valid_idx])
        pairs = [(valid_idx[k], v) for k, v in enumerate(vecs_raw) if v is not None]
        if len(pairs) < 2:
            return None
        idxs = [p[0] for p in pairs]
        mat = np.vstack([np.asarray(p[1], dtype=np.float32) for p in pairs])
        # L2 정규화 → 코사인 = 내적
        mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-10)
        centroid = mat.mean(axis=0)
        centroid = centroid / (np.linalg.norm(centroid) + 1e-10)
        sims = mat @ centroid  # 각 후보의 centroid 근접도
        ranked = sorted(
            [{"index": int(idxs[k]), "consistency": round(float(max(0.0, min(1.0, sims[k]))), 4)}
             for k in range(len(idxs))],
            key=lambda d: (-d["consistency"], d["index"]),
        )
        for j, d in enumerate(ranked):
            d["representative"] = (j == 0)
        return ranked
    except Exception as e:
        print(f"[Consensus] self-consistency 실패(비차단): {e}")
        return None
