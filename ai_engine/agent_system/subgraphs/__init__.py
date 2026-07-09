"""도메인 서브그래프 빌더 패키지.

각 `build_*_subgraph(deps)` 는 compiled Runnable(CompiledStateGraph)을 반환하며, Top
그래프(build_top_graph)가 이를 노드로 add 하여 graph-of-graphs 를 구성한다(요구사항 1.1).
공통 조립 로직은 `_common.build_domain_subgraph` 에 있고, 각 모듈은 도메인 도구 집합만
다르게 바인딩한다(요구사항 1.6).
"""

from ai_engine.agent_system.subgraphs.chat import build_chat_subgraph
from ai_engine.agent_system.subgraphs.coding import (
    CODING_TOOLS,
    build_coding_subgraph,
)
from ai_engine.agent_system.subgraphs.media import MEDIA_TOOLS, build_media_subgraph
from ai_engine.agent_system.subgraphs.ops import OPS_TOOLS, build_ops_subgraph
from ai_engine.agent_system.subgraphs.research import (
    RESEARCH_TOOLS,
    build_research_subgraph,
)

__all__ = [
    "build_coding_subgraph",
    "build_media_subgraph",
    "build_research_subgraph",
    "build_ops_subgraph",
    "build_chat_subgraph",
    "CODING_TOOLS",
    "MEDIA_TOOLS",
    "RESEARCH_TOOLS",
    "OPS_TOOLS",
]
