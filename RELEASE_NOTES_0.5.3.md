# Mogam Works v0.5.3 — 릴리스 노트

## 개요
reasoning 오케스트레이터의 **프로덕션 견고성**을 보강한 릴리스입니다. 프로덕션 실행 경로
(astream_events → 스트리밍)에서 라이브로 검증했습니다.

## 견고성 보강

### 스트리밍 실패 시 converse 폴백 (무손상)
- **배경**: 프로덕션은 그래프를 `astream_events`(스트리밍) 로 실행한다. 일부 모델은 게이트웨이
  스트리밍 엔드포인트를 지원하지 않아 스트림이 에러/무응답으로 끝나고, 이때 노드가 기능
  저하(evaluator: achieved=True 기본값 등)로 폴백됐다.
- **개선**: `GatewayChatModel._astream` 이 콘텐츠를 하나도 방출하지 못한 채 스트림이 끝나면
  `converse`(비스트리밍)로 폴백해 산출물을 확보한다. 빠른-실패 스트림에서 기능 저하 대신
  정상 복구된다.
- **무손상 보장**: 정상 스트리밍(콘텐츠 방출)에서는 폴백이 트리거되지 않아 기존 동작이 불변.
  라이브 재확인: 프로덕션 astream_events 경로 22.8초, 189 스트림 이벤트, evaluator 진짜
  verdict, 정상 종료.

## 이전 릴리스 반영(0.5.2 포함) — 핵심 프로덕션 수정
- **Evaluator 재계획 루프 정상화**: 프로덕션(스트리밍) 경로에서 기본 evaluator 모델이던
  Opus 가 스트리밍 엔드포인트 미지원으로 실패 → evaluator 폴백 → refine 루프가 죽어
  있었다. reasoning 메타 노드 기본 모델을 Sonnet 4.5 로 변경해 정상화(라이브 검증 완료).
- 비스트리밍(ainvoke) 경로 방어: converse 비동기 잡 toolUse 보존, prefer_streaming(opt-in),
  planner 모델 역할/타임아웃 정합.

## 알려진 제한(정직)
- **Opus 는 reasoning 메타 노드에 실용적이지 않다**: 스트리밍 엔드포인트에서 빠른 실패가
  아니라 read-timeout(최대 300초)까지 멈춘 뒤 폴백되므로 지연이 크다. 스트리밍 지원 모델
  (Sonnet 계열)을 사용해야 한다. (게이트웨이가 Opus 스트리밍을 지원하면 해소됨.)
- reasoning 메타 호출 비용은 quota 캐시에 별도 집계되지 않는다(소규모 호출, 영향 제한적).

## 보안/정합
- LLM 호출은 Bedrock Gateway 경유만. 자격증명 미저장. 무한 종료 방지(개별 ainvoke wait_for).
