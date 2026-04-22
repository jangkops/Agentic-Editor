# AI Editor 복원 상태 — 최종

## 작동 확인된 기능

### Bedrock Gateway 연동
- ✅ SigV4 인증 (botocore.auth.SigV4Auth)
- ✅ BedrockUser-{username} assume role
- ✅ SSO 프로파일 선택 (bedrock-gw)
- ✅ 단일 모델 호출 (동기 ALLOW 응답)
- ✅ 병렬 모델 호출 (세마포어 1 + 1초 딜레이로 rate limit 대응)
- ✅ ACCEPTED 응답 시 S3 폴링 fallback (30초)
- ✅ 자격증명 5분 캐시

### 프론트엔드
- ✅ 단일/병렬 모드 토글
- ✅ 모델 검색 드롭다운 (대분류별 모델 수 표시)
- ✅ 병렬 모델 선택 (중복 허용, 스킬/커스텀 role 설정)
- ✅ 가운데 패널 — 모델별 결과 카드 그리드 (실시간 업데이트)
- ✅ 우측 패널 — 모델 리스트 상태 표시
- ✅ 합의 도출 (성공한 모델 중 자동 선택)
- ✅ 대화 세션 탭
- ✅ 파일 탐색기 (폴더 열기, 재귀 탐색, 우클릭 메뉴)
- ✅ 파일/폴더 생성, 이름 변경
- ✅ Monaco 에디터 (탭, 언어 자동감지)
- ✅ 터미널 (다중 탭, PTY)
- ✅ 스킬 관리 (추가/편집/삭제, GitHub MD import)
- ✅ 파일 첨부 (PDF, PPTX, 이미지)
- ✅ 복사 (📋 아이콘 + 우클릭 메뉴)
- ✅ SSO 로그인 다이얼로그 (프로파일 select + BedrockUser 입력)
- ✅ 설정 (자격증명 확인, Connection 테스트, 프로파일 전환)
- ✅ 사용량 대시보드
- ✅ 에디터 패널 단축키 리사이즈

### 백엔드
- ✅ FastAPI 서버 (health, models, run-stream, run, workflow, quota)
- ✅ 에이전트 그래프 (Planner→Coder→Reviewer→Executor)
- ✅ 도구 레지스트리 (read/write/list/run/search)
- ✅ 체크포인트 저장소

## 실행 방법
```bash
npm run dev
```

## 아키텍처
```
Electron → preload.js (IPC) → main.js (fs, SSO, terminal, settings)
         → src/index.html + main.js (UI)
         → http://localhost:8765 (FastAPI)
              → gateway_module.py (botocore SigV4 + BedrockUser assume role)
              → https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1/converse
```

## 추가 피드백 (우선 구현 필요)

### 1. 설정 — 글자 크기 조절
- 설정 패널에서 에디터 글자 크기 늘리기/줄이기 기능
- Monaco editor의 fontSize를 동적으로 변경해야 함

### 2. 단일/병렬 호출 정상 동작 확인
- 단일 모드: 스트리밍 응답이 채팅에 실시간 표시되는지
- 병렬 모드: 모든 모델에 동시 호출 → 각 카드에 결과 표시되는지
- 에러 핸들링 (모델 미지원, 타임아웃 등)

### 3. 채팅 메시지 — Copy/RunCommand 버튼
- 각 AI 답변에 두 가지 액션 버튼 필요:
  - **Copy 버튼** (클립보드 아이콘 — 두 개 겹친 사각형): 답변 텍스트를 클립보드에 복사
  - **RunCommand 버튼** (터미널 아이콘 — >_ 모양): 답변 중 명령어를 터미널에서 실행
- 현재 글자 색이 너무 어두워서 가독성 나쁨 → 밝게 개선
- 사용자 메시지와 AI 답변의 시각적 구분을 일관성 있게

### 4. 파일 트리 디자인 개선
- 폴더/파일 아이콘 구분 명확하게
- 들여쓰기, 폴더 열기/닫기 애니메이션
- 파일 확장자별 아이콘 색상 차별화
- 전체적으로 가독성 좋고 깔끔한 VS Code 스타일

### 5. 검색 결과 → 에디터 연동
- 검색에서 코드 찾은 결과 클릭 시 해당 파일이 에디터 탭에 열리고
- 검색된 텍스트가 하이라이트(강조) 표시되어야 함
