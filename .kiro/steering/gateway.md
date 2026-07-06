# Bedrock Gateway Integration Steering

> 이 문서는 `ai_engine/gateway_module.py`의 실제 동작과 정합화되어 있다.

## Auth (SigV4)
- 인증은 botocore `SigV4Auth`로 요청에 서명한다 — `appid`/`apitoken` 헤더 방식 아님
- Gateway(`/converse`, `/invoke`) 서명 서비스: `execute-api`
- Lambda Function URL(SSE 스트리밍) 서명 서비스: `lambda`
- 자격증명은 `inject_credentials`로 주입되거나, `BedrockUser-{name}` 역할을
  STS `assume_role`로 획득 (`arn:aws:iam::{account}:role/BedrockUser-{bedrock_user}`)
- 자격증명은 5분간 캐시 후 갱신

## Endpoints
- `POST {gateway_url}/converse`  → 논스트리밍 Converse (비동기 모델은 `ACCEPTED` 후 S3 job 폴링)
- `POST {gateway_url}/invoke`    → InvokeModel (이미지 모델 등)
- Lambda Function URL            → SSE 스트리밍 (`converse_stream_live`, `stream_sse_realtime`)
- `POST {gateway_url}/openai/responses`       → OpenAI Responses 동기 라우트
- `POST {gateway_url}/openai/responses-jobs`  → OpenAI Responses 비동기 잡 제출
- `GET  {gateway_url}/openai/responses-jobs/{job_id}` → 잡 결과 폴링

> OpenAI Responses 라우트는 본문을 백엔드로 그대로 전달하므로 동기 경로에는
> `modelId` 같은 게이트웨이 전용 필드를 넣지 않는다(비동기 잡 라우트는 `modelId` 요구).

하드코딩 URL:
- Gateway: `https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1`
  (`GATEWAY_URL` env로 오버라이드 가능)
- Stream:  `https://5kzi5pmk6leqq74cq64jza37lu0qipbk.lambda-url.us-west-2.on.aws/`

## Request body (Converse 형식)
- 필수: `modelId`, `messages`, `inferenceConfig` (`{maxTokens}`)
- 선택: `system` (`[{text}]`), `toolConfig`

## Timeout
- `/converse`: 300초 (`urllib` read), quota 조회 전용(`converse_quota_only`)은 15초
- 실시간 SSE(`stream_sse_realtime`): total 3600초(1시간) / connect 30초 / read 300초
  (5분 무응답 시 끊김으로 판단)
- 스트리밍 단발(`_converse_stream_live_once`): total 300초 / connect 30초
- `/invoke`: 기본 30초, connect 10초
- OpenAI Responses: 동기 120초 / 비동기 잡 제출 30초

## Retry / Fallback
- 토큰 만료(`expired` / `security token` / `not authorized`) 시 자격증명 강제 갱신 후
  최대 3회 재시도
- 모델 ID가 `not in allowed`로 거부되면 `us.` prefix를 붙여 1회 재시도
  (논스트리밍·스트리밍 경로 모두 적용)
- 모델별 `max_tokens`를 `_MODEL_MAX_TOKENS_MAP`으로 자동 조정 —
  `min(env_cap, model_limit)`을 사용. env `AE_MAX_TOKENS`는 상한선으로만 작동하며
  **기본값 64000**, 맵에 매칭되는 패턴이 없는 모델은 `_DEFAULT_MAX_TOKENS`(4096)로 폴백
- 실시간 SSE는 `maximum tokens ... exceeds` 검증 실패를 감지하면 한계의 50%(또는 에러가
  알려준 실제 한계−1)로 줄여 최대 2회 재시도

## Models
- anthropic.claude-3-opus-20240229-v1:0     → Planner, Evaluator
- anthropic.claude-3-5-sonnet-20241022-v2:0 → Generator

## Security
- NEVER store credentials (accessKeyId, secretAccessKey) in settings.json —
  자격증명은 어떤 파일에도 저장하지 않고 런타임 주입/assume-role로만 사용

## Image Generation Exception (사용자 결정 — 2026-05-29)
Bedrock 카탈로그에 Nano Banana Pro / Gemini Image / Imagen 동급의 텍스트 정확
렌더링 이미지 모델이 없는 현실을 반영해, **이미지 생성에 한해** Vertex AI 호출을
사용자 결정으로 예외 허용.

- LLM/추론/operation JSON 생성: Bedrock Gateway 경유 그대로 유지 (예외 없음)
- 이미지 생성: `AE_ENABLE_VERTEX_IMAGE=1` 옵트인 시 Vertex AI 호출 허용
- Vertex 호출은 `ai_engine/vertex_image_module.py` 단일 모듈에서만 발생
- 자격증명: service account JSON 키 파일 경로를
  `GOOGLE_APPLICATION_CREDENTIALS` 환경변수로 지정. 키 파일은 절대 repo에
  commit 되지 않음 (.gitignore 등록됨)
- Bedrock 이미지 모델(Stability / Nova Canvas / Titan)은 Gateway 경유 그대로
  유지 — Vertex가 폴백 또는 보조로만 사용
- 비용 추적: GCP 콘솔에서 별도 측정 (POC 단계 한정)
