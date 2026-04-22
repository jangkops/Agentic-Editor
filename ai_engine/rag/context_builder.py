"""컨텍스트 빌더 — RAG 검색 결과를 시스템 프롬프트로 조합.

프론트엔드에서 전달받는 정보:
- project_path: 프로젝트 절대 경로
- open_file: 현재 열린 파일 경로 + 내용
- query: 사용자 질문

출력: 시스템 프롬프트 문자열
"""
from typing import Optional, List, Dict
from ai_engine.rag.indexer import ProjectIndexer


# 전역 인덱서 캐시 (프로젝트별)
_indexer_cache: Dict[str, ProjectIndexer] = {}


def get_indexer(project_path: str) -> ProjectIndexer:
    """프로젝트 인덱서를 가져오거나 생성."""
    if project_path not in _indexer_cache:
        idx = ProjectIndexer()
        idx.index_project(project_path)
        _indexer_cache[project_path] = idx
    else:
        idx = _indexer_cache[project_path]
        # 변경 감지 — 5분마다 재인덱싱
        if idx.needs_reindex(project_path):
            idx.index_project(project_path)
    return idx


def build_context(
    project_path: str,
    query: str,
    open_file: Optional[str] = None,
    open_file_content: Optional[str] = None,
    max_context_chars: int = 12000,
) -> str:
    """RAG 기반 컨텍스트를 시스템 프롬프트로 조합."""
    if not project_path:
        return ""

    idx = get_indexer(project_path)
    parts = []

    # 1. 프로젝트 개요
    parts.append(f"## 프로젝트: {project_path.split('/')[-1]}")
    parts.append(f"경로: {project_path}")
    parts.append(f"인덱싱된 청크: {len(idx.chunks)}개")
    parts.append("")

    # 2. 파일 트리 (축약)
    tree = idx.get_file_tree()
    if tree:
        tree_lines = tree.split('\n')
        if len(tree_lines) > 50:
            tree = '\n'.join(tree_lines[:50]) + f'\n... ({len(tree_lines) - 50}줄 더)'
        parts.append("## 파일 구조")
        parts.append(f"```\n{tree}\n```")
        parts.append("")

    used_chars = sum(len(p) for p in parts)

    # 3. 현재 열린 파일 (우선 포함)
    if open_file and open_file_content:
        file_section = f"## 현재 열린 파일: {open_file}\n```\n"
        # 최대 3000자
        if len(open_file_content) > 3000:
            file_section += open_file_content[:3000] + "\n... (truncated)"
        else:
            file_section += open_file_content
        file_section += "\n```\n"
        if used_chars + len(file_section) < max_context_chars:
            parts.append(file_section)
            used_chars += len(file_section)

    # 4. RAG 검색 — 질문과 관련된 코드 청크
    search_results = idx.search(query, top_k=8)
    if search_results:
        parts.append("## 관련 코드")
        for chunk, score in search_results:
            if used_chars > max_context_chars:
                break
            # 열린 파일과 중복이면 스킵
            if open_file and chunk.file_path == open_file:
                continue
            section = f"### {chunk.file_path} (L{chunk.start_line}-{chunk.end_line}, 관련도: {score:.2f})\n```{chunk.language}\n{chunk.content}\n```\n"
            if used_chars + len(section) < max_context_chars:
                parts.append(section)
                used_chars += len(section)

    context = '\n'.join(parts)
    return context


def build_system_prompt(
    project_path: str,
    query: str,
    open_file: Optional[str] = None,
    open_file_content: Optional[str] = None,
    base_system_prompt: str = "",
) -> str:
    """최종 시스템 프롬프트 생성."""
    context = build_context(project_path, query, open_file, open_file_content)

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
