"""검색 근거 파이프라인 — query확장 → 하이브리드(BM25+neural) → RRF → rerank → context.

리뷰 지적(async rerank/expand vs sync build_context 충돌)을 억지 패치 대신 파이프라인을
명확히 분리해 해소한다. 핵심은 async `retrieve_evidence`이고, 서버 이벤트 루프 안에서
`asyncio.run()`을 호출하는 안티패턴을 피하도록 sync 어댑터는 별도 스레드+루프로 실행한다.

각 부가 단계(expand/rerank)는 플래그 게이트 + 비차단 폴백. 모든 산출물은 EvidenceTrace로
계측된다(계측 우선 원칙). LLM 호출은 게이트웨이 경유(gw)만 사용한다.

Requirements: 5.2, 6.2, 10.1  /  Property 5(폴백)
"""
import os
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ai_engine.rag.hybrid_search import rrf_fuse
from ai_engine.rag.reranker import rerank as _rerank, parse_rerank_order  # noqa: F401
from ai_engine.rag.query_expand import should_expand, expand_query
from ai_engine.rag.trace import EvidenceTrace, hits_from_results, Stopwatch


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class RetrievalConfig:
    top_k: int = 8
    candidate_k: int = 40           # rerank 전 후보 풀
    fusion: str = "weighted"        # "weighted" | "rrf"
    use_query_expand: bool = False
    use_rerank: bool = False
    rerank_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    expand_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    score_threshold: float = 0.0
    use_mmr: bool = False
    rerank_timeout: float = 8.0
    expand_timeout: float = 6.0
    # metadata 필터(파일 경로 → 포함 여부). rerank/융합 이전에 적용해 낭비·오염 방지.
    file_filter: Optional[Callable[[str], bool]] = None

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        c = cls()
        c.fusion = (env.get("AE_FUSION") or c.fusion)
        c.use_query_expand = _truthy(env.get("AE_QUERY_EXPAND"))
        c.use_rerank = _truthy(env.get("AE_RERANK"))
        if env.get("AE_RERANK_MODEL"):
            c.rerank_model = env["AE_RERANK_MODEL"]
        try:
            if env.get("AE_TOP_K"):
                c.top_k = int(env["AE_TOP_K"])
            if env.get("AE_CANDIDATE_K"):
                c.candidate_k = int(env["AE_CANDIDATE_K"])
        except (TypeError, ValueError):
            pass
        return c


@dataclass
class EvidenceBundle:
    chunks: list = field(default_factory=list)          # [(chunk, score), ...]
    context_ids: List[str] = field(default_factory=list)
    trace: Optional[EvidenceTrace] = None


def _cid(chunk) -> str:
    fp = getattr(chunk, "file_path", "") or ""
    s = getattr(chunk, "start_line", 0) or 0
    e = getattr(chunk, "end_line", 0) or 0
    return f"{fp}:{s}-{e}"


def _candidate_summary(chunk, limit: int = 200) -> str:
    head = (getattr(chunk, "content", "") or "")[:limit].replace("\n", " ")
    return f"{_cid(chunk)} {head}"


async def retrieve_evidence(query: str, searcher, *, gw=None,
                            config: Optional[RetrievalConfig] = None,
                            env=None) -> EvidenceBundle:
    """근거 검색 파이프라인(async). searcher는 HybridSearcher(색인/임베더 세팅 완료)."""
    cfg = config or RetrievalConfig.from_env(env)
    trace = EvidenceTrace(user_query=query, retriever_config={
        "fusion": cfg.fusion, "top_k": cfg.top_k, "candidate_k": cfg.candidate_k,
        "use_query_expand": cfg.use_query_expand, "use_rerank": cfg.use_rerank,
    })

    # 1) (opt) 쿼리 확장 — 원쿼리 + 확장쿼리 결과를 RRF로 융합
    queries = [query]
    if cfg.use_query_expand and gw is not None and should_expand(query):
        with Stopwatch(trace, "expand"):
            try:
                exp = await expand_query(gw, cfg.expand_model, query, timeout=cfg.expand_timeout)
                if exp:
                    queries = exp
                    trace.rewritten_query = " | ".join(exp[:3])
            except Exception:
                queries = [query]

    # 2) 하이브리드 검색 (후보 풀 확보)
    with Stopwatch(trace, "retrieve"):
        if len(queries) == 1:
            results = searcher.search(queries[0], top_k=cfg.candidate_k,
                                      use_mmr=cfg.use_mmr, score_threshold=cfg.score_threshold,
                                      file_filter=cfg.file_filter, fusion=cfg.fusion)
        else:
            # 다중 쿼리 결과를 인덱스 랭크 리스트로 만들어 RRF 융합
            id_to_item = {}
            rank_lists = []
            for q in queries:
                r = searcher.search(q, top_k=cfg.candidate_k, use_mmr=False,
                                    score_threshold=cfg.score_threshold,
                                    file_filter=cfg.file_filter, fusion=cfg.fusion)
                order = []
                for chunk, score in r:
                    cid = _cid(chunk)
                    id_to_item.setdefault(cid, (chunk, score))
                    order.append(cid)
                rank_lists.append(order)
            ids = list(id_to_item.keys())
            id_index = {cid: i for i, cid in enumerate(ids)}
            fused = rrf_fuse([[id_index[c] for c in ol] for ol in rank_lists])
            results = [id_to_item[ids[i]] for i, _s in fused][:cfg.candidate_k]
    trace.bm25 = hits_from_results(results, "hybrid")

    # 3) (opt) LLM 리랭커 — 후보를 관련성 순으로 재정렬 후 top_k
    if cfg.use_rerank and gw is not None and len(results) > 1:
        with Stopwatch(trace, "rerank"):
            try:
                cands = [_candidate_summary(c) for c, _ in results]
                order = await _rerank(gw, cfg.rerank_model, query, cands, timeout=cfg.rerank_timeout)
                results = [results[i] for i in order if 0 <= i < len(results)]
                trace.reranked = hits_from_results(results[:cfg.top_k], "rerank")
            except Exception:
                pass  # 폴백: 원 순위 유지

    final = results[:cfg.top_k]
    trace.final_context_ids = [_cid(c) for c, _ in final]
    return EvidenceBundle(chunks=final, context_ids=trace.final_context_ids, trace=trace)


def retrieve_evidence_sync(query: str, searcher, *, gw=None,
                           config: Optional[RetrievalConfig] = None,
                           env=None) -> EvidenceBundle:
    """sync 어댑터. 서버 이벤트 루프 안에서 asyncio.run() 금지 규칙 준수 —
    실행 중 루프가 있으면 별도 스레드에서 새 루프로 돌린다."""
    import asyncio

    async def _coro():
        return await retrieve_evidence(query, searcher, gw=gw, config=config, env=env)

    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False

    if not running:
        return asyncio.run(_coro())

    # 실행 중 루프 내부 → 별도 스레드 + 독립 루프
    import concurrent.futures

    def _runner():
        return asyncio.run(_coro())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_runner).result()
