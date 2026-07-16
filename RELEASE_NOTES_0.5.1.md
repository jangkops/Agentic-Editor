# Mogam Works v0.5.1 — 릴리스 노트

## 개요
v0.5.0(LangGraph 추론 고도화)에서 라이브 게이트웨이 e2e 검증 중 발견한 **프로덕션 결함
2건을 수정**한 릴리스입니다. v0.5.0 은 기능이 기본 활성(planner/evaluator)이었으나, 아래
결함으로 실제로는 무력화되어 있었습니다. v0.5.1 에서 정상 동작을 라이브로 실증했습니다.

## 수정 (Fixes)

### 1. [핵심] 게이트웨이 비동기 잡 경로에서 toolUse 유실
- **증상**: `/converse` 가 일부 모델(Opus, toolConfig 동반 Sonnet 등)에서 `ACCEPTED` 를
  반환하고 비동기 S3 잡 폴링 경로를 탄다. 기존 폴링 헬퍼가 **text 블록만** 추출해 반환하여
  toolUse 블록을 통째로 유실했다.
- **영향**: planner(`select_plan`) / evaluator(`submit_evaluation`) 등 toolChoice 강제
  호출이 `tool_calls` 를 받지 못해 폴백으로 무력화.
  - Planner: 요청을 항상 단일 서브태스크로 축약 → **DAG 병렬 분해가 전혀 동작하지 않음**.
  - Evaluator: 파싱 실패 기본값(`achieved=True`)으로 종료 → **재계획 루프가 사실상 죽음**.
- **수정**: 구조화 잡 결과를 손실 없이 보존하는 폴링 경로를 도입. `/converse` 가 toolUse
  블록을 포함한 전체 응답 메시지를 그대로 전달하도록 변경. 스트리밍/미디어 경로는 하위 호환
  유지.

### 2. Evaluator/Planner 기본 모델을 동기 저지연 경로(Sonnet 4.5)로 변경
- **증상**: Opus 비동기 폴링은 최대 300초까지 걸려, evaluator/planner 의 단발 호출
  타임아웃(300초)에 자주 걸려 폴백됐다.
- **수정**: 재계획 루프 신뢰성을 위해 기본값을 동기 응답하는 Sonnet 4.5 로 변경. 설계
  의도(Opus 평가자)는 설정 주입으로 그대로 사용 가능.

### 3. Planner 모델 역할 배분 정합 + 타임아웃 정렬
- **증상**: Planner 가 전용 planner 모델을 무시하고 coding 모델을 썼고(역할 배분 미반영),
  60초 타임아웃이 게이트웨이 비동기 잡 폴링(최대 300초)보다 짧아 슬로우 응답 시 DAG
  분해가 통째로 단일 서브태스크로 조기 폴백됐다.
- **수정**: planner 가 전용 planner 모델을 우선 사용(미주입 시 하위 호환 폴백).
  planner 타임아웃을 폴링 상한(300초)과 정렬해 조기 폴백 제거(정상 시 지연 없음).

## 알려진 제한(향후 개선 후보)
- reasoning 메타 호출(planner/evaluator/aggregate)은 non-streaming 경로라 워커 생성 비용
  (스트리밍 settlement 로 집계됨)과 달리 quota 캐시에 별도 집계되지 않는다. 소규모 호출로
  영향은 제한적이며, 정밀 원가 배분은 후속 개선 대상.

## 라이브 검증 (실제 Bedrock Gateway)
- Planner: 3-독립작업 프롬프트 → `t1(coding)/t2(research)/t3(ops)` 로 정상 분해(병렬 fan-out).
- Evaluator: 미충족 산출물 → `achieved=False`, `missing_domains=[coding, ops]`, 실제 사유
  생성, `refine_count 0→1` 재계획 루프 발동(진짜 verdict 실증).
- 유한 종료 + 자격증명 미유출 유지.

## 테스트
- 신규 회귀 테스트: 비동기 잡 경로 toolUse 보존(5 케이스, 네트워크 불필요 mock).
- 기존 게이트웨이/추론 스위트 회귀 0.

## 보안/정합
- LLM 호출은 Bedrock Gateway 경유만. 자격증명 미저장. 무한 종료 방지(개별 ainvoke wait_for).
