"""ops 서브그래프 — 명령 실행 / git / 원격 SSH 작업 도메인.

Task 3.3 산출물. `_common.build_domain_subgraph` 를 재사용하고 ops 도메인의 도구 집합만
바인딩한다(요구사항 1.6). design.md 서브그래프 분할 기준(ops 행)은 `run_command / git 도구 /
브리지 라우팅` 을 명시한다.

설계 결정:
- git 계열 작업은 별도 도구 없이 `run_command`("git ...") 로 커버한다(server.py `_execute_tool`
  의 run_command 가 임의 셸 명령을 실행).
- 원격 SSH 브리지 라우팅은 이 서브그래프가 특별히 다루지 않는다 — GatewayToolNode 가
  `state["is_remote"]` 와 `_bridge_is_remote()` 를 보고 브리지(_call_bridge) vs 로컬
  (_execute_tool) 을 자동 분기한다(run_command 는 브리지 지원 도구 집합에 포함).

⚠️ 도구 name 정합성(server.py 실측): run_command 는 `_execute_tool` / `_format_bridge_result`
  양쪽에서 정확히 이 이름으로 처리된다.
"""

from __future__ import annotations

from typing import Any, List

from ai_engine.agent_system.subgraphs._common import build_domain_subgraph


# ─────────────────────────────────────────────────────────────────────────────
# OPS_TOOLS — 명령 실행 중심. git 은 run_command 로 커버, 원격 분기는 GatewayToolNode 담당.
# ─────────────────────────────────────────────────────────────────────────────
OPS_TOOLS: List[dict] = [
    {
        "name": "run_command",
        "description": (
            "셸 명령을 실행하고 표준출력/표준에러를 반환한다. git 계열 작업(git status/add/"
            "commit/log 등)도 이 도구로 수행한다. 원격 세션이면 SSH 브리지로 자동 라우팅된다."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "실행할 셸 명령(예: 'git status', 'ls -la')",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "명령을 실행할 작업 디렉토리(선택, 기본 프로젝트 루트)",
                    },
                },
                "required": ["command"],
            }
        },
    },
]


def build_ops_subgraph(deps: Any):
    """ops 서브그래프를 조립해 compiled Runnable 을 반환.

    구성은 coding 과 동일한 ReAct 루프(retrieve → model → tools → verify)이며 도구 집합만
    OPS_TOOLS(run_command 중심)로 다르다. model_id 는 deps.model_coding(sonnet-4-5).
    """
    # MCP 도구(있으면)를 ops 도구에 병합 — API/인프라 조작 계열 MCP 서버에 적합.
    mcp = list(getattr(deps, "mcp_tools", None) or [])
    return build_domain_subgraph(
        deps,
        tools=OPS_TOOLS + mcp,
        model_id=deps.model_coding,
        domain="ops",
    )
