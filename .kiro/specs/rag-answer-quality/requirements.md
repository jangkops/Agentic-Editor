# Requirements Document

RAG Answer Quality (Ultra Production)

## Introduction

에디터의 AI 응답 품질을 "울트라 프로덕션급"으로 끌어올린다. 목표는 두 가지다: (1) 할루시네이션(근거 없는 서술)을 구조적으로 억제하고, (2) 검색 정밀도와 모델 활용을 극대화해 정답률을 높인다.

본 스펙은 현재 구현(LangGraph 계열 멀티에이전트, 단일/병렬/합의 호출, role+skills.md, **로컬 TF-IDF 벡터 + BM25 하이브리드 RAG**, 오프라인·사용자별 배포)을 전제로 한다. 신규 무거운 의존성(onnxruntime/torch)이나 게이트웨이 임베딩 권한에 의존하지 않는 개선을 우선하고, 시맨틱 임베딩은 교체 가능한(pluggable) 후단계로 둔다.

핵심 제약(보존):
- LLM/추론 호출은 Bedrock Gateway(SigV4+assume-role) 경유만 유지. 이미지 생성만 Vertex 예외.
- 자격증명은 어떤 파일에도 저장하지 않는다.
- 오프라인·사용자별 로컬 인덱스/벡터 구조 유지(외부 벡터DB 도입 금지).
- 기존 25명 인증 흐름·Monaco·터미널·remote SSH·PPTX 파이프라인 무변경.

## Glossary

- **Grounding(근거)**: 응답의 사실 주장이 검색된 컨텍스트 또는 도구 실행 결과로 뒷받침되는 성질.
- **Faithfulness(충실도)**: 응답이 제공된 근거와 모순되지 않고 그 범위를 벗어나 지어내지 않는 정도(0~1).
- **RRF (Reciprocal Rank Fusion)**: 여러 검색기의 순위를 점수 스케일에 무관하게 순위 기반으로 융합하는 기법.
- **Reranker**: 후보 청크를 쿼리 관련성 기준으로 재정렬하는 2차 정렬기. 본 스펙에서는 별도 모델 없이 LLM(경량 모델)로 수행.
- **HyDE (Hypothetical Document Embeddings)**: 쿼리로 가상의 정답을 먼저 생성해 그 텍스트로 검색 recall을 높이는 기법.
- **EmbeddingProvider**: 임베딩 생성을 추상화한 인터페이스. TF-IDF / 게이트웨이 Titan / 로컬 ONNX 구현을 교체 가능.
- **Faithfulness Verifier**: 응답과 근거를 대조해 충실도를 채점하고, 임계 미만이면 교정 재생성을 트리거하는 노드.
- **Golden set**: 평가용 질의-정답(및 근거 파일) 모음.

## Requirements

### Requirement 1: 근거 기반 응답 계약 (Anti-Hallucination 프롬프트)

**User Story:** 사용자로서, 모델이 모르는 것을 지어내지 않고 제공된 근거 안에서만 답하기를 원한다. 그래야 답변을 신뢰할 수 있다.

#### Acceptance Criteria
1. THE 시스템 SHALL 시스템 프롬프트에서 "거부 표현 절대 금지"류의 지시를 제거한다.
2. THE 시스템 SHALL 시스템 프롬프트에 "제공된 컨텍스트와 도구 실행 결과에 근거해서만 사실을 서술하라"는 근거 계약을 포함한다.
3. WHEN 검색/도구로 확보한 근거가 질의에 답하기 불충분한 경우, THE 시스템 SHALL 모델이 불확실성을 명시하거나(예: "확인 불가") 도구로 추가 확인을 시도하도록 지시한다.
4. THE 시스템 SHALL 도구 사용(read_file/run_command 등) 지침과 미디어 생성 지침은 보존한다(기존 기능 회귀 금지).
5. THE 시스템 SHALL 근거 계약을 기존 `build_system_prompt`가 생성하는 프롬프트에 통합하며, project_path가 없는(RAG 미적용) 경로에서도 근거 계약 문구는 유지한다.

### Requirement 2: 인용 강제 및 인용 검증

**User Story:** 사용자로서, 사실 주장에 출처(파일:라인)가 붙고 그 출처가 실제로 존재하는지 검증되기를 원한다.

#### Acceptance Criteria
1. WHERE RAG 컨텍스트가 주입된 코드 관련 질의인 경우, THE 시스템 SHALL 응답의 사실 주장에 대해 `파일경로:시작-끝라인` 형식의 인용을 요구한다.
2. THE 시스템 SHALL 응답에 포함된 인용이 실제 검색 결과(제공된 청크)의 파일/라인 범위와 일치하는지 사후 검증한다.
3. IF 인용이 검색 결과에 존재하지 않으면(허위 인용), THEN THE 시스템 SHALL 해당 인용을 플래그하고 응답 메타데이터에 `unverified_citations` 목록으로 표기한다.
4. THE 시스템 SHALL 인용 검증을 순수 함수로 구현하여 단위 테스트가 가능하게 한다(LLM 호출 없이 문자열/범위 대조).
5. THE 시스템 SHALL 인용이 하나도 없거나 전부 미검증인 경우에도 응답 자체는 차단하지 않고 메타데이터로만 표기한다(가용성 우선).

### Requirement 3: 충실도 검증 노드 (Faithfulness Verifier)

**User Story:** 사용자로서, 최종 응답이 근거와 모순되지 않는지 자동으로 점검되고, 문제가 있으면 교정되기를 원한다.

#### Acceptance Criteria
1. THE 시스템 SHALL 응답 생성 후 응답과 검색 근거를 입력으로 충실도 점수(0.0~1.0)를 산출하는 검증 단계를 제공한다.
2. THE 시스템 SHALL 충실도 검증을 게이트웨이의 경량 모델(예: Haiku/Sonnet)로 수행하며, 판정 프롬프트는 근거 외 서술·모순·과장 주장을 감점하도록 구성한다.
3. IF 충실도 점수가 임계값(기본 0.7) 미만이고 재시도 횟수가 남아있으면, THEN THE 시스템 SHALL 근거를 강조한 지시로 1회 교정 재생성한다(최대 재시도 기본 1회).
4. THE 시스템 SHALL 검증을 옵트인/옵트아웃 가능하게 하고(환경변수 또는 요청 플래그), 기본은 활성으로 하되 지연 예산을 초과하면 비차단으로 원응답을 반환한다.
5. THE 시스템 SHALL 검증 실패(모델 오류/타임아웃) 시 원응답을 그대로 반환하고 저하(degraded) 플래그를 남긴다(가용성 우선).
6. THE 시스템 SHALL 충실도 점수 파싱을 결정론적 순수 함수로 분리해 단위 테스트가 가능하게 한다.

### Requirement 4: 검색 융합 개선 (RRF)

**User Story:** 개발자로서, BM25와 벡터 점수의 스케일 차이에 흔들리지 않는 견고한 융합을 원한다.

#### Acceptance Criteria
1. THE 시스템 SHALL 하이브리드 검색 융합에 Reciprocal Rank Fusion(RRF, 기본 k=60)을 사용하는 경로를 제공한다.
2. THE 시스템 SHALL RRF 융합을 순수 함수로 구현하여 단위 테스트가 가능하게 한다.
3. THE 시스템 SHALL 기존 가중합(alpha) 방식과 RRF 방식을 설정으로 선택 가능하게 하고, 기본값을 RRF로 한다.
4. WHERE 벡터 검색이 비활성(임베더 없음)인 경우, THE 시스템 SHALL BM25 단독 순위로 정상 동작하며 예외를 던지지 않는다.
5. THE 시스템 SHALL 기존 MMR·score_threshold·file_filter 동작을 RRF 경로에서도 보존한다.

### Requirement 5: LLM 리랭커 (Cross-encoder 대체, 무의존성)

**User Story:** 사용자로서, 검색 상위 후보가 질의와 진짜 관련 있는 순서로 재정렬되기를 원한다.

#### Acceptance Criteria
1. THE 시스템 SHALL 융합 상위 N개(기본 top_k*2, 최대 상한 존재) 후보를 경량 LLM으로 관련성 재정렬하는 리랭커를 제공한다.
2. THE 시스템 SHALL 리랭커 프롬프트를 후보 목록→관련성 순위(JSON 또는 인덱스 목록)로 응답하도록 구성하고, 응답 파싱을 순수 함수로 분리한다.
3. IF 리랭커 호출이 실패/타임아웃하면, THEN THE 시스템 SHALL 원래 융합 순위를 그대로 사용한다(비차단 폴백).
4. THE 시스템 SHALL 리랭커를 옵트인/옵트아웃 가능하게 하고, 동일 (쿼리, 후보 집합)에 대한 결과를 캐시하여 반복 호출을 줄인다.
5. THE 시스템 SHALL 리랭커가 후보 인덱스를 임의로 창작(존재하지 않는 인덱스)해도 이를 무시하고 유효 인덱스만 사용한다.

### Requirement 6: 쿼리 확장 (HyDE / 다중 쿼리)

**User Story:** 개발자로서, 사용자의 짧거나 모호한 질의에도 관련 코드가 잘 검색되기를 원한다.

#### Acceptance Criteria
1. WHERE 질의가 짧거나(토큰 수 임계 미만) 모호하다고 판단되는 경우, THE 시스템 SHALL 경량 LLM으로 쿼리 확장(가상 정답 또는 동의 키워드)을 1회 생성해 검색 recall을 높인다.
2. THE 시스템 SHALL 확장 쿼리로 얻은 결과와 원 쿼리 결과를 RRF로 융합한다.
3. IF 쿼리 확장 호출이 실패하면, THEN THE 시스템 SHALL 원 쿼리 검색 결과만 사용한다(비차단).
4. THE 시스템 SHALL 쿼리 확장을 옵트인/옵트아웃 가능하게 하고 기본은 비활성(지연 최소화)으로 한다.

### Requirement 7: 교체 가능한 임베딩 (Pluggable EmbeddingProvider)

**User Story:** 유지보수자로서, TF-IDF를 시맨틱 임베딩으로 교체하거나 게이트웨이/로컬 중 선택할 수 있는 구조를 원한다.

#### Acceptance Criteria
1. THE 시스템 SHALL `embed(text)`, `embed_batch(texts)`, `dimension`, `is_ready` 를 노출하는 EmbeddingProvider 인터페이스를 정의한다.
2. THE 시스템 SHALL 기존 TF-IDF 임베더를 EmbeddingProvider로 어댑트하며, 기본 provider로 사용해 기존 동작을 보존한다.
3. THE 시스템 SHALL 게이트웨이 Titan 임베딩 provider(옵트인)를 정의하되, 게이트웨이가 임베딩 모델을 허용하지 않으면 자동으로 비활성화하고 TF-IDF로 폴백한다.
4. THE 시스템 SHALL 로컬 ONNX 임베딩 provider의 인터페이스 자리(placeholder)와 활성 플래그를 정의하되, 실제 모델 번들·동결 통합은 별도 태스크로 분리한다(용량·빌드 영향 격리).
5. WHEN provider 교체로 벡터 차원이 바뀌면, THE 시스템 SHALL 캐시된 벡터 차원과 불일치를 감지해 재인덱싱하거나 벡터 검색을 안전하게 비활성화한다(기존 차원 가드 보존).

### Requirement 8: 평가 하네스 및 품질 게이트

**User Story:** 리드로서, "울트라 프로덕션급"을 주장하려면 측정 가능한 근거가 필요하다.

#### Acceptance Criteria
1. THE 시스템 SHALL 대상 저장소에서 golden 질의-근거 세트를 구성/로드하는 평가 하네스를 제공한다.
2. THE 시스템 SHALL 검색 품질 지표(recall@k, MRR)를 계산하는 순수 함수를 제공하고 단위 테스트한다.
3. THE 시스템 SHALL 응답 충실도 지표(근거 인용율/미검증 인용 비율)를 계산하는 순수 함수를 제공한다.
4. THE 시스템 SHALL 평가를 `./venv/bin/python -m pytest <file> -p no:cacheprovider -q` 로 실행 가능한 파일 기반 테스트로 제공한다(인라인 멀티라인 금지).
5. THE 시스템 SHALL 개선 전/후 지표를 비교할 수 있도록 baseline 대비 회귀를 감지하는 임계 게이트를 제공한다.

### Requirement 9: 멀티에이전트 교차 검증 (합의 고도화)

**User Story:** 사용자로서, 여러 모델이 같은 오답에 합의하는 상황을 피하고 서로의 근거를 검증하기를 원한다.

#### Acceptance Criteria
1. THE 시스템 SHALL 합의(consensus) 경로에서 단순 다수결/병합이 아니라, 한 모델이 다른 모델 응답의 근거 충실도를 비평·검증하는 단계를 옵션으로 제공한다.
2. WHERE 후보 응답 간 사실 충돌이 감지되면, THE 시스템 SHALL 충돌 지점을 근거와 대조해 해소하거나 사용자에게 불확실성으로 표기한다.
3. THE 시스템 SHALL 교차 검증을 옵트인으로 하고, 비활성 시 기존 합의 동작을 그대로 보존한다.
4. THE 시스템 SHALL 교차 검증 실패 시 기존 합의 결과로 비차단 폴백한다.

### Requirement 10: 성능·안정성 예산

**User Story:** 사용자로서, 품질 개선이 응답을 과도하게 느리게 만들지 않기를 원한다.

#### Acceptance Criteria
1. THE 시스템 SHALL 검증/리랭커/쿼리확장 각각에 지연 예산(타임아웃)을 두고 초과 시 비차단 폴백한다.
2. THE 시스템 SHALL 추가 LLM 호출(검증/리랭커/확장)의 활성 여부를 환경변수로 일괄 제어할 수 있게 한다.
3. THE 시스템 SHALL 인라인 멀티라인 `python -c` 를 사용하지 않고, 장시간 프로세스는 백그라운드로 실행한다(개발/검증 규칙 준수).
4. THE 시스템 SHALL 추가 기능이 모두 비활성일 때 기존 응답 경로와 동등한 지연/동작을 보인다(무회귀).
