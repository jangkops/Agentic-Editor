# Verification Matrix: app-deployment-readiness

> 이 문서는 배포 준비도(deployment-readiness) 검증을 **두 범주로 명시 분리**한다.
> (1) 저장소 안에서 완결·검증 가능한 IN-REPO 항목, (2) 실제 클린 머신에서만 검증
> 가능한 CLEAN-MACHINE 전용 항목. 최상위 원칙은 **정직한 검증**이다 — 저장소 안에서
> 확인 불가한 항목은 절대 "통과"로 표시하지 않고 "실제 클린 머신 검증 필요"로 남긴다.
>
> **최종 수용 게이트(R7.1/7.5)**: `Clean_Machine`(AWS CLI·Python·`~/.aws/config`
> 부재)에서 설치 앱의 인증 + 모든 주요 기능 경로 스모크가 **전부 통과**해야 배포를
> 승인한다. 클린머신 스모크가 **하나라도 불합격이면 배포를 승인하지 않는다.**
>
> _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 3.6, 6.5_

## 범례

- **PASS**: 저장소 안에서 검증 완료(테스트 통과·정적 검사 통과·구성 존재 확인).
- **PRESENT (cert user-provided)**: 구성/코드는 존재하나 최종 효과는 사용자 자산 또는
  클린머신에서만 확인 가능.
- **CLEAN-MACHINE 필요**: 저장소 안에서 원천적으로 확인 불가 — 사유를 명시.
- **CONFIRM (배포 담당자)**: 값/자산이 존재하나 배포 담당자의 최종 확인이 필요.

---

## 1. IN-REPO 검증 가능 항목 (저장소 안에서 완결)

| # | 검증 항목 | HOW verified (방법) | 근거 위치 | 현재 상태 |
|---|-----------|---------------------|-----------|-----------|
| I1 | 자격증명 매핑 형태 불변 (Property 1) | fast-check property test (≥100회) — `mapCredentials()`가 SDK 반환을 `AWS_*` env-var 키로 무손실 매핑 | `electron/core/aws-sso-manager.js` · P1 테스트 | **PASS** |
| I2 | 로그인 결과 계약 불변 (Property 2) | fast-check property test (≥100회) — 성공 `{success:true, profile}` / 실패 `{success:false, error}` 두 형태만 | `login()` (SDK/CLI mock) · P2 테스트 | **PASS** |
| I3 | 프로파일 정렬 불변 (Property 3) | fast-check property test (≥100회) — `bedrockuser-*` 상단 + 그룹 내 `localeCompare` | `listProfiles()` 정렬부 · P3 테스트 | **PASS** |
| I4 | BedrockUser ARN 파싱 불변 (Property 4) | fast-check property test (≥100회) — 이메일/비이메일/분해불가 후보 규칙, 항상 문자열 반환 | ARN 후보 빌더 · P4 테스트 | **PASS** |
| I5 | SSO config 블록 왕복 (Property 5) | fast-check property test (≥100회) — `buildSsoProfileBlock` → `parseSsoProfileBlock` 왕복 복원 | `build/parseSsoProfileBlock()` · P5 테스트 | **PASS** |
| I6 | 자격증명 비저장 불변 (Property 6) | fast-check property test (≥100회) — settings.json 직렬화 + config 블록 어디에도 access key/secret/token 부재 | settings 직렬화 + config 블록 · P6 테스트 | **PASS** |
| I7 | 스모크 판정 집계 불변 (Property 7) | hypothesis property test (≥100회) — `passed == all(ok)`, `failed_paths`가 실패 경로 집합과 정확히 일치 | `scripts/smoke_frozen_backend.py::evaluate_smoke` · P7 테스트 | **PASS** |
| I8 | botocore/SDK 인증 코드 경로 존재·계약 보존 | 단위/회귀 테스트 (mock, 파일 IO 없음) — SDK v3 재구현 후 IPC 반환 형태가 재구현 전과 동일 | `aws-sso-manager.js`, `ipc-sso-handlers.js`(무변경), `src/main.js`(무변경) | **PASS** |
| I9 | hidden-import 게이트 존재 | 정적 확인 — 빌드 전 필수 4모듈(`matplotlib, scipy, langgraph, pptx`) import 게이트 스크립트 및 CI 스텝 존재 | `scripts/check_frozen_imports.py`, `.github/workflows/release.yml`, `ai-engine-server.spec` `_THIRD_PARTY` | **PASS** |
| I10 | publish placeholder 부재 | 정적 검사 — `grep -c PLACEHOLDER electron-builder.yml == 0`, owner/repo가 실제 값으로 대체 | `electron-builder.yml` publish (`owner: jangkops`, `repo: Agentic-Editor`) | **PASS** (배포 담당자 최종 확인 권장 → C-note 참조) |
| I11 | gateway.md 정합성 | 문서 리뷰 체크리스트 — SigV4 인증/필수 필드/엔드포인트(`/converse`,`/invoke`,Lambda URL)/300초 타임아웃/assume-role/재시도·`us.` prefix 폴백이 실제 코드와 일치 | `.kiro/steering/gateway.md` vs `ai_engine/gateway_module.py` | **PASS** |
| I12 | electron-builder mac 서명/공증 설정 존재 | 정적 확인 — `hardenedRuntime: true`, `gatekeeperAssess: false`, `entitlements`/`entitlementsInherit`, `notarize` 배선 및 entitlements plist 존재 | `electron-builder.yml` `mac`, `build/entitlements.mac.plist` | **PRESENT (cert user-provided)** — 서명/공증 *구성*은 존재. 실제 서명·공증은 인증서 필요(→ C1) |
| I13 | 제로 설정(zero-config) 온보딩 — 최종 사용자 무입력 | fast-check property test (≥200회) — `resolveDefaultSsoPreset(env)`가 override 없으면 조직 기본 프리셋(startUrl=`d-906617189d`, region=`us-east-1`, account=`107650139384`, role=`ViewOnlyAccess`) 반환, AE_SSO_* override 개별 반영, 항상 5개 문자열 키, 프리셋→ini 블록 secret-free | `electron/core/aws-sso-manager.js::resolveDefaultSsoPreset/DEFAULT_SSO_PRESET`, `electron/main.js::aws:ensure-default-sso-profile`, `src/main.js::showOnboardingDialog`, `tests/unit/default-sso-preset.property.test.js` | **PASS** — 앱 실행→(입력0) 자동 프로파일 생성→로그인 버튼 경로 성립. 실제 파일 IO·브라우저 로그인은 클린머신(→ C2/C4) |

### IN-REPO 재실행 명령

```bash
# JS 프로퍼티/단위/회귀 테스트 (P1~P6, 계약 보존)
npm test

# Python 스모크 집계 프로퍼티 테스트 (P7)
./venv/bin/python -m pytest scripts/test_smoke_evaluate_pbt.py -p no:cacheprovider -q

# 정적 검사 — publish placeholder 부재
grep -c PLACEHOLDER electron-builder.yml   # 기대값: 0

# hidden-import 게이트 (로컬 검증)
./venv/bin/python scripts/check_frozen_imports.py

# 제로 설정 온보딩 프로퍼티 테스트 (I13)
npx jest tests/unit/default-sso-preset.property.test.js
```

### 제로 설정 온보딩 — 최종 사용자 흐름 (I13)

앱을 받은 사내 사용자는 **아무 값도 입력하지 않는다**:

1. 앱 실행 → `~/.aws/config`에 SSO 프로파일이 없으면(`listProfiles()` 빈 배열)
   `showOnboardingDialog()`가 진입 즉시 `ensureDefaultSsoProfile()`를 호출
2. 조직 기본 프리셋(`bedrock-gw` / `ViewOnlyAccess`)으로 `~/.aws/config` 프로파일 자동 생성 (secret-free)
3. 입력 폼 없이 곧바로 로그인 다이얼로그로 진입 → **"로그인" 버튼만 클릭**
4. 브라우저 device-code SSO 인증(ViewOnlyAccess) → 로그인 후 `BedrockUser-{name}`는 STS caller ARN에서 자동 추론(무입력) → Bedrock Gateway 정상

- **관리자/다른 조직**: 온보딩의 "다른 조직 / 관리자 설정" 고급 토글에서 role을
  `AdministratorAccess`로 바꾸거나 다른 조직 값 입력 가능(조직 프리셋으로 프리필됨).
- **배포 시 값 변경**: `AE_SSO_START_URL` / `AE_SSO_REGION` / `AE_SSO_ACCOUNT_ID` /
  `AE_SSO_ROLE_NAME` / `AE_SSO_PROFILE_NAME` 환경변수로 하드코딩 기본값 override 가능.
- **기존 사용자 보존**: `~/.aws/config`에 SSO 프로파일이 이미 있는 사용자는 이 자동
  경로를 타지 않고 기존 로그인 흐름을 그대로 사용(무변경).

---

## 2. CLEAN-MACHINE 전용 항목 (저장소 안에서 검증 불가 — WHY)

> 이 항목들은 **원천적으로 저장소 안에서 확인 불가**하다. 실제 OS 러너 산출·설치·
> 실행·대화형 로그인·외부 서비스 호출이 필요하기 때문이다. 모두 "실제 클린 머신
> 검증 필요"로 표시한다.

| # | 검증 항목 | WHY 저장소 안에서 불가 | 검증 위치 | Requirements |
|---|-----------|------------------------|-----------|--------------|
| C1 | 코드서명·공증 Gatekeeper 통과 (`spctl -a`) | Apple Developer ID 인증서(USER-PROVIDED)와 Apple 공증 서비스가 필요. 서명 결과는 macOS Gatekeeper에서만 판정 | macOS Clean_Machine | 3.3, 3.4 |
| C2 | 설치·실행 (`.dmg`/`.exe` 설치 후 기동) | 설치형 산출물의 설치·런타임 동작은 실제 OS에서만 재현 | macOS/Windows Clean_Machine | 7.1, 7.2 |
| C3 | 동결 바이너리(`ai_engine_dist`) 30초 기동 + 기능 스모크 | PyInstaller onedir는 크로스컴파일 불가, 동결 런타임 동작은 대상 OS에서만 확인 | Clean_Machine (설치 앱) | 1.4, 1.5, 5.6 |
| C4 | 실제 SSO device-code 브라우저 로그인 | 대화형 브라우저 device-code 인증은 실사용자 로그인이 필요 — mock으로 대체 불가 | Clean_Machine (설치 앱) | 2.1, 5.7 |
| C5 | 실제 게이트웨이/Vertex 호출 | 유효 임시 자격증명 + 실제 Bedrock Gateway/Secrets Manager/Vertex 엔드포인트 호출 필요 | Clean_Machine (설치 앱) | 2.8, 5.5 |
| C6 | CI OS 러너(macos/windows) 동결 빌드 산출 | `macos-latest`/`windows-latest` 러너에서만 OS별 `ai_engine_dist/ai-engine-server/` 산출 가능(크로스컴파일 불가) | GitHub Actions matrix | 1.1, 1.2 |

---

## 3. 사용자 자산 · 배포 담당자 확인 항목

- **Signing_Assets (USER-PROVIDED, R3.6)**: Apple Developer ID 인증서 및 공증 자격
  (`CSC_LINK`/`CSC_KEY_PASSWORD`, `APPLE_ID`/`APPLE_APP_SPECIFIC_PASSWORD`/`APPLE_TEAM_ID`)
  은 **사용자가 제공하는 자산**이며 이 스펙은 발급하지 않는다. 자산 미제공 시 빌드는
  미서명/미공증 산출물로 계속되며(R3.5), Gatekeeper 통과는 보장되지 않는다.
- **C-note (electron-builder publish owner/repo)**: `owner: jangkops` / `repo:
  Agentic-Editor`로 대체 완료(placeholder 잔존 0). 다만 릴리스 대상 저장소가 실제
  배포 origin과 일치하는지는 **배포 담당자가 최종 확인**해야 한다(R6.4, CONFIRM).

---

## 4. 최종 수용 게이트 — 클린머신에서 실행하는 정확한 명령

> 아래 절차를 **AWS CLI·Python·`~/.aws/config`가 없는 클린 머신**에서 수행한다.
> 하나라도 실패하면 **배포 미승인** + 불합격 항목 보고(R7.5).

### 4-1. OS별 동결 백엔드 빌드 (해당 OS 러너/머신에서)

```bash
# macOS
npm run build:python
# → ai_engine_dist/ai-engine-server/ai-engine-server 생성 확인

# Windows (PowerShell)
npm run build:python
# → ai_engine_dist\ai-engine-server\ai-engine-server.exe 생성 확인
```

### 4-2. 설치 (서명·공증된 산출물 사용 — C1/C2)

```bash
# macOS: .dmg 마운트 후 /Applications 로 드래그 설치, 그 다음 Gatekeeper 판정
spctl -a -vvv -t install "/Applications/AI Editor.app"
#   기대: "accepted" + "source=Notarized Developer ID"  (미승인/미공증이면 게이트 불합격)

# Windows: AI Editor-Setup-<version>.exe 실행하여 설치
```

### 4-3. 앱 실행 + 동결 백엔드 30초 기동 + 기능 스모크 (C3)

```bash
# 설치 앱 실행 후, 동결 바이너리를 대상으로 스모크 (경로는 설치 위치에 맞게 지정)
./venv/bin/python scripts/smoke_frozen_backend.py \
  --target frozen \
  --binary "/Applications/AI Editor.app/Contents/Resources/ai_engine_dist/ai-engine-server/ai-engine-server"
#   → /health 30초 폴링 + LLM 채팅/PPTX/다이어그램/이미지/하이브리드 렌더 경로 1회 실행
#   → evaluate_smoke 판정이 passed=true 여야 통과 (실패 시 종료코드 ≠ 0, 실패 경로 보고)
```

### 4-4. 실제 SSO device-code 로그인 (C4) + 게이트웨이/Vertex 호출 (C5)

1. 설치 앱에서 `~/.aws/config`가 없으면 온보딩 화면이 뜬다 → start URL / region /
   account id / role name 입력하여 프로파일 생성.
2. SSO 로그인 클릭 → 브라우저 device-code 인증 완료 → 자격증명이 백엔드에 주입되는지
   확인.
3. 짧은 LLM 채팅 1회 → Bedrock Gateway 정상 응답 확인(C5).
4. (선택) 이미지 생성 시 Secrets Manager 기반 Vertex 자동 활성화가 정상 동작하거나
   실패해도 로그인/기타 기능이 계속되는지 확인.

### 4-5. 판정

- 4-2 `spctl -a`가 accepted + Notarized, 4-3 스모크 `passed=true`, 4-4 로그인·게이트웨이
  호출 성공 → **배포 승인 가능**.
- 위 중 **하나라도 불합격** → **배포 미승인**, 불합격 항목(경로명·원인·`spctl` 출력)을
  보고(R7.5).

---

## 5. 요약

| 범주 | 항목 수 | 현재 상태 |
|------|---------|-----------|
| IN-REPO (I1~I11) | 11 | 전부 **PASS** (Property 1~7 통과, import 게이트 존재, placeholder 부재, gateway.md 정합, 계약 보존) |
| IN-REPO 구성 존재 (I12) | 1 | **PRESENT** — mac 서명/공증 구성 존재, 인증서 USER-PROVIDED |
| CLEAN-MACHINE 전용 (C1~C6) | 6 | **실제 클린 머신 검증 필요** — §4 명령으로 최종 게이트 수행 |
| 배포 담당자 확인 | 2 | Signing_Assets(USER-PROVIDED), publish owner/repo(CONFIRM) |

**결론**: 저장소 안에서 검증 가능한 모든 항목은 통과했다. 배포 승인은 §4의 클린머신
설치 스모크 최종 게이트가 전부 통과할 때에만 내려야 한다. 클린머신 스모크가 하나라도
불합격이면 배포를 승인하지 않는다.
