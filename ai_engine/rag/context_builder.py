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
) -> HybridSearcher:
    """하이브리드 검색기를 가져오거나 생성."""
    idx = get_indexer(project_path)

    if project_path not in _searcher_cache:
        searcher = HybridSearcher(alpha=0.6)
        searcher.index(idx.chunks)

        # 벡터 임베딩 시도
        try:
            embedder = BedrockEmbedder(
                aws_profile=aws_profile or "default",
                region="us-west-2",
                bedrock_user=bedrock_user,
            )
            # 캐시된 벡터 저장소 로드 시도
            cache_dir = os.path.join(project_path, ".rag_cache")
            store = VectorStore()
            cache_path = os.path.join(cache_dir, "vectors")
            if store.load(cache_path) and store.size == len(idx.chunks):
                print(f"[RAG] 캐시된 벡터 로드: {store.size}개")
            else:
                # 새로 임베딩
                print(f"[RAG] {len(idx.chunks)}개 청크 임베딩 시작...")
                store = VectorStore()
                for i, chunk in enumerate(idx.chunks):
                    # 파일 경로 + 내용을 합쳐서 임베딩
                    text = f"File: {chunk.file_path}\n{chunk.content}"
                    vec = embedder.embed(text)
                    if vec is not None:
                        store.add(vec, {"chunk_idx": i, "file": chunk.file_path})
                    if (i + 1) % 20 == 0:
                        print(f"[RAG] 임베딩 진행: {i+1}/{len(idx.chunks)}")
                # 캐시 저장
                store.save(cache_path)
                print(f"[RAG] 벡터 저장 완료: {store.size}개")
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
    max_context_chars: int = 12000,
) -> str:
    """하이브리드 RAG 기반 컨텍스트 생성."""
    if not project_path:
        return ""

    idx = get_indexer(project_path)
    searcher = get_searcher(project_path, aws_profile, bedrock_user)
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
        content = open_file_content[:3000] + ("\n... (truncated)" if len(open_file_content) > 3000 else "")
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
) -> str:
    """최종 시스템 프롬프트 생성."""
    context = build_context(
        project_path, query, open_file, open_file_content,
        aws_profile, bedrock_user,
    )
    prompt_parts = []
    if base_system_prompt:
        prompt_parts.append(base_system_prompt)
    prompt_parts.append("""당신은 사용자의 프로젝트를 이해하고 도와주는 AI 코딩 어시스턴트입니다.
아래에 프로젝트의 파일 구조와 관련 코드가 제공됩니다.
이 컨텍스트를 활용하여 정확하고 구체적인 답변을 제공하세요.
코드를 수정하거나 생성할 때는 프로젝트의 기존 스타일과 패턴을 따르세요.""")
    if context:
        prompt_parts.append(f"\n---\n{context}")
    return '\n\n'.join(prompt_parts)
