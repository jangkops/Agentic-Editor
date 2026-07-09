"""research 서브그래프 — 웹/문서 리서치·요약 도메인.

Task 3.3 산출물. `_common.build_domain_subgraph` 를 재사용하고 research 도메인의 도구
집합만 바인딩한다(요구사항 1.6). design.md 서브그래프 분할 기준(research 행)은
`search_web(향후), read_file` 을 명시한다. search_web 은 아직 server.py 에 도구로 존재하지
않으므로, 현재는 실측 가능한 read_file / search_files 만 바인딩한다.

⚠️ 도구 name 정합성(server.py 실측):
- read_file / search_files 는 server.py `_execute_tool` 이 정확히 이 이름으로 디스패치한다.
- search_web 은 미구현 — 후속 태스크에서 도구 추가 시 이 목록에 넣는다.
"""

from __future__ import annotations

from typing import Any, List

from ai_engine.agent_system.subgraphs._common import build_domain_subgraph


# ─────────────────────────────────────────────────────────────────────────────
# RESEARCH_TOOLS — 읽기/검색 중심(부작용 없는 조회). name 은 server.py 실측과 일치.
# ─────────────────────────────────────────────────────────────────────────────
RESEARCH_TOOLS: List[dict] = [
    {
        "name": "read_file",
        "description": "프로젝트 내 파일의 내용을 읽어 반환한다.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "읽을 파일의 프로젝트 상대 경로",
                    }
                },
                "required": ["path"],
            }
        },
    },
    {
        "name": "search_files",
        "description": "프로젝트 내에서 텍스트/패턴에 일치하는 위치를 검색한다.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색할 텍스트 또는 정규식 패턴",
                    },
                    "path": {
                        "type": "string",
                        "description": "검색 범위를 한정할 하위 경로(선택)",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "파일 패턴(예: *.py, *.md) — 선택",
                    },
                },
                "required": ["query", "path"],
            }
        },
    },
]


def build_research_subgraph(deps: Any):
    """research 서브그래프를 조립해 compiled Runnable 을 반환.

    구성은 coding 과 동일한 ReAct 루프(retrieve → model → tools → verify)이며 도구 집합만
    RESEARCH_TOOLS(읽기/검색 전용)로 다르다. model_id 는 deps.model_coding(sonnet-4-5).
    """
    return build_domain_subgraph(
        deps,
        tools=RESEARCH_TOOLS,
        model_id=deps.model_coding,
    )
