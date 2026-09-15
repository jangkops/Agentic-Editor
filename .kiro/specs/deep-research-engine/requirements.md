# Requirements Document

## Introduction

이 기능은 Mogam Works AI 코드 에디터(저장소 `jangkops/Agentic-Editor`)에 **최고 품질 티어의
외부 리서치 능력**을 추가한다. 현재 시스템은 외부 웹 검색·논문 검색·딥리서치가 사실상
미구현이다(실측 확인).

- `research` 서브그래프(`ai_engine/agent_system/subgraphs/research.py`)의 도구는 `read_file`,
  `search_files` 둘뿐이며 모두 로컬 프로젝트 파일 대상이다. 코드 주석에도 `search_web` 미존재가
  명시되어 있다.
- `ai_engine/agent_system/tool_registry.py`의 `search_web`는 "Web search not implemented" 스텁이다.
- tavily/arxiv/semantic scholar 등 외부 검색 통합 코드는 0건이다(전체 검색으로 확인).
- 유일한 웹 경로는 MCP 도구(`ai_engine/agent_system/mcp_tools.py`)이나 `AE_MCP_ENABLED` 기본 off
  (옵트인)라 실사용 경로가 아니다.

이 스펙은 세 가지 리서치 능력을 필수 범위로 추가한다.

1. **일반 웹 검색**: 외부 웹에서 최신·외부 지식을 검색.
2. **학술/논문 검색**: 논문·학술 자료 검색.
3. **딥리서치**: 멀티스텝 조사(질의 분해 → 다중 소스 검색 → 본문 수집 → 중복제거/재랭크 →
   인용 포함 종합 → 필요 시 반복 심화).

사용자는 여기에 더해 "그 이상의 퀄리티 검색 능력"과 검색 제공자 "max 퀄리티급"을 요구했다.
따라서 품질 기준을 최상위로 잡되, 이를 **측정 가능한 수용 기준**(관련성·최신성·출처 신뢰도·
인용 정확도·커버리지·중복제거)으로 표현한다.

이 기능은 기존 강점 스택 **위에** 구축한다(재구현 금지). 즉 딥리서치는 "소스를 로컬 파일 →
웹/논문으로 확장"하는 형태이며, 기존 검색→재랭크→인용→grounding→멀티에이전트 종합 파이프라인을
재사용한다.

핵심 제약(steering 실측 준수):
- LLM·추론 호출은 Bedrock Gateway(SigV4) 경유만. 외부 검색 API는 LLM 호출이 아니므로 허용되나,
  Vertex 이미지 예외(`gateway.md`)처럼 **문서화된 사용자 결정 + 옵트인 환경변수 플래그** 형태로
  도입한다.
- 검색 제공자 API 키는 어떤 파일에도 저장 금지. env/시크릿으로만 런타임 주입, repo 미커밋
  (`.gitignore`), 로그 마스킹(앞 4자만).
- 검색 캐시·리서치 산출물 영속은 `userData` 하위만.
- 스택 고정: Python 3.11 / FastAPI / HTTPX 백엔드, Electron + Vanilla JS 프론트. 신규 무거운
  프레임워크 금지.
- 멀티에이전트 필수(Coordinator → Planner → Generator → Evaluator). 딥리서치도 이 패턴으로.
- 프라이버시: 사용자 질의가 외부 검색 제공자로 전송됨을 고지·동의 처리.
- 비차단·타임아웃·폴백 등 기존 가용성 원칙 유지(외부 API 장애 시 그래프 진행 차단 금지).

구체 제공자(웹: Exa/Tavily/Brave 등, 논문: Semantic Scholar/arXiv/OpenAlex/PubMed 등) 비교·선정은
**설계 단계**에서 수행한다. 본 요구사항 문서는 "무엇을(기능·품질 기준)"만 규정한다.

## Glossary

- **Research_Engine**: 본 스펙으로 추가되는 외부 리서치 능력 전체(웹 검색 + 논문 검색 + 딥리서치 + 정규화/캐시/인용/품질 측정).
- **Web_Search_Provider**: 일반 웹 검색을 제공하는 외부 검색 API(구체 제공자는 설계에서 선정).
- **Academic_Search_Provider**: 학술·논문 검색을 제공하는 외부 검색 API(구체 제공자는 설계에서 선정).
- **Content_Fetcher**: 선택된 소스 URL의 본문을 Python 백엔드(HTTPX)에서 조회·추출하는 구성요소.
- **Search_Result**: 웹 검색 결과 1건의 정규 스키마(제목, URL, 발췌문, 발행일, 출처 도메인, 관련성 점수).
- **Paper_Result**: 논문 검색 결과 1건의 정규 스키마(제목, 저자 목록, 발행연도, 게재처, 초록, DOI 또는 URL, 피인용수).
- **Result_Normalizer**: 이기종 제공자 응답을 Search_Result / Paper_Result 정규 스키마로 변환하는 순수 함수(파서). 캐시 직렬화기(프린터)와 짝을 이룬다.
- **Deep_Research_Pipeline**: 질의 분해 → 다중 소스 검색 → 본문 수집 → 중복제거/재랭크 → 인용 포함 종합 → 필요 시 반복 심화로 구성된 멀티스텝 조사 파이프라인.
- **Research_Report**: 딥리서치의 최종 산출물. 인용을 포함한 종합 답변.
- **Deduplicator**: 병합된 결과에서 중복 소스를 제거하는 순수 함수.
- **Reranker**: 병합·중복제거된 후보를 관련성 순으로 재정렬하는 구성요소. 기존 RRF/MMR/LLM 리랭커 자산을 재사용한다.
- **Coordinator / Planner / Generator / Evaluator**: steering이 요구하는 멀티에이전트 역할. 각각 조율 / 조사 계획·질의 분해 / 인용 포함 종합 생성 / 목표 대비 평가를 담당한다.
- **Research_Subgraph**: 기존 research 도메인 서브그래프(`subgraphs/research.py`).
- **RESEARCH_TOOLS**: Research_Subgraph에 바인딩되는 도구 목록.
- **Gateway_Tool_Node**: 도구 호출을 `ainvoke`로 실행하고 ToolMessage/verified_files를 반환하는 기존 노드(`nodes/tool_node.py`의 `GatewayToolNode`).
- **Search_Provider_Flag**: 외부 검색·본문 조회 활성 여부를 제어하는 옵트인 환경변수(예: `AE_ENABLE_WEB_RESEARCH`). Vertex 이미지 예외 정책과 동일한 형태.
- **Provider_Credential**: 검색 제공자 API 키. 런타임에 env/시크릿으로만 주입되며 파일 미저장.
- **Deepening_Cap**: 딥리서치 심화 반복 횟수 상한(예: `AE_MAX_DEEPENING`). 유한 종료를 보장한다.
- **Min_Sources / Min_Providers**: 종합 전 확보해야 할 최소 소스 수 / 최소 제공자 수(커버리지 기준).
- **Gateway**: Bedrock Gateway(SigV4 + assume-role). 모든 LLM·추론 호출 경로.
- **userData**: Electron `app.getPath('userData')` 하위 폴더. 모든 데이터 영속의 유일한 위치.
- **RRF**: Reciprocal Rank Fusion. 점수 스케일에 무관하게 순위 기반으로 결과를 융합하는 기법(기존 `rrf_fuse` 재사용).
- **MMR**: Maximal Marginal Relevance. 관련성과 다양성의 균형(기존 HybridSearcher 재사용).
- **Search_Status_Event**: 검색 도구 실행의 시작과 종료를 채팅 UI에 알리기 위해 기존 SSE_Event_Channel로 방출되는 이벤트. 검색 종류, 대상 제공자 목록, 질의 요약 등 진행 표시용 메타데이터를 포함하며 Provider_Credential은 포함하지 않는다.
- **Search_Indicator**: 채팅 UI에서 검색 진행 상태(활성 / 완료 / 해제)를 표시하는 진행 인디케이터. Search_Status_Event를 수신하여 상태가 갱신된다.
- **SSE_Event_Channel**: 백엔드가 채팅 UI로 진행·부분 응답을 실시간 전달하는 기존 Server-Sent Events 스트리밍 채널. Search_Status_Event 전달에 재사용되며 신규 채널이나 CSP 변경을 요구하지 않는다.

## Requirements

### Requirement 1: 일반 웹 검색

**User Story:** As a 에디터 사용자, I want 에이전트가 외부 웹을 검색해 최신·외부 정보를 가져오기를, so that 로컬 프로젝트 파일에 없는 지식까지 근거로 활용한 답변을 받는다.

#### Acceptance Criteria

1. WHEN 웹 검색 도구가 앞뒤 공백을 제거한 뒤 비어 있지 않은 질의 문자열과 함께 호출되면, THE Research_Engine SHALL Web_Search_Provider를 통해 해당 질의로 웹 검색을 수행하여 구성된 상한(top-k, 예: 최대 10건) 이하의 검색 결과를 반환한다.
2. WHEN Web_Search_Provider가 검색 결과를 반환하면, THE Research_Engine SHALL 각 결과를 제목, URL, 발췌문, 발행일, 출처 도메인, 관련성 점수의 6개 필드를 갖는 Search_Result로 변환한다.
3. IF Web_Search_Provider가 텍스트 필드(제목, URL, 발췌문, 발행일, 출처 도메인) 중 일부를 제공하지 않으면, THEN THE Research_Engine SHALL 누락된 필드를 빈 값으로 설정하고 관련성 점수를 정렬 가능한 수치값으로 채운 Search_Result를 예외 없이 생성한다.
4. THE Research_Engine SHALL 웹 검색 결과를 관련성 점수 내림차순으로 정렬하여 반환한다(정확성 속성 P7).
5. IF 정렬 대상에 관련성 점수가 동일한 둘 이상의 Search_Result가 존재하면, THEN THE Research_Engine SHALL 해당 결과들을 Web_Search_Provider가 반환한 순서대로 배치하여 정렬 결과를 결정적으로 유지한다.
6. THE Research_Engine SHALL 웹 검색 기능을 고유한 도구 name을 갖는 웹 검색 도구로 RESEARCH_TOOLS에 노출한다.
7. WHEN 웹 검색 도구가 호출되면, THE Gateway_Tool_Node SHALL 해당 도구를 `ainvoke`로 실행하고 도구 호출당 정확히 1개의 ToolMessage를 반환한다.
8. IF 웹 검색 질의가 비어 있거나, 공백만으로 구성되거나, 구성된 최대 질의 길이(예: 2,048자)를 초과하면, THEN THE Research_Engine SHALL Web_Search_Provider를 호출하지 않고 검증 실패 사유를 담은 구조화된 오류를 반환하며, 예외를 전파하거나 그래프 실행을 중단하지 않는다(정확성 속성 P8).

### Requirement 2: 학술/논문 검색

**User Story:** As a 연구·개발 사용자, I want 에이전트가 논문·학술 자료를 검색하기를, so that 근거 기반의 기술적·학술적 답변을 얻는다.

#### Acceptance Criteria

1. WHEN 논문 검색 도구가 앞뒤 공백을 제거한 뒤 비어 있지 않은 질의 문자열과 함께 호출되면, THE Research_Engine SHALL Academic_Search_Provider를 통해 해당 질의로 논문 검색을 수행하여 구성된 상한(top-k, 예: 최대 10건) 이하의 논문 결과를 반환한다.
2. WHEN Academic_Search_Provider가 결과를 반환하면, THE Research_Engine SHALL 각 논문을 제목, 저자 목록, 발행연도, 게재처, 초록, DOI 또는 URL, 피인용수의 7개 필드를 갖는 Paper_Result로 변환한다.
3. THE Research_Engine SHALL 논문 검색 결과를 관련성 점수 내림차순으로 정렬하여 반환한다(정확성 속성 P7).
4. IF 정렬 대상에 관련성 점수가 동일한 둘 이상의 Paper_Result가 존재하면, THEN THE Research_Engine SHALL 피인용수 내림차순으로 배치하고, 피인용수도 동일하면 Academic_Search_Provider가 반환한 순서대로 배치하여 정렬 결과를 결정적으로 유지한다.
5. THE Research_Engine SHALL 논문 검색 기능을 고유한 도구 name을 갖는 논문 검색 도구로 RESEARCH_TOOLS에 노출한다.
6. IF Academic_Search_Provider가 텍스트 필드(제목, 저자 목록, 게재처, 초록, DOI 또는 URL) 중 일부를 제공하지 않으면, THEN THE Research_Engine SHALL 누락된 필드를 빈 값으로 설정하고 발행연도와 피인용수를 정렬 가능한 수치값으로 채운 Paper_Result를 예외 없이 생성한다.

### Requirement 3: 소스 본문 수집

**User Story:** As a 사용자, I want 검색 결과의 실제 본문이 수집되기를, so that 발췌문이 아닌 원문 근거로 종합·인용이 이루어진다.

#### Acceptance Criteria

1. WHEN Content_Fetcher가 http 또는 https 스킴의 소스 URL과 함께 호출되면, THE Content_Fetcher SHALL 해당 URL의 본문을 Python 백엔드에서 HTTPX로 조회한다.
2. WHEN Content_Fetcher가 HTML 응답을 수신하면, THE Content_Fetcher SHALL 본문 텍스트를 추출하여 정규화된 텍스트로 반환한다.
3. THE Content_Fetcher SHALL 단일 소스 본문을 구성된 상한(예: 소스당 최대 100,000자) 이하로 잘라 반환한다.
4. WHEN Content_Fetcher가 소스를 조회하면, THE Content_Fetcher SHALL 각 조회에 구성된 개별 타임아웃(예: 조회당 10초)을 적용한다.
5. IF 특정 소스의 조회가 실패하거나 구성된 타임아웃을 초과하면, THEN THE Content_Fetcher SHALL 해당 소스를 결과에서 제외하고 예외를 전파하지 않으며 나머지 소스 수집을 계속한다(정확성 속성 P8).
6. THE Content_Fetcher SHALL 본문 조회를 Electron 렌더러가 아닌 Python 백엔드에서 수행하여 CSP 변경을 요구하지 않는다.

### Requirement 4: 검색 결과 정규화 및 캐시

**User Story:** As a 유지보수자, I want 이기종 제공자 응답이 단일 정규 스키마로 변환되고 안정적으로 캐시되기를, so that 파서·프린터 라운드트립이 보장되고 반복 검색 비용이 절감된다.

#### Acceptance Criteria

1. THE Research_Engine SHALL 이기종 제공자 응답을 Search_Result 또는 Paper_Result 정규 스키마로 변환하는 Result_Normalizer(파서)를 제공한다.
2. THE Research_Engine SHALL 정규화된 결과를 userData 하위 캐시로 직렬화하는 직렬화기(프린터)를 제공한다.
3. THE Result_Normalizer SHALL 정규화와 직렬화를 외부 상태에 의존하지 않는 순수 함수로 구현하여 단위 테스트가 가능하게 한다.
4. THE Result_Normalizer SHALL 정규화된 Search_Result 및 Paper_Result를 직렬화한 뒤 역직렬화하면 모든 정규 필드 값이 원본과 동등한 결과를 산출한다(round-trip — 정확성 속성 P1).
5. IF 제공자 응답에서 일부 정규 필드를 추출할 수 없으면, THEN THE Result_Normalizer SHALL 예외를 던지지 않고 추출 가능한 필드만 채우며 누락 텍스트 필드는 빈 값, 누락 수치 필드는 정렬 가능한 기본값으로 설정한 정규 결과를 생성한다(정확성 속성 P8).
6. WHEN 정규화된 질의 문자열이 동일한 검색 요청이 구성된 캐시 유효 기간(예: 24시간) 이내에 재요청되면, THE Research_Engine SHALL Web_Search_Provider 또는 Academic_Search_Provider를 재호출하지 않고 캐시된 정규 결과를 반환한다.
7. THE Research_Engine SHALL 질의 정규화를 멱등하게 구현하여 정규화를 2회 적용한 결과가 1회 적용한 결과와 동일하도록 보장한다(정확성 속성 P11).

### Requirement 5: 딥리서치 멀티스텝 파이프라인

**User Story:** As a 사용자, I want 복잡한 질문에 대해 여러 소스를 조사·종합한 인용 포함 리서치 결과를 받기를, so that 단일 검색으로는 얻기 어려운 깊이 있는 답변을 얻는다.

#### Acceptance Criteria

1. WHEN 딥리서치 도구가 앞뒤 공백을 제거한 뒤 비어 있지 않은 질의와 함께 호출되면, THE Deep_Research_Pipeline SHALL 원 질의를 1개 이상 구성된 상한(예: 최대 8개) 이하의 하위 조사 질의로 분해한다.
2. WHEN 하위 질의가 생성되면, THE Deep_Research_Pipeline SHALL 각 하위 질의에 대해 Web_Search_Provider와 Academic_Search_Provider를 모두 사용하여 검색을 수행한다.
3. WHEN 하위 질의별 검색 결과가 수집되면, THE Deep_Research_Pipeline SHALL 재랭킹 상위 구성된 상한(예: 하위 질의당 상위 5건) 이하의 소스 본문을 Content_Fetcher로 수집한다.
4. WHEN 소스 본문이 수집되면, THE Deep_Research_Pipeline SHALL 수집된 소스에 대해 중복 제거를 수행한다.
5. WHEN 중복 제거가 완료되면, THE Deep_Research_Pipeline SHALL 중복 제거된 소스에 대해 관련성 재랭킹을 수행한다.
6. WHEN 재랭킹된 근거가 확보되면, THE Deep_Research_Pipeline SHALL 인용을 포함한 Research_Report를 생성한다.
7. IF 확보된 고유 소스 수가 Min_Sources 미만이거나 고유 제공자 수가 Min_Providers 미만이거나 미검증 인용 비율이 구성된 임계값(예: 0.2)을 초과하는 조건 중 하나 이상이 참이고 심화 반복 횟수가 Deepening_Cap 미만이면, THEN THE Deep_Research_Pipeline SHALL 추가 하위 질의로 심화 조사를 1회 수행하고 심화 반복 횟수를 1 증가시킨다.
8. THE Deep_Research_Pipeline SHALL 심화 반복 횟수를 Deepening_Cap 이하로 유지하여 유한 종료를 보장한다(정확성 속성 P13).

### Requirement 6: 멀티에이전트 오케스트레이션

**User Story:** As a 아키텍처 담당자, I want 딥리서치가 Coordinator → Planner → Generator → Evaluator 멀티에이전트 패턴으로 실행되기를, so that steering의 필수 아키텍처를 준수하고 단일 에이전트 안티패턴을 배제한다.

#### Acceptance Criteria

1. THE Deep_Research_Pipeline SHALL 딥리서치 실행을 Coordinator, Planner, Generator, Evaluator의 4개 역할로 구성된 멀티에이전트 패턴으로 수행한다.
2. THE Deep_Research_Pipeline SHALL 질의 분해 및 조사 계획 수립에 Planner 역할을 사용한다.
3. THE Deep_Research_Pipeline SHALL 인용 포함 종합 생성에 Generator 역할을 사용한다.
4. THE Deep_Research_Pipeline SHALL Research_Report의 목표 대비 충족 여부 평가에 Evaluator 역할을 사용한다.
5. THE Deep_Research_Pipeline SHALL 모든 딥리서치 실행 경로가 Planner 역할과 Evaluator 역할을 각각 최소 1회 이상 거치도록 보장한다.
6. THE Deep_Research_Pipeline SHALL 기존 LangGraph 오케스트레이션(계획, DAG 웨이브, aggregate 종합, evaluator 재계획, 체크포인트, 세션 간 store)을 재사용한다.

### Requirement 7: 중복 제거 및 재랭킹

**User Story:** As a 사용자, I want 여러 소스에서 온 결과가 중복 없이 관련성 순으로 정렬되기를, so that 근거 집합이 간결하고 상위 근거가 실제로 관련성 높은 순서가 된다.

#### Acceptance Criteria

1. WHEN 다중 제공자·다중 질의 결과가 단일 후보 목록으로 병합되면, THE Deduplicator SHALL 정규 URL 또는 DOI가 동일한 소스를 제거하되 각 정규 URL 또는 DOI에 대해 병합 목록에서 최초로 등장한 소스를 보존한다.
2. THE Deduplicator SHALL 중복 제거 결과의 개수가 입력 개수 이하가 되고 결과의 모든 항목이 입력에 존재하도록(소스 창작 없음) 보장한다(정확성 속성 P2).
3. THE Deduplicator SHALL 중복 제거 결과에 동일한 정규 URL 또는 DOI를 가진 서로 다른 두 소스가 존재하지 않도록 보장한다.
4. THE Deduplicator SHALL 중복 제거를 2회 적용한 결과가 1회 적용한 결과와 동일하도록 보장한다(멱등성 — 정확성 속성 P3).
5. WHEN 병합·중복 제거된 후보를 재랭킹하면, THE Reranker SHALL 기존 RRF, MMR, LLM 리랭커 자산을 재사용한다.
6. THE Reranker SHALL 재랭킹 결과가 입력 소스 집합의 순열이 되도록 하여 소스를 창작하거나 누락하지 않는다(정확성 속성 P4).

### Requirement 8: 인용 및 근거 검증

**User Story:** As a 사용자, I want Research_Report의 사실 주장에 출처가 붙고 그 출처가 실제 근거에 존재하는지 검증되기를, so that 할루시네이션 없이 근거를 신뢰할 수 있다.

#### Acceptance Criteria

1. WHEN Research_Report가 생성되면, THE Generator SHALL 각 사실 주장에 대해 수집된 근거 집합의 소스 식별자를 가리키는 인용을 최소 1개 포함한다.
2. WHEN Research_Report가 생성되면, THE Research_Engine SHALL 각 인용에 대해 참조된 소스 식별자가 수집된 근거 집합에 존재하는지 여부를 검증된/미검증 중 하나로 판정한다.
3. IF 인용이 참조하는 소스 식별자가 수집된 근거 집합에 존재하지 않으면, THEN THE Research_Engine SHALL 해당 인용을 미검증으로 표기하고 Research_Report를 차단 없이 반환한다.
4. THE Research_Engine SHALL 인용 검증과 근거성 판정에 기존 citation 검증, faithfulness, local grounding, grounding gate 자산을 재사용한다.
5. THE Research_Report SHALL 검증된으로 표기된 모든 인용이 근거 집합의 소스 식별자를 참조하도록 하여 참조 무결성을 보장한다(정확성 속성 P6).

### Requirement 9: 최고 품질 티어 측정 기준

**User Story:** As a 리드, I want "최고 품질 티어"를 관련성·최신성·출처 신뢰도·인용 정확도·커버리지·중복제거의 측정 가능한 값으로 규정하기를, so that 품질 주장을 실측으로 검증하고 회귀를 감지한다.

#### Acceptance Criteria

1. THE Research_Engine SHALL golden 질의 세트에 대해 구성된 k(예: k=10)에서의 precision@k와 MRR를 산출하는 평가 하네스를 제공하고 기존 `eval_metrics`를 재사용한다.
2. WHEN 검색 요청에 최신성 창(recency window)이 지정되면, THE Research_Engine SHALL 발행일이 최신성 창 이내인 결과만 남긴 뒤 발행일 내림차순으로 정렬하고, 발행일이 동일하면 관련성 점수 내림차순으로 배치하며, 관련성 점수도 동일하면 제공자가 반환한 순서대로 배치하여 정렬 결과를 결정적으로 유지한다(정확성 속성 P12).
3. IF 최신성 창이 지정된 검색 결과에 발행일이 미상인 항목이 포함되면, THEN THE Research_Engine SHALL 해당 항목을 최신성 창 필터에서 일괄 제외하거나 정렬 결과의 말미에 일괄 배치하는 구성된 단일 규칙으로 처리하여 동일 입력에 대해 항상 동일한 정렬 결과를 산출한다(정확성 속성 P12).
4. THE Research_Engine SHALL 각 소스에 도메인 권위, 게재처, 피인용수 중 하나 이상의 출처 신뢰도 신호를 부여하고 순위 산정 입력으로 사용한다.
5. THE Research_Engine SHALL Research_Report의 미검증 인용 비율(미검증 인용 수를 전체 인용 수로 나눈 값, 전체 인용이 0이면 0)을 0 이상 1 이하의 값으로 산출하여 응답 메타데이터에 포함한다.
6. WHILE 종합 단계 이전이면, THE Deep_Research_Pipeline SHALL 확보된 고유 소스 수가 Min_Sources 이상이고 고유 제공자 수가 Min_Providers 이상이 되도록 근거 확보를 시도하되, 심화 반복 상한 내에서 확보되지 않으면 확보된 근거로 비차단 진행한다.
7. THE Research_Engine SHALL 관련성, 최신성, 출처 신뢰도, 인용 정확도, 커버리지, 중복제거 지표를 각각 수치값으로 산출하고, 어느 지표라도 baseline 대비 구성된 허용 하락폭(예: 5%)을 초과하여 낮아지면 회귀로 판정하는 품질 게이트를 제공한다.

### Requirement 10: 외부 검색 제공자 도입 정책 (Gateway 제약 + 옵트인 플래그)

**User Story:** As a 보안·아키텍처 담당자, I want 모든 LLM 호출은 Gateway 경유를 유지하고 외부 검색은 문서화된 사용자 결정과 옵트인 플래그로만 도입되기를, so that steering의 Gateway-only 정책과 예외 도입 절차를 위반하지 않는다.

#### Acceptance Criteria

1. THE Research_Engine SHALL 모든 LLM·추론 호출을 Bedrock Gateway(SigV4) 경유로 수행한다.
2. WHERE Search_Provider_Flag가 활성이면, THE Research_Engine SHALL 외부 검색·본문 조회를 수행하고, 해당 호출은 Bedrock Gateway 경유 대상이 아닌 데이터 조회로 수행한다.
3. WHERE Search_Provider_Flag가 비활성이면, THE Research_Engine SHALL 외부 검색·본문 조회를 수행하지 않고 기존 로컬 검색 동작만 유지한다.
4. THE Research_Engine SHALL 외부 검색·본문 조회 호출을 지정된 단일 백엔드 모듈에 한정한다.
5. THE Research_Engine SHALL 모델 호출 경로를 Bedrock Gateway 단일 경로로 한정하여 boto3, Anthropic SDK, OpenAI SDK 등 직접 모델 SDK 호출 경로를 포함하지 않는다.

### Requirement 11: 검색 제공자 자격증명 보안

**User Story:** As a 보안 담당자, I want 검색 제공자 API 키가 어떤 파일에도 저장되지 않고 로그에서 마스킹되기를, so that 자격증명 노출 위험을 제거한다.

#### Acceptance Criteria

1. THE Research_Engine SHALL Provider_Credential을 어떤 파일에도 저장하지 않는다.
2. THE Research_Engine SHALL Provider_Credential을 런타임에 환경변수 또는 시크릿으로만 주입받아 사용한다.
3. THE Research_Engine SHALL Provider_Credential 관련 파일을 저장소에 커밋하지 않도록 `.gitignore`로 제외한다.
4. WHEN Research_Engine이 Provider_Credential을 로그에 기록하면, THE Research_Engine SHALL 앞 4자만 남기고 나머지 문자를 마스킹 문자로 대체하여 기록한다.
5. THE Research_Engine SHALL 캐시, 리서치 산출물, 로그 어디에도 Provider_Credential 원문을 포함하지 않는다(정확성 속성 P9).

### Requirement 12: userData 영속

**User Story:** As a 배포 담당자, I want 검색 캐시와 리서치 산출물이 userData 하위에만 저장되기를, so that 데이터 격리와 배포 정책을 준수한다.

#### Acceptance Criteria

1. THE Research_Engine SHALL 검색 캐시를 userData 하위 폴더에만 저장한다.
2. THE Research_Engine SHALL 리서치 산출물(Research_Report, 근거 스냅샷)을 userData 하위 폴더에만 저장한다.
3. THE Research_Engine SHALL 앱 설치 디렉터리나 원격 작업 폴더 등 userData 외부에 검색 캐시나 리서치 산출물을 저장하지 않는다.
4. THE Research_Engine SHALL 기록하는 모든 산출물 경로가 userData 루트 하위가 되도록 보장한다(정확성 속성 P10).

### Requirement 13: 가용성 — 비차단·타임아웃·폴백

**User Story:** As a 시스템 운영자, I want 외부 검색·조회 실패가 그래프 진행을 막지 않기를, so that 외부 API 장애 시에도 리서치가 유한 시간에 안전하게 종료된다.

#### Acceptance Criteria

1. WHEN Research_Engine이 외부 검색 또는 본문 조회를 호출하면, THE Research_Engine SHALL 각 호출에 구성된 개별 타임아웃(예: 호출당 10~15초)을 적용한다.
2. IF 외부 제공자 호출이 실패하거나 구성된 타임아웃을 초과하면, THEN THE Research_Engine SHALL 확보된 부분 결과로 파이프라인을 계속 진행하고 그래프 진행을 차단하지 않는다.
3. IF 모든 외부 제공자 호출이 실패하거나 구성된 타임아웃을 초과하면, THEN THE Research_Engine SHALL 확보된 로컬 검색 결과를 반환하고, 로컬 검색 결과가 없으면 외부 근거를 확보하지 못했음을 나타내는 표시를 포함한 응답으로 비차단 종료한다.
4. THE Research_Engine SHALL 외부 호출 실패 시 예외를 그래프로 전파하지 않고 구조화된 오류 또는 빈 결과로 폴백한다(정확성 속성 P8).

### Requirement 14: 프라이버시 고지 및 동의

**User Story:** As a 사용자, I want 내 질의가 외부 검색 제공자로 전송됨을 사전에 알고 동의 여부를 통제하기를, so that 프라이버시를 스스로 관리한다.

#### Acceptance Criteria

1. WHERE Search_Provider_Flag가 활성이면, THE Research_Engine SHALL 사용자 질의가 외부 검색 제공자로 전송됨을 사용자에게 고지한다.
2. IF 사용자의 외부 검색 전송 동의 설정이 활성 상태가 아니면, THEN THE Research_Engine SHALL 외부 검색을 수행하지 않고 기존 로컬 검색 동작만 유지한다.
3. THE Research_Engine SHALL 외부 전송 대상 제공자와 전송되는 데이터 범위(질의문)를 사용자가 확인 가능하도록 표기한다.

### Requirement 15: 스택 제약

**User Story:** As a 백엔드 개발자, I want 리서치 기능이 고정된 스택 안에서 구현되기를, so that 프로젝트 표준을 유지하고 무거운 신규 의존성을 배제한다.

#### Acceptance Criteria

1. THE Research_Engine SHALL 백엔드를 Python 3.11, FastAPI, HTTPX로 구현한다.
2. THE Research_Engine SHALL 프론트엔드를 Electron과 Vanilla JavaScript로 구현한다.
3. THE Research_Engine SHALL 외부 벡터DB나 신규 대형 런타임을 포함한 무거운 신규 프레임워크를 도입하지 않는다.

### Requirement 16: 기존 자산 재사용

**User Story:** As a 백엔드 개발자, I want 리서치 기능이 기존 검증된 자산을 재사용하기를, so that 중복 구현에 따른 회귀 위험을 피한다.

#### Acceptance Criteria

1. THE Research_Engine SHALL 검색 융합·재랭킹에 기존 HybridSearcher, RRF, MMR, LLM 리랭커를 재사용한다.
2. THE Research_Engine SHALL 근거·인용 검증에 기존 citation 검증, faithfulness, local grounding, grounding gate를 재사용한다.
3. THE Research_Engine SHALL 오케스트레이션에 기존 LangGraph 계획, DAG 웨이브, aggregate, evaluator, 체크포인트, 세션 간 store를 재사용한다.
4. THE Research_Engine SHALL 벡터 저장소와 교체형 임베더를 재사용하며 신규 벡터 인프라를 도입하지 않는다.
5. THE Research_Engine SHALL 리서치를 로컬 파일 소스에서 웹·논문 소스로 확장하는 형태로 기존 검색→재랭크→인용→grounding→종합 파이프라인 위에 구축한다.

### Requirement 17: 도구 통합 정합성

**User Story:** As a 백엔드 개발자, I want 신규 검색·수집 도구가 기존 도구 실행 경로에 정확히 통합되기를, so that research 서브그래프에서 도구 호출·실행·검증이 일관되게 동작한다.

#### Acceptance Criteria

1. THE Research_Engine SHALL 웹 검색, 논문 검색, 본문 수집 도구를 RESEARCH_TOOLS에 추가한다.
2. THE Research_Engine SHALL 신규 도구를 `server.py`의 `_execute_tool` 디스패치에 등록한다.
3. WHEN 신규 도구가 실행되면, THE Gateway_Tool_Node SHALL 도구를 `ainvoke`로 실행하고 각 도구 호출당 정확히 1개의 ToolMessage를 반환한다.
4. THE Research_Engine SHALL 신규 도구의 name을 `_execute_tool` 디스패치와 RESEARCH_TOOLS에서 동일한 문자열로 일치시킨다.
5. WHEN 신규 도구가 파일 산출물(리서치 리포트 등)을 생성하면, THE Gateway_Tool_Node SHALL 디스크에 실제로 존재함이 확인된 항목만 verified_files에 포함한다.

### Requirement 18: 채팅 UI 검색 진행 표시 (Kiro 스타일)

**User Story:** As a 에디터 사용자, I want 웹·논문·딥리서치 검색이 실행되는 동안 채팅창에 검색 진행 표시가 나타나고 완료·실패 시 갱신되기를, so that 에이전트가 외부 검색을 수행 중임을 실시간으로 인지하고 진행 상태를 이해한다.

#### Acceptance Criteria

1. WHEN 웹 검색 도구, 논문 검색 도구, 또는 딥리서치 도구의 실행이 시작되면, THE Research_Engine SHALL 검색 시작을 알리는 Search_Status_Event를 기존 SSE_Event_Channel로 방출하여 채팅 UI의 Search_Indicator를 활성 상태로 표시하게 한다.
2. THE Search_Status_Event SHALL 검색 종류(web, academic, deep 중 하나), 대상 제공자 목록, 질의 요약을 포함하며, Provider_Credential은 포함하지 않는다(정확성 속성 P9).
3. WHEN 검색 도구의 실행이 성공 또는 실패로 종료되면, THE Research_Engine SHALL 종료 Search_Status_Event를 기존 SSE_Event_Channel로 방출하여 Search_Indicator를 완료 상태 또는 해제 상태로 갱신한다.
4. THE Research_Engine SHALL Search_Indicator 렌더링에 기존 SSE_Event_Channel을 재사용하여 CSP 변경을 요구하지 않는다.
5. IF Search_Status_Event의 방출 또는 Search_Indicator의 렌더링이 실패하면, THEN THE Research_Engine SHALL 검색 수행과 답변 생성 진행을 차단하지 않는다(정확성 속성 P8).
6. THE Research_Engine SHALL 각 검색 도구 실행에 대해 시작 Search_Status_Event를 정확히 1회, 종료 Search_Status_Event를 정확히 1회 방출하여 Search_Indicator가 활성 상태로 잔류하지 않도록 보장한다(정확성 속성 P14).
7. WHERE Search_Provider_Flag가 활성이면, THE Research_Engine SHALL Search_Status_Event에 포함되는 대상 제공자 목록과 질의 요약을 요구사항 14의 프라이버시 고지 범위와 정합하게 표기한다.

## 정확성 속성 (Correctness Properties)

아래 속성은 설계·테스트(속성 기반 테스트 포함) 단계의 검증 목표다. 각 속성은 요구사항과
매핑되며, 유형(불변식 / 라운드트립 / 멱등성 / 준동형 / 오류조건 / 유한종료)을 표기한다.

- **P1 (라운드트립, 파서/프린터):** 모든 정규화된 Search_Result·Paper_Result `r`에 대해
  `deserialize(serialize(r))`는 `r`의 정규 필드와 동등하다. 파서(Result_Normalizer)와
  프린터(캐시 직렬화기)의 왕복이 정보를 보존한다. — Req 4
- **P2 (불변식, 중복제거 크기):** `len(dedup(results)) <= len(results)`이며 `dedup` 결과의 모든
  항목은 입력에 존재한다(창작 없음). — Req 7
- **P3 (멱등성, 중복제거):** `dedup(dedup(x)) == dedup(x)`. — Req 7
- **P4 (불변식, 재랭킹 순열):** `rerank(query, sources)`는 입력 `sources`의 순열이다(소스 창작·
  누락 없음). 기존 `parse_rerank_order`의 순열 보장 규약을 계승한다. — Req 7
- **P5 (준동형, 커버리지 단조):** 제공자·소스를 추가로 병합해도 고유 소스 커버리지는 감소하지
  않는다. `len(merge_dedup(A, B)) >= len(dedup(A))`. — Req 5, 9
- **P6 (불변식, 인용 참조 무결성):** Research_Report의 모든 인용은 수집된 근거 집합에 존재하는
  소스 식별자를 참조한다(dangling citation 없음). — Req 8
- **P7 (불변식, 관련성 정렬):** 관련성 정렬 결과는 내림차순이다. 모든 `i < j`에 대해
  `score[i] >= score[j]`. — Req 1, 2
- **P8 (오류조건):** 유효하지 않은 질의, 제공자 호출 실패·시간초과, 형식 오류 응답에 대해
  시스템은 예외를 전파하지 않고 구조화된 오류 또는 빈 결과로 폴백한다. — Req 1, 3, 4, 13, 18
- **P9 (불변식, 자격증명 비노출):** 캐시·리포트·로그 등 모든 영속 산출물에 Provider_Credential
  원문이 포함되지 않는다(로그는 앞 4자 마스킹만 허용). — Req 11
- **P10 (불변식, 영속 경로):** Research_Engine이 기록하는 모든 산출물 경로는 userData 루트
  하위이다. — Req 12
- **P11 (멱등성, 질의 정규화):** `normalize_query(normalize_query(q)) == normalize_query(q)`. — Req 4, 5
- **P12 (준동형, 최신성 필터):** 최신성 창 `W`로 필터한 결과는 모두 `W` 이내의 발행일을 갖는다
  (발행일 미상은 규정된 규칙으로 일관 처리). — Req 9
- **P13 (유한종료, 심화 반복):** 딥리서치 심화 반복 횟수는 Deepening_Cap 이하로 유한 종료한다. — Req 5
- **P14 (불변식, 인디케이터 라이프사이클 무결성):** 모든 검색 도구 실행에 대해 시작
  Search_Status_Event가 정확히 1회 방출된 뒤 종료 Search_Status_Event가 정확히 1회 방출된다
  (고아 시작 없음, 미종료 없음). 따라서 Search_Indicator는 검색 종료 후 활성 상태로
  잔류하지 않는다. — Req 18
