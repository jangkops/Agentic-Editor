# Mogam Works v0.5.2 — 릴리스 노트

## 개요
v0.5.1(toolUse 유실 + evaluator/planner 모델 안정화)에 이어, **reasoning 오케스트레이터의
지연을 약 2.2배 단축**한 성능 릴리스입니다. 기능·정확성은 전부 보존하며 라이브로 검증했습니다.

## 성능 개선 (Performance)

### reasoning 메타 호출을 스트리밍 경로로 전환
- **문제**: 게이트웨이 `/converse` 는 toolConfig 동반 호출(planner/evaluator/router 등)을
  비동기 S3 잡 폴링으로 처리해 느리다. 라이브 실측: 동일 `select_plan` 호출이
  `converse` 35.1초 vs `converse_stream_live`(스트리밍) 7.6초 — **약 4.6배** 차이.
- **개선**: reasoning 메타 노드(router/planner/evaluator/aggregate)가 실시간 스트리밍
  경로를 우선 사용하도록 전환. toolUse 는 converse 와 동일한 형태로 조립되어 정확성 동일.
  스트리밍 실패 시 `converse` 로 자동 폴백해 무회귀를 보장한다.
- **효과(라이브)**: 동일 2워커 프롬프트 전체 그래프 실행 **160.2초 → 74.3초(약 2.2배)**.
  planner 분해·aggregate 종합·evaluator 실제 verdict·유한 종료·자격증명 미유출 전부 보존.
- **범위**: 도메인 워커 model 노드는 UI 토큰 스트리밍 상호작용 불확실성 때문에 이번 범위에서
  제외(안전 우선). 향후 UI 스트리밍 경로 확인 후 확장 가능.

## 이전 릴리스(0.5.1) 포함 수정 요약
1. 게이트웨이 비동기 잡 경로 toolUse 유실 수정 — planner/evaluator 무력화 결함 해결.
2. Evaluator/Planner 기본 모델을 동기 저지연 경로(Sonnet 4.5)로 변경(Opus 는 주입 가능).
3. Planner 모델 역할 배분 정합 + 타임아웃 정렬(DAG 조기 폴백 제거).

## 알려진 제한(향후 개선 후보)
- reasoning 메타 호출 비용은 quota 캐시에 별도 집계되지 않는다(소규모 호출, 영향 제한적).
- 도메인 워커 model 노드 지연 최적화는 후속 대상(UI 스트리밍 경로 검증 필요).

## 보안/정합
- LLM 호출은 Bedrock Gateway 경유만. 자격증명 미저장. 무한 종료 방지(개별 ainvoke wait_for).
