"""Evidence 파이프라인 계측(trace) 계약 — 검색→근거→생성→검증 전 구간 기록.

"개선했다"를 증명하려면 질문·재작성·BM25/dense/RRF/rerank 결과·최종 context·
citation·verifier 결과·지연·비용을 모두 구조화해 남겨야 한다. 본 모듈은 그
계약(dataclass)과 직렬화를 제공한다(순수, 부작용 없음). 실제 기록 위치(파일/DB)는
호출부가 결정한다. 자격증명/토큰은 절대 담지 않는다.

우선순위 1단계(계측 고정)에 해당. RAGChecker류의 retrieval/generation 분리 진단과
동일 철학 — 단계별 산출물을 남겨 어느 구간에서 품질이 깨지는지 특정한다.
"""
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class RetrievalHit:
    """검색 단계 산출물 1건."""
    source_id: str          # file_path:start-end
    file_path: str
    start_line: int
    end_line: int
    stage: str              # "bm25" | "dense" | "rrf" | "rerank"
    score: float
    rank: int


@dataclass
class EvidenceTrace:
    """한 질의의 검색-근거-생성-검증 전 구간 trace."""
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    user_query: str = ""
    rewritten_query: str = ""
    retriever_config: dict = field(default_factory=dict)
    bm25: List[RetrievalHit] = field(default_factory=list)
    dense: List[RetrievalHit] = field(default_factory=list)
    fused: List[RetrievalHit] = field(default_factory=list)
    reranked: List[RetrievalHit] = field(default_factory=list)
    final_context_ids: List[str] = field(default_factory=list)
    answer: str = ""
    citations: List[str] = field(default_factory=list)
    unsupported_claims: List[str] = field(default_factory=list)
    verifier: dict = field(default_factory=dict)   # {score, degraded, action}
    latency_ms: dict = field(default_factory=dict)  # {retrieve, rerank, generate, verify, total}
    cost: dict = field(default_factory=dict)        # {input_tokens, output_tokens, krw}
    started_at: float = field(default_factory=time.time)

    def to_json(self) -> str:
        """민감정보 없이 JSON 직렬화(로그/파일 기록용)."""
        return json.dumps(asdict(self), ensure_ascii=False)

    def summary(self) -> dict:
        """핵심 관측치만 요약(대시보드/스모크용)."""
        return {
            "trace_id": self.trace_id,
            "query": self.user_query[:120],
            "n_bm25": len(self.bm25),
            "n_dense": len(self.dense),
            "n_fused": len(self.fused),
            "n_reranked": len(self.reranked),
            "n_context": len(self.final_context_ids),
            "n_citations": len(self.citations),
            "n_unsupported": len(self.unsupported_claims),
            "verifier_action": self.verifier.get("action", ""),
            "total_ms": self.latency_ms.get("total"),
        }


class Stopwatch:
    """단계별 지연 측정 헬퍼(ms). with 블록으로 사용."""
    def __init__(self, trace: EvidenceTrace, key: str):
        self._trace = trace
        self._key = key
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.time()
        return self

    def __exit__(self, *exc):
        self._trace.latency_ms[self._key] = round((time.time() - self._t0) * 1000, 1)
        return False


def hits_from_results(results, stage: str) -> List[RetrievalHit]:
    """[(chunk, score), ...] → List[RetrievalHit] (순수, 방어적)."""
    out: List[RetrievalHit] = []
    for rank, item in enumerate(results or []):
        chunk = item[0] if isinstance(item, (tuple, list)) and item else item
        score = float(item[1]) if isinstance(item, (tuple, list)) and len(item) > 1 else 0.0
        fp = getattr(chunk, "file_path", "") or ""
        s = int(getattr(chunk, "start_line", 0) or 0)
        e = int(getattr(chunk, "end_line", 0) or 0)
        out.append(RetrievalHit(
            source_id=f"{fp}:{s}-{e}", file_path=fp, start_line=s, end_line=e,
            stage=stage, score=score, rank=rank,
        ))
    return out
