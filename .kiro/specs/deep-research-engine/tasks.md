# Implementation Plan: Deep Research Engine

## Overview

design.md의 모듈 레이아웃(`ai_engine/research/`)과 재사용 우선(재구현 금지)·무회귀 원칙을 그대로
따른다. 구현 순서는 **순수 함수 우선**이다: 정규화(normalize)·중복제거(dedup)·정렬/재랭킹(rank)·
제공자 어댑터(providers)를 먼저 구현·속성검증한 뒤, 그 위에 단일 egress 백엔드(backend)·조회 도구
(축 A)·딥리서치 멀티에이전트 파이프라인(축 B)·인용 검증·품질 게이트·프론트 설정 UI를 쌓고 마지막에
배선한다. 각 단계는 이전 단계 위에 점진적으로 쌓이며 고아 코드가 남지 않도록 마지막에 통합 배선한다.

- **정확성 속성 = 속성 테스트 1:1(P1~P15).** 각 속성 테스트 파일명은 design.md "속성 테스트 목록"
  표를 따르고, 파일 상단에 `# Feature: deep-research-engine, Property N: {속성}` 주석과 본문에
  `**Validates: Requirements X.Y**` 를 포함하며 Hypothesis `@settings(max_examples>=100)` 로
  최소 100회 반복한다(기존 `scripts/test_*_pbt.py` 관례 계승).
- **재사용(재구현 금지):** 검색융합=`rag/hybrid_search.rrf_fuse`, 재랭킹=`rag/reranker.rerank`/
  `parse_rerank_order`+HybridSearcher MMR, 인용=`rag/citation.verify_citations`/`parse_citations`,
  근거성=`rag/answer_quality`+`agent_system/grounding_gate`, 지표=`rag/eval_metrics`(precision@k·
  MRR·recall@k), 오케스트레이션=`agent_system/supervisor`(`_make_plan`/`make_model_node`/
  `aggregate_node`/`make_evaluator_node`)·`dag`(`sanitize_depends_on`/`topological_waves`)·
  `checkpoint_store.JsonFileCheckpointSaver`/`store`, 도구 실행=`nodes/tool_node.GatewayToolNode`.
- **스택 고정:** Python 3.11 / FastAPI / HTTPX 백엔드, Electron + Vanilla JS 프론트. 신규 무거운
  프레임워크·외부 벡터DB 금지. 본문 추출은 기존 의존성 `lxml` 재사용.

## Tasks

- [x] 1. 프로젝트 구조 및 데이터 모델
  - [x] 1.1 `ai_engine/research/` 패키지와 정규 데이터 모델 정의
    - `research/__init__.py` 생성, `research/models.py` 에 `SearchResult`(6+계측 필드), `PaperResult`
      (7+계측 필드), `EvidenceSource`, `ResearchMetrics`, `ResearchReport` dataclass 정의
    - 누락 텍스트 필드 기본값 `""`, 누락 수치 필드는 정렬 가능한 기본값(`0`/`0.0`), `source_id`
      스킴(`web:<canonical_url>` / `doi:<canonical_doi>`) 포함
    - _Requirements: 1.2, 2.2, 5.6, 15.1_

  - [x] 1.2 리서치 설정 로더(`research/config.py`) 구현
    - design.md 환경변수 표를 `DeepResearchConfig`로 매핑(`AE_ENABLE_WEB_RESEARCH`,
      `AE_RESEARCH_CONSENT`, `AE_RESEARCH_*_PROVIDERS`, `AE_RESEARCH_TOPK/MAX_SUBQUERIES/
      FETCH_PER_SUBQUERY`, `AE_MAX_DEEPENING`, `AE_RESEARCH_MIN_SOURCES/MIN_PROVIDERS`,
      `AE_RESEARCH_UNVERIFIED_THRESHOLD`, `AE_SEARCH_TIMEOUT`, `AE_FETCH_TIMEOUT/MAX_CHARS`,
      `AE_RESEARCH_CACHE_TTL`, `AE_RESEARCH_RECENCY_UNKNOWN`, `AE_RESEARCH_REGRESSION_TOLERANCE`)
    - env 미설정 시 표의 기본값 사용, 자격증명 키는 config에 값 저장 금지(이름만)
    - _Requirements: 10.2, 13.1, 14.2_

- [x] 2. 순수 정규화·직렬화 (`research/normalize.py`)
  - [x] 2.1 `normalize_query` 구현
    - NFKC → 소문자 → NFKC(재정규화) → 공백 축약/trim(외부 상태 비의존 순수 함수).
      소문자화가 도입하는 비정규 결합 순서를 소문자화 이후 NFKC로 되돌려 NFKC 고정점을
      만들어 멱등성(P11) 보장
    - _Requirements: 4.7, 5.1_

  - [x]* 2.2 `normalize_query` 멱등성 속성 테스트
    - **Property 11: 질의 정규화 멱등성** — `scripts/test_research_query_normalize_idempotent_pbt.py`
    - 공백/대소문자/유니코드/제어문자 포함 임의 질의 생성기로 `normalize_query(normalize_query(q)) == normalize_query(q)` 검증
    - **Validates: Requirements 4.7, 5**

  - [x] 2.3 `Result_Normalizer` 파서(`parse_search_result`/`parse_paper_result`) 구현
    - 원시 항목 dict → 정규 스키마 순수 변환, 추출 불가 필드는 예외 없이 기본값 채움(부분 파싱)
    - _Requirements: 4.1, 4.3, 4.5, 1.3, 2.6_

  - [x] 2.4 캐시 직렬화기/역직렬화기(`serialize_result`/`deserialize_result`) 구현
    - 모든 정규 필드를 JSON-호환 타입으로 저장/복원(발행일 ISO 문자열, 점수 float 등)
    - _Requirements: 4.2, 4.4_

  - [x]* 2.5 직렬화 라운드트립 속성 테스트
    - **Property 1: 정규화 결과 직렬화 라운드트립** — `scripts/test_research_normalize_roundtrip_pbt.py`
    - 임의 정규 `SearchResult`/`PaperResult` 생성 → `deserialize_result(serialize_result(r))` 가 `r`의 정규 필드와 동등
    - **Validates: Requirements 4.4**

- [x] 3. 제공자 어댑터 (`research/providers.py`, 순수 매핑 — egress 없음)
  - [x] 3.1 웹 검색 어댑터 구현
    - Tavily/Exa/Brave 원시 응답 → `SearchResult` 매핑(design.md 매핑 표), Brave는 순위 파생
      점수 `1/(rank+1)`, `source_domain` 은 urlparse 파생, `WebSearchAdapter` 프로토콜 준수
    - _Requirements: 1.2, 4.1_

  - [x] 3.2 학술 검색 어댑터 구현
    - Semantic Scholar/OpenAlex(+arXiv/PubMed) 원시 응답 → `PaperResult` 매핑(매핑 표),
      OpenAlex `abstract_inverted_index` 재구성, `AcademicSearchAdapter` 프로토콜 준수
    - _Requirements: 2.2, 4.1_

  - [x]* 3.3 어댑터 매핑 단위 테스트
    - 대표 원시 응답 샘플로 필드 매핑 정확성 + 부분 필드 누락 시 기본값 채움 검증
    - _Requirements: 1.3, 2.6_

- [x] 4. 중복제거 (`research/dedup.py`, 순수)
  - [x] 4.1 `canonical_url`/`canonical_doi`/`source_key`/`dedup_sources` 구현
    - URL 정규화(스킴·호스트 소문자, 기본 포트/추적 쿼리/fragment 제거, 말미 슬래시 정리), DOI
      정규화(소문자·`https://doi.org/` 프리픽스 제거), DOI 우선 키, first-wins 보존
    - _Requirements: 7.1, 7.2, 7.3_

  - [x]* 4.2 중복제거 불변식 속성 테스트
    - **Property 2: 중복제거 크기·부분집합 불변식** — `scripts/test_research_dedup_invariants_pbt.py`
    - 중복 포함 임의 소스 → 크기 ≤ 입력, 결과 ⊆ 입력, 키 유일성, first-wins 검증
    - **Validates: Requirements 7.1, 7.2, 7.3**

  - [x]* 4.3 중복제거 멱등성 속성 테스트
    - **Property 3: 중복제거 멱등성** — `scripts/test_research_dedup_idempotent_pbt.py`
    - `dedup_sources(dedup_sources(xs)) == dedup_sources(xs)` 검증
    - **Validates: Requirements 7.4**

  - [x]* 4.4 커버리지 단조성 속성 테스트
    - **Property 5: 커버리지 단조성(준동형)** — `scripts/test_research_coverage_monotonic_pbt.py`
    - 임의 `A`,`B` → `len(dedup_sources(A + B)) >= len(dedup_sources(A))` 검증
    - **Validates: Requirements 5, 9**

- [x] 5. 정렬·최신성·재랭킹 (`research/rank.py`, 기존 자산 조합)
  - [x] 5.1 관련성 정렬(`sort_by_relevance_web`/`sort_by_relevance_papers`) 구현
    - 관련성 내림차순 안정 정렬, 웹 동점=제공자 순서 보존, 논문 동점=피인용수 내림차순→제공자 순서(결정적)
    - _Requirements: 1.4, 1.5, 2.3, 2.4_

  - [x]* 5.2 관련성 정렬 속성 테스트
    - **Property 7: 관련성 정렬(결정적 내림차순)** — `scripts/test_research_relevance_sort_pbt.py`
    - 동점 다수 포함 임의 리스트 → 내림차순 + tie-break 결정성(재정렬 안정) 검증
    - **Validates: Requirements 1.4, 1.5, 2.3, 2.4**

  - [x] 5.3 최신성 필터(`apply_recency`) 구현
    - 최신성 창 필터 + 발행일 내림차순(동점: 관련성→제공자순), 발행일 미상은 단일 규칙
      (`exclude`|`last`, `AE_RESEARCH_RECENCY_UNKNOWN`)으로 일관 처리
    - _Requirements: 9.2, 9.3_

  - [x]* 5.4 최신성 필터 속성 테스트
    - **Property 12: 최신성 필터(준동형)** — `scripts/test_research_recency_filter_pbt.py`
    - 발행일 미상/경계값 포함 임의 결과 → 창 이내 보장 + 동일 입력 동일 정렬(결정성) 검증
    - **Validates: Requirements 9.2, 9.3**

  - [x] 5.5 융합·재랭킹(`merge_and_rerank`) + 출처 신뢰도(`source_authority`) 구현
    - 다중 제공자 순위 리스트를 `rag/hybrid_search.rrf_fuse`로 융합 → `dedup_sources` → (opt)
      `rag/reranker.rerank`/`parse_rerank_order`·HybridSearcher MMR 재정렬(재구현 금지),
      도메인 권위/게재처/피인용수로 신뢰도 신호 산출해 순위 입력에 반영
    - _Requirements: 7.5, 7.6, 9.4, 16.1_

  - [x]* 5.6 재랭킹 순열 속성 테스트
    - **Property 4: 재랭킹 순열 불변식** — `scripts/test_research_rerank_permutation_pbt.py`
    - 범위밖/중복/누락 인덱스를 내는 리랭커 mock으로도 결과가 입력 소스의 순열임을 검증(`parse_rerank_order` 순열 규약 계승)
    - **Validates: Requirements 7.5, 7.6**

- [x] 6. 체크포인트 — 순수 함수 계층 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. 자격증명 보안 (`research/security.py`)
  - [x] 7.1 `load_credential`(env 전용) + `mask_secret` 구현
    - 자격증명은 env/시크릿에서만 로딩(파일 미저장·메모리 유지), 로그용 마스킹은 앞 4자 + `****`
    - _Requirements: 11.1, 11.2, 11.4_

  - [x]* 7.2 자격증명 비노출 속성 테스트
    - **Property 9: 자격증명 비노출** — `scripts/test_research_credential_masking_pbt.py`
    - 임의 키 주입 직렬화/로그 산출 → 원문 부재, `mask_secret` 규칙(앞 4자만 잔존) 성립 검증
    - **Validates: Requirements 11.1, 11.4, 11.5**

  - [x] 7.3 자격증명 관련 파일 `.gitignore` 제외 등록
    - 제공자 키/시크릿 파일 패턴을 저장소 `.gitignore`에 추가(커밋 방지)
    - _Requirements: 11.3_

- [x] 8. userData 캐시 (`research/cache.py`)
  - [x] 8.1 경로 가드 + 캐시 read/write 구현
    - `userData/research_cache/{sha1(normalize_query(q)+provider_set)}.json`, TTL 검사,
      `serialize_result`/`deserialize_result` 사용, 경로는 userData 루트 하위로 정규화(`..`·절대경로 차단),
      캐시 엔트리에 자격증명 원문 미포함
    - _Requirements: 4.6, 12.1, 12.2, 12.3_

  - [x]* 8.2 영속 경로 불변식 속성 테스트
    - **Property 10: 영속 경로 불변식** — `scripts/test_research_userdata_path_pbt.py`
    - `..`/절대경로/특수문자 포함 임의 파일명·질의 키 → 산출 경로가 항상 userData 루트 하위임을 검증
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.4**

  - [x]* 8.3 캐시 히트 단위 테스트
    - TTL 이내 동일 정규화 질의 재요청 시 제공자 재호출 0(캐시 반환) 검증
    - _Requirements: 4.6_

- [x] 9. 단일 외부 egress 백엔드 (`research/backend.py`)
  - [x] 9.1 옵트인/동의 게이트(`web_research_enabled`) 구현
    - `AE_ENABLE_WEB_RESEARCH` + `AE_RESEARCH_CONSENT` 모두 참일 때만 True, False면 egress 함수는
      네트워크 미호출·즉시 로컬/빈 결과 폴백(무회귀 경로)
    - _Requirements: 10.2, 10.3, 14.2_

  - [x]* 9.2 옵트인 게이트 무회귀 속성 테스트
    - **Property 15: 옵트인 게이트 무회귀** — `scripts/test_research_optin_gate_pbt.py`
    - 플래그/동의 off + 임의 질의 → egress mock 호출 카운트 0(외부 미도입 상태와 동등) 검증
    - **Validates: Requirements 10.3, 10.5, 14.2**

  - [x] 9.3 웹/논문 검색 egress(`web_search_raw`/`academic_search_raw`) 구현
    - HTTPX 클라이언트 소유(유일 egress 지점), 제공자별 개별 타임아웃(`AE_SEARCH_TIMEOUT`),
      `security.load_credential` 주입·마스킹 로그, 예외/타임아웃/4xx·5xx/JSON 오류는 구조화 오류 dict로 폴백
    - _Requirements: 1.1, 2.1, 3.1, 10.4, 13.1, 13.2_

  - [x] 9.4 본문 수집 egress(`fetch_url_raw`, Content_Fetcher) 구현
    - http/https 스킴만, 개별 타임아웃(`AE_FETCH_TIMEOUT`), 크기 상한(`AE_FETCH_MAX_CHARS`),
      `lxml`로 HTML 본문 추출(Python 백엔드 수행 — CSP 변경 불필요), 실패 소스는 `ok=False`로 제외(예외 없음)
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 10.4_

  - [x] 9.5 입력 검증 + 폴백 체인 구현
    - 질의 `strip()` 비어있지 않음·최대 길이(2,048자) 검증 후 위반 시 제공자 미호출·구조화 오류 반환,
      제공자 폴백 체인(1차→보조→폴백→로컬 검색→"외부 근거 미확보" 비차단 종료)
    - _Requirements: 1.8, 13.3_

  - [x]* 9.6 오류조건 비차단 폴백 속성 테스트
    - **Property 8: 오류조건 비차단 폴백** — `scripts/test_research_error_conditions_pbt.py`
    - 임의 실패 유형·부분 응답·불량 질의(빈/공백/과길이) → 예외 없음, 구조화 오류/빈 결과/부분 정규 결과 검증
    - **Validates: Requirements 1.3, 1.8, 2.6, 3.5, 4.5, 8.3, 13.2, 13.3, 13.4**

- [x] 10. 조회 도구 통합 (축 A — RESEARCH_TOOLS + _execute_tool)
  - [x] 10.1 `web_search`/`search_papers`/`fetch_content` 도구를 RESEARCH_TOOLS에 추가
    - `subgraphs/research.py`의 `RESEARCH_TOOLS`에 고유 name 도구 스키마 추가(기존 read_file/search_files 유지),
      각 도구는 `backend`+`normalize`+`rank`(fetch는 `backend.fetch_url_raw`) 경로로 조립
    - _Requirements: 1.6, 2.5, 17.1_

  - [x] 10.2 `server._execute_tool` 디스패치 등록
    - `server.py`의 `_execute_tool`에 3개 도구 분기 추가, name을 RESEARCH_TOOLS와 동일 문자열로 일치,
      결과 JSON 문자열 반환(`GatewayToolNode`가 `ainvoke`로 호출당 ToolMessage 1개 생성)
    - _Requirements: 17.2, 17.3, 17.4_

  - [x]* 10.3 도구 등록·name 정합·디스패치 단위 테스트
    - RESEARCH_TOOLS name 유일성, `_execute_tool` 디스패치 인지, 호출당 ToolMessage 1개 검증
    - _Requirements: 1.6, 1.7, 2.5, 17.1, 17.4_

- [x] 11. 체크포인트 — egress·조회 도구(축 A) 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 12. 딥리서치 멀티에이전트 파이프라인 (`research/deep_research.py`, 축 B)
  - [x] 12.1 Planner: 질의 분해 + 웨이브 스케줄 구현
    - `supervisor._make_plan`과 동형의 Gateway 분해(원 질의 → ≤`AE_RESEARCH_MAX_SUBQUERIES` 하위질의,
      `depends_on`), `dag.sanitize_depends_on`/`topological_waves`로 웨이브 분할(재구현 금지)
    - _Requirements: 5.1, 6.2, 6.6, 16.3_

  - [x] 12.2 Wave 실행: 다중 소스 검색→정규화→융합→dedup→재랭킹→본문 수집
    - 하위질의별 웹+논문 검색 → `Result_Normalizer` 정규화 → `merge_and_rerank`(RRF+dedup+rerank)
      → 상위 K(`AE_RESEARCH_FETCH_PER_SUBQUERY`) `fetch_content`, 선행 Wave 근거를 후속 컨텍스트로 사용
    - _Requirements: 5.2, 5.3, 5.4, 5.5_

  - [x] 12.3 Generator: 인용 포함 Research_Report 종합
    - research 도메인 model 노드 패턴(`make_model_node`, GatewayChatModel) + `aggregate_node` 동형 종합,
      각 사실 주장에 근거 집합 소스 식별자 인용 최소 1개 포함(모든 LLM 호출 Gateway 경유)
    - _Requirements: 5.6, 6.3, 8.1, 10.1_

  - [x] 12.4 Evaluator + 심화 루프(should_deepen) 구현
    - 커버리지 지표(고유 소스 수<Min_Sources, 고유 제공자 수<Min_Providers, 미검증 인용 비율>임계)로
      심화 판정, `deepening_count < Deepening_Cap`(`AE_MAX_DEEPENING`)에서만 재계획(monotonic 카운터)
    - _Requirements: 5.7, 5.8, 6.4, 9.6_

  - [x]* 12.5 심화 유한 종료 속성 테스트
    - **Property 13: 심화 반복 유한 종료** — `scripts/test_research_deepening_cap_pbt.py`
    - 임의 (커버리지 지표, deepening_count) → cap 도달 시 `should_deepen` False, mock 파이프라인 심화 횟수 ≤ cap 검증
    - **Validates: Requirements 5.7, 5.8, 9.6**

  - [x] 12.6 Coordinator 진입점(`run_deep_research`) + 체크포인트/스토어 + 실행 seam
    - Planner→Wave→Generator→Evaluator 조율, `JsonFileCheckpointSaver`/`JsonFileStore` base_dir를
      userData 하위로 재사용, `retrieval_pipeline` 방식의 "실행 중 루프면 별도 스레드+독립 루프"로
      async 파이프라인 실행(이벤트 루프 내 `asyncio.run` 회피), 리포트/근거 스냅샷 userData 하위 저장
    - _Requirements: 6.1, 6.5, 6.6, 12.2_

- [x] 13. 인용·근거 검증 통합 (기존 자산 재사용)
  - [x] 13.1 소스 식별자 스킴 + `verify_citations` 소스 id 대조 구현
    - 각 외부 소스에 안정적 `source_id`(`web:<canonical_url>`/`doi:<canonical_doi>`) 부여, 리포트
      인용을 `rag/citation.parse_citations`/`verify_citations`로 근거 소스 id 집합과 대조(재구현 금지),
      dangling citation은 미검증 표기하되 차단하지 않음
    - _Requirements: 8.2, 8.3, 8.4, 16.2_

  - [x] 13.2 미검증 인용 비율 산출 + 근거성 메타데이터 병합
    - 미검증 인용 비율 [0,1] 산출(전체 인용 0이면 0), `rag/answer_quality.enhance_answer`
      (`local_grounding_score`+faithfulness)·`grounding_gate` 재사용, 응답 메타데이터/`ResearchMetrics`에 반영
    - _Requirements: 9.5, 8.4_

  - [x]* 13.3 인용 참조 무결성 속성 테스트
    - **Property 6: 인용 참조 무결성** — `scripts/test_research_citation_integrity_pbt.py`
    - 임의 인용/근거 id 집합 → verified ⊆ 근거 id 집합, verified∪unverified == 전체 인용(분류 누락 없음) 검증
    - **Validates: Requirements 8.2, 8.5**

- [x] 14. 딥리서치 도구 통합 + verified_files
  - [x] 14.1 `deep_research` 도구를 RESEARCH_TOOLS + _execute_tool에 등록
    - `subgraphs/research.py`에 `deep_research` 도구 스키마 추가, `server._execute_tool`에서
      `run_deep_research`로 디스패치(name 정합), 리포트 파일 산출을 `GatewayToolNode`가 디스크 실측 후
      verified_files에 포함
    - _Requirements: 17.1, 17.2, 17.4, 17.5_

  - [x]* 14.2 딥리서치 e2e 통합 테스트(mock 제공자)
    - mock 제공자로 분해→검색→종합 1건 실행, Planner·Evaluator 최소 1회 경유·비차단 종료 검증
    - _Requirements: 5, 6_

- [x] 15. 체크포인트 — 딥리서치 파이프라인(축 B) 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. 품질 평가 하네스 + baseline 회귀 게이트 (`research/eval_harness.py`)
  - [x] 16.1 평가 하네스 구현(지표 산출)
    - golden 질의 세트에 대해 precision@k·MRR·최신성·출처 신뢰도·인용 정확도(1−미검증비율)·커버리지
      (고유 소스/제공자)·중복제거(감소율) 산출, precision@k·MRR·recall@k는 `rag/eval_metrics` 재사용(재구현 금지)
    - _Requirements: 9.1, 9.7, 16.1_

  - [x] 16.2 회귀 게이트 + golden/baseline 스냅샷 + 실행 스크립트
    - 각 지표가 baseline 대비 허용 하락폭(`AE_RESEARCH_REGRESSION_TOLERANCE`) 초과 하락 시 회귀 판정,
      `scripts/golden_research.json` 스냅샷 + `scripts/eval_research_quality.py` 재현 실행기
    - _Requirements: 9.7_

  - [x]* 16.3 회귀 판정 단위 테스트
    - baseline 대비 하락 케이스로 회귀 판정 True, 허용 범위 내는 False 검증
    - _Requirements: 9.7_

- [x] 17. 프론트엔드 설정·동의 UI (Electron + Vanilla JS)
  - [x] 17.1 외부 리서치 설정 UI 컴포넌트 구현
    - 옵트인 토글(→`AE_ENABLE_WEB_RESEARCH` 상당)·웹/논문 제공자 선택·프라이버시 동의 체크,
      `userData/settings/settings.json`에 프로파일/플래그만 저장(자격증명 미저장), Web Component + CustomEvent
    - _Requirements: 14.1, 14.3, 15.2_

  - [x] 17.2 프라이버시 고지 + 리서치 진행/출처/인용 렌더
    - 옵트인 활성 시 "질의가 선택된 외부 제공자로 전송됨"과 데이터 범위(질의문) 고지, 진행·출처·인용·
      미검증 표시는 기존 SSE 이벤트 채널로 렌더(신규 CSP 불필요)
    - _Requirements: 14.1, 14.3_

  - [x]* 17.3 설정 UI Playwright 테스트
    - webapp-testing: 토글/동의/제공자 선택 상호작용 및 미동의 시 로컬 전용 표기 검증
    - _Requirements: 14.1, 14.3_

- [x] 18. 최종 통합 배선 및 아키텍처 가드
  - [x] 18.1 패키지 공개 API 배선 + 설정 플래그 흐름 연결
    - `research/__init__.py` 공개 심볼 export, 프론트 settings.json 플래그 → server → `backend.web_research_enabled`
      게이트까지 흐름 연결 확인, 4개 도구(`web_search`/`search_papers`/`fetch_content`/`deep_research`)
      전 경로 배선(고아 코드 없음)
    - _Requirements: 10.2, 10.3, 14.2, 17.2_

  - [x]* 18.2 정적 가드 테스트(Gateway-only / 단일 egress / 의존성 제약)
    - `research/` 패키지에 `boto3`/`anthropic`/`openai` 직접 import 부재, 외부 `httpx` egress가 `backend.py`
      에만 존재, 신규 의존성이 `httpx`/`lxml`만이고 외부 벡터DB 미도입임을 import 스캔으로 검증
      (`test_native_layout_render_units.py` forbidden import 패턴 재사용)
    - _Requirements: 10.1, 10.4, 10.5, 15.3_

  - [x]* 18.3 기존 자산 재사용 스모크 테스트
    - `rrf_fuse`/`reranker`/`citation`/`answer_quality`/`grounding_gate`/`eval_metrics`/`dag`/
      `checkpoint_store`/`store` import·호출 경로 확인(재구현 부재)
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5_

- [x] 19. 최종 체크포인트 — 전체 검증
  - Ensure all tests pass, ask the user if questions arise. 품질 게이트(`eval_research_quality.py`) 실행으로 회귀 부재 확인.

- [x] 20. 검색 진행 인디케이터 (Requirement 18, Kiro 스타일)
  - [x] 20.1 GatewayToolNode 검색 상태 방출 (`ai_engine/agent_system/nodes/tool_node.py`)
    - `_SEARCH_TOOLS={web_search,search_papers,deep_research}` 실행 경계에서 `adispatch_custom_event("search_status", ...)` start 1회 + try/finally의 finally에서 end 1회 방출
    - 페이로드: phase/kind/providers(이름만)/query_summary(`AE_RESEARCH_QUERY_SUMMARY_MAX` 절단)/status. 자격증명 미포함. 방출은 try/except로 감싸 실패해도 비차단
    - _Requirements: 18.1, 18.2, 18.3, 18.5, 18.6_ _Property: P14, P9, P8_

  - [x] 20.2 sse_bridge searchStatus 중계 (`ai_engine/agent_system/sse_bridge.py`)
    - `ALLOWED_EVENT_KEYS`에 `"searchStatus"` 추가, `on_custom_event(name="search_status")` → `{searchStatus: data}` 매핑. 기존 이벤트 계약 보존(무회귀), CSP 변경 없음
    - `scripts/test_sse_key_subset_pbt.py`의 예상 emit 키 집합 갱신(부분집합 불변식 유지)
    - _Requirements: 18.4_

  - [x] 20.3 Search_Indicator Web Component (`src/components/search-indicator.js`, 신규)
    - Vanilla JS `customElements.define`, shadow DOM 미사용, VS Code 다크 미학·스피너 마이크로 인터랙션, CustomEvent
    - phase=start→활성("웹 검색 중…·제공자·질의 요약", kind별 라벨) / end(ok)→해제 / end(error)→해제
    - _Requirements: 18.1, 18.3_

  - [x] 20.4 프론트 SSE 배선 (`src/main.js`, `src/center-views.js`)
    - `main.js` readSSEStream에 searchStatus 분기 추가, `center-views.js`에서 스트림 시작 시 `<search-indicator>` 마운트·onSearchStatus 연결. 렌더 실패 비차단
    - _Requirements: 18.1, 18.3, 18.4, 18.5_

  - [x]* 20.5 인디케이터 라이프사이클 속성 테스트
    - **Property 14: 인디케이터 라이프사이클 무결성** — `scripts/test_research_search_indicator_lifecycle_pbt.py`
    - 임의 검색 도구 시퀀스·임의 종료 유형(성공/타임아웃/예외)에 대해 실행마다 (start,end)=(1,1), 순서 start→end 검증
    - **Validates: Requirements 18.1, 18.3, 18.6**

  - [x]* 20.6 인디케이터 UI Playwright 테스트
    - webapp-testing: start→활성/end→해제 전환, 제공자·질의 요약 표기 범위(프라이버시 정합) 검증
    - _Requirements: 18.1, 18.3, 18.7_

## Notes

- `*` 표시 하위 작업은 선택(테스트: 속성/단위/통합/정적/스모크)이며 빠른 MVP에서 건너뛸 수 있다. 최상위 작업에는 `*`를 붙이지 않는다.
- 정확성 속성 P1~P15는 각각 단일 property-based 테스트(Hypothesis, 최소 100회)로 구현하며, 파일 상단 `# Feature: deep-research-engine, Property N: ...` 주석 + 본문 `**Validates: Requirements X.Y**` 규약을 따른다.
- 검색 진행 인디케이터(작업 20, P14)는 기존 SSE 이벤트(`searchStatus`) 재사용으로 구현하며, 각 검색 도구 실행마다 시작·종료를 각 1회 방출해 인디케이터 잔류를 방지한다(신규 CSP·채널 불필요).
- 순수 함수(normalize/dedup/rank/providers)를 먼저 구현·검증한 뒤 egress(backend)·파이프라인(deep_research)을 쌓는다.
- 재사용(재구현 금지): rrf_fuse/reranker/citation/answer_quality/grounding_gate/eval_metrics/dag/checkpoint_store/store를 그대로 사용한다.
- 옵트인 무회귀(P15)·자격증명 보안(P9)·userData 경로(P10)는 명시적 작업(9.1/9.2*, 7.1/7.2*, 8.1/8.2*)으로 포함했다.
- 체크포인트(6/11/15/19)에서 계층별 테스트 통과를 확인하고 진행한다.
- 모든 LLM·추론 호출은 Bedrock Gateway 경유를 유지하고, 외부 검색·본문 조회 egress는 `research/backend.py` 단일 모듈에 한정한다.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "7.3"] },
    { "id": 1, "tasks": ["2.1", "3.1", "4.1", "5.1", "7.1", "17.1", "17.2", "20.2", "20.3"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.2", "4.2", "4.3", "4.4", "5.2", "5.3", "7.2", "9.1", "17.3", "20.4"] },
    { "id": 3, "tasks": ["2.4", "3.3", "5.4", "5.5", "9.2", "9.3", "20.6"] },
    { "id": 4, "tasks": ["2.5", "5.6", "8.1", "9.4"] },
    { "id": 5, "tasks": ["8.2", "8.3", "9.5"] },
    { "id": 6, "tasks": ["9.6", "10.1", "10.2", "12.1"] },
    { "id": 7, "tasks": ["10.3", "12.2", "20.1"] },
    { "id": 8, "tasks": ["12.3", "20.5"] },
    { "id": 9, "tasks": ["12.4"] },
    { "id": 10, "tasks": ["12.5", "12.6"] },
    { "id": 11, "tasks": ["13.1"] },
    { "id": 12, "tasks": ["13.2"] },
    { "id": 13, "tasks": ["13.3", "14.1", "16.1"] },
    { "id": 14, "tasks": ["14.2", "16.2"] },
    { "id": 15, "tasks": ["16.3", "18.1"] },
    { "id": 16, "tasks": ["18.2", "18.3"] }
  ]
}
```
