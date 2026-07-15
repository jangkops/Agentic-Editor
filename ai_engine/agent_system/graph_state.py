"""LangGraph 공유 상태(GraphState) + reducer 정의.

Task 1.2 산출물. design.md의 Data Models(State 스키마)와 API_NOTES.md(실측 확정
인터페이스)를 근거로 한다.

핵심 사항 (API_NOTES.md 대조):
- `add_messages`는 langchain_core가 아니라 **`langgraph.graph.message`** 에서 import한다
  (API_NOTES 항목 6에서 실측 확정된 경로).
- reducer는 `Annotated[list[...], <reducer>]` 형태로 필드에 부착한다. 노드가 부분값을
  반환하면 LangGraph가 reducer로 병합한다(수동 append 제거).

보안 (요구사항 8.1 — 자격증명 미저장):
- 이 상태에는 AWS 자격증명(accessKeyId / secretAccessKey / sessionToken 등)을 담는
  필드를 **절대 두지 않는다.** `aws_profile`(프로파일 *이름*)과 `bedrock_user`(역할 대상
  사용자 이름) 같은 **문자열 식별자만** 전달한다. 실제 자격증명은 런타임에
  GatewayClient가 assume-role / 주입으로 획득하며 상태·체크포인트 어디에도 기록되지
  않는다(요구사항 8.2 / 8.3와 정합).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, List, Literal, Optional, TypedDict

from langchain_core.messages import BaseMessage

# API_NOTES 항목 6: add_messages의 확정된 import 경로.
from langgraph.graph.message import add_messages

# ── 라우팅 라벨 ──
# Top Supervisor가 결정하는 도메인 route. "done"은 종료(재라우팅 hop cap 도달 포함).
RouteName = Literal["coding", "media", "research", "ops", "chat", "done"]


class Evidence(TypedDict):
    """RAG retrieve 노드가 적재하는 근거.

    - context: 시스템 프롬프트에 주입되는 RAG 컨텍스트 문자열.
    - chunks: [(chunk, score), ...] 형태. verify 노드의 citation 검증에 사용.
    """

    context: str
    chunks: list


class VerifiedFile(TypedDict):
    """디스크에 실재하는 것으로 검증된 생성 산출물.

    - path: 프로젝트 상대 경로 (예: .generated/...).
    - absPath: 절대 경로 — 디스크 존재/크기 검증 및 dedup 기준.
    - tool: 산출물을 생성한 도구 이름.
    """

    path: str
    absPath: str
    tool: str


class Evaluation(TypedDict, total=False):
    """Evaluator_Node 채점 결과 (langgraph-reasoning-upgrade).

    aggregate 이후 evaluator 노드가 원래 사용자 목표 대비 현재 산출물/답변의 달성
    여부를 채점하여 이 형태로 상태의 `evaluation` 채널에 기록한다.

    - achieved:        원래 요청이 모두 충족되었는가(달성/미달).
    - reason:          판정 사유(한국어).
    - missing_domains: 미달 시 보완이 필요한 도메인 라벨 목록. 항상 유효 도메인
                       라벨(_ROUTE_LABELS)의 부분집합만 포함한다.

    ⚠️ 보안(요구사항 10.1): 이 dict 에는 AWS 자격증명(accessKeyId / secretAccessKey /
    sessionToken)을 절대 담지 않는다. 평가 메타데이터만 보관한다.
    """

    achieved: bool
    reason: str
    missing_domains: List[str]


def _take_right(left: Any, right: Any) -> Any:
    """스칼라 채널 병합 reducer — last-wins(우측 우선, None 이면 좌측 보존).

    병렬 fan-out(Send)에서 여러 워커 서브그래프가 같은 단일값 채널(prompt/system_prompt/
    final_text 등)을 동시에 반환하면 LangGraph 가 INVALID_CONCURRENT_GRAPH_UPDATE 를 던진다.
    이 채널들에 reducer 를 부여하면 여러 값이 와도 하나로 병합된다(마지막 non-None 채택).
    순차 실행에서는 단일 write 뿐이라 동작이 바뀌지 않는다(무회귀).
    """
    return right if right is not None else left


def _take_max_int(left: Any, right: Any) -> int:
    """정수 채널 병합 reducer — 두 값 중 큰 정수를 반환하는 monotonic MAX(단조 증가).

    None 은 0 으로 취급하고, 둘 다 None 이면 0 을 반환한다. 정수로 해석할 수 없는 값도
    방어적으로 0 으로 취급한다(비차단).

    ⚠️ Send fan-out echo/reset 면역 — 워커가 0 을 emit해도 running max 유지:
    병렬 fan-out(Send)으로 분기된 워커 서브그래프는 동일한 GraphState 스키마를 공유하므로,
    병합 시 refine_count 채널에 0 을 emit할 수 있다. last-wins(_take_right)라면
    `_take_right(1, 0) == 0` 이 되어 카운터가 리셋되고 cap 판정(refine_count >= cap)이 절대
    성립하지 않아 Refine_Loop 가 무한 반복한다(과거 GraphRecursionError 이력). MAX reducer 는
    `_take_max_int(1, 0) == 1` 로 워커의 0-리셋에 면역이며, 0→1→2 로 단조 증가하는 카운터
    의미상 정확하다(cap 판정이 `>=` 이므로 running max 로 충분).
    """
    def _to_int(v: Any) -> int:
        if v is None:
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    return max(_to_int(left), _to_int(right))


def _merge_verified_files(
    left: Optional[List[VerifiedFile]],
    right: Optional[List[VerifiedFile]],
) -> List[VerifiedFile]:
    """verified_files 병합 reducer — absPath 기준 dedup.

    여러 도구 호출/노드가 각각 verified_files 부분 리스트를 반환하면 LangGraph가 이
    reducer로 누적 병합한다. 동일 absPath는 한 번만 유지한다(먼저 등장한 항목 우선).

    Precondition:  left / right는 None 이거나 VerifiedFile dict의 리스트.
    Postcondition: 반환 리스트는 absPath가 유일하며, left의 순서를 보존한 뒤 right의
                   신규 항목을 append한 결과.
    Invariant:     입력을 변경하지 않는다(새 리스트 반환).
    """
    left = left or []
    right = right or []
    # left 는 통상 이전 reduce 결과(이미 dedup)지만, 방어적으로 left 내부 중복도 제거해
    # "결과 absPath 는 항상 유일" 불변식을 입력과 무관하게 보장한다.
    merged: List[VerifiedFile] = []
    seen: set = set()
    for vf in list(left) + list(right):
        ap = vf["absPath"]
        if ap not in seen:
            merged.append(vf)
            seen.add(ap)
    return merged


class GraphState(TypedDict, total=False):
    """LangGraph 계층적 오케스트레이션의 공유 상태.

    total=False: 모든 필드는 선택적. 노드는 자신이 갱신하는 필드만 부분 반환한다.

    ⚠️ 보안(요구사항 8.1): 자격증명 필드를 두지 않는다. aws_profile / bedrock_user는
    문자열 식별자일 뿐이며 실제 키를 담지 않는다.
    """

    # ── 입력 컨텍스트 ──
    # ⚠️ 병렬 fan-out(Send)에서 여러 워커 서브그래프가 이 스칼라 채널들을 동시에 반환하면
    # INVALID_CONCURRENT_GRAPH_UPDATE 가 발생하므로 last-wins reducer(_take_right)를 부여한다.
    # 순차 실행에는 영향 없다(단일 write).
    prompt: Annotated[str, _take_right]
    session_id: Annotated[str, _take_right]
    project_path: Annotated[str, _take_right]
    open_file: Annotated[str, _take_right]
    open_file_content: Annotated[str, _take_right]
    aws_profile: Annotated[str, _take_right]          # 프로파일 *이름* 문자열만 (자격증명 아님)
    bedrock_user: Annotated[str, _take_right]         # assume-role 대상 사용자 *이름* 문자열만
    template_id: Annotated[str, _take_right]
    system_prompt: Annotated[str, _take_right]
    is_remote: Annotated[bool, _take_right]

    # ── 대화 / 추론 ──
    # reducer로 누적: 노드가 부분 메시지를 반환하면 LangGraph가 병합.
    messages: Annotated[List[BaseMessage], add_messages]
    route: Annotated[RouteName, _take_right]      # Top Supervisor 결정
    # 관측용(라우터 프롬프트의 방문 도메인 표시). 서브그래프 echo로 중복 누적될 수 있어
    # hop cap 판정에는 쓰지 않음 — route_hops 사용.
    visited_routes: Annotated[List[str], operator.add]
    # 라우터 재라우팅 hop 계수 — 서브그래프 공유 채널(operator.add) echo에 면역인 last-wins
    # 카운터. hop cap 판정의 신뢰 지표.
    route_hops: Annotated[int, _take_right]
    iteration: Annotated[int, _take_right]        # 서브그래프 내 model↔tool 반복 카운터
    # 병렬 fan-out 계획(의존성 인식 DAG 확장):
    #   [{"id": <str>, "domain": <route>, "subtask": <str>, "depends_on": List[str]}, ...]
    # planner 노드가 1회 세팅한다. len>=2 이면 Send 로 병렬 실행, <=1 이면 단일 실행.
    # - id:         서브태스크 고유 식별자(누락 시 "t{i}" 로 보정).
    # - depends_on: 이 서브태스크 시작 전 완료되어야 할 선행 서브태스크 id 목록(기본 []).
    #               위상정렬(topological_waves)으로 Wave 를 분할하는 근거.
    # reducer(_take_right)는 불변 유지 — DAG_Planner off 시 depends_on 을 무시하고 기존
    # 단일 Wave 동작을 그대로 수행한다(무회귀).
    # ⚠️ Send fan-out echo/reset 주의: 워커 서브그래프가 병합 시 plan=[] 를 echo 하면
    # `_take_right(plan, [])=[]` 로 plan 이 지워져 다중 Wave 스케줄이 소실된다. 이를 막기 위해
    # supervisor.plan_dispatch 가 현재 plan 을 워커 substate 로 함께 전달해 워커가 같은 값을
    # echo 하도록 만든다(`_take_right(plan, plan)=plan`, 리셋 차단).
    plan: Annotated[List[dict], _take_right]

    # ── RAG / 검증 ──
    evidence: Annotated[Optional[Evidence], _take_right]
    citations: Annotated[dict, _take_right]       # {"verified": [...], "unverified": [...]}
    answer_quality: Annotated[dict, _take_right]  # answer_quality metadata

    # ── 산출물 ──
    # absPath 기준 dedup 병합 reducer.
    verified_files: Annotated[List[VerifiedFile], _merge_verified_files]
    final_text: Annotated[str, _take_right]
    error: Annotated[str, _take_right]

    # ── 신규: Evaluator 재계획 루프 (langgraph-reasoning-upgrade) ──
    # evaluator 노드가 채점 결과를 1회 기록. 단일 노드만 write 하므로 last-wins 안전.
    evaluation: Annotated[Optional[Evaluation], _take_right]
    # 재계획(Refine_Loop) 수행 횟수. monotonic MAX reducer(_take_max_int) —
    # Send fan-out echo/reset 면역: 워커 서브그래프가 병합 시 refine_count=0 을 emit해도
    # running max 를 유지하므로 카운터가 리셋되지 않는다. last-wins(_take_right)는
    # `_take_right(1, 0)=0` 으로 워커의 0-리셋을 막지 못해 cap 판정(refine_count >= cap)이
    # 절대 성립하지 않고 Refine_Loop 가 무한 반복했다(과거 GraphRecursionError 이력, 요구사항
    # 2.2/2.4 및 Property 1 위반). refine_count 는 0→1→2 로 단조 증가하고 cap 판정이 `>=`
    # 이므로 MAX reducer 가 의미상 정확하다(요구사항 2.3).
    refine_count: Annotated[int, _take_max_int]

    # ── 신규: DAG Wave 스케줄링 (langgraph-reasoning-upgrade) ──
    # 완료된 Wave 수. 다음 dispatch 대상 Wave 인덱스로 사용. last-wins(_take_right) —
    # planner 가 새 plan 생성(refine) 시 0 으로 리셋해야 하므로 MAX reducer 를 쓸 수 없다.
    # ⚠️ Send fan-out echo/reset 면역은 reducer 가 아니라 supervisor.plan_dispatch 가
    # completed_waves(및 plan)를 워커 substate 로 함께 전달해 워커가 같은 값을 echo 하도록
    # 만들어 확보한다(`_take_right(x, x)=x`). substate 로 전달하지 않으면 워커가 기본값 0 을
    # echo 해 Wave 커서가 되감기고 다중 Wave 가 무한 재-dispatch 된다.
    completed_waves: Annotated[int, _take_right]
