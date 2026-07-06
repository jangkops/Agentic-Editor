"""하이브리드 검색 — 벡터 유사도 + BM25 키워드 점수 결합.

점수 = alpha * vector_score + (1 - alpha) * bm25_score
alpha = 0.6 (의미 검색 60%, 키워드 40%)
"""
import math
import re
from typing import List, Tuple, Dict, Callable, Optional
from ai_engine.rag.indexer import Chunk


class BM25:
    """BM25 키워드 검색."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs: Dict[str, int] = {}
        self.doc_lens: List[int] = []
        self.avg_dl: float = 0
        self.n_docs: int = 0
        self.doc_tokens: List[List[str]] = []

    def index(self, chunks: List[Chunk]):
        """청크 목록으로 BM25 인덱스 구축."""
        self.n_docs = len(chunks)
        self.doc_tokens = [c.tokens for c in chunks]
        self.doc_lens = [len(t) for t in self.doc_tokens]
        self.avg_dl = sum(self.doc_lens) / max(self.n_docs, 1)
        self.doc_freqs = {}
        for tokens in self.doc_tokens:
            seen = set(tokens)
            for t in seen:
                self.doc_freqs[t] = self.doc_freqs.get(t, 0) + 1

    def score(self, query_tokens: List[str], doc_idx: int) -> float:
        """단일 문서의 BM25 점수."""
        doc_tokens = self.doc_tokens[doc_idx]
        dl = self.doc_lens[doc_idx]
        score = 0.0
        tf_map = {}
        for t in doc_tokens:
            tf_map[t] = tf_map.get(t, 0) + 1
        for qt in query_tokens:
            if qt not in tf_map:
                continue
            tf = tf_map[qt]
            df = self.doc_freqs.get(qt, 0)
            idf = math.log((self.n_docs - df + 0.5) / (df + 0.5) + 1)
            tf_norm = (tf * (self.k1 + 1)) / (tf + self.k1 * (1 - self.b + self.b * dl / self.avg_dl))
            score += idf * tf_norm
        return score

    def search(self, query_tokens: List[str], top_k: int = 10) -> List[Tuple[int, float]]:
        """상위 K개 문서 인덱스 + 점수."""
        scores = [(i, self.score(query_tokens, i)) for i in range(self.n_docs)]
        scores.sort(key=lambda x: -x[1])
        return [(i, s) for i, s in scores[:top_k] if s > 0]


def tokenize(text: str) -> List[str]:
    """텍스트를 토큰으로 분할."""
    return re.findall(r'[a-z_][a-z0-9_]*|[가-힣]+', text.lower())


class HybridSearcher:
    """벡터 + BM25 하이브리드 검색."""

    def __init__(self, alpha: float = 0.6):
        """alpha: 벡터 점수 가중치 (0~1). 1-alpha가 BM25 가중치."""
        self.alpha = alpha
        self.bm25 = BM25()
        self.chunks: List[Chunk] = []
        self.vector_store = None  # VectorStore 인스턴스
        self._embedder = None

    def set_embedder(self, embedder):
        self._embedder = embedder

    def set_vector_store(self, store):
        self.vector_store = store

    def index(self, chunks: List[Chunk]):
        """청크 목록으로 BM25 인덱스 구축."""
        self.chunks = chunks
        self.bm25.index(chunks)

    def search(self, query: str, top_k: int = 8,
               score_threshold: float = 0.05,
               use_mmr: bool = True,
               mmr_lambda: float = 0.5,
               file_filter: Optional[Callable[[str], bool]] = None,
               fusion: str = "weighted") -> List[Tuple[Chunk, float]]:
        """하이브리드 검색 + MMR + score threshold + metadata 필터.

        Args:
            query: 검색어
            top_k: 최종 반환 개수
            score_threshold: 이 점수 미만은 제외 (관련성 낮은 결과 제거)
            use_mmr: True면 Maximal Marginal Relevance로 다양성 확보
            mmr_lambda: MMR balance — 1.0=정확도만, 0.0=다양성만, 0.5=균형
            file_filter: chunk.file_path를 받아서 True 반환하면 포함 (metadata 필터)
            fusion: "weighted"(기본, 기존 동작) | "rrf"(순위 기반 융합, opt-in).
                RRF는 시맨틱 벡터 랭커가 있을 때 스케일 차이에 견고하다. 기본값을
                rrf로 전환하는 것은 평가 하네스(semantic embedding)로 정밀도 우위를
                실측한 뒤 수행한다(무회귀 우선).
        """
        if not self.chunks:
            return []

        query_tokens = tokenize(query)
        weighted_scores: Dict[int, float] = {}
        bm25_order: List[int] = []
        vec_order: List[int] = []

        # candidate pool — top_k * 4로 늘려 MMR이 다양성을 확보할 여지 제공
        pool_k = top_k * 4

        # 1. BM25 검색
        bm25_results = self.bm25.search(query_tokens, top_k=pool_k)
        if bm25_results:
            max_bm25 = max(s for _, s in bm25_results) or 1
            for idx, s in bm25_results:
                weighted_scores[idx] = weighted_scores.get(idx, 0) + (1 - self.alpha) * (s / max_bm25)
            bm25_order = [idx for idx, _ in bm25_results]

        # 2. 벡터 검색 (임베더가 있을 때만)
        query_vec = None
        if self.vector_store and self._embedder and self.vector_store.size > 0:
            query_vec = self._embedder.embed(query)
            if query_vec is not None:
                # 런타임 차원 가드 — 차원 불일치 시 벡터 검색 비활성화
                cached_dim = self.vector_store.vectors.shape[1] if self.vector_store.vectors is not None else 0
                if query_vec.shape[0] != cached_dim:
                    print(f"[Hybrid] 차원 불일치 감지 (query={query_vec.shape[0]}, cached={cached_dim}) — 벡터 검색 스킵")
                    query_vec = None
                else:
                    vec_results = self.vector_store.search(query_vec, top_k=pool_k)
                    for meta, s in vec_results:
                        idx = meta.get("chunk_idx", -1)
                        if 0 <= idx < len(self.chunks):
                            weighted_scores[idx] = weighted_scores.get(idx, 0) + self.alpha * s
                            vec_order.append(idx)

        # 3. 융합 방식 선택 — 기본 weighted(기존 동작 보존), opt-in rrf.
        if str(fusion).lower() == "rrf":
            rank_lists = [o for o in (bm25_order, vec_order) if o]
            fused = rrf_fuse(rank_lists)
            max_rrf = (max((s for _, s in fused), default=0.0)) or 1.0
            scores: Dict[int, float] = {idx: s / max_rrf for idx, s in fused}  # [0,1] 정규화
        else:
            # weighted 경로 — 기존 로직 정확히 보존.
            scores = dict(weighted_scores)
            if not self.vector_store and bm25_results:
                max_bm25 = max(s2 for _, s2 in bm25_results) or 1
                scores = {idx: s / max_bm25 for idx, s in bm25_results}

        # 3b. metadata filter (예: 특정 파일 경로/확장자 제외)
        if file_filter is not None:
            scores = {idx: s for idx, s in scores.items()
                      if file_filter(self.chunks[idx].file_path)}

        # 4. score threshold — 관련성 낮은 결과 제거
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        ranked = [(idx, s) for idx, s in ranked if s >= score_threshold]

        if not ranked:
            return []

        # 5. MMR로 다양성 확보 (벡터가 있을 때만 작동)
        if use_mmr and query_vec is not None and self.vector_store and self.vector_store.vectors is not None:
            selected = self._mmr_select(ranked[:pool_k], query_vec, top_k, mmr_lambda)
            return [(self.chunks[idx], score) for idx, score in selected]

        return [(self.chunks[idx], score) for idx, score in ranked[:top_k]]

    def _mmr_select(self, ranked: List[Tuple[int, float]], query_vec,
                    top_k: int, mmr_lambda: float) -> List[Tuple[int, float]]:
        """Maximal Marginal Relevance — 정확도와 다양성의 균형.

        sim(query, doc_i) - λ * max_j sim(doc_i, doc_j_selected)
        """
        import numpy as _np
        if not ranked:
            return []

        # candidate vectors
        cand_indices = [idx for idx, _ in ranked]
        # chunk_idx → vector store row 매핑이 1:1이 아닐 수 있어
        # vector_store.metadata에서 chunk_idx로 검색
        idx_to_vec = {}
        if self.vector_store and self.vector_store.metadata:
            for i, m in enumerate(self.vector_store.metadata):
                cidx = m.get("chunk_idx", -1)
                if cidx in cand_indices and i < len(self.vector_store.vectors):
                    idx_to_vec[cidx] = self.vector_store.vectors[i]

        if not idx_to_vec:
            # MMR 적용 불가 — 정렬 결과 그대로 반환
            return ranked[:top_k]

        # 정규화
        def _norm(v):
            n = _np.linalg.norm(v) + 1e-10
            return v / n

        q_n = _norm(query_vec)
        # 후보별 query 유사도
        cand_query_sim = {}
        for cidx, v in idx_to_vec.items():
            cand_query_sim[cidx] = float(_norm(v) @ q_n)

        # MMR 알고리즘
        selected: List[Tuple[int, float]] = []
        remaining = list(idx_to_vec.keys())
        rank_dict = dict(ranked)

        while remaining and len(selected) < top_k:
            best_idx = None
            best_mmr = -1e9
            for cidx in remaining:
                relevance = cand_query_sim.get(cidx, 0.0)
                if not selected:
                    diversity = 0.0
                else:
                    sims = []
                    cand_vec = _norm(idx_to_vec[cidx])
                    for sidx, _ in selected:
                        sel_vec = _norm(idx_to_vec[sidx])
                        sims.append(float(cand_vec @ sel_vec))
                    diversity = max(sims) if sims else 0.0
                mmr_score = mmr_lambda * relevance - (1 - mmr_lambda) * diversity
                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_idx = cidx
            if best_idx is None:
                break
            selected.append((best_idx, rank_dict.get(best_idx, 0.0)))
            remaining.remove(best_idx)

        # 만약 idx_to_vec에 없던 것들이 있으면 끝에 추가 (정렬 순서 유지)
        for idx, score in ranked:
            if idx not in idx_to_vec and len(selected) < top_k:
                selected.append((idx, score))

        return selected



# ─────────────────────────────────────────────────────────────────────────
# Reciprocal Rank Fusion (RRF) — 순수 함수 (Requirements 4.1, 4.2 / Property 1,2)
#
# 점수 스케일이 다른 여러 검색기(BM25/벡터)의 결과를 "순위" 기반으로 융합한다.
# 가중합(alpha)과 달리 점수 절대값에 무관하므로 스케일 차이에 견고하다.
#   RRF(d) = Σ_r 1 / (k + rank_r(d))
# rank는 1-based. k는 상위권 지배를 완화하는 상수(기본 60, TREC 관례).
#
# search() 흐름 통합은 평가 하네스(task 9.2)로 무회귀를 확인한 뒤 단계적으로
# 반영한다. 본 함수는 그 전에도 단독으로 테스트·사용 가능하다.
# ─────────────────────────────────────────────────────────────────────────
def rrf_fuse(rank_lists, k: int = 60):
    """여러 랭크 리스트(각 리스트는 관련성 높은 순의 문서 인덱스)를 RRF로 융합.

    Args:
        rank_lists: List[List[int]] — 각 검색기의 순위 리스트(중복 인덱스는 리스트
            내 첫 등장 순위만 사용).
        k: RRF 상수(기본 60). k>0.

    Returns:
        List[Tuple[int, float]] — (문서 인덱스, RRF 점수) 내림차순.
        동점은 인덱스 오름차순으로 안정 정렬(결정론적).
    """
    if k <= 0:
        k = 60
    scores = {}
    for rl in rank_lists or []:
        seen = set()
        for rank, idx in enumerate(rl or []):
            if idx in seen:
                continue
            seen.add(idx)
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)  # rank는 0-based → +1
    # 점수 내림차순, 동점은 인덱스 오름차순(결정론)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))
