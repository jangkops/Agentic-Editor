# AGENTS.md — ai-editor (agentic-editor)

## Design spec (읽고 시작할 것)

UI·스타일·사용자 노출 문구 작업 전에 **`~/DESIGN.md`를 먼저 읽는다.**

- `~/DESIGN.md` = 조직 공용 기본 스펙 (토큰 + Voice/Narrative/Principles/Personas/States/Motion)
- 이 프로젝트만의 예외는 아래 **Project overrides**에 적는다. 적히지 않은 항목은 전부 `~/DESIGN.md`를 따른다
- 코드와 스펙이 어긋나면 **스펙이 진실**이다. 코드를 고치거나, 스펙을 고쳐 커밋한다
- 팔레트·폰트·간격·라운딩 값을 새로 만들지 않는다. 필요하면 DESIGN.md 수정을 먼저 제안한다

## Stack

- Electron 데스크톱 앱 — 메인 프로세스 `electron/`
- 터미널: `node-pty` + `xterm` (+ `xterm-addon-fit`)
- 원격/클라우드: `ssh2`, AWS SDK v3 (`sso`, `sso-oidc`, `sts`, `credential-providers`)
- Python 사이드카: `ai_engine/` → `ai-engine-server.spec`으로 번들
- 테스트: `jest` + `fast-check` (property-based)
- 패키징: `electron-builder` (`electron-builder.yml`)

## Project overrides

- **다크 전용.** 라이트 테마를 만들지 않는다 (에디터·터미널 UI). `~/DESIGN.md`의 Dark 컬럼만 사용
- 터미널 영역의 색은 xterm 테마 설정을 정본으로 두고, 그 값을 DESIGN.md 팔레트와 일치시킨다. 터미널 안의 ANSI 색은 팔레트 제약 예외
- 에디터·터미널 본문은 `var(--font-mono)`, 그 외 UI 크롬은 `var(--font-ui)`. CSS 변수를 우회해 폰트를 직접 지정하지 않는다
- 밀도: 에디터 크롬(탭바·상태바·사이드바)은 `~/DESIGN.md` 기본보다 한 단계 촘촘하게 — 높이 28px, 패딩 `4px 8px` 허용
- `.generated/` 산출물(슬라이드·리포트 HTML)도 같은 토큰을 쓴다. 템플릿에 새 색을 심지 않는다

## 주의

- 자격증명(AWS SSO 토큰, SSH 키)은 로그·화면·에러 메시지에 절대 노출하지 않는다. 에러는 원인과 다음 행동만
- 목업·데모 데이터는 `SAMPLE` 표기. 실제 계정 ID·호스트명·경로를 화면 예시에 넣지 않는다
- `node_modules`, `dist_electron`, `build`, `coverage`, `ai_engine_dist`, `.generated`는 산출물이다. 직접 수정하지 않는다
