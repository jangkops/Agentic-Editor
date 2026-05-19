# 구현 계획: 미디어 생성·편집 기능

## 개요

AI 에디터의 이미지 편집(inpaint/outpaint) 도구 추가, 기존 생성 도구 검증/개선, 채팅 인라인 이미지 렌더링, 파일 미리보기 Web Component 구현을 단계적으로 진행한다. 백엔드는 Python FastAPI (`ai_engine/server.py`), 프론트엔드는 Electron + Vanilla JS Web Component로 구현한다.

## Tasks

- [ ] 1. GatewayClient.invoke_model() 메서드 추가 및 edit_image 도구 등록
  - [ ] 1.1 GatewayClient에 invoke_model 메서드 구현
    - `ai_engine/gateway_module.py`에 `invoke_model(model_id, body, timeout=30)` async 메서드 추가
    - 기존 `_get_creds()` / `_sign()` 메서드를 재사용하여 SigV4 서명 처리
    - 성공 시 `{"images": [...]}`, 실패 시 `{"error": "..."}` 반환
    - 타임아웃 30초 설정, httpx.AsyncClient 사용
    - _Requirements: 2.6, 3.6_

  - [ ] 1.2 AGENT_TOOLS에 edit_image toolSpec 등록
    - `ai_engine/server.py`의 `AGENT_TOOLS["tools"]` 배열에 edit_image 스키마 추가
    - mode(enum: inpaint/outpaint), image_path, prompt 필수 파라미터 정의
    - mask_path(inpaint용), direction/extend_pixels(outpaint용) 조건부 파라미터 정의
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 1.3 _execute_tool에 edit_image 라우팅 추가
    - `_execute_tool()` 함수에 `elif tool_name == "edit_image":` 분기 추가
    - `asyncio.run(_tool_edit_image(tool_input, project_path))` 호출
    - _Requirements: 8.1, 8.6_

  - [ ]* 1.4 AGENT_TOOLS 스키마 단위 테스트 작성
    - **Property 11: edit_image mode 유효성 검증**
    - **Validates: Requirements 8.5**

- [ ] 2. _tool_edit_image() inpaint 모드 구현
  - [x] 2.1 입력 유효성 검증 로직 구현
    - mode 검증: "inpaint"/"outpaint" 외 값 → "invalid-mode" 에러
    - image_path 파일 존재 여부 → "file-not-found" 에러
    - 이미지 형식 검증 (PNG/JPEG만 허용) → "invalid-image" 에러
    - 파일 크기 5MB 제한 → "invalid-image" 에러
    - mask_path 존재 여부 → "mask-not-found" 에러
    - 마스크-원본 해상도 일치 검증 → "mask-dimension-mismatch" 에러
    - prompt 길이 검증 (1~512자) → "invalid-parameter" 에러
    - _Requirements: 2.2, 2.4, 2.5, 2.8, 2.9, 8.5_

  - [x] 2.2 inpaint 모델 호출 및 폴백 체인 구현
    - 원본 이미지 + 마스크 이미지를 base64 인코딩
    - Titan Image v2 INPAINTING taskType 요청 본문 구성
    - `gw.invoke_model()` 호출, 실패 시 Nova Canvas로 폴백 (`_resolve_callable_model_id` 거쳐 us./global. prefix 자동 부착)
    - 타임아웃 30초 초과 또는 에러 응답 시 다음 모델로 전환
    - 모든 모델 실패 시 "model-unavailable" 에러 반환
    - _Requirements: 2.1, 2.6, 2.7_

  - [ ] 2.3 inpaint 결과 저장 및 응답 구성
    - 성공 시 `.generated/inpaint_{timestamp}.png` 파일 저장
    - 응답 JSON: `{"path", "model", "width", "height"}` 반환
    - _Requirements: 2.3, 8.6_

  - [ ]* 2.4 inpaint 입력 유효성 검증 Property 테스트
    - **Property 4: edit_image inpaint 입력 유효성 검증**
    - **Validates: Requirements 2.2, 2.8, 2.9**

  - [ ]* 2.5 편집 폴백 체인 Property 테스트
    - **Property 6: 이미지 편집 폴백 체인 정확성**
    - **Validates: Requirements 2.6, 3.6**

- [ ] 3. _tool_edit_image() outpaint 모드 구현
  - [x] 3.1 outpaint 입력 유효성 검증 로직 구현
    - image_path 파일 존재 여부 → "file-not-found" 에러
    - 이미지 형식 검증 (PNG/JPEG/WEBP 허용) → "invalid-input" 에러
    - 원본 크기 한 변 4096px 제한 → "invalid-input" 에러
    - direction 허용 목록 검증 (left/right/top/bottom, 1~4개) → "invalid-parameter" 에러
    - extend_pixels 범위 검증 (1~1024) → "invalid-parameter" 에러
    - prompt 길이 검증 (1~512자) → "invalid-parameter" 에러
    - _Requirements: 3.2, 3.4, 3.5, 3.7_

  - [x] 3.2 outpaint 모델 호출 및 폴백 체인 구현
    - 원본 이미지 base64 인코딩
    - Titan Image v2 OUTPAINTING taskType 요청 본문 구성
    - direction/extend_pixels를 outPaintingParams에 매핑
    - `gw.invoke_model()` 호출, 실패 시 Nova Canvas로 폴백
    - _Requirements: 3.1, 3.6_

  - [x] 3.3 outpaint 결과 저장 및 응답 구성
    - 성공 시 `.generated/outpaint_{timestamp}.png` 파일 저장
    - 응답 JSON: `{"path", "model", "width", "height"}` 반환 (최종 이미지 크기 포함)
    - _Requirements: 3.3_

  - [ ]* 3.4 outpaint 입력 유효성 검증 Property 테스트
    - **Property 5: edit_image outpaint 입력 유효성 검증**
    - **Validates: Requirements 3.2, 3.5, 3.7**

- [ ] 4. Checkpoint — 백엔드 편집 도구 검증
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. generate_image 폴백 체인 검증 및 개선
  - [x] 5.1 기존 _tool_generate_image 폴백 로직 검증 및 개선
    - 폴백 체인 순서 확인: SD3.5 → Stable Image Core → Titan Image v2
    - prompt 빈 문자열/누락 시 에러 응답 검증 (Req 1.6)
    - prompt 2000자 초과 시 에러 응답 추가 (Req 1.7)
    - 모든 모델 실패 시 마지막 에러 메시지(200자 제한) 포함 확인 (Req 1.2)
    - 성공 응답에 path, model, size 필드 포함 확인 (Req 1.3)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [ ]* 5.2 이미지 생성 폴백 체인 Property 테스트
    - **Property 1: 이미지 생성 폴백 체인 정확성**
    - **Validates: Requirements 1.1**

  - [ ]* 5.3 이미지 생성 입력 유효성 검증 Property 테스트
    - **Property 2: generate_image 입력 유효성 검증**
    - **Validates: Requirements 1.4, 1.7**

  - [ ]* 5.4 이미지 생성 성공 응답 구조 Property 테스트
    - **Property 3: 이미지 생성 성공 시 응답 구조 완전성**
    - **Validates: Requirements 1.3**

- [x] 6. generate_pdf 검증 및 개선
  - [x] 6.1 기존 _tool_generate_pdf 검증 및 개선
    - title 빈 문자열/누락 시 "title is required" 에러 추가 (Req 4.5)
    - sections 빈 배열/누락 시 "sections is required" 에러 확인 (Req 4.4)
    - Heading2 스타일 14pt 볼드, Normal 스타일 10pt 확인 (Req 4.2)
    - 응답에 path, pageCount, fileSize(sizeBytes) 포함 확인 (Req 4.3)
    - reportlab 미설치 시 missing-dep 에러 확인 (Req 4.6)
    - 파일 저장 실패 시 "pdf-generation-failed" + detail 확인 (Req 4.7)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7_

  - [ ]* 6.2 PDF 생성 라운드트립 Property 테스트
    - **Property 7: PDF 생성 라운드트립**
    - **Validates: Requirements 4.1, 4.3**

- [x] 7. generate_pptx 검증 및 개선
  - [x] 7.1 기존 _tool_generate_pptx 검증 및 개선
    - title 표지 슬라이드 생성 확인 (Req 5.1)
    - slideCount = len(slides) + 1 (표지 포함) 확인 (Req 5.4)
    - imagePrompt 포함 시 generate_image 호출 및 이미지 삽입 확인 (Req 5.2)
    - 이미지 생성 실패 시 슬라이드 유지하고 계속 진행 확인 (Req 5.3)
    - slides 빈 배열/누락 시 "slides is required" 에러 확인 (Req 5.5)
    - python-pptx 미설치 시 missing-dep 에러 확인 (Req 5.6)
    - layout 필드 (title/content/two-column) 적용 확인 (Req 5.7)
    - 파일 저장 실패 시 "pptx-generation-failed" + detail 확인 (Req 5.8)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [ ]* 7.2 PPTX 슬라이드 수 Property 테스트
    - **Property 8: PPTX 생성 슬라이드 수 정확성**
    - **Validates: Requirements 5.1, 5.3, 5.4**

- [ ] 8. Checkpoint — 백엔드 생성 도구 검증 완료
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. 채팅 인라인 이미지 렌더러 구현
  - [x] 9.1 renderMessages()에 이미지 경로 감지 및 썸네일 렌더링 추가
    - `src/main.js`의 `renderMessages()` 함수 내 도구 결과 처리 부분 수정
    - `.generated/*.png` 패턴 매칭으로 이미지 경로 추출
    - 썸네일 표시: max-width 320px, max-height 240px, object-fit: contain
    - 이미지 아래 모델명 + 크기(너비×높이 px) 메타 정보 한 줄 표시
    - 최대 4개 썸네일 표시, 초과분 "+N개 더보기" 링크
    - 단일/병렬/합의 모드 모두 동일 레이아웃 적용
    - _Requirements: 6.1, 6.3, 6.5, 6.6_

  - [x] 9.2 썸네일 클릭 시 에디터 영역 전체 보기 구현
    - 썸네일 클릭 이벤트 → 에디터 영역에 이미지 뷰어 표시
    - 뷰포트 맞춤 (max-width/height 100%, 원본 비율 유지)
    - _Requirements: 6.2_

  - [x] 9.3 이미지 로드 실패 시 에러 플레이스홀더 구현
    - `onerror` 핸들러로 에러 감지
    - 320px × 80px 영역에 파일 경로 텍스트 표시
    - _Requirements: 6.4_

  - [ ]* 9.4 채팅 이미지 표시 개수 제한 Property 테스트
    - **Property 10: 채팅 이미지 표시 개수 제한**
    - **Validates: Requirements 6.6**

- [x] 10. `<file-preview-panel>` Web Component 구현
  - [x] 10.1 파일 목록 로드 및 렌더링 구현
    - `src/components/file-preview-panel.js` 파일 생성
    - `class FilePreviewPanel extends HTMLElement` + `customElements.define('file-preview-panel', FilePreviewPanel)`
    - `.generated/` 폴더 스캔, 수정 시간 기준 최신순 정렬, 최대 100개 표시
    - 각 파일: 이름, 생성 시간(YYYY-MM-DD HH:mm), 파일 크기(bytes/KB/MB) 표시
    - 폴더 비어있거나 미존재 시 "생성된 파일이 없습니다" 메시지
    - _Requirements: 7.1, 7.2, 7.9, 7.11_

  - [x] 10.2 파일 선택 시 확장자별 뷰어 디스패치 구현
    - 이미지(.png/.jpg/.webp) → 에디터 영역 이미지 뷰어
    - PDF(.pdf) → 에디터 영역 PDF 뷰어
    - PPTX(.pptx) → 에디터 영역 PPTX 뷰어
    - 50MB 초과 파일 → 미리보기 차단, 다운로드만 허용
    - _Requirements: 7.3, 7.4, 7.5, 7.6_

  - [x] 10.3 다운로드 버튼 및 Electron 저장 다이얼로그 구현
    - 다운로드 버튼 클릭 → `window.electronAPI.showSaveDialog()` 호출
    - 사용자 선택 경로에 파일 복사
    - 실패 시 에러 메시지 표시
    - _Requirements: 7.7, 7.8_

  - [x] 10.4 파일 감시(fs.watch) 자동 갱신 구현
    - `window.electronAPI.watchDirectory('.generated/')` IPC 호출
    - 파일 변경 감지 시 2초 이내 `loadFileList()` 재호출
    - `disconnectedCallback()`에서 watcher 해제
    - _Requirements: 7.10_

  - [ ]* 10.5 formatFileSize 함수 Property 테스트
    - **Property 9: 파일 크기 포맷팅 정확성**
    - **Validates: Requirements 7.2**

  - [ ]* 10.6 파일 목록 정렬 및 제한 Property 테스트
    - **Property 12: 파일 목록 정렬 및 제한**
    - **Validates: Requirements 7.1**

- [x] 11. IPC 핸들러 구현 (파일 미리보기 지원)
  - [x] 11.1 watchDirectory IPC 핸들러 구현
    - `electron/src/ipc-fs-handlers.js`에 `watch-directory` 핸들러 추가
    - `fs.watch()` 기반 디렉토리 감시, 변경 시 renderer에 이벤트 전송
    - watcher 해제를 위한 `unwatch-directory` 핸들러 추가
    - _Requirements: 7.10_

  - [x] 11.2 showSaveDialog IPC 핸들러 구현
    - `electron/src/ipc-fs-handlers.js`에 `show-save-dialog` 핸들러 추가
    - `dialog.showSaveDialog()` 호출 후 선택 경로에 파일 복사
    - 실패 시 에러 객체 반환
    - _Requirements: 7.7, 7.8_

  - [x] 11.3 preload.js에 새 IPC 메서드 노출
    - `electron/preload.js`의 `contextBridge.exposeInMainWorld`에 추가:
      - `watchDirectory(path)` → `ipcRenderer.invoke('watch-directory', path)`
      - `unwatchDirectory(path)` → `ipcRenderer.invoke('unwatch-directory', path)`
      - `showSaveDialog(options)` → `ipcRenderer.invoke('show-save-dialog', options)`
    - _Requirements: 7.7, 7.10_

- [x] 12. 통합 및 연결
  - [x] 12.1 file-preview-panel을 사이드바/에디터 영역에 배치
    - `src/index.html`에 `<file-preview-panel>` 태그 추가
    - `src/main.js`에서 컴포넌트 import 및 초기화
    - 사이드바 메뉴에 "생성 파일" 탭 추가
    - _Requirements: 7.11_

  - [x] 12.2 채팅 이미지 클릭 → file-preview-panel 연동
    - 썸네일 클릭 시 file-preview-panel의 해당 파일 선택 상태 동기화
    - CustomEvent 기반 컴포넌트 간 통신
    - _Requirements: 6.2, 7.3_

- [ ] 13. Final Checkpoint — 전체 기능 검증
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- 각 태스크는 특정 요구사항을 참조하여 추적 가능
- Checkpoints에서 증분 검증 수행
- Property 테스트는 설계 문서의 정확성 속성을 검증
- 단위 테스트는 특정 예제와 엣지 케이스를 검증
- 검증 명령어:
  - Python: `ai_engine/.venv/bin/python -c "import ast; ast.parse(open('file').read())"`
  - JavaScript: `node --check <file>`
- 모든 LLM/이미지 모델 호출은 Bedrock Gateway 경유
- 생성 파일은 `.generated/` 폴더에 저장
- 프론트엔드는 Web Components (customElements.define), NO React/TypeScript

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["1.3", "1.4"] },
    { "id": 2, "tasks": ["2.1", "3.1", "5.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "3.2", "3.3", "6.1", "7.1"] },
    { "id": 4, "tasks": ["2.4", "2.5", "3.4", "5.2", "5.3", "5.4", "6.2", "7.2"] },
    { "id": 5, "tasks": ["9.1", "10.1", "11.1", "11.2"] },
    { "id": 6, "tasks": ["9.2", "9.3", "10.2", "10.3", "10.4", "11.3"] },
    { "id": 7, "tasks": ["9.4", "10.5", "10.6"] },
    { "id": 8, "tasks": ["12.1", "12.2"] }
  ]
}
```
