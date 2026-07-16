# Mogam Works v0.5.2 — 릴리스 노트

## 개요
reasoning 오케스트레이터(planner/evaluator 재계획 루프)의 **프로덕션 동작을 정상화**하고,
비스트리밍 경로를 방어적으로 보강한 릴리스입니다. 프로덕션 실행 경로(astream_events →
스트리밍)에서 라이브로 검증했습니다.

## 핵심 프로덕션 수정

### Evaluator 재계획 루프 정상화 (기본 모델 Opus → Sonnet 4.5)
- **원인(라이브 실측)**: 프로덕션은 그래프를 `astream_events`(스트리밍) 로 실행하는데,
  기본 evaluator 모델이던 **Opus 는 게이트웨이 스트리밍 엔드포인트를 지원하지 않아**
  `decision=ERROR`/"No generation chunks" 로 실패했다. 그 결과 evaluator 가 예외 폴백
  (achieved=True 기본값)으로 종료되어 **재계획(refine) 루프가 프로덕션에서 사실상 죽어
  있었다**.
- **수정**: reasoning 메타 노드(planner/evaluator) 기본 모델을 스트리밍 엔드포인트에서
  정상 동작하는 Sonnet 4.5 로 변경.
- **검증(프로덕션 경로 = astream_events)**: planner 2 subtasks 분해, aggregate 종합
  1217자, **evaluator achieved 판정 + 사유 212자(진짜 verdict)**, refine 루프 정상 동작,
  유한 종료, 자격증명 미유출.

## 보강 (비스트리밍 경로 방어 — 하위 경로 정확성)
아래는 프로덕션 주경로(스트리밍)가 아니라 `ainvoke`(비스트리밍) 경로에서 유효한 보강이다.
비스트리밍으로 그래프/모델을 호출하는 부수 경로(배치·일부 내부 호출·테스트)의 정확성/지연을
개선한다. 프로덕션 스트리밍 경로에는 영향이 없다(무해).

- **게이트웨이 비동기 잡 toolUse 보존**: `/converse` 의 비동기 S3 잡 폴링 헬퍼가 text 만
  뽑아 toolUse 를 유실하던 결함 수정(`_poll_job_data` 구조화 보존). 비스트리밍 도구 호출의
  tool_calls 파싱을 복구한다.
- **prefer_streaming(opt-in)**: 비스트리밍 `ainvoke` 를 스트리밍 경로로 우회해 지연을 줄이는
  옵션(실패 시 converse 폴백). reasoning 메타 노드가 opt-in. 프로덕션 스트리밍 경로는 이미
  스트리밍이므로 영향 없음.
- **planner 모델 역할/타임아웃 정합**: planner 가 전용 planner 모델을 우선 사용(요구사항 9.2),
  타임아웃을 폴링 상한과 정렬.

## 알려진 제한
- **Opus 를 reasoning 메타 노드에 주입하면 프로덕션에서 폴백**된다(게이트웨이 스트리밍
  엔드포인트가 Opus 미지원). 스트리밍 지원 모델(Sonnet 계열)을 사용해야 한다.
- reasoning 메타 호출 비용은 quota 캐시에 별도 집계되지 않는다(소규모 호출, 영향 제한적).

## 보안/정합
- LLM 호출은 Bedrock Gateway 경유만. 자격증명 미저장. 무한 종료 방지(개별 ainvoke wait_for).
