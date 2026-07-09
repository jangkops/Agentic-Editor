"""coding 서브그래프 조립 — `retrieve → model → tools → verify` 패턴.

Task 1.12 산출물. Task 3.3 에서 공통 조립 로직을 `_common.py` 로 추출하고, 이 모듈은
coding 도메인의 **도구 집합(CODING_TOOLS)** 과 공개 심볼(`build_coding_subgraph`)만
보유하도록 리팩터되었다. 검증된 동작(모델 노드 타임아웃 / iteration cap / checkpointer
미주입)은 `_common.py` 로 이동했을 뿐 변경 없이 보존된다.

기존 자산 재사용(재구현 금지 — 요구사항 7.5):
- GraphState (`agent_system/graph_state.py`)
- GatewayChatModel (`agent_system/chat_model_adapter.py`)
- GatewayToolNode (`agent_system/nodes/tool_node.py`)

하위 호환 재노출(re-export):
- `make_model_node` / `make_retrieve_node` / `make_verify_node` /
  `tools_condition_or_verify` / `MODEL_NODE_TIMEOUT` / `SUBGRAPH_RECURSION_LIMIT`
  는 기존 import 경로(`subgraphs.coding`)에서 계속 사용할 수 있도록 `_common` 에서
  재노출한다.
"""

from __future__ import annotations

from typing import Any, List

# 공통 조립 프리미티브 재노출 — 기존 import 경로 호환 보존.
from ai_engine.agent_system.subgraphs._common import (  # noqa: F401
    MODEL_NODE_TIMEOUT,
    SUBGRAPH_RECURSION_LIMIT,
    build_domain_subgraph,
    make_model_node,
    make_retrieve_node,
    make_verify_node,
    tools_condition_or_verify,
)


# ─────────────────────────────────────────────────────────────────────────────
# CODING_TOOLS — Bedrock toolSpec dict 리스트 (GatewayChatModel.bind_tools 입력)
# design.md 서브그래프 분할 기준: coding = read_file/write_file/search_files/run_command
# ─────────────────────────────────────────────────────────────────────────────
CODING_TOOLS: List[dict] = [
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
        "name": "write_file",
        "description": "프로젝트 내 파일에 내용을 기록한다(없으면 생성).",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "기록할 파일의 프로젝트 상대 경로",
                    },
                    "content": {
                        "type": "string",
                        "description": "파일에 기록할 전체 내용",
                    },
                },
                "required": ["path", "content"],
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
                },
                "required": ["query"],
            }
        },
    },
    {
        "name": "run_command",
        "description": "프로젝트 루트에서 셸 명령을 실행하고 표준출력/표준에러를 반환한다.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "실행할 셸 명령",
                    }
                },
                "required": ["command"],
            }
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# 서브그래프 빌더
# ─────────────────────────────────────────────────────────────────────────────
def build_coding_subgraph(deps: Any):
    """coding 서브그래프를 조립해 compiled Runnable 을 반환.

    구성 (design.md 섹션 4):
      START → retrieve → model
      conditional(model, tools_condition_or_verify) → {tools, verify}
      tools → model   (ReAct 루프)
      verify → END

    Postcondition: sg.compile() 결과(CompiledStateGraph)를 반환한다. checkpointer 는
                   주입하지 않는다(부모 그래프가 주입 — API_NOTES 항목 6).
    """
    return build_domain_subgraph(
        deps,
        tools=CODING_TOOLS,
        model_id=deps.model_coding,
    )
