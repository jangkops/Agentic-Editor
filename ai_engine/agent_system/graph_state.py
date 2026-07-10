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


def _take_right(left: Any, right: Any) -> Any:
    """스칼라 채널 병합 reducer — last-wins(우측 우선, None 이면 좌측 보존).

    병렬 fan-out(Send)에서 여러 워커 서브그래프가 같은 단일값 채널(prompt/system_prompt/
    final_text 등)을 동시에 반환하면 LangGraph 가 INVALID_CONCURRENT_GRAPH_UPDATE 를 던진다.
    이 채널들에 reducer 를 부여하면 여러 값이 와도 하나로 병합된다(마지막 non-None 채택).
    순차 실행에서는 단일 write 뿐이라 동작이 바뀌지 않는다(무회귀).
    """
    return right if right is not None else left


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
    # 병렬 fan-out 계획: [{"domain": <route>, "subtask": <str>}, ...]. planner 노드가 1회
    # 세팅한다. len>=2 이면 Send 로 병렬 실행, <=1 이면 단일 실행.
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
