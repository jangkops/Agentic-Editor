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
        searcher = HybridSearcher(alpha=0.6)
        searcher.index(idx.chunks)

        # 벡터 임베딩 시도
        try:
            if gateway_client:
                embedder = BedrockEmbedder(gateway_client=gateway_client)
            else:
                # GatewayClient가 없으면 BM25만 사용
                raise RuntimeError("GatewayClient 필요")
            # 캐시된 벡터 저장소 로드 시도
            # project_path가 read-only 파일시스템(예: /fsx)이면 ~/.cache로 fallback
            _primary_cache = os.path.join(project_path, ".rag_cache")
            cache_dir = _primary_cache
            try:
                os.makedirs(cache_dir, exist_ok=True)
                # 쓰기 가능 여부 테스트
                _test_path = os.path.join(cache_dir, ".write_test")
                with open(_test_path, "w") as _tf:
                    _tf.write("ok")
                os.remove(_test_path)
            except (OSError, PermissionError) as _e:
                # Read-only 파일시스템 → 사용자 홈 디렉토리 캐시로 fallback
                import hashlib
                _proj_hash = hashlib.md5(project_path.encode()).hexdigest()[:12]
                cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "ae_rag", _proj_hash)
                try:
                    os.makedirs(cache_dir, exist_ok=True)
                    print(f"[RAG] 프로젝트 경로 쓰기 불가 ({_e}) → fallback: {cache_dir}")
                except Exception as _e2:
                    # 그것도 실패 → /tmp
                    cache_dir = os.path.join("/tmp", "ae_rag", _proj_hash)
                    os.makedirs(cache_dir, exist_ok=True)
                    print(f"[RAG] 홈 캐시도 실패 → /tmp fallback: {cache_dir}")
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

                print(f"[RAG] {len(idx.chunks)}개 청크 TF-IDF 임베딩...")
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
) -> str:
    """하이브리드 RAG 기반 컨텍스트 생성."""
    if not project_path:
        return ""

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

    # 4. 하이브리드 검색 — 관련 코드
    results = searcher.search(query, top_k=8)
    if results:
        parts.append("## 관련 코드 (유사도 순)")
        for chunk, score in results:
            if used_chars > max_context_chars:
                break
            if open_file and chunk.file_path == open_file:
                continue
            section = f"### {chunk.file_path} (L{chunk.start_line}-{chunk.end_line}, score: {score:.2f})\n```{chunk.language}\n{chunk.content}\n```\n"
            if used_chars + len(section) < max_context_chars:
                parts.append(section)
                used_chars += len(section)

    return '\n'.join(parts)


def build_system_prompt(
    project_path: str,
    query: str,
    open_file: Optional[str] = None,
    open_file_content: Optional[str] = None,
    base_system_prompt: str = "",
    aws_profile: str = "",
    bedrock_user: str = "",
    gateway_client=None,
) -> str:
    """최종 시스템 프롬프트 생성."""
    context = build_context(
        project_path, query, open_file, open_file_content,
        aws_profile, bedrock_user, gateway_client,
    )
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
- "할 수 없다", "권한이 없다", "직접 만들 수 없다" 같은 거부 표현 절대 금지
- 텍스트로 PDF/PPT 내용을 길게 나열하지 말고 도구로 실제 파일을 생성하세요
- 확인 질문 없이 바로 도구 사용
- 도구 실행 결과를 기반으로 정확한 답변 제공

답변 스타일 (매우 중요):
- 중간 보고 금지. "확인하겠습니다", "읽어보겠습니다", "진행하겠습니다" 같은 예고 없이 바로 작업 수행
- 도구를 여러 번 사용해야 하면 한 번에 모두 호출하고, 최종 결과만 깔끔하게 정리
- 마크다운 헤딩(##), 리스트(·), 코드블록(```)을 활용해 구조적으로 답변
- 파일을 생성한 후에는 결과 JSON의 path 필드를 그대로 마크다운 링크 ![이름](.generated/...) 형태로 답변에 포함하세요
- 불필요한 인사말, 감탄사, 이모지 최소화
- 핵심만 간결하게""")
    if context:
        prompt_parts.append(f"\n---\n{context}")
    return '\n\n'.join(prompt_parts)
