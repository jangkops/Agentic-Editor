# 배포 가이드 (Mogam Works)

> 빌드 → 서명 → 릴리스 → 수신자 설치 → 문제 해결까지, **현재 코드 기준**으로 검증한 절차입니다.
> 기준 커밋: 2026-09-16, `package.json` 버전 0.5.4. 명령·파일명은 모두 저장소의 실제 설정
> (`package.json`, `electron-builder.yml`, `.github/workflows/release.yml`, `scripts/`)에서 확인했습니다.
> 개발 환경 설정은 [README 5장](../README.md#5-시작하기-개발), 요약은 [README 10장](../README.md#10-빌드배포)을 보세요.

## 1. 배포 채널 한눈에

| 대상 | 산출물 | 만드는 곳 | 비고 |
|---|---|---|---|
| macOS (Apple Silicon / Intel) | `dist_electron/Mogam Works-<버전>-arm64.dmg`, `-x64.dmg` (+ 같은 이름의 `.zip`) | 로컬 Mac 또는 CI `macos-latest` | **미서명이 기본**. 수신자는 `scripts/install-mac.command`로 Gatekeeper 경고를 우회 |
| Windows (x64) | `dist_electron/Mogam Works-Setup-<버전>.exe` (NSIS, 사용자 단위 설치, 설치 경로 선택 가능) | Windows 머신 또는 CI `windows-latest` | PyInstaller는 크로스컴파일이 안 되므로 반드시 Windows에서 빌드 |
| Linux | AppImage 타깃이 `electron-builder.yml`에 정의만 되어 있음 | — | 릴리스 CI 매트릭스에 Linux 러너가 없어 **자동 산출되지 않음** |
| GitHub Releases | 위 산출물 + `latest*.yml` | `release.yml` (`v*` 태그) | `electron-updater`는 앱 의존성에 **없음** → 자동 업데이트는 동작하지 않고 수동 재설치 |

앱 식별자: `appId com.internal.mogam-works`, `productName Mogam Works`. 백엔드(FastAPI 사이드카)는 PyInstaller로 동결해
`resources/ai_engine_dist/ai-engine-server/`에 함께 들어가며, 앱이 시작할 때 이 바이너리를 띄웁니다(`electron/core/process-manager.js`).

## 2. 사전 준비 (빌드 머신)

- Node.js 18 이상(CI는 20), Python 3.11 이상(개발 venv는 3.14 — README 11장).
- 조직 AWS SSO 접근 권한과 `BedrockUser-{이름}` IAM 역할. 값(SSO 시작 URL·계정 ID)은 관리자가 안내하며, 앱은 첫 로그인 시 프로파일을 자동 생성합니다.

```bash
git clone https://github.com/jangkops/Agentic-Editor.git
cd Agentic-Editor
npm install            # postinstall 이 node-pty 를 Electron ABI 로 리빌드(@electron/rebuild)
npm run setup:venv     # ai_engine/.venv 생성 + requirements.txt + pyinstaller 설치 (scripts/setup-venv.js)
```

## 3. 로컬 빌드

```bash
npm run build:python                                # 1) 백엔드 동결
npx electron-builder --mac --arm64 --publish never  # 2) arm64 DMG/zip 만 (사내 테스트 배포에 주로 사용)
npm run dist:mac                                    #    또는 arm64 + x64 전부
npm run dist:win                                    #    Windows 에서만 실행
npm run dist                                        # build:python + electron-builder 한 번에
```

- `build:python`(`scripts/build-python.js`)은 `ai-engine-server.spec`으로 `PyInstaller --noconfirm --clean`을 실행해
  `ai_engine_dist/ai-engine-server/`를 만들고, 오프라인 실행을 위해 fastembed 다국어 임베딩 모델을 실행파일 옆
  `fastembed_models/`에 미리 내려받습니다(런타임의 `FastEmbedProvider`가 자동 인식).
- 동결 전에 `scripts/check_frozen_imports.py`를 돌려 릴리스 필수 4모듈(matplotlib, scipy, langgraph, pptx)이 import 되는지
  확인하세요. spec의 `collect_all`은 미설치 패키지를 **조용히** 건너뛰므로, 이 게이트가 없으면 부팅은 되지만 런타임에 깨지는
  빌드가 나옵니다(CI는 이 스크립트를 동결 전에 실행).
- `node-pty`는 대상 아키텍처로 리빌드되어야 하고 asar 밖으로 풀립니다(`asarUnpack`).
- 산출물 위치는 `dist_electron/`, 파일명 규칙은 `${productName}-${version}-${arch}.${ext}`(mac), `${productName}-Setup-${version}.exe`(win).

## 4. 서명·공증

| 상태 | 동작 |
|---|---|
| 기본(시크릿 없음) | 미서명·미공증. `hardenedRuntime: true`, `notarize: false`. 빌드는 경고만 남기고 계속됩니다(CI의 "미서명 빌드" 비차단 폴백). |
| `CSC_LINK` + `CSC_KEY_PASSWORD` | electron-builder가 Developer ID Application 인증서로 자동 서명. |
| + `APPLE_ID`, `APPLE_APP_SPECIFIC_PASSWORD`, `APPLE_TEAM_ID` | 공증. 로컬은 `bash scripts/sign-and-notarize-mac.sh`(전제 검사 후 서명+공증+스테이플). CI에서 쓰려면 `electron-builder.yml`의 `notarize`를 켜야 합니다. |

미서명 DMG를 그대로 배포할 때는 **`bash scripts/resign-mac-dmg.sh`** 를 한 번 돌리세요. electron-builder가 만든 ad-hoc 서명은
상위 번들의 CodeResources seal이 맞지 않아 Apple Silicon에서 "손상되었기 때문에 열 수 없습니다"가 뜨는데, 이 스크립트가
`codesign --force --deep --sign -`로 유효한 ad-hoc 서명을 다시 적용하고 DMG를 재패키징합니다. 스크립트 안의 앱·DMG 경로에
버전(현재 `0.5.4`)이 박혀 있으니 버전을 올릴 때 함께 고치세요. PKG 타깃은 Developer ID **Installer** 인증서가 있을 때만
`electron-builder.yml`에서 주석을 풀어 켭니다.

## 5. 릴리스 (GitHub Actions)

`.github/workflows/release.yml`은 `v*` 태그 푸시 또는 수동 실행(workflow_dispatch)으로 돌며, `macos-latest`와 `windows-latest`
두 러너에서 각각 Node 20 + Python 3.11로 `npm ci` → 의존성 설치 + `check_frozen_imports.py` → `npm run build:python` →
`npx electron-builder --mac|--win --publish always`(`GITHUB_TOKEN`) 순으로 실행하고, 산출물을 GitHub Releases와 워크플로
아티팩트(`ai-editor-mac`, `ai-editor-win`)에 올립니다. **CI에는 테스트 스텝이 없습니다**(README 11장) — 태그 전에 로컬에서 돌립니다.

```bash
# 1) 버전 올리기: package.json "version" (+ scripts/resign-mac-dmg.sh 의 버전 상수)
# 2) 로컬 게이트
npx jest tests/unit/ --coverage=false
ai_engine/.venv/bin/python -m pytest -q -p no:cacheprovider scripts/test_health_boot_id.py scripts/test_api_models_catalog_creds.py scripts/test_capability_api_models_baseline.py
node --check electron/main.js
# 3) 커밋·태그·푸시 (gamma 와 main 을 함께 유지)
git tag -a v0.5.5 -m "Release v0.5.5"
git push origin gamma && git push origin gamma:main && git push origin v0.5.5
# 4) Actions 에서 두 러너 모두 성공했는지, Release 에 dmg/zip/exe 가 붙었는지 확인
```

## 6. 수신자 패키지 만들기 (사내 테스트 배포)

한 폴더에 아래를 담아 zip 또는 드라이브로 전달합니다. 파일명은 바꾸지 않아도 됩니다 — 설치 스크립트가 같은 폴더에서
CPU에 맞는 **가장 최근** DMG를 자동으로 고르기 때문에, 새 버전은 DMG만 갈아 넣으면 됩니다.

```
Mogam-Works-<버전>/
├── Mogam Works-<버전>-arm64.dmg     # Apple Silicon
├── Mogam Works-<버전>-x64.dmg       # Intel Mac (필요 시)
├── install-mac.command              # scripts/install-mac.command 사본
└── 설치 안내 (이 문서 7~8절 사본 또는 README 10장)
```

Windows에는 `Mogam Works-Setup-<버전>.exe` 하나면 됩니다.

## 7. 수신자 설치

**macOS** — DMG와 `install-mac.command`를 같은 폴더에 두고 스크립트를 실행합니다(Finder 더블클릭 또는 우클릭 → 열기,
터미널이면 `bash install-mac.command`). 스크립트는 다음을 순서대로 합니다.

1. CPU 감지(arm64/x64) → 맞는 DMG 선택
2. DMG 마운트 → `Mogam Works.app`을 `/Applications`로 복사(기존 버전은 교체)
3. ad-hoc 깊은 재서명(`codesign --force --deep --sign -`) → Apple Silicon "손상됨" 오류 예방
4. `com.apple.quarantine` 제거 → Gatekeeper 차단 해제, 검증 결과 출력, DMG 언마운트

실행은 Launchpad 또는 응용 프로그램 폴더의 "Mogam Works". 첫 실행에 "확인되지 않은 개발자" 경고가 남으면
우클릭 → 열기, 또는 시스템 설정 → 개인정보 보호 및 보안 → "열기"를 누릅니다.

**Windows** — Setup exe 실행. 미서명이면 SmartScreen에서 "추가 정보 → 실행". 사용자 단위 설치라 관리자 권한은 필요 없습니다.

## 8. 로그인 전제조건 (설치는 됐는데 답변이 안 나올 때)

앱의 "로그인" 버튼은 내장 AWS SDK v3 device-code 흐름으로 브라우저 승인 페이지를 엽니다(`electron/core/aws-sso-manager.js`).
**AWS CLI는 필요 없습니다**(설치돼 있으면 SDK 실패 시 폴백으로만 사용). 다만 모델을 실제로 호출하려면 관리자가 미리:

| 전제조건 | 설명 |
|---|---|
| 조직 AWS SSO 멤버십 | 사용자가 조직 SSO에 등록돼 있어야 로그인 자체가 됩니다 |
| `BedrockUser-{이름}` IAM 역할 | 이 역할이 존재하고 사용자의 SSO 신원이 assume 할 수 있어야 합니다. 없으면 로그인은 되지만 호출이 `AccessDenied` |

자격증명은 어떤 파일에도 저장되지 않고, 메인 프로세스가 사이드카 메모리에 주입합니다(README 3.1). 사이드카가 재기동돼도
메인이 `/health`의 `boot_id` 변화를 보고 5초 안에 다시 주입합니다.

## 9. 문제 해결

| 증상 | 원인·조치 |
|---|---|
| 설치 스크립트 `permission denied` | `chmod +x install-mac.command` 후 `bash install-mac.command` |
| "오류: 같은 폴더에서 설치용 .dmg 파일을 찾지 못했습니다." | DMG와 스크립트를 같은 폴더에 두었는지 확인(파일명은 `*arm64*.dmg`/`*x64*.dmg`면 됨) |
| "손상되었기 때문에 열 수 없습니다" / "확인되지 않은 개발자" | 이미 설치된 앱만 있어도 `install-mac.command`를 다시 실행하면 재서명 + quarantine 제거로 복구. 수동: `xattr -cr "/Applications/Mogam Works.app"` |
| 앱은 뜨는데 모델 목록이 비거나 `AccessDenied` | 8절 전제조건(SSO 멤버십, BedrockUser 역할) 미충족 → 관리자에게 요청 |
| 사이드카가 안 뜸 / 포트 8765 충돌 | `lsof -i :8765`로 점유 프로세스 확인. 개발 모드의 `npm run dev`는 `predev`에서 8765 점유 프로세스를 정리함 |
| SSO 세션 만료 | 앱에서 다시 로그인. CLI가 있으면 `aws sso login --profile bedrock-gw`. 토큰 캐시는 `~/.aws/sso/cache/` |
| Electron 빌드 실패(node-pty ABI 등) | `REPO=/절대/경로/agentic-editor` 로 두고 `rm -rf "$REPO/node_modules"` 후 `npm install`(postinstall 리빌드). Electron 캐시는 `~/Library/Caches/electron` |
| 동결 빌드가 부팅 후 import 오류 | `python scripts/check_frozen_imports.py`로 누락 모듈 확인 → venv에 설치 후 `npm run build:python` 재실행 |
| venv 손상 | `rm -rf "$REPO/ai_engine/.venv"`(절대 경로) 후 `npm run setup:venv` |

## 10. 릴리스 전 체크리스트

- [ ] `package.json` 버전(+ `resign-mac-dmg.sh` 상수) 올림, README 11장 "현재 상태" 갱신
- [ ] `npx jest tests/unit/ --coverage=false` 통과, 관련 `scripts/test_*.py` 통과, `node --check electron/main.js`
- [ ] `npm run build:python` → `check_frozen_imports.py` 통과
- [ ] arm64(+ x64) DMG 빌드 → `resign-mac-dmg.sh` → 깨끗한 Mac에서 `install-mac.command`로 설치 리허설
- [ ] 스모크: 로그인 → 모델 목록 → 채팅 1회 → 문서 생성(PPTX) 1회 → 폴더 열기·파일 저장
- [ ] 태그 `v*` 푸시 → Actions 두 러너 성공 → Release 자산 확인
- [ ] 수신자 패키지(6절) 구성, 릴리스 노트에 변경점·알려진 제한 기재

## 11. 롤백

자동 업데이트가 없으므로 롤백은 "이전 산출물을 다시 배포"입니다. GitHub Releases에서 문제 릴리스의 자산을 지우거나
`gh release edit v<이전> --latest`로 이전 릴리스를 latest로 되돌리고, 수신자에게는 이전 DMG/exe와 `install-mac.command`를
다시 전달합니다. 코드 되돌림은 `git revert`로 새 커밋을 만들어 `gamma`·`main`에 푸시합니다(강제 푸시 금지).

## 12. 문서 이력

2026-07 v0.4.0 테스트 배포 때 저장소 루트에 있던 5개 문서(`DEPLOYMENT_GUIDE.md`, `DEPLOYMENT_CHECKLIST.md`,
`DELIVERY_CHECKLIST.md`, `DEPLOYMENT_PACKAGE_README.md`, `TEST_DELIVERY.md`)를 2026-09-16에 이 문서 하나로 통합했습니다.
옛 문서는 존재하지 않는 npm 스크립트(`build:mac` 등)·다른 산출물 이름·v1.x 버전 표기·특정 PR 일정을 담고 있어 저장소에서
제외했고, 원본은 저장소 밖(로컬 백업 폴더)에 보관합니다.
