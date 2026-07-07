"""컨텍스트 빌더 — 하이브리드 RAG (벡터 + BM25) 기반.

프로젝트 인덱싱 → 하이브리드 검색 → 시스템 프롬프트 조합.
Bedrock 임베딩 사용 가능 시 벡터 검색 활성화, 불가 시 BM25 폴백.
"""
import os
import time
from typing import Optional, Dict
from ai_engine.rag.indexer import ProjectIndexer
from ai_engine.rag.hybrid_search import HybridSearcher
from ai_engine.rag.embedder import BedrockEmbedder, VectorStore


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


# 전역 캐시
_indexer_cache: Dict[str, ProjectIndexer] = {}
_searcher_cache: Dict[str, HybridSearcher] = {}
_last_index_time: Dict[str, float] = {}
REINDEX_INTERVAL = 300  # 5분마다 재인덱싱 체크


def get_indexer(project_path: str) -> ProjectIndexer:
    """프로젝트 인덱서를 가져오거나 생성."""
    if project_path not in _indexer_cache:
        idx = ProjectIndexer()
        idx.index_project(project_path)
        _indexer_cache[project_path] = idx
        _last_index_time[project_path] = time.time()
    else:
        idx = _indexer_cache[project_path]
        if time.time() - _last_index_time.get(project_path, 0) > REINDEX_INTERVAL:
            if idx.needs_reindex(project_path):
                idx.index_project(project_path)
            _last_index_time[project_path] = time.time()
    return idx


def get_searcher(
    project_path: str,
    aws_profile: str = "",
    bedrock_user: str = "",
    gateway_client=None,
) -> HybridSearcher:
    """하이브리드 검색기를 가져오거나 생성."""
    idx = get_indexer(project_path)

    if project_path not in _searcher_cache:
        # alpha=0.5 — 30-query GOLDEN 벤치 실측 최적(recall 1.0 유지, mrr 0.872→0.919).
        # 벡터(neural)와 BM25(키워드)를 대등하게 융합: 함수명 등 정확 키워드 매칭 보존.
        searcher = HybridSearcher(alpha=0.5)
        searcher.index(idx.chunks)

        # 벡터 임베딩 시도 — neural(fastembed) 우선 자동 선택, 미가용 시 TF-IDF 폴백.
        try:
            from ai_engine.rag.embedder import get_embedding_provider
            # 공수 0: 기본 provider를 neural로. 게이트웨이 불필요(로컬 ONNX)라 gw 없어도
            # 활성. fastembed/모델 미가용 시 get_embedding_provider가 TF-IDF로 정직 폴백한다.
            # 끄려면 AE_EMBED_PROVIDER=tfidf(또는 다른 값) 명시.
            _emb_env = dict(os.environ)
            _emb_env.setdefault("AE_EMBED_PROVIDER", "fastembed")
            embedder = get_embedding_provider(_emb_env, gateway_client=gateway_client)
            # 캐시된 벡터 저장소 로드 시도
            # 우선순위:
            # 1) /fsx/home/<user>/.cache/ae_rag/<projhash> — FSx 사용자 홈 (원격 모드)
            # 2) ~/.cache/ae_rag/<projhash>              — 일반 사용자 홈
            # 3) /tmp/ae_rag/<projhash>                  — 최종 fallback
            import hashlib
            _proj_hash = hashlib.md5(project_path.encode()).hexdigest()[:12]

            def _try_dir(d):
                """디렉토리에 쓰기 가능한지 검증. 가능하면 경로 반환, 아니면 None."""
                try:
                    os.makedirs(d, exist_ok=True)
                    _t = os.path.join(d, ".write_test")
                    with open(_t, "w") as _tf:
                        _tf.write("ok")
                    os.remove(_t)
                    return d
                except (OSError, PermissionError):
                    return None

            cache_dir = None

            # 후보 1: FSx 사용자 홈 (project_path가 /fsx/home/<user>/... 형태일 때)
            _fsx_user_home = None
            if project_path.startswith("/fsx/home/"):
                _parts = project_path.split("/")
                if len(_parts) >= 4:  # ['', 'fsx', 'home', '<user>', ...]
                    _fsx_user_home = "/".join(_parts[:4])  # /fsx/home/<user>
            if _fsx_user_home:
                _candidate = os.path.join(_fsx_user_home, ".cache", "ae_rag", _proj_hash)
                cache_dir = _try_dir(_candidate)
                if cache_dir:
                    print(f"[RAG] FSx 사용자 홈 캐시 사용: {cache_dir}")

            # 후보 2: project_path 자체 (~/dev/myproj 같은 일반 로컬 경로)
            if cache_dir is None:
                _candidate = os.path.join(project_path, ".rag_cache")
                cache_dir = _try_dir(_candidate)

            # 후보 3: 사용자 홈 .cache
            if cache_dir is None:
                _candidate = os.path.join(os.path.expanduser("~"), ".cache", "ae_rag", _proj_hash)
                cache_dir = _try_dir(_candidate)
                if cache_dir:
                    print(f"[RAG] 사용자 홈 캐시 fallback: {cache_dir}")

            # 후보 4: /tmp (최종)
            if cache_dir is None:
                cache_dir = os.path.join("/tmp", "ae_rag", _proj_hash)
                os.makedirs(cache_dir, exist_ok=True)
                print(f"[RAG] /tmp fallback: {cache_dir}")

            store = VectorStore()
            cache_path = os.path.join(cache_dir, "vectors")

            # 코퍼스 텍스트 (fit + 재인덱싱 모두 사용)
            texts = [f"File: {c.file_path}\n{c.content}" for c in idx.chunks]

            # 캐시 로드 → 청크 수 일치 → 코퍼스로 fit → 차원 검증
            cache_valid = False
            if store.load(cache_path) and store.size == len(idx.chunks):
                if embedder.fit_corpus(texts):
                    # vocabulary 크기와 캐시된 벡터 차원이 일치해야 matmul 가능
                    cached_dim = store.vectors.shape[1] if store.vectors is not None else 0
                    vocab_dim = embedder.vocab_size
                    if cached_dim == vocab_dim and cached_dim > 0:
                        cache_valid = True
                        print(f"[RAG] 캐시된 벡터 로드: {store.size}개 (dim={cached_dim})")
                    else:
                        print(f"[RAG] 캐시 차원 불일치 — 재인덱싱 (cached={cached_dim}, vocab={vocab_dim})")

            if not cache_valid:
                # 캐시 폐기 후 재인덱싱
                try:
                    if os.path.exists(cache_path + ".npy"):
                        os.remove(cache_path + ".npy")
                    if os.path.exists(cache_path + ".meta.json"):
                        os.remove(cache_path + ".meta.json")
                except Exception as _e:
                    print(f"[RAG] 캐시 삭제 실패: {_e}")

                print(f"[RAG] {len(idx.chunks)}개 청크 임베딩 "
                      f"(provider={type(embedder).__name__}, model="
                      f"{getattr(embedder, 'model_name', 'tfidf')})...")
                store = VectorStore()
                vectors = embedder.embed_batch(texts)
                for i, vec in enumerate(vectors):
                    if vec is not None:
                        store.add(vec, {"chunk_idx": i, "file": idx.chunks[i].file_path})
                store.save(cache_path)
                print(f"[RAG] 벡터 저장 완료: {store.size}개 (dim={embedder.vocab_size})")
            searcher.set_embedder(embedder)
            searcher.set_vector_store(store)
        except Exception as e:
            print(f"[RAG] 벡터 임베딩 실패 (BM25 폴백): {e}")

        _searcher_cache[project_path] = searcher
    else:
        searcher = _searcher_cache[project_path]
        # 인덱스가 변경되었으면 검색기도 갱신
        if len(searcher.chunks) != len(idx.chunks):
            searcher.index(idx.chunks)

    return searcher


def build_context(
    project_path: str,
    query: str,
    open_file: Optional[str] = None,
    open_file_content: Optional[str] = None,
    aws_profile: str = "",
    bedrock_user: str = "",
    gateway_client=None,
    max_context_chars: int = 24000,
    return_chunks: bool = False,
):
    """하이브리드 RAG 기반 컨텍스트 생성.

    return_chunks=True면 (context_str, results) 튜플 반환 — results는 [(chunk, score), ...].
    기본(False)은 기존과 동일하게 context_str만 반환(무회귀).
    """
    if not project_path:
        return ("", []) if return_chunks else ""

    idx = get_indexer(project_path)
    searcher = get_searcher(project_path, aws_profile, bedrock_user, gateway_client)
    parts = []

    # 1. 프로젝트 개요
    parts.append(f"## 프로젝트: {project_path.split('/')[-1]}")
    parts.append(f"인덱싱: {len(idx.chunks)}개 청크")
    parts.append("")

    # 2. 파일 트리 (축약)
    tree = idx.get_file_tree()
    if tree:
        tree_lines = tree.split('\n')
        if len(tree_lines) > 40:
            tree = '\n'.join(tree_lines[:40]) + f'\n... ({len(tree_lines) - 40}줄 더)'
        parts.append("## 파일 구조")
        parts.append(f"```\n{tree}\n```\n")

    used_chars = sum(len(p) for p in parts)

    # 3. 현재 열린 파일 (우선 포함)
    if open_file and open_file_content:
        section = f"## 현재 열린 파일: {open_file}\n```\n"
        content = open_file_content[:6000] + ("\n... (truncated)" if len(open_file_content) > 6000 else "")
        section += content + "\n```\n"
        if used_chars + len(section) < max_context_chars:
            parts.append(section)
            used_chars += len(section)

    # 4. 하이브리드 검색 — 관련 코드 (MMR + score threshold + metadata 필터)
    # 쿼리 유형으로 검색 전략 자동 선택:
    # - "다양한 관점/예시/리서치" → MMR (다양성)
    # - "정확한 답변/특정 함수" → similarity (관련성 우선)
    is_research_like = any(kw in query.lower() for kw in (
        "다양한", "예시", "비교", "여러", "list", "examples",
        "compare", "find all", "show me", "explore",
    ))
    is_specific_lookup = any(kw in query.lower() for kw in (
        "function", "함수", "class", "클래스", "method", "메서드",
        "어디", "where", "정확히", "exact",
    ))

    # MMR은 리서치류일 때 활성, specific lookup이면 관련성만
    use_mmr = is_research_like and not is_specific_lookup
    mmr_lambda = 0.4 if is_research_like else 0.7  # 리서치는 다양성 더, 그 외는 관련성 더
    score_threshold = 0.1 if is_specific_lookup else 0.05  # 정확 lookup은 더 엄격

    # 빌드/캐시 폴더 사전 제외 (metadata 필터 — 관련 없는 문서 사전 제거)
    def _file_filter(file_path: str) -> bool:
        excluded_substrings = (
            "/node_modules/", "/.git/", "/__pycache__/", "/.venv/",
            "/dist/", "/build/", "/coverage/", "/.cache/",
            "/.generated/", "/.rag_cache/", "/.pytest_cache/",
        )
        return not any(ex in file_path for ex in excluded_substrings)

    # 근거 파이프라인 배선 — 플래그 게이트(AE_RETRIEVAL_PIPELINE), 기본 off=무회귀.
    # on + 게이트웨이 있을 때만 query확장→하이브리드→RRF→LLM리랭크 경로를 사용하고,
    # 실패 시 기존 searcher.search로 안전 폴백한다. file_filter는 rerank 이전 적용.
    results = None
    retrieval_mode = "MMR(다양성)" if use_mmr else "Similarity(관련성)"
    if _truthy(os.environ.get("AE_RETRIEVAL_PIPELINE")) and gateway_client is not None:
        try:
            from ai_engine.rag.retrieval_pipeline import (
                retrieve_evidence_sync, RetrievalConfig,
            )
            cfg = RetrievalConfig.from_env(os.environ)
            cfg.top_k = 8
            cfg.score_threshold = score_threshold
            cfg.use_mmr = False  # 파이프라인은 rerank로 정밀도 확보(MMR 대체)
            cfg.file_filter = _file_filter
            bundle = retrieve_evidence_sync(
                query, searcher, gw=gateway_client, config=cfg, env=os.environ,
            )
            results = bundle.chunks
            retrieval_mode = (
                f"Pipeline(fusion={cfg.fusion}"
                f"{',expand' if cfg.use_query_expand else ''}"
                f"{',rerank' if cfg.use_rerank else ''})"
            )
        except Exception as e:
            print(f"[RAG] 근거 파이프라인 실패 (기존 검색 폴백): {e}")
            results = None

    if results is None:
        results = searcher.search(
            query, top_k=8,
            score_threshold=score_threshold,
            use_mmr=use_mmr,
            mmr_lambda=mmr_lambda,
            file_filter=_file_filter,
        )
    if results:
        parts.append(f"## 관련 코드 ({retrieval_mode}, threshold={score_threshold})")
        for chunk, score in results:
            if used_chars > max_context_chars:
                break
            if open_file and chunk.file_path == open_file:
                continue
            section = f"### {chunk.file_path} (L{chunk.start_line}-{chunk.end_line}, score: {score:.2f})\n```{chunk.language}\n{chunk.content}\n```\n"
            if used_chars + len(section) < max_context_chars:
                parts.append(section)
                used_chars += len(section)

    context_str = '\n'.join(parts)
    if return_chunks:
        return context_str, (results or [])
    return context_str


def build_system_prompt(
    project_path: str,
    query: str,
    open_file: Optional[str] = None,
    open_file_content: Optional[str] = None,
    base_system_prompt: str = "",
    aws_profile: str = "",
    bedrock_user: str = "",
    gateway_client=None,
    return_evidence: bool = False,
):
    """최종 시스템 프롬프트 생성.

    return_evidence=True면 (prompt, evidence) 튜플 반환 —
    evidence = {"context": <RAG 컨텍스트 문자열>, "chunks": [(chunk, score), ...]}.
    스트리밍 경로가 answer_quality(인용/충실도) 검증에 재검색 없이 재사용하도록 한다.
    기본(False)은 기존과 동일하게 prompt 문자열만 반환(무회귀).
    """
    if return_evidence:
        context, _chunks = build_context(
            project_path, query, open_file, open_file_content,
            aws_profile, bedrock_user, gateway_client, return_chunks=True,
        )
    else:
        context = build_context(
            project_path, query, open_file, open_file_content,
            aws_profile, bedrock_user, gateway_client,
        )
        _chunks = []
    prompt_parts = []
    if base_system_prompt:
        prompt_parts.append(base_system_prompt)
    prompt_parts.append("""당신은 사용자의 프로젝트를 완전히 이해하고 적극적으로 도와주는 AI 코딩 어시스턴트입니다.

아래에 프로젝트의 파일 구조, 현재 열린 파일의 실제 내용, 관련 코드가 제공됩니다.

규칙:
- 파일 내용이 필요하면 반드시 read_file 도구를 사용하세요. 추측하지 마세요
- 파일을 수정해야 하면 반드시 write_file 도구를 사용하세요
- 명령 실행이 필요하면 반드시 run_command 도구를 사용하세요
- 이미지 생성 요청 시 반드시 generate_image 도구 사용 (PNG 자동 저장)
- PDF 문서 작성 요청 시 반드시 generate_pdf 도구 사용 (제목 + sections 배열)
- PPT/슬라이드 작성 요청 시 반드시 generate_pptx 도구 사용 (제목 + slides 배열)
- 이미지 편집(inpaint/outpaint) 요청 시 반드시 edit_image 도구 사용
- 도구로 가능한 작업(파일 읽기/쓰기, 명령 실행, 이미지/PDF/PPT 생성)은 "할 수 없다"고 거부하지 말고 반드시 해당 도구를 사용하세요
- 텍스트로 PDF/PPT 내용을 길게 나열하지 말고 도구로 실제 파일을 생성하세요
- 확인 질문 없이 바로 도구 사용
- 도구 실행 결과를 기반으로 정확한 답변 제공

근거 규칙 (매우 중요 — 할루시네이션 방지):
- 사실 관계(코드 동작, 함수/파일 존재, 값, 설정 등)는 아래 제공된 컨텍스트 또는 도구 실행 결과에 근거해서만 서술하세요. 근거 없이 추측하거나 지어내지 마세요
- 컨텍스트에 근거가 없으면 먼저 read_file/run_command 등 도구로 확인하세요
- 도구로도 확인이 불가능하면 "제공된 정보로는 확인할 수 없습니다"라고 명시하세요 — 지어내는 것보다 모른다고 답하는 것이 낫습니다
- 특정 파일/코드를 근거로 사실을 서술할 때는 가능하면 `파일경로:시작-끝라인` 형식으로 출처를 표기하세요

답변 스타일 (매우 중요):
- 중간 보고 금지. "확인하겠습니다", "읽어보겠습니다", "진행하겠습니다" 같은 예고 없이 바로 작업 수행
- 도구를 여러 번 사용해야 하면 한 번에 모두 호출하고, 최종 결과만 깔끔하게 정리
- 마크다운 헤딩(##), 리스트(·), 코드블록(```)을 활용해 구조적으로 답변
- 파일을 생성한 후에는 결과 JSON의 path 필드를 그대로 마크다운 링크 ![이름](.generated/...) 형태로 답변에 포함하세요
- 불필요한 인사말, 감탄사, 이모지 최소화
- 핵심만 간결하게""")
    if context:
        prompt_parts.append(f"\n---\n{context}")
    prompt = '\n\n'.join(prompt_parts)
    if return_evidence:
        return prompt, {"context": context, "chunks": _chunks}
    return prompt
