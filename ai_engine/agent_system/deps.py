"""GraphDeps — 그래프/서브그래프 빌더에 주입하는 의존성 컨테이너.

Task 1.12 산출물. design.md 섹션 4의 서브그래프 빌더 시그니처(`build_*_subgraph(deps)`)가
받는 `deps` 객체의 표준 형태를 정의한다.

⚠️ 보안 (요구사항 8.1 — 자격증명 미저장):
- 이 컨테이너에는 AWS 자격증명(accessKeyId / secretAccessKey / sessionToken)을 절대
  담지 않는다. `gateway`(GatewayClient)는 런타임에 assume-role / 주입으로 자격증명을
  획득하며, 이 dataclass 는 gateway 참조와 모델 ID / checkpointer 참조만 보관한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# design.md 서브그래프 분할 기준: coding/media/research/ops 는 sonnet-4-5 기본.
_DEFAULT_CODING_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"

# ── 모델 역할 배분 (steering project.md / 요구사항 9) ──
# 설계 의도(steering project.md): Planner=Opus, Generator=Sonnet, Evaluator=Opus.
#
# ⚠️ 프로덕션 기본값 결정 (라이브 게이트웨이 e2e 검증 근거 — 2026, 두 경로 모두 실측):
#   Opus 계열은 reasoning 메타 노드(planner/evaluator)에서 **양쪽 실행 경로 모두 실패**한다:
#     (A) [프로덕션 경로 = astream_events → _astream → stream_sse_realtime]
#         Opus 는 게이트웨이 스트리밍(Lambda) 엔드포인트를 지원하지 않아 decision=ERROR /
#         "No generation chunks" 로 실패한다(라이브 실측: converse_stream_live 3.4s ERROR).
#         → 노드가 예외를 잡아 폴백(evaluator: achieved=True 기본값)하므로 **재계획 루프가
#         프로덕션에서 무력화**된다. 이것이 Sonnet 기본값이 필요한 핵심(프로덕션) 이유다.
#     (B) [비스트리밍 경로 = ainvoke → _agenerate → converse]
#         Opus 는 `ACCEPTED` → 비동기 S3 잡 폴링(최대 300s)을 타 wait_for 타임아웃 폴백된다.
#   → 두 경로 모두에서 안정 동작하는 Sonnet 4.5 를 Planner/Evaluator 기본값으로 채택한다.
#     라이브 검증: 프로덕션 경로(astream_events)에서 planner 2 subtasks 분해 + evaluator
#     achieved 판정(reason 212자) + refine 루프 정상 동작 확인.
#
#   참고(별도 수정): 과거 converse 비동기 폴링 헬퍼가 text 만 뽑아 toolUse 를 유실하던 결함은
#   gateway_module._poll_job_data 로 해결(비스트리밍 경로 방어 — 프로덕션 주경로 _astream 은
#   원래 toolUse 델타를 직접 파싱해 영향 없었음).
#
# ⚠️ Opus 주입 주의(요구사항 9.5): `deps.model_planner`/`deps.model_evaluator` 로 주입은
# 가능하나, 위 (A) 때문에 **프로덕션(스트리밍) 경로에서는 Opus reasoning 메타 호출이 실패해
# 폴백**된다. Opus 를 실제로 쓰려면 스트리밍 엔드포인트가 Opus 를 지원하도록 게이트웨이가
# 개선되어야 한다. 그 전까지 reasoning 메타 노드에는 스트리밍 지원 모델(Sonnet 계열)을 쓴다.
_DEFAULT_PLANNER_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"    # Planner(동기 신뢰 경로)
_DEFAULT_GENERATOR_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"  # Generator=Sonnet
_DEFAULT_EVALUATOR_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"  # Evaluator(동기 신뢰 경로)


@dataclass
class GraphDeps:
    """그래프 빌더 의존성.

    Attributes:
        gateway:      GatewayClient (converse / stream_sse_realtime 제공). LLM 호출은
                      반드시 이 게이트웨이 경유(직접 SDK 금지 — 요구사항 2.2).
        model_coding: coding 서브그래프 model 노드가 사용할 Bedrock model_id.
        model_planner:   DAG_Planner 노드가 사용할 model_id(기본 Sonnet 4.5 — 동기 신뢰
                         경로. Opus 는 주입으로 사용 가능, 요구사항 9.2/9.5).
        model_generator: Generator(도메인 model 노드)가 사용할 model_id(기본 Sonnet, 9.4).
        model_evaluator: Evaluator_Node 가 사용할 model_id(기본 Sonnet 4.5 — 동기 신뢰
                         경로. Opus 는 주입으로 사용 가능, 요구사항 9.3/9.5).
        checkpointer: LangGraph BaseCheckpointSaver. Top 그래프 compile 시에만 주입되며
                      서브그래프는 부모의 checkpointer 를 상속한다(API_NOTES 항목 6).
                      Phase 1 단일 서브그래프 스모크에서는 None 이어도 무방.
    """

    gateway: Any = None
    model_coding: str = _DEFAULT_CODING_MODEL
    # ── 모델 역할 배분 (요구사항 9) ──
    # 미주입 시 기본값(Opus/Sonnet/Opus)을 사용. 기존 model_coding 필드는 하위 호환 유지.
    model_planner: str = _DEFAULT_PLANNER_MODEL        # DAG_Planner 역할 (기본 Sonnet, Opus 주입 가능)
    model_generator: str = _DEFAULT_GENERATOR_MODEL    # Generator 역할 (기본 Sonnet)
    model_evaluator: str = _DEFAULT_EVALUATOR_MODEL    # Evaluator 역할 (기본 Sonnet, Opus 주입 가능)
    checkpointer: Optional[Any] = None
    # LangGraph Store(BaseStore) — 세션 간(cross-thread) 장기 메모리. Top 그래프 compile 시
    # 주입되며 노드는 deps.store 를 직접 참조하거나 부모 그래프에서 전파받는다. None 이면
    # 장기 메모리 비활성(비차단). 자격증명은 저장하지 않는다(요구사항 8.x).
    store: Optional[Any] = None
    # MCP 도구 — langchain-mcp-adapters 로 로드한 LangChain BaseTool 리스트(도메인 서브그래프에
    # 병합되어 bind_tools 로 model 에 노출). mcp_tool_map 은 {name: BaseTool} 로 GatewayToolNode
    # 가 ainvoke 로 실행할 때 사용. 기본 빈 값(MCP 비활성 시 no-op).
    mcp_tools: Optional[Any] = None       # list[BaseTool]
    mcp_tool_map: Optional[Any] = None    # dict[str, BaseTool]
