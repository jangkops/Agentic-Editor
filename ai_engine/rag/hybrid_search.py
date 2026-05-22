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
               file_filter: Optional[Callable[[str], bool]] = None) -> List[Tuple[Chunk, float]]:
        """하이브리드 검색 + MMR + score threshold + metadata 필터.

        Args:
            query: 검색어
            top_k: 최종 반환 개수
            score_threshold: 이 점수 미만은 제외 (관련성 낮은 결과 제거)
            use_mmr: True면 Maximal Marginal Relevance로 다양성 확보
            mmr_lambda: MMR balance — 1.0=정확도만, 0.0=다양성만, 0.5=균형
            file_filter: chunk.file_path를 받아서 True 반환하면 포함 (metadata 필터)
        """
        if not self.chunks:
            return []

        query_tokens = tokenize(query)
        scores: Dict[int, float] = {}

        # candidate pool — top_k * 4로 늘려 MMR이 다양성을 확보할 여지 제공
        pool_k = top_k * 4

        # 1. BM25 검색
        bm25_results = self.bm25.search(query_tokens, top_k=pool_k)
        if bm25_results:
            max_bm25 = max(s for _, s in bm25_results) or 1
            for idx, s in bm25_results:
                scores[idx] = scores.get(idx, 0) + (1 - self.alpha) * (s / max_bm25)

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
                            scores[idx] = scores.get(idx, 0) + self.alpha * s
        elif not self.vector_store:
            scores = {}
            for idx, s in bm25_results:
                max_bm25 = max(s2 for _, s2 in bm25_results) or 1
                scores[idx] = s / max_bm25

        # 3. metadata filter (예: 특정 파일 경로/확장자 제외)
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

