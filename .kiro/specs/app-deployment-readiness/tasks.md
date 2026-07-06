# Implementation Plan: app-deployment-readiness

## Overview

이 계획은 `agentic-editor`를 사내 설치형 앱으로 배포하기 위한 하드닝 작업을 **저장소 안에서
구현·검증 가능한(IN-REPO)** 코딩 작업 중심으로 분해한다. 최상위 원칙은 **보존(Preservation)**
이며, 인증 → Bedrock Gateway 런타임과 모든 에디터 기능의 기능적 동작은 변경하지 않는다.

작업은 순수 헬퍼 → 프로퍼티 테스트 → 통합/재구현 → 배선 순으로 점진적으로 쌓이며, 각 단계는
이전 단계 위에 빌드되고 마지막에 서로 연결된다. 설계의 언어 선택(Electron main/렌더러는 JS,
백엔드 스모크 집계는 Python)을 그대로 사용하므로 구현 언어 질의는 생략한다.

**IN-REPO vs CLEAN-MACHINE 구분**: 실제 Apple 인증서 서명·공증 Gatekeeper 통과, 클린머신
설치 스모크, 실제 SSO device-code 로그인, CI OS 러너의 동결 빌드 산출은 저장소 안에서 완결할
수 없다. 이런 항목은 `[사용자 자산/클린머신 필요]`로 표시하고 **비차단(non-blocking) 선택
작업(`*`)**으로 분리하여, IN-REPO 완료가 이들에 의해 막히지 않도록 한다.

## Tasks

- [x] 1. 의존성 및 테스트 하네스 준비
  - `package.json`에 AWS SDK for JS v3 프로덕션 의존성 추가: `@aws-sdk/client-sso-oidc`,
    `@aws-sdk/client-sso`, `@aws-sdk/client-sts`, `@aws-sdk/credential-providers`
    (모두 순수 JS — 네이티브 빌드 없음, 크로스플랫폼 안전)
  - JS 프로퍼티 테스트용 `fast-check`를 devDependency로 추가 (미설치 상태이므로 필수)
  - Python 스모크 집계 프로퍼티 테스트용 `hypothesis`가 `ai_engine` venv에 있는지 확인,
    없으면 `ai_engine/requirements.txt`(또는 dev 요구사항)에 추가
  - `npm install`로 설치 검증 (레포 패키지 매니저는 npm)
  - _Requirements: 2.1, 2.2, 3.1_

- [x] 2. Auth_Manager 순수 헬퍼 구현 및 프로퍼티 테스트
  - [x] 2.1 순수 헬퍼 함수 구현 (`electron/core/aws-sso-manager.js`)
    - `mapCredentials()`: SDK 반환 `{accessKeyId, secretAccessKey, sessionToken, region}`을
      `{AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_DEFAULT_REGION}`로 매핑
    - `listProfiles()` 정렬부: `bedrockuser` 접두 프로파일을 상단, 그룹 내 `localeCompare` 유지
    - BedrockUser ARN 후보 빌더: 이메일(`first.last@`)이면
      `[first[:2]+last, first[:1]+last, first[:3]+last, first+last]`, 아니면 원본 식별자,
      분해 불가 시 정의된 폴백 — 항상 문자열 반환(예외 없음)
    - `buildSsoProfileBlock({name,startUrl,region,accountId,roleName})` → ini 블록 문자열
      (비밀키 필드 절대 미포함), `parseSsoProfileBlock(iniText, name)` → 구성 객체
    - 파일 IO와 무관하게 export 가능한 순수 함수로 분리 (기존 IPC 계약·반환 형태 불변)
    - _Requirements: 2.2, 2.4, 2.5, 2.3, 4.2, 4.6_

  - [x]* 2.2 mapCredentials 프로퍼티 테스트 (fast-check)
    - **Property 1: 자격증명 매핑 형태 불변**
    - **Validates: Requirements 2.2, 2.4**
    - 최소 100회 반복, 태그: `Feature: app-deployment-readiness, Property 1: 자격증명 매핑 형태 불변`

  - [x]* 2.3 listProfiles 정렬 프로퍼티 테스트 (fast-check)
    - **Property 3: 프로파일 정렬 불변**
    - **Validates: Requirements 2.5**
    - 최소 100회 반복, 태그: `Feature: app-deployment-readiness, Property 3: 프로파일 정렬 불변`

  - [x]* 2.4 BedrockUser ARN 파싱 프로퍼티 테스트 (fast-check)
    - **Property 4: BedrockUser ARN 파싱 불변**
    - **Validates: Requirements 2.3**
    - 최소 100회 반복, 태그: `Feature: app-deployment-readiness, Property 4: BedrockUser ARN 파싱 불변`

  - [x]* 2.5 SSO config 블록 왕복 프로퍼티 테스트 (fast-check)
    - **Property 5: SSO config 블록 왕복(round-trip)**
    - **Validates: Requirements 4.2**
    - 최소 100회 반복, 태그: `Feature: app-deployment-readiness, Property 5: SSO config 블록 왕복`

- [x] 3. Auth_Manager 로그인/자격증명 SDK v3 재구현 (계약 보존)
  - [x] 3.1 SDK v3 device-code 로그인 구현 (`electron/core/aws-sso-manager.js`)
    - `~/.aws/config`에서 `sso_start_url/sso_region/sso_account_id/sso_role_name` 파싱
    - `SSOOIDCClient`: `RegisterClient` → `StartDeviceAuthorization` →
      `shell.openExternal(verificationUriComplete)` → `CreateToken` 폴링
      (`AuthorizationPendingException` 대기, `SlowDownException` 백오프)
    - 토큰을 AWS CLI 호환 형식으로 `~/.aws/sso/cache/<sha1(startUrl)>.json`에 기록
      (기존 `sso:get-expiry`와 Python `boto3.Session(profile)` SSO 해석 재사용 — 보존)
    - `SSOClient.GetRoleCredentials`로 검증 → 성공 시 `{success:true, profile}` 반환
    - CLI 폴백: SDK 실패 + `aws` 실행파일 탐지(`which/where`) 성공일 때만 best-effort
      `aws sso login` shell-out 시도, CLI 없으면 폴백 없이 명확한 오류 반환
    - _Requirements: 2.1, 2.4, 2.7_

  - [x]* 3.2 login 결과 계약 프로퍼티 테스트 (fast-check, SDK/CLI mock)
    - **Property 2: 로그인 결과 계약 불변 (성공·실패)**
    - **Validates: Requirements 2.4, 2.7**
    - 최소 100회 반복, 태그: `Feature: app-deployment-readiness, Property 2: 로그인 결과 계약 불변`

  - [x] 3.3 getCredentials / getBedrockUsername SDK v3 재구현 (`electron/core/aws-sso-manager.js`)
    - `getCredentials(profile)`: `fromSSO({profile})()` → `mapCredentials()`로 env-var 키
      형태 반환, 실패 시 `null` (반환 계약 불변)
    - `getBedrockUsername(profile)`: `STSClient` `GetCallerIdentity` + `AssumeRole`로
      `BedrockUser-{cand}` 후보 검증 (후보 생성 규칙은 2.1 헬퍼 그대로 사용, 실행 수단만 SDK)
    - _Requirements: 2.2, 2.3, 2.4_

  - [x]* 3.4 CLI 폴백/실패 계약 단위 테스트
    - CLI 탐지 실패 시 폴백 없이 오류 반환 분기, SDK 성공 경로, 자격증명 부재 시 `null` 반환
    - _Requirements: 2.1, 2.7_

  - [x] 3.5 IPC 반환 계약 보존 회귀 체크
    - `ipc-sso-handlers.js`/`src/main.js` **무변경** 확인 및 `sso:list-profiles/login/
      get-credentials/get-bedrock-username/get-expiry` 반환 형태가 재구현 전과 정확히
      동일함을 검증하는 회귀 테스트 작성 (mock 기반, 파일 IO 없이)
    - _Requirements: 2.4, 2.5, 2.6, 5.7_

- [x] 4. start_server.py 개발 진입 자격증명 사전 로드 정리 (dev 전용)
  - [x] 4.1 boto3 사전 로드로 CLI shell-out 대체 (`scripts/start_server.py`)
    - `aws configure export-credentials` shell-out을 `boto3.Session(profile).get_credentials()`로
      대체 (개발 편의·선택적, 실패해도 기동 계속 — 실제 경로는 앱의 `/api/reset-cache` 주입)
    - 동결 진입(`run_server.py`)은 무변경 확인
    - _Requirements: 2.1, 2.6_

- [x] 5. 체크포인트 — 인증 계층 테스트 통과 확인
  - 모든 IN-REPO 프로퍼티/단위/회귀 테스트를 실행하고, 문제가 있으면 사용자에게 질문한다.

- [x] 6. Onboarding_Flow 구현 (R4)
  - [x] 6.1 aws:write-sso-profile IPC 및 config writer (`electron/main.js`)
    - `security.md` 준수: IPC 핸들러는 `electron/main.js`에서만 등록
    - 2.1의 `buildSsoProfileBlock`로 블록 생성 후 `~/.aws/config`에 append/생성
      (기존 config 존재 시 중복 프로파일명 검사)
    - 쓰기 권한 오류 시 실패 사유 + 수동 구성 ini 예시를 결과에 포함 (secret-free)
    - _Requirements: 4.2, 4.5, 4.6_

  - [x] 6.2 온보딩 다이얼로그 (`src/main.js`)
    - 부팅 시 `listProfiles()`가 빈 배열이면 로그인 다이얼로그 대신 온보딩 화면 표시
    - start URL / region / account id / role name 입력·검증(형식 검사) 후
      `aws:write-sso-profile` 호출, 완료 시 기존 `showSSODialog`로 로그인 진입
    - `SSO_Profile` 부재 동안 게이트웨이 기능 진입 차단, 온보딩 유지
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_

  - [x]* 6.3 자격증명 비저장 프로퍼티 테스트 (fast-check)
    - **Property 6: 자격증명 비저장 불변** (settings.json 직렬화 + config 블록)
    - **Validates: Requirements 2.6, 4.6**
    - 최소 100회 반복, 태그: `Feature: app-deployment-readiness, Property 6: 자격증명 비저장 불변`

  - [x]* 6.4 온보딩 권한 오류/입력 검증 단위 테스트
    - `~/.aws/config` 쓰기 권한 오류 안내, 필수 입력 누락/형식 오류 시 온보딩 유지
    - _Requirements: 4.5_

- [x] 7. Smoke_Harness 및 빌드 게이트 (R1)
  - [x] 7.1 smoke_frozen_backend.py 구현 (`scripts/smoke_frozen_backend.py`)
    - `--target <dev|frozen> [--binary <path>] [--base ...]` 인자, 대상 기동 후 `/health`
      30초 폴링, 기능별 대표 경로 1회 실행(LLM 채팅/PPTX/다이어그램/이미지/하이브리드 렌더)
    - `evaluate_smoke(results) -> {passed, failed_paths}` **순수 함수**로 판정 집계 분리
      (`passed == all(ok)`, 실패 경로·원인 수집), 실패 시 종료 코드 ≠ 0
    - 자격증명 부재 시 LLM/이미지 경로는 skip(사유 기록) — 최종 판정은 클린머신
    - _Requirements: 1.4, 1.5, 1.7, 5.6_

  - [x]* 7.2 evaluate_smoke 프로퍼티 테스트 (hypothesis)
    - **Property 7: 스모크 판정 집계 불변**
    - **Validates: Requirements 1.7**
    - 최소 100회 반복, 태그: `Feature: app-deployment-readiness, Property 7: 스모크 판정 집계 불변`
    - 실행: `./venv/bin/python -m pytest <file> -p no:cacheprovider -q`

  - [x] 7.3 release.yml 빌드 전 import 체크 + spec hidden-import 감사 (`.github/workflows/release.yml`, `ai-engine-server.spec`)
    - 빌드 전 스텝: `python -c "import matplotlib, scipy, langgraph, pptx"` — 실패 시 잡
      즉시 실패 + 누락 모듈명 로그 출력
    - `ai-engine-server.spec`의 `_THIRD_PARTY` 필수 4모듈 수집 대상 포함 여부 감사(주석/명시)
    - _Requirements: 1.1, 1.2, 1.3, 1.6_

- [x] 8. Packaging 서명·공증 구성 (R3)
  - [x] 8.1 electron-builder.yml mac 서명/공증 + entitlements (`electron-builder.yml`, `build/entitlements.mac.plist`)
    - `mac` 섹션에 `hardenedRuntime: true`, `gatekeeperAssess: false`,
      `entitlements`/`entitlementsInherit: build/entitlements.mac.plist`, `notarize` 배선
    - `build/entitlements.mac.plist` 신규: `com.apple.security.cs.allow-jit`,
      `allow-unsigned-executable-memory` 등 Electron/PyInstaller 실행 허용
    - _Requirements: 3.1, 3.2, 3.3_

  - [x] 8.2 release.yml 서명 env 배선 + 미서명 폴백 로그 (`.github/workflows/release.yml`)
    - `CSC_LINK`/`CSC_KEY_PASSWORD`, `APPLE_ID`/`APPLE_APP_SPECIFIC_PASSWORD`/`APPLE_TEAM_ID`
      env 배선
    - 빌드 스텝 앞에 서명 env 존재 여부 체크 스텝: 없으면 "⚠️ 미서명 빌드(서명 자산 미제공)"
      로그 출력 후 빌드 계속
    - _Requirements: 3.2, 3.3, 3.5, 3.6_

- [x] 9. 문서 및 설정 정합화 (R6)
  - [x] 9.1 gateway.md 코드 정합화 (`.kiro/steering/gateway.md`)
    - 인증(SigV4 서명), 필수 필드(`modelId/messages/inferenceConfig/...`), 엔드포인트
      (`/converse`, `/invoke`, Lambda URL), 300초 타임아웃, `BedrockUser-{name}` assume-role,
      하드코딩 Gateway URL, 3회 재시도·`us.` prefix 폴백을 실제 코드 동작으로 정정
    - 이미지 Vertex 예외·자격증명 비저장 원칙 서술은 현행 유지
    - _Requirements: 6.1, 6.2, 6.5_

  - [x] 9.2 publish placeholder 대체 (`electron-builder.yml`)
    - `OWNER_PLACEHOLDER`/`REPO_PLACEHOLDER`를 실제 GitHub owner/repo로 교체하고 잔존 검사
    - git remote origin(`https://github.com/jangkops/Agentic-Editor.git`)에서 owner=`jangkops`,
      repo=`Agentic-Editor`를 파싱해 교체 완료. `grep -c PLACEHOLDER electron-builder.yml` = 0 확인.
      배포 담당자는 릴리스 대상 저장소가 이 origin과 일치하는지 최종 확인 권장
    - _Requirements: 6.3, 6.4_

- [x] 10. Verification 산출물 작성 (R7)
  - [x] 10.1 verification.md 작성 (`.kiro/specs/app-deployment-readiness/verification.md`)
    - IN-REPO 검증 가능 항목(인증 코드 경로/파싱/정렬, config 왕복, 비밀키 비저장, 스모크
      집계, hidden-import 존재, publish placeholder 부재, gateway.md 정합성)과
      CLEAN-MACHINE 전용 항목(서명·공증 Gatekeeper, 설치·실행, 동결 바이너리 런타임, 실제
      SSO 로그인, 실제 게이트웨이/Vertex 호출)을 명시 분리한 매트릭스 작성
    - 클린머신 스모크를 최종 수용 게이트로 정의, 확인 불가 항목은 "실제 클린 머신 검증 필요"
      + 사유 기록, `Signing_Assets`가 USER-PROVIDED임을 명시
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 3.6, 6.5_

- [x] 11. 최종 체크포인트 — IN-REPO 검증 및 매트릭스 산출
  - 모든 IN-REPO 프로퍼티/단위/회귀 테스트를 실행 (JS: `npm test` / jest, Python:
    `./venv/bin/python -m pytest scripts/<file> -p no:cacheprovider -q`)
  - 자격증명이 있으면 개발 백엔드 대상 `smoke_frozen_backend.py --target dev` dry 스모크
    1회 실행, 없으면 skip(사유 기록)
  - verification.md IN-REPO/CLEAN-MACHINE 매트릭스를 최종 갱신하고, 클린머신 전용으로
    남는 항목을 정직하게 명시. 문제가 있으면 사용자에게 질문한다.

- [ ] 12. 클린머신/사용자 자산 검증 (비차단·수동 — IN-REPO 완료를 막지 않음)
  - [ ]* 12.1 Apple 인증서 실서명·공증 Gatekeeper 검증 [사용자 자산/클린머신 필요]
    - 실제 Developer ID 인증서(USER-PROVIDED)로 서명·공증 후 `spctl -a`/설치 실행 확인
    - _Requirements: 3.3, 3.4_

  - [ ]* 12.2 클린머신 설치 스모크 [사용자 자산/클린머신 필요]
    - AWS CLI·Python·`~/.aws/config` 부재 머신에서 설치 앱의 인증 + 모든 기능 경로 스모크
    - _Requirements: 7.1, 7.2, 5.6_

  - [ ]* 12.3 실제 SSO device-code 로그인 검증 [사용자 자산/클린머신 필요]
    - 설치 앱에서 실제 브라우저 device-code 로그인 → 자격증명 주입 → 게이트웨이 호출 확인
    - _Requirements: 2.1, 5.7_

  - [ ]* 12.4 CI OS 러너 동결 빌드 산출 확인 [사용자 자산/클린머신 필요]
    - `macos-latest`/`windows-latest`에서 `ai_engine_dist/ai-engine-server/` 산출 및 30초
      기동 확인 (크로스컴파일 불가 — OS별 러너 필요)
    - _Requirements: 1.1, 1.2, 1.4_

## Notes

- **보존(Preservation)**: `gateway_module.py`, `process-manager.js` 런타임 분기, Vertex
  자동 활성화는 **코드 변경 없음**. `aws-sso-manager.js` 재구현은 IPC 반환 계약(3.5 회귀
  체크)을 100% 보존한다. 렌더러(`src/main.js` 로그인 경로)·주입 경로(`/api/reset-cache`,
  `/api/models`)는 무변경.
- **secret-free**: 생성되는 `~/.aws/config` 블록과 `settings.json`에는 자격증명을 절대
  쓰지 않는다(Property 6로 강제). `settings.json`은 프로파일 이름만 저장.
- **IN-REPO vs CLEAN-MACHINE**: 작업 1~11은 저장소 안에서 구현·검증 완결 가능. 작업 12는
  실제 사용자 자산(Apple 인증서)·클린머신·CI 러너가 필요하여 `*` 비차단 수동 검증으로 분리.
- **사용자 자산·확인 필요 항목**: (a) Apple Developer ID 인증서/공증 자격(USER-PROVIDED),
  (b) `electron-builder.yml` publish 실제 owner/repo 값(9.2, needs-user-input),
  (c) 실 자격증명이 있어야 하는 SSO 로그인·게이트웨이 호출.
- `*` 표시 하위 작업(프로퍼티/단위/통합/수동 검증)은 선택이며 스킵 가능하지만, 프로퍼티
  테스트는 설계의 Correctness Property를 직접 검증하므로 배포 승인 전 실행을 권장.
- 테스트 실행: JS는 레포 npm(jest), Python은 `./venv/bin/python -m pytest <file>
  -p no:cacheprovider -q`. 인라인 멀티라인 `python -c` 금지.
- 장시간 실행 명령(dev 서버/watch)은 사용자가 직접 터미널에서 실행. 스모크는 단발 실행.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["2.1", "4.1", "7.1", "8.1", "9.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "2.4", "2.5", "7.2", "6.1", "7.3", "9.2"] },
    { "id": 3, "tasks": ["3.1", "6.2", "8.2"] },
    { "id": 4, "tasks": ["3.2", "3.3", "6.3", "6.4"] },
    { "id": 5, "tasks": ["3.4", "3.5", "10.1"] },
    { "id": 6, "tasks": ["12.1", "12.2", "12.3", "12.4"] }
  ]
}
```
