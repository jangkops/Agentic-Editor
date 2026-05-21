# 남은 UI/UX 수정 사항 (새 대화에서 진행)

## 현재 커밋 상태 (gamma branch, fc1251f + 02ea506)

### ✅ 완료된 핵심 기능
1. **서버 103개 모델 반환** — `ai_engine/server.py` 패치 (chat 76 + img 17 + vid 1 + emb 7 + rer 2)
2. **클라이언트 모델 합치기** — `src/main.js` loadModelsFromServer + SSO 다이얼로그에서 extraCatalogs 합침
3. **카테고리 분류 드롭다운** — `src/model-dropdown-ui.js` (별도 파일, main.js 오버라이드)
4. **denylist 자동 초기화** — 앱 시작 시 디스크 denylist 클리어
5. **병렬 호출 수정** — `_expandedSlots` 선언 순서 (TDZ 버그 수정)
6. **runPipeline 함수** — 단계별 모델 전환 순차 실행
7. **모드 무관 추천 검사** — sendMessage에서 병렬 모드에서도 추천 카드 표시
8. **model-recommender.js 전체** — 24개 작업 + 17개 전략 + 버전 정렬 + image-prompt/video-prompt

### ❌ 누락된 UI/UX 수정 (새 대화에서 적용 필요)

#### 1. 병렬 카드 모달 확장
- **현재**: 카드 "확장" 버튼 클릭 시 그리드 아이템이 길어져 페이지 늘어남
- **목표**: 확장 시 모달 팝업으로 표시 (80% 너비, 80vh 높이, overlay 클릭/ESC 닫기)
- **위치**: `src/main.js` `renderParallelResultGrid()` 내 `.card-toggle` 이벤트 핸들러
- **함수**: `_showParallelCardModal(r)` 추가 필요
- **CSS**: `.parallel-card-modal` 관련 스타일 (`src/styles/components.css`)

#### 2. 생성 파일 카드 — 수정/삭제/다운로드 버튼
- **현재**: 미리보기 + 다운로드만 있음
- **목표**: 수정/삭제/다운로드 3개 버튼 + 이모지 제거 (📄→"PDF", 📊→"PPTX" 텍스트)
- **위치**: `src/main.js` 내 `// ── 비-이미지 (PDF/PPTX) 단일 카드 ──` 블록
- **함수**: `_attachGeneratedFileForEdit(info)` 추가 필요 — 파일을 채팅 첨부로 등록
- **이미지 갤러리**: `.tit-open-folder` 이모지(📂) → "폴더 열기" 텍스트 + 수정/삭제 버튼 추가

#### 3. 합의 도출 카드 이모지 제거
- **현재**: "🔗 이 모델로 계속 대화", "🔀 병렬 모드 유지"
- **목표**: 이모지 없이 텍스트만 ("이 모델로 계속 대화", "병렬 모드 유지")
- **위치**: `src/main.js` 내 `lockBtn.textContent`, `parallelBtn.textContent`, 관련 system 메시지

#### 4. 로컬 터미널 — 커서만 깜빡임 수정
- **현재**: `processManager.createTerminal(id, mainWindow)` — mainWindow가 null이라 데이터 송신 안 됨
- **목표**: `event.sender`(webContents)를 전달하여 데이터 송신 + cwd로 현재 폴더 적용
- **위치**: 
  - `electron/src/ipc-terminal-handlers.js` — `processManager.createTerminal(terminalId, sender, opts)` 호출
  - `electron/core/process-manager.js` — `createTerminal(id, target, opts)` 시그니처 변경
  - `src/main.js` `addTerminal()` — 로컬에서도 `cwd: state.folderPath` 전달

#### 5. 서버 병렬 호출 heartbeat 강화
- **현재**: 배치 사이에만 heartbeat → 첫 배치가 180초 이상 걸리면 클라이언트 idle timeout
- **목표**: 배치 내부에서도 30초마다 heartbeat 전송
- **위치**: `ai_engine/server.py` `/api/agents/run-parallel` 엔드포인트의 `parallel_stream()` 내부

#### 6. 추천 카드 탭 CSS
- **현재**: 추천 카드에 탭(단일/병렬/파이프라인) 스타일 없음
- **목표**: `.recommend-tabs`, `.recommend-tab`, `.recommend-tab-body`, `.recommend-strategy-list` 등
- **위치**: `src/styles/components.css`

## 작업 원칙 (재발 방지)

1. **main.js 직접 수정 최소화** — 가능하면 별도 파일로 분리 (model-dropdown-ui.js 패턴)
2. **변경 후 즉시 git commit** — 에디터 "되돌리기" 기능이 uncommitted 변경을 복원함
3. **서버 변경 시 Python 패치 스크립트 사용** — str_replace가 들여쓰기 문제로 실패하는 경우 많음
4. **서버 재시작 필수** — uvicorn은 파일 변경 시 자동 reload 안 됨 (--reload 플래그 없으면)

## 파일 구조 참고

```
src/
  main.js                    — 5000줄+ 메인 (수정 주의)
  model-recommender.js       — 추천 엔진 (24개 작업 + 17개 전략)
  model-dropdown-ui.js       — 카테고리 분류 드롭다운 (별도 파일, 안정적)
  index.html                 — script 로드 순서: model-recommender → main → model-dropdown-ui
  styles/layout.css          — 드롭다운 CSS
  styles/components.css      — 추천 카드/병렬 카드 CSS

ai_engine/
  server.py                  — /api/models 엔드포인트 (video/embed/rerank 카탈로그 포함)
  gateway_module.py          — Bedrock Gateway 클라이언트

electron/
  src/ipc-terminal-handlers.js  — 터미널 IPC (로컬/원격 분기)
  core/process-manager.js       — PTY 프로세스 관리
```
