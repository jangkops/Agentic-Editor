# Requirements Document

## Introduction

이 스펙은 `agentic-editor` Electron 앱을 사내 약 25명의 사용자에게 **설치형 앱**
(macOS `.dmg`, Windows `.exe`)으로 안전하게 배포할 수 있도록 하드닝·패키징하는 것을
목표로 한다. 배포 준비도 감사(deployment-readiness audit)에서 확인된 실제 결함을
근거로 하며, 각 요구사항은 디스크에서 검증된 파일과 코드 동작에 기반한다.

핵심 전제는 **보존(Preservation)** 이다. 현재 25명 사용자가 사용 중인 인증 →
Bedrock Gateway 런타임 로직(SSO 로그인, 자격증명 주입, SigV4 서명, BedrockUser
assume-role, 토큰 만료 3회 재시도, `us.` prefix 폴백, 로그인 시 Secrets Manager
기반 Vertex 자동 활성화)과 모든 에디터 기능(Monaco, 터미널/node-pty, 파일 작업,
원격 SSH, LLM 채팅, PPTX/다이어그램/이미지 생성, 하이브리드 렌더)은 배포 하드닝
이후에도 기능적으로 동일하게 유지되어야 한다.

이 스펙은 기능 추가가 아니라 **깨끗한 설치(clean-install) 배포 차단 요소 제거**에
초점을 둔다. 감사에서 식별된 차단 요소는 심각도 순으로 다음과 같다.

1. [critical] macOS 코드서명/공증 미설정 (`electron-builder.yml` 미서명)
2. [critical] `ai_engine_dist/` 동결 백엔드 미빌드, PyInstaller 동결 미검증
3. [critical] AWS CLI v2 강한 의존성 (`aws-sso-manager.js`가 CLI shell-out)
4. [high] 사전 구성된 `~/.aws/config` SSO 프로파일 요구, 앱이 생성하지 않음
5. [medium] 문서 드리프트 (`gateway.md`가 실제 코드와 불일치)
6. [medium] `electron-builder.yml` publish의 OWNER/REPO placeholder 미기입

## Glossary

- **Frozen_Backend (동결_백엔드)**: PyInstaller `onedir` 방식으로 동결한 Python
  백엔드 산출물. `ai-engine-server.spec`으로 빌드되며 `ai_engine_dist/ai-engine-server/`
  폴더에 `ai-engine-server`(Windows는 `ai-engine-server.exe`) 실행 파일로 생성된다.
  패키징 앱에서 `process.resourcesPath/ai_engine_dist/ai-engine-server/` 경로로 실행된다.
- **PyInstaller_Onedir**: 단일 파일(onefile)이 아닌 폴더 형태의 동결 방식. 실행 시
  임시 압축 해제 지연이 없어 빠르고 안정적이다. OS별로 각 러너에서 빌드해야 하며
  크로스컴파일이 불가능하다.
- **Build_Pipeline (빌드_파이프라인)**: `.github/workflows/release.yml`의 GitHub
  Actions matrix 잡. `macos-latest`, `windows-latest` 러너에서 `npm run build:python`
  (동결) 후 `electron-builder`로 패키징·배포한다.
- **Packaging_System (패키징_시스템)**: `electron-builder.yml` 설정과
  `electron-builder` 도구. `.dmg`/`.zip`/`.exe`(NSIS)/`.AppImage` 산출물을 생성한다.
- **Auth_Manager (인증_관리자)**: `electron/core/aws-sso-manager.js`. 현재
  `aws sso login`, `aws configure export-credentials`, `aws sts` 명령을 shell-out으로
  실행하여 SSO 로그인·자격증명 획득·BedrockUser 이름 추출을 수행한다.
- **Gateway_Client (게이트웨이_클라이언트)**: `ai_engine/gateway_module.py`의
  `GatewayClient`. 자격증명 주입(`inject_credentials`), botocore SigV4 서명,
  BedrockUser assume-role, 토큰 만료 3회 재시도, `us.` prefix 폴백을 담당한다.
- **AWS_CLI_Dependency (AWS_CLI_의존성)**: `Auth_Manager`가 요구하는 AWS CLI v2
  설치 요건. CLI가 없는 머신에서는 현재 인증이 불가능하다.
- **Botocore_Auth (botocore_인증)**: AWS CLI shell-out 대신 이미 의존성으로 포함된
  `botocore`/`boto3`를 사용하여 SSO 로그인·자격증명 획득·STS 호출을 수행하는 방식.
- **Code_Signing (코드서명)**: macOS Developer ID로 앱 바이너리에 서명하는 절차.
  `electron-builder`의 `CSC_LINK`/`CSC_KEY_PASSWORD` 환경변수로 자동 서명된다.
- **Notarization (공증)**: Apple에 서명된 앱을 제출하여 Gatekeeper 통과 티켓을 받는
  절차. `APPLE_ID`/`APPLE_APP_SPECIFIC_PASSWORD`/`APPLE_TEAM_ID` 환경변수를 사용한다.
- **Signing_Assets (서명_자산)**: Apple Developer ID 인증서와 관련 자격증명.
  **사용자가 제공하는 자산(USER-PROVIDED)** 이며 이 스펙이 발급하지 않는다.
- **SSO_Profile (SSO_프로파일)**: `~/.aws/config`(또는 `/fsx/home/{user}/.aws/config`)의
  `[profile ...]` 항목. SSO start URL, region, account, role 정보를 포함한다.
- **Credential_Injection (자격증명_주입)**: Electron이 획득한 임시 자격증명을 IPC로
  Python `Gateway_Client.inject_credentials`에 전달하여 boto3 SSO 캐시를 우회하는 흐름.
- **SigV4**: AWS Signature Version 4 요청 서명. `Gateway_Client._sign`이 botocore
  `SigV4Auth`로 `execute-api`(및 Lambda URL은 `lambda`) 서비스에 서명한다.
- **Onboarding_Flow (온보딩_플로우)**: SSO 프로파일이 없는 신규/미구성 사용자를 위해
  앱이 최초 실행 시 프로파일 구성을 안내·생성하는 흐름.
- **Smoke_Test (스모크_테스트)**: 동결 백엔드 및 설치된 앱에 대해 주요 기능 경로를
  1회 실행하여 정상 동작을 확인하는 검증. 대량 반복이 아닌 대표 경로 실행이다.
- **Editor_Features (에디터_기능)**: Monaco 편집기, 터미널(node-pty, `process-manager.js`),
  파일 작업, 원격 SSH, LLM 채팅, PPTX/다이어그램/이미지 생성, 하이브리드 렌더 등
  현재 제공 중인 모든 사용자 대면 기능.
- **Preservation (보존)**: 하드닝·패키징 변경 이후에도 기존 기능·동작이 기능적으로
  동일하게 유지되는 성질. 회귀(regression) 없음을 의미한다.
- **Clean_Machine (클린_머신)**: AWS CLI, Python, 개발 도구, `~/.aws/config`가 없는
  상태의 사용자 머신. 배포 대상 사용자의 최악 시나리오를 대표한다.
- **Documentation_Set (문서_세트)**: `.kiro/steering/gateway.md` 및
  `electron-builder.yml` publish 설정 등 배포와 인증을 설명하는 문서·설정.

## Requirements

### Requirement 1: 동결 백엔드 빌드 및 기능별 스모크 검증

**User Story:** 배포 담당자로서, 각 OS에서 동결된 백엔드가 실제로 빌드되고 모든 주요
기능 경로가 동결 바이너리에서 동작함을 확인하고 싶다. 그래야 Python 미설치 사용자
머신에서도 앱이 정상 실행됨을 신뢰할 수 있다.

#### Acceptance Criteria

1. WHEN `Build_Pipeline`이 `macos-latest` 러너에서 실행되면, THE `Build_Pipeline`
   SHALL `ai-engine-server.spec`으로 macOS용 `Frozen_Backend`를
   `ai_engine_dist/ai-engine-server/` 경로에 생성한다.
2. WHEN `Build_Pipeline`이 `windows-latest` 러너에서 실행되면, THE `Build_Pipeline`
   SHALL Windows용 `Frozen_Backend`를 `ai-engine-server.exe`를 포함하여
   `ai_engine_dist/ai-engine-server/` 경로에 생성한다.
3. THE `Frozen_Backend` SHALL `matplotlib`, `scipy`, `langgraph`, `pptx` 모듈을
   포함하여 동결되고, 실행 시 해당 모듈을 import 오류 없이 로드한다.
4. WHEN `Frozen_Backend`가 실행되면, THE `Frozen_Backend` SHALL 30초 이내에 HTTP
   요청을 수신 가능한 상태로 진입한다.
5. WHEN 각 주요 기능 경로(LLM 채팅, PPTX 생성, 다이어그램 생성, 이미지 생성,
   하이브리드 렌더)에 대해 `Smoke_Test`가 `Frozen_Backend`를 대상으로 실행되면, THE
   `Smoke_Test` SHALL 각 경로에 대해 성공 응답을 확인하고 결과를 기록한다.
6. IF `Frozen_Backend` 빌드 중 필수 모듈 수집이 실패하면, THEN THE `Build_Pipeline`
   SHALL 빌드를 실패 처리하고 실패한 모듈 이름을 로그에 출력한다.
7. IF 주요 기능 경로 중 하나라도 `Smoke_Test`에서 실패하면, THEN THE `Smoke_Test`
   SHALL 실패한 경로 이름과 오류 원인을 보고하고 검증을 불합격으로 표시한다.

### Requirement 2: AWS CLI 강한 의존성 없는 인증 (botocore 마이그레이션)

**User Story:** AWS CLI가 설치되지 않은 사용자로서, 앱만으로 SSO 로그인과 자격증명
획득을 완료하고 싶다. 그래야 별도 CLI 설치 없이 Bedrock Gateway를 사용할 수 있다.

#### Acceptance Criteria

1. WHEN 사용자가 SSO 로그인을 요청하면, THE `Auth_Manager` SHALL `Botocore_Auth`를
   사용하여 로그인을 수행하고 AWS CLI v2 실행 파일을 요구하지 않는다.
2. WHEN 자격증명 획득이 필요하면, THE `Auth_Manager` SHALL `Botocore_Auth`로 임시
   자격증명(AccessKeyId, SecretAccessKey, SessionToken)을 획득한다.
3. THE `Auth_Manager` SHALL BedrockUser 이름 추출 및 assume-role 확인을
   `Botocore_Auth`의 STS 호출로 수행한다.
4. WHERE 사용자 머신에 AWS CLI v2가 설치되어 있는 경우, THE `Auth_Manager` SHALL
   기존 happy-path 로그인 결과(성공 시 `{ success: true, profile }` 형태)와 동일한
   결과를 반환한다.
5. THE `Auth_Manager` SHALL `bedrockuser-*` assume-role 프로파일을 프로파일 목록
   상단에 정렬하는 기존 정렬 동작을 유지한다.
6. WHEN 획득한 자격증명이 Python 백엔드로 전달되면, THE `Auth_Manager` SHALL
   `Credential_Injection` 경로(`Gateway_Client.inject_credentials`)를 통해 전달하고
   자격증명을 어떤 파일에도 저장하지 않는다.
7. IF `Botocore_Auth` 로그인이 실패하면, THEN THE `Auth_Manager` SHALL 실패
   사유를 포함한 오류 결과(`{ success: false, error }`)를 반환한다.
8. THE `Gateway_Client` SHALL 자격증명 주입, botocore SigV4 서명, BedrockUser
   assume-role, 토큰 만료 3회 재시도, `us.` prefix 폴백, 300초 converse 타임아웃
   동작을 기존과 기능적으로 동일하게 유지한다.

### Requirement 3: macOS 코드서명 및 공증 구성

**User Story:** macOS 사용자로서, Gatekeeper 경고나 수동 우회 없이 배포된 `.dmg`를
설치·실행하고 싶다. 그래야 신뢰할 수 있는 설치 경험을 얻는다.

#### Acceptance Criteria

1. THE `Packaging_System` SHALL macOS 타깃에 대해 `Signing_Assets`(Apple Developer
   ID 인증서)를 사용한 `Code_Signing`을 지원하도록 구성된다.
2. THE `Packaging_System` SHALL `Notarization`을 위한 환경변수
   (`APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID`)를 사용하도록 구성된다.
3. WHERE `Signing_Assets`가 `CSC_LINK`/`CSC_KEY_PASSWORD` 환경변수로 제공되는 경우,
   THE `Packaging_System` SHALL macOS 앱에 자동으로 서명한다.
4. WHEN 서명·공증된 `.dmg`가 `Clean_Machine`의 macOS에서 열리면, THE 배포 산출물
   SHALL Gatekeeper 수동 우회 없이 설치 가능한 상태가 된다.
5. IF `Signing_Assets`가 제공되지 않으면, THEN THE `Build_Pipeline` SHALL 미서명
   빌드임을 로그에 명시하고 빌드를 계속 진행한다.
6. THE 스펙 문서 SHALL `Signing_Assets`가 사용자 제공 자산(USER-PROVIDED)이며 이
   스펙 범위에서 발급하지 않음을 명시한다.

### Requirement 4: 미구성 SSO 사용자 최초 실행 온보딩

**User Story:** `~/.aws/config`가 없는 신규 사용자로서, 앱 최초 실행 시 SSO 프로파일
구성을 안내받고 완료하고 싶다. 그래야 사전 설정 없이 인증을 시작할 수 있다.

#### Acceptance Criteria

1. WHEN 앱이 최초 실행되고 `SSO_Profile`이 존재하지 않으면, THE `Onboarding_Flow`
   SHALL 프로파일 구성을 안내하는 화면을 표시한다.
2. WHEN 사용자가 온보딩 화면에서 SSO 구성 정보(start URL, region, account, role)를
   입력하면, THE `Onboarding_Flow` SHALL `~/.aws/config`에 `SSO_Profile`을 생성한다.
3. WHILE `SSO_Profile`이 존재하지 않는 상태에서는, THE `Onboarding_Flow` SHALL
   Bedrock Gateway 기능 진입 대신 온보딩 안내를 유지한다.
4. WHEN `SSO_Profile` 생성이 완료되면, THE `Onboarding_Flow` SHALL 사용자가 SSO
   로그인을 시작할 수 있는 상태로 전환한다.
5. IF `~/.aws/config` 파일 쓰기가 권한 오류로 실패하면, THEN THE `Onboarding_Flow`
   SHALL 실패 사유와 수동 구성 방법을 안내한다.
6. THE `Onboarding_Flow` SHALL 생성한 `SSO_Profile`에 프로파일 이름만 저장하고
   자격증명(AccessKeyId, SecretAccessKey)을 저장하지 않는다.

### Requirement 5: 기존 에디터 기능 보존 (회귀 방지)

**User Story:** 현재 사용자로서, 배포 하드닝 이후에도 모든 에디터 기능이 이전과
동일하게 동작하기를 원한다. 그래야 배포 변경이 기존 작업 흐름을 깨뜨리지 않는다.

#### Acceptance Criteria

1. THE 패키징된 앱 SHALL Monaco 편집기, 터미널(node-pty), 파일 작업, 원격 SSH,
   LLM 채팅, PPTX 생성, 다이어그램 생성, 이미지 생성, 하이브리드 렌더 기능을
   기존과 기능적으로 동일하게 제공한다.
2. WHEN 패키징된 앱이 실행되면, THE `process-manager.js` SHALL
   `process.resourcesPath/ai_engine_dist/ai-engine-server/`의 `Frozen_Backend`
   바이너리를 실행하고 사용자별 쓰기 가능한 `AE_GENERATED_ROOT`(userData 하위)를
   주입하는 기존 동작을 유지한다.
3. WHEN node-pty 네이티브 모듈이 로드되면, THE 패키징된 앱 SHALL `asarUnpack`으로
   풀린 `node-pty`를 사용하여 터미널을 생성한다.
4. IF node-pty 로드가 실패하면, THEN THE `process-manager.js` SHALL 기존
   `spawn` 폴백 경로로 터미널을 생성한다.
5. WHEN 로그인이 완료되면, THE 백엔드 SHALL 주입된 SSO 자격증명으로 AWS Secrets
   Manager에서 GCP 키를 해석하는 Vertex 자동 활성화 동작을 기존과 동일하게 수행하되,
   실패해도 로그인을 정상 진행한다.
6. WHEN 전체 기능 `Smoke_Test`가 `Frozen_Backend`에 대해 실행되면, THE `Smoke_Test`
   SHALL 모든 주요 기능 경로가 회귀 없이 통과함을 확인한다.
7. THE 하드닝 변경 SHALL 기존 25명 사용자의 인증 → Bedrock Gateway 런타임 동작을
   기능적으로 변경하지 않는다.

### Requirement 6: 문서 및 설정 정합화

**User Story:** 신규 사용자·유지보수자로서, 문서가 실제 코드 동작과 일치하고 배포
설정에 미기입 placeholder가 없기를 원한다. 그래야 잘못된 안내로 인한 혼란을 피할 수 있다.

#### Acceptance Criteria

1. THE `Documentation_Set` SHALL `gateway.md`의 인증 설명을 실제 코드 동작(SigV4
   서명, BedrockUser assume-role, 300초 converse 타임아웃, 하드코딩된 Gateway URL)과
   일치하도록 갱신한다.
2. THE `Documentation_Set` SHALL `gateway.md`에서 실제 코드와 불일치하는
   `appid`/`apitoken`/required-fields/120초 타임아웃 서술을 실제 동작으로 정정한다.
3. THE `Packaging_System` 설정 SHALL `electron-builder.yml` publish 섹션의
   `OWNER_PLACEHOLDER`와 `REPO_PLACEHOLDER`를 실제 GitHub 저장소 owner와 이름으로
   대체한다.
4. WHEN `Build_Pipeline`이 `electron-builder --publish always`를 실행하면, THE
   `Build_Pipeline` SHALL 유효한 owner/repo 설정으로 GitHub Releases에 산출물을
   업로드한다.
5. IF `Documentation_Set`과 실제 코드 동작 사이에 불일치가 남아 있으면, THEN THE
   검증 절차 SHALL 해당 불일치 항목을 검토 대상으로 보고한다.

### Requirement 7: 정직한 검증 — 클린 머신 설치 앱 스모크 테스트가 수용 기준

**User Story:** 배포 승인자로서, 배포 결정이 실제 클린 머신에서의 설치·실행 검증에
근거하기를 원한다. 그래야 저장소 안에서만 검증 가능한 항목과 실제 머신에서만 검증
가능한 항목을 구분하여 정직하게 판단할 수 있다.

#### Acceptance Criteria

1. THE 검증 절차 SHALL `Clean_Machine` 프로파일(AWS CLI·Python·`~/.aws/config`
   부재)에서 설치된 앱을 대상으로 `Smoke_Test`를 수행하는 것을 최종 수용 게이트로
   정의한다.
2. WHEN `Clean_Machine`에서 설치된 앱의 `Smoke_Test`가 실행되면, THE `Smoke_Test`
   SHALL 인증(SSO 로그인·자격증명 주입)과 모든 주요 기능 경로가 통과함을 확인한다.
3. THE 검증 절차 SHALL 저장소 안에서 검증 가능한 항목(동결 스펙 구성, 설정
   placeholder 대체, 문서 정합성, botocore 인증 코드 경로)과 실제 `Clean_Machine`
   에서만 검증 가능한 항목(코드서명·공증 Gatekeeper 통과, 설치 실행, 동결 바이너리
   런타임 동작)을 명시적으로 구분하여 기록한다.
4. IF 어떤 검증 항목이 저장소 안에서 확인 불가능하면, THEN THE 검증 절차 SHALL 해당
   항목을 "실제 클린 머신 검증 필요"로 표시하고 그 이유를 기록한다.
5. WHEN `Clean_Machine` `Smoke_Test`가 하나라도 불합격하면, THE 검증 절차 SHALL
   배포를 승인하지 않고 불합격 항목을 보고한다.
