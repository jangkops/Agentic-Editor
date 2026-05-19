# 설계: Bedrock Gateway /invoke 라우트 추가 (옵션 A++)

## 목표
게이트웨이 구조를 안전하게 확장하여 Converse API 미지원 모델(이미지/임베딩/리랭크)도
게이트웨이를 경유해 호출 가능하게 하되, 비용 정확도를 한치의 오차 없이 보장.

## 인프라 현황 (2026-05-19 실측)

| 컴포넌트 | 값 |
|---|---|
| API Gateway | REST API `5l764dh7y9`, stage `v1`, `{proxy+}` → Lambda, IAM auth, timeout 29s |
| Gateway Lambda | `bedrock-gw-dev-gateway`, handler.py 3006줄, timeout 900s, 256MB |
| Lambda IAM | `bedrock:InvokeModel *`, `bedrock:InvokeModelWithResponseStream *` |
| SFN | `bedrock-gw-dev-job-orchestrator`, STANDARD, TimeoutSeconds=3600 |
| Fargate Worker | `bedrock-gw-dev-worker:3`, ECR `latest` (4월 22일), 256 CPU / 512 MB |
| Fargate IAM | `bedrock:InvokeModel *`, `bedrock:Converse *` |
| SQS | `bedrock-gw-dev-job-queue`, visibility 120s, retention 24h, DLQ maxReceive=10 |
| ModelPricing | PK: model_id, 168 rows, image 모델 3개 있음 (input_price_per_1k 기반, output=0) |
| MonthlyUsage | PK: principal_id_month, SK: model_id, fields: cost_krw, input_tokens, output_tokens |
| BedrockUser-{name} IAM | path별 화이트리스트: `/v1/POST/converse` 만 허용 |

## 변경 범위

### 1. Gateway Lambda handler.py — 신규 라우트 `/invoke`

**동기 path만 (SFN/Fargate 미사용)**. API Gateway 29초 내 완료.

```
POST /invoke
Body: {"modelId": "stability.sd3-5-large-v1:0", "body": {...}}
Response: {"decision": "ALLOW", "modelId": "...", "output": {...}, "usage": {...}, "cost_krw": 51.89}
```

파이프라인 (기존 /converse sync와 동일 8단계):
1. idempotency check
2. principal extraction (기존 함수 재사용)
3. policy lookup
4. model_access check
5. pricing lookup — **모달별 pricing_unit 분기 추가**
6. quota pre-check
7. `bedrock-runtime.invoke_model()` 직접 호출 (timeout 25s)
8. **모달별 usage 추출** → **모달별 cost 계산** → update_monthly_usage → ledger

신규 함수:
- `_classify_invoke_modal(model_id)` → (modal, default_unit)
- `_extract_invoke_usage(model_id, request_body, response_body)` → usage dict
- `_estimate_cost_krw_invoke(model_id, usage, pricing)` → Decimal
- `_invoke_bedrock_raw(model_id, body, timeout=25)` → response dict
- `handle_invoke(principal_id, identity_fields, body, request_id)` → HTTP response

### 2. Gateway Lambda handler.py — 비동기 라우트 `/invoke-jobs`

기존 `/converse-jobs`와 동일 패턴. SFN → Fargate Worker.

```
POST /invoke-jobs → 202 + jobId
GET /invoke-jobs/{jobId} → status + result
POST /invoke-jobs/{jobId}/cancel → cancel
```

신규 함수:
- `handle_invoke_job_submit(...)` — 기존 handle_converse_job_submit 복제 + invoke 전용 payload_ref
- `handle_invoke_job_status(...)` — 기존 handle_converse_job_status 재사용 (동일 JobState 테이블)
- `handle_invoke_job_cancel(...)` — 기존 handle_converse_job_cancel 재사용

### 3. Fargate Worker main.py — invoke_model 분기

환경변수 `INVOKE_MODE` 추가 (SFN ContainerOverrides에서 전달):
- `INVOKE_MODE=converse` (기본, 기존 동작)
- `INVOKE_MODE=invoke` → `bedrock.invoke_model()` 호출 + 모달별 usage/cost 계산

Worker 변경:
- `main()` 안에서 `invoke_mode = os.environ.get('INVOKE_MODE', 'converse')`
- `if invoke_mode == 'invoke':` 분기 → `_invoke_and_settle()` 신규 함수
- 기존 `bedrock.converse()` 경로는 0% 변경

### 4. Step Functions 정의 변경

기존 state machine에 `INVOKE_MODE` 환경변수를 ContainerOverrides에 추가.
SFN input에 `invoke_mode` 필드 추가 (기본값 "converse").

```json
{"Name": "INVOKE_MODE", "Value.$": "$.invoke_mode"}
```

### 5. BedrockUser-{name} IAM 정책 추가

```hcl
resource "aws_iam_role_policy" "bedrock_user_invoke" {
  name = "AllowDevGatewayInvoke"
  role = aws_iam_role.bedrock_user[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "execute-api:Invoke"
      Resource = [
        "arn:aws:execute-api:us-west-2:107650139384:5l764dh7y9/v1/POST/invoke",
        "arn:aws:execute-api:us-west-2:107650139384:5l764dh7y9/v1/POST/invoke-jobs",
        "arn:aws:execute-api:us-west-2:107650139384:5l764dh7y9/v1/GET/invoke-jobs/*",
        "arn:aws:execute-api:us-west-2:107650139384:5l764dh7y9/v1/POST/invoke-jobs/*/cancel",
      ]
    }]
  })
}
```

### 6. ModelPricing 테이블 — 모달별 단가 시드

기존 row 변경 0건. 신규 attribute 추가:
- `pricing_unit`: "token" | "per_image" | "embedding_token" | "rerank_doc"
- `per_image_price_krw`: 이미지 1장당 KRW
- `per_doc_price_krw`: 리랭크 1K 문서당 KRW

시드 대상 (AWS 공식 가격 × 1,300 KRW/USD):
| model_id | pricing_unit | per_image_price_krw | input_price_per_1k |
|---|---|---|---|
| stability.sd3-5-large-v1:0 | per_image | 104.00 | 51.89 (기존) |
| stability.stable-image-core-v1:1 | per_image | 44.20 | 44.48 (기존) |
| stability.stable-image-ultra-v1:1 | per_image | 88.40 | 88.95 (기존) |
| amazon.titan-image-generator-v2:0 | per_image | 10.40 | 14.83 (기존) |
| amazon.nova-canvas-v1:0 | per_image | 10.40 | — (신규) |
| stability.stable-image-inpaint-v1:0 | per_image | 5.20 | — (신규) |
| stability.stable-outpaint-v1:0 | per_image | 5.20 | — (신규) |
| ... (나머지 Stability 편집 모델) | per_image | 5.20 | — |
| cohere.rerank-v3-5:0 | rerank_doc | 2.60 | 1.48 (기존) |
| amazon.rerank-v1:0 | rerank_doc | 2.60 | 1.48 (기존) |

### 7. 비용 정확도 보장 메커니즘

1. **fail-closed**: usage 추출 실패 또는 pricing 누락 시 → deny (0원 침묵 누적 0건)
2. **모달별 공식**:
   - token: `(input_tokens × input_price / 1000) + (output_tokens × output_price / 1000)`
   - per_image: `image_count × per_image_price_krw`
   - embedding_token: `input_tokens × input_price / 1000` (output=0)
   - rerank_doc: `doc_count × per_doc_price_krw / 1000`
3. **idempotency**: 동일 request_id 재전송 시 캐시 반환 (비용 중복 누적 0건)
4. **ledger 불변**: RequestLedger에 PutItem only (IAM Deny UpdateItem/DeleteItem)

### 8. 격리 원칙 (기존 흐름 0% 영향)

1. `/invoke` 동기 path는 SFN/SQS/Fargate 호출 안 함
2. `/invoke-jobs` 비동기 path는 기존 SFN + Fargate 재사용하되 `INVOKE_MODE` 분기로 격리
3. Worker의 기존 `converse` 경로는 코드 0줄 변경
4. ModelPricing 기존 row는 0건 변경 (신규 attribute만 추가)
5. DynamoDB 스키마 변경 없음 (PK/SK 그대로, 신규 attribute는 자유)

### 9. 라이브 검증 계획

| # | 테스트 | 기대 결과 |
|---|---|---|
| 1 | 기존 /converse 텍스트 (Haiku 4.5) | 정상 동작, MonthlyUsage 정확 |
| 2 | /invoke 이미지 (SD3.5, 1장) | 200 OK, cost_krw=104.00, MonthlyUsage += 104.00 |
| 3 | /invoke 임베딩 (Titan embed) | 200 OK, cost_krw = input_tokens × 0.0297 / 1000 |
| 4 | /invoke-jobs 이미지 (비동기) | 202 → SUCCEEDED, MonthlyUsage += per_image |
| 5 | /invoke pricing 누락 모델 | deny response (fail-closed) |
| 6 | /invoke usage 추출 실패 | deny response (fail-closed) |

### 10. 배포 순서 (안전 우선)

1. ModelPricing 시드 (DynamoDB PutItem — 기존 row 미변경)
2. BedrockUser-cgjang에만 AllowDevGatewayInvoke 인라인 정책 추가
3. Gateway Lambda handler.py 패치 + 재배포 (terraform apply 또는 aws lambda update-function-code)
4. 라이브 검증 #1 (기존 /converse 무영향 확인)
5. 라이브 검증 #2 (동기 /invoke 이미지)
6. Worker main.py 패치 + ECR push + task definition 갱신
7. SFN 정의 업데이트 (INVOKE_MODE env var 추가)
8. 라이브 검증 #4 (비동기 /invoke-jobs)
9. 전체 통과 후 Terraform 코드에 반영 + 다른 BedrockUser-* 일괄 적용

## 파일 변경 목록

| 파일 | 변경 유형 | 영향 |
|---|---|---|
| `lambda/handler.py` | 신규 함수 6개 + 라우터 분기 3줄 | 기존 함수 0% 변경 |
| `worker/main.py` (또는 `app/main.py`) | `_invoke_and_settle()` 신규 + main() 분기 | 기존 converse 경로 0% 변경 |
| `ecs.tf` | task definition env에 INVOKE_MODE 추가 | 기존 env 미변경 |
| `sfn.tf` (또는 inline definition) | ContainerOverrides에 INVOKE_MODE 추가 | 기존 states 미변경 |
| `iam.tf` | BedrockUser 역할에 AllowDevGatewayInvoke 정책 추가 | 기존 정책 미변경 |
| ModelPricing DynamoDB | 신규 row PutItem + 기존 row에 pricing_unit attribute 추가 | 기존 값 미변경 |
