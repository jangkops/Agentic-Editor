# 구현 계획: PPTX 템플릿 스타일링 (pptx-template-styling)

## 개요

사용자가 등록한 PowerPoint 템플릿(`.pptx`)의 슬라이드 마스터·레이아웃·테마와 색/폰트 토큰을
이후 생성되는 PPTX에 상속시키는 기능을, 기존 생성 경로에 **비침투적으로** 얹는다.

- 백엔드: Python 3.11+ / FastAPI (`ai_engine/server.py` 및 신규 모듈), python-pptx 지연 import
- 프론트엔드: Electron + Vanilla JS Web Component (`src/components/`), no shadow DOM
- 테스트: `scripts/` 하위 pytest + hypothesis (직접 실행형, `ai_engine/.venv/bin/python scripts/test_*.py`)

**빌드 순서 원칙**: 결정론적 직렬화 모델(가장 안쪽, PBT 1순위) → Template_Manager →
FastAPI → 생성 경로(`_tool_generate_pptx`) → 배경 파이프라인 전파 → Electron 배선 → UI → 통합.
하위 호환(요구사항 5.2)과 폴백 격리(요구사항 9)는 모든 단계에서 보존한다.

## Tasks

- [x] 1. Style_Profile 데이터 모델 및 결정론적 Serializer 구현
  - [x] 1.1 StyleProfile dataclass + 상수 + normalize_color 구현
    - 파일: `ai_engine/style_profile.py` (신규) — 설계 §구성요소 3
    - `STYLE_PROFILE_KEY_ORDER`(7키 고정 순서), `REQUIRED_FIELDS`, `COLOR_FIELDS` 상수 정의
    - `@dataclass(frozen=True) StyleProfile`: primaryColor/secondaryColor/accentColor/textColor/backgroundColor/headingFont/bodyFont
    - `normalize_color(value)`: 선택적 `#` + 정확히 6자리 16진수(대소문자 무관) → 대문자 `#RRGGBB`, 불일치 시 `None`
    - _Requirements: 3.2, 4.7_

  - [x] 1.2 serialize() 결정론적 직렬화 구현
    - 파일: `ai_engine/style_profile.py` — 설계 §구성요소 3
    - `STYLE_PROFILE_KEY_ORDER`로 OrderedDict 구성 후 `json.dumps(..., ensure_ascii=False, separators=(',', ':'), sort_keys=False)`
    - 동일 객체 → 매 호출 바이트 단위 동일(키 순서·공백 고정)
    - _Requirements: 4.1, 4.2_

  - [x] 1.3 deserialize() 검증 순서 구현
    - 파일: `ai_engine/style_profile.py` — 설계 §구성요소 3
    - 검증 순서: `json.loads` 실패 → `invalid-json`(4.5) → 필수 필드 누락 시 누락 필드명 포함 `invalid-style-profile`, 부분 객체 생성 금지(4.6) → 색상 필드 `normalize_color` 실패 시 field 포함 `invalid-color`, 첫 실패에서 즉시 중단(4.7)
    - 선택 필드(secondary/accent/background) 누락 시 `SLIDE_DESIGN` 대응 기본값으로 채움
    - _Requirements: 4.3, 4.5, 4.6, 4.7_

  - [x]* 1.4 Property 테스트: Style_Profile 직렬화 왕복 보존 + 결정론적 직렬화
    - 파일: `scripts/test_style_profile_roundtrip_property.py` (신규)
    - **Property 1: Style_Profile 직렬화 왕복 보존**
    - **Validates: Requirements 4.2, 4.4**
    - hypothesis로 임의의 유효 StyleProfile 생성 → `serialize(p) == serialize(deserialize(serialize(p)))` 바이트 단위 검증(4.4), 동일 객체 반복 직렬화 시 바이트 동일 검증(4.2)

  - [x]* 1.5 deserialize 검증 에러 단위 테스트
    - 파일: `scripts/test_style_profile_validation.py` (신규)
    - 손상 JSON → `invalid-json`, 필수 필드 누락 → `invalid-style-profile`(missing 목록 포함, 부분 객체 미생성), 색상 형식 위반 → `invalid-color`(field 포함, 즉시 중단)
    - _Requirements: 4.5, 4.6, 4.7_

- [x] 2. Template_Manager 저장 루트·검증·등록 구현
  - [x] 2.1 저장 루트 결정 + templateId 검증 구현
    - 파일: `ai_engine/template_manager.py` (신규) — 설계 §구성요소 1
    - `resolve_template_store_root()`: `AE_GENERATED_ROOT`(Electron 주입 userData) → `~/.agentic-editor` → 불가 시 `None`. 기존 `_resolve_local_root` 우선순위 재사용
    - `_validate_template_id(tid)`: 1–128자 AND `/`·`\`·`..` 미포함. 모든 경로 조립 전 강제
    - 산출물 경로는 항상 `os.path.join(store_root, "templates", template_id, fname)`, `os.path.realpath`가 `{templateId}` prefix 벗어나면 거부
    - _Requirements: 2.1, 2.3, 2.4, 2.7, 2.8_

  - [x] 2.2 register_template 검증 순서 + 저장 격리 구현
    - 파일: `ai_engine/template_manager.py` — 설계 §구성요소 1(등록 검증 순서)
    - 순서: python-pptx import 불가 → `missing-dep`(9.3) → 이름 trim 길이 ∉[1,100] → `invalid-name`(1.2,1.7) → 크기 >50MB → `template-too-large`(1.4) → 확장자 `.pptx` AND `Presentation()` 열림 실패 → `invalid-template`(1.3) → 이름 중복(trim+casefold) → `duplicate-name`(1.6) → store_root 불가 → `no-storage-root`(2.4) → `{templateId}/` 생성 + base.pptx 복사 + style_profile.json + metadata.json 저장
    - 디스크 쓰기는 마지막에 수행, 중간 예외 시 부분 산출물 정리 후 `template-store-write-failed`(2.6)
    - 성공 응답 `{templateId, name, path, layoutCount}`(1.5), ISO 8601 createdAt 기록(1.1)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.2, 2.5, 2.6, 9.3_

  - [x]* 2.3 등록 검증 순서 단위 테스트
    - 파일: `scripts/test_template_register_validation.py` (신규)
    - 각 에러 분기(invalid-name/template-too-large/invalid-template/duplicate-name/no-storage-root) 검증, 검증 실패 시 Template_Store에 산출물 미생성 확인
    - _Requirements: 1.2, 1.3, 1.4, 1.6, 1.7_

  - [x]* 2.4 templateId 경로 탈출 거부 단위 테스트
    - 파일: `scripts/test_template_id_path_escape.py` (신규)
    - `..`, `/`, `\` 포함 또는 길이 위반 templateId → `invalid-template-id`, realpath 기준 `{templateId}` 디렉토리 탈출 차단
    - _Requirements: 2.1, 2.7_

- [x] 3. Style_Profile 추출 (테마 XML) 구현
  - [x] 3.1 테마 part 접근 + 6토큰 추출 구현
    - 파일: `ai_engine/template_manager.py` — 설계 §구성요소 2
    - `_theme_element(prs)`: `slide_masters[0].part.part_related_by(theme 관계)._element`
    - `extract_style_profile(prs)`: `a:clrScheme`(accent1→primary, accent2→secondary, accent3→accent, dk1/tx1→text, lt1/bg1→background), `a:fontScheme`(majorFont→heading, minorFont→body). srgbClr `val`, sysClr `lastClr` 처리
    - `_first_real_family()`: 폰트 스택에서 1–64자 단일 의미 패밀리 추출
    - _Requirements: 3.1, 3.2_

  - [x] 3.2 per-token 폴백 + style_profile.json 저장 구현
    - 파일: `ai_engine/template_manager.py` — 설계 §구성요소 2(per-token 폴백 표)
    - 각 토큰 개별 검증(색상 `#RRGGBB` 정규화 / 폰트 1–64자), 부재·무효 시 그 토큰만 `SLIDE_DESIGN` 대응 기본값으로 채움 → 6토큰 항상 비어있지 않음
    - `serialize()`로 `style_profile.json` 저장, `get_style_profile()`이 매 호출 바이트 동일 반환
    - _Requirements: 3.3, 3.4, 3.5_

  - [x]* 3.3 per-token 폴백 단위 테스트
    - 파일: `scripts/test_style_profile_extraction_fallback.py` (신규)
    - 테마 토큰 누락/무효 시 해당 토큰만 SLIDE_DESIGN 기본값, 정상 토큰은 추출값 유지, 6토큰 모두 비어있지 않음 검증
    - _Requirements: 3.1, 3.3_

- [x] 4. Template_Manager 조회·삭제 구현
  - [x] 4.1 list/get/delete 구현
    - 파일: `ai_engine/template_manager.py` — 설계 §구성요소 1
    - `list_templates`: createdAt 내림차순, 최대 200개 `{templateId, name, createdAt}`
    - `get_template`: `{templateId, name, templatePath(절대), styleProfile, createdAt}` 또는 `{error: invalid-template-id | template-not-found}`
    - `delete_template`: 성공 `{ok, templateId}`, 실패 시 디렉토리 보존 후 `template-delete-failed`(8.12) / `invalid-template-id` / `template-not-found`
    - _Requirements: 2.7, 5.3, 8.1, 8.8, 8.12_

  - [x]* 4.2 조회·삭제 단위 테스트
    - 파일: `scripts/test_template_get_delete.py` (신규)
    - 부재 templateId 조회 → `template-not-found`, 삭제 예외 주입 시 디렉토리 보존 + `template-delete-failed`, 정상 삭제 시 디렉토리/하위 제거
    - _Requirements: 5.4, 8.12_

- [x] 5. FastAPI 템플릿 엔드포인트 5종 추가
  - [x] 5.1 등록/목록/조회/Style_Profile/삭제 라우트 구현
    - 파일: `ai_engine/server.py` — 설계 §구성요소 9
    - `POST /api/templates`(등록), `GET /api/templates`(목록), `GET /api/templates/{id}`(단건), `GET /api/templates/{id}/style-profile`(바이트 동일 반환), `DELETE /api/templates/{id}`
    - Template_Manager 결과를 `JSONResponse`로 변환, 에러 이름 ↔ 요구사항 매핑(설계 표) 준수, `missing-dep` 시 lib/hint 포함
    - _Requirements: 1.5, 2.7, 3.5, 5.3, 8.1, 8.8, 9.3_

- [x] 6. Checkpoint — Template_Manager 백엔드 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. _tool_generate_pptx 템플릿 분기 + 동적 레이아웃 매핑
  - [x] 7.1 Presentation(template_path) 분기 + 타임아웃 폴백 구현
    - 파일: `ai_engine/server.py` — 설계 §구성요소 4
    - `tool_input["templatePath"]` 있으면 `_open_presentation_with_timeout(path, timeout=10)`으로 열어 마스터/레이아웃/테마 상속(6.1), 예외/타임아웃 시 `Presentation()` baseline로 폴백(6.9, 9.1)
    - 성공 응답에 `templateId`, `path`, `slideCount`(표지 포함), `sizeBytes`(>0) 포함(6.7)
    - _Requirements: 5.2, 6.1, 6.7, 6.9, 9.1_

  - [x] 7.2 _resolve_layout 동적 레이아웃 폴백 체인 구현
    - 파일: `ai_engine/server.py` — 설계 §구성요소 4(동적 레이아웃 매핑)
    - 템플릿 사용 시 레이아웃 이름 정규화 매칭(title/content/two-column) → 실패 시 첫 콘텐츠 레이아웃(body placeholder 보유)(6.3) → 콘텐츠 레이아웃 없음 시 `slide_layouts[0]`(6.4)
    - 무템플릿 경로는 기존 `LAYOUT_MAP {title:0, content:1, two-column:3}` 그대로 사용(5.2)
    - _Requirements: 6.2, 6.3, 6.4_

  - [x] 7.3 전체 배경 z-order + 편집 가능 텍스트 유지 구현
    - 파일: `ai_engine/server.py` — 설계 §구성요소 4
    - `slideBackground`(1920×1080) 있으면 `add_picture(0,0, 13.333×7.5)` 후 `spTree.insert(2, pic._element)`로 마스터 배경 위·placeholder 아래 배치(6.5), 전체 배경 없으면 그림 미추가로 마스터 배경 유지(6.6)
    - 제목/본문은 placeholder text_frame에 텍스트로 채워 편집 가능 유지(6.8)
    - _Requirements: 6.5, 6.6, 6.8_

  - [x]* 7.4 동적 레이아웃 폴백 체인 단위 테스트
    - 파일: `scripts/test_dynamic_layout_mapping.py` (신규)
    - 이름 매칭 성공/실패→첫 콘텐츠 레이아웃, 콘텐츠 레이아웃 없는 템플릿→index 0 폴백 검증
    - _Requirements: 6.2, 6.3, 6.4_

- [x] 8. _tool_generate_pptx 폴백 격리 보강
  - [x] 8.1 템플릿 단계 실패 격리 구현
    - 파일: `ai_engine/server.py` — 설계 §개요(폴백 격리 원칙)
    - 기준 `.pptx` 열기/Style_Profile 로드/토큰 적용 실패가 슬라이드 콘텐츠 생성으로 전파되지 않도록 격리, 손상 style_profile 시 SLIDE_DESIGN 기본값 폴백(9.2), 어떤 템플릿 단계 실패에도 모든 슬라이드 포함한 유효 PPTX 산출 완료(9.5, 9.6)
    - _Requirements: 9.1, 9.2, 9.5, 9.6_

  - [x]* 8.2 폴백 격리 단위 테스트
    - 파일: `scripts/test_template_fallback_isolation.py` (신규)
    - 템플릿 열기 실패 / style_profile.json 손상 주입 → 무템플릿 경로 진행, 모든 슬라이드 포함 유효 PPTX 생성 확인
    - _Requirements: 9.1, 9.2, 9.5, 9.6_

  - [x]* 8.3 무템플릿 하위 호환 단위 테스트
    - 파일: `scripts/test_no_template_backward_compat.py` (신규)
    - templateId 미전달 시 baseline과 슬라이드 수·레이아웃 매핑(LAYOUT_MAP) 동일, `Presentation()` 13.333×7.5 경로 보존 검증
    - _Requirements: 5.2_

- [x] 9. slide_templates 토큰 오버라이드 + HTML 배경 전파
  - [x] 9.1 design_tokens_for_profile 헬퍼 구현
    - 파일: `ai_engine/slide_templates.py` — 설계 §구성요소 5
    - `SLIDE_DESIGN` 사본 생성 후 profile 유효 토큰만 매핑 교체(primaryColor→primary, textColor→text_dark, backgroundColor→bg_light, headingFont→font_heading, bodyFont→font_body, accentColor→accent, secondaryColor→secondary), `profile is None`이면 원본 그대로(7.5), 무효 토큰만 기본값 유지(7.6)
    - _Requirements: 7.1, 7.5, 7.6_

  - [x] 9.2 render_* 시그니처 확장 및 HTML 배경 전파
    - 파일: `ai_engine/slide_templates.py` — 설계 §구성요소 5(Tier 0)
    - cover/divider/two-col/grid/timeline 등 `render_*`에 `design=design_tokens_for_profile(sp)` 전달, 주/텍스트/배경 색·폰트가 Style_Profile 값과 일치하고 기본값이 아니도록 함(7.1)
    - _Requirements: 7.1_

  - [x]* 9.3 per-token 오버라이드 단위 테스트
    - 파일: `scripts/test_design_tokens_override.py` (신규)
    - profile None → SLIDE_DESIGN 동일, 일부 토큰 무효 → 그 토큰만 기본값·나머지 SP 값 적용 검증
    - _Requirements: 7.5, 7.6_

- [x] 10. _force_generate_from_text Style_Profile 로드 및 plumbing
  - [x] 10.1 _resolve_active_template + Style_Profile 1회 로드 구현
    - 파일: `ai_engine/server.py` — 설계 §구성요소 6
    - `_resolve_active_template(template_id, store_root)`: 없음/"" → (None,None,False)(5.2), `template-not-found` → 로그 + 무템플릿(5.4), base.pptx/style_profile 로드 실패 → 로그(≤200자) + 무템플릿(5.5), 정상 → (abs_path, profile, True)(5.3)
    - 유효 시 `inp["templatePath"]`/`inp["templateId"]` 주입 + styleProfile을 각 Tier에 전달, UI→요청→파이프라인 templateId 흐름 수신(5.1)
    - _Requirements: 5.1, 5.3, 5.4, 5.5_

  - [x]* 10.2 활성 템플릿 해석 폴백 단위 테스트
    - 파일: `scripts/test_resolve_active_template.py` (신규)
    - not-found / 로드 실패 → (None,None,False)로 무템플릿 진행, 정상 → templatePath·profile 반환 검증
    - _Requirements: 5.4, 5.5_

- [x] 11. 배경 Tier 토큰 주입 (Mermaid / matplotlib / Vertex)
  - [x] 11.1 Mermaid 테마 변수 주입 구현
    - 파일: `ai_engine/server.py` (`_llm_generate_mermaid`) — 설계 §구성요소 5(Tier 1)
    - 프롬프트에 Style_Profile primary/text 색 명시 + 렌더 후 `%%{init: {'theme':'base','themeVariables':{'primaryColor':SP.primary,'textColor':SP.text}}}%%` 헤더 주입
    - _Requirements: 7.2_

  - [x] 11.2 matplotlib 팔레트 주입 구현
    - 파일: `ai_engine/server.py` (`_tool_generate_native_diagram`) — 설계 §구성요소 5(Tier 2)
    - 팔레트 인자 추가, Style_Profile primary를 첫 항목으로 하는 2색 이상 팔레트를 차트 색상에 적용
    - _Requirements: 7.3_

  - [x] 11.3 Vertex 프롬프트 색/폰트 주입 구현
    - 파일: `ai_engine/server.py` (`_try_vertex_for_section` 호출부) — 설계 §구성요소 5(Tier 0.6). `vertex_image_module.py` 시그니처는 변경하지 않음
    - Active_Template + Vertex 활성 시 프롬프트 끝에 `Color palette: primary {SP.primary}, accent {SP.accent}. Typography style cues: heading font "{SP.headingFont}", body font "{SP.bodyFont}".` 추가
    - _Requirements: 7.4_

  - [x]* 11.4 토큰 주입 격리 단위 테스트
    - 파일: `scripts/test_pipeline_token_injection.py` (신규)
    - Style_Profile 지정 시 각 Tier가 SP 토큰 사용, 특정 토큰 부재/무효 시 해당 토큰만 기본값 대체하고 렌더링 중단 없이 계속 확인
    - _Requirements: 7.6, 9.4_

- [x] 12. Checkpoint — 생성 경로(백엔드) 통합 검증
  - Ensure all tests pass, ask the user if questions arise.

- [x] 13. Electron IPC + preload + 파일 다이얼로그 필터 배선
  - [x] 13.1 fs:open-file 선택적 필터 확장
    - 파일: `electron/src/ipc-fs-handlers.js` — 설계 §구성요소 8
    - `fs:open-file` 핸들러가 `opts.filters`(예: PowerPoint `.pptx`)를 선택적으로 받도록 확장, 인자 없으면 기존과 동일(하위 호환)
    - _Requirements: 8.3_

  - [x] 13.2 ipc-template-handlers.js FastAPI 프록시 구현
    - 파일: `electron/src/ipc-template-handlers.js` (신규) — 설계 §구성요소 8
    - `template:register`(filePath+name → POST), `template:list`, `template:get`, `template:get-style-profile`, `template:delete`를 FastAPI 엔드포인트로 프록시, 응답 JSON 반환. `ipcRenderer` 미노출
    - _Requirements: 1.1, 5.3, 8.1, 8.8_

  - [x] 13.3 preload.js 화이트리스트 메서드 노출
    - 파일: `electron/preload.js` — 설계 §구성요소 8, security.md 준수
    - `registerTemplate/listTemplates/getTemplate/getTemplateStyleProfile/deleteTemplate` 및 `openFile(opts)`를 `contextBridge`로만 노출(화이트리스트, ipcRenderer 직접 노출 금지)
    - _Requirements: 8.3_

  - [x] 13.4 main.js 템플릿 IPC 핸들러 등록
    - 파일: `electron/main.js` — 설계 §구성요소 8
    - `registerTemplateHandlers(mainWindow)` 호출로 `template:*` 핸들러를 main 프로세스에서만 등록, `AE_ENGINE_URL` 기반 FastAPI 베이스 사용
    - _Requirements: 8.1, 8.8_

- [x] 14. `<template-panel>` Web Component 구현
  - [x] 14.1 컴포넌트 골격 + 목록/빈 상태 렌더
    - 파일: `src/components/template-panel.js` (신규) — 설계 §구성요소 7. `file-preview-panel.js` 패턴 미러링
    - `class TemplatePanel extends HTMLElement` + `customElements.define('template-panel', ...)`, `connectedCallback`→`_render()`+`_refresh()`
    - `_refresh()`: `electronAPI.listTemplates()` → createdAt 내림차순, 최대 200개, `_renderList()`로 이름 + `YYYY-MM-DD HH:mm`(24h) 표시(8.1), 빈 목록 시 `_renderEmpty()` "등록된 템플릿이 없습니다" + 업로드 컨트롤(8.9)
    - `var(--color-*)` 토큰 사용, no shadow DOM(8.6)
    - _Requirements: 8.1, 8.6, 8.9_

  - [x] 14.2 업로드 진입점 (클릭 + 드래그&드롭)
    - 파일: `src/components/template-panel.js` — 설계 §구성요소 7
    - `_renderUploadControl()` 상단 '+' 버튼(8.2), `_onUploadClick()`: `openFile({filters:[{name:'PowerPoint',extensions:['pptx']}]})` → 이름 입력 → `registerTemplate`(8.3), 다이얼로그 취소 시 상태 무변경(8.4), `_bindDragAndDrop()` 보조 진입점으로 `.pptx` drop → 이름 입력 → 등록(8.5)
    - _Requirements: 8.2, 8.3, 8.4, 8.5_

  - [x] 14.3 미리보기 견본 + 삭제 확인 플로우 + 선택 이벤트
    - 파일: `src/components/template-panel.js` — 설계 §구성요소 7
    - `_onSelect(id)`: `getTemplateStyleProfile` → `_renderSwatches`로 primary/accent/text/background를 `#RRGGBB` 라벨 색상 견본 + 제목/본문 폰트 텍스트 표시(8.7)
    - `_confirmDelete(id,name)`: 이름 + 확정/취소 확인 단계, 취소 시 중단(8.10); `_doDelete(id)`: 성공 시 목록 제거(8.11), `template-delete-failed` 시 항목 유지 + 에러 메시지(8.13)
    - 선택 시 `document.dispatchEvent(new CustomEvent('template:selected', {detail:{templateId}}))` 디스패치, "템플릿 없음" 기본 선택값 제공(5.6)
    - _Requirements: 5.6, 8.7, 8.8, 8.10, 8.11, 8.13_

- [x] 15. 프론트엔드 통합 및 최종 배선
  - [x] 15.1 src/main.js _apiBody templateId 주입 + 셀렉터 이벤트 수신
    - 파일: `src/main.js` — 설계 §구성요소 6
    - `state.activeTemplateId` 도입, `_apiBody(extra)`에서 projectPath 주입 직후 `if (state.activeTemplateId) body.templateId = state.activeTemplateId`(5.1), `template:selected` 수신 시 `state.activeTemplateId` 갱신("템플릿 없음"이면 빈 값 → 무템플릿)
    - _Requirements: 5.1, 5.6_

  - [x] 15.2 index.html에 <template-panel> 마운트 + 패널 초기화
    - 파일: `src/index.html`, `src/main.js` — 설계 §구성요소 7
    - `<template-panel>` 태그 추가 및 컴포넌트 import/초기화, 사이드바에 템플릿 패널 진입점 배치
    - _Requirements: 8.1, 8.6_

  - [x]* 15.3 엔드투엔드 하위 호환 자동 검증 테스트
    - 파일: `scripts/test_template_end_to_end_backward_compat.py` (신규)
    - templateId 미전달 입력에 대해 `_force_generate_from_text`/`_tool_generate_pptx` 산출이 baseline과 슬라이드 수·레이아웃 매핑·배경 Tier 단계 동일함을 자동 검증(5.2), 템플릿 처리 단계 실패를 주입해도 모든 슬라이드 포함 유효 PPTX 완료(9.1, 9.5, 9.6) — 앱 직접 구동 없이 함수 호출 기반
    - _Requirements: 5.2, 9.1, 9.5, 9.6_

- [x] 16. Final Checkpoint — 전체 테스트 통과
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- `*`가 붙은 서브태스크는 선택적(단위·Property 테스트)이며 MVP 가속 시 생략 가능. Top-level 태스크에는 `*`가 없다.
- Property 테스트는 1개(Property 1: Style_Profile 왕복 보존, 요구사항 4.2/4.4)이며, 이것이 본 기능의 PBT 1순위 대상이다. 나머지 테스트는 결정론적 검증 분기를 다루는 단위 테스트다.
- 모든 신규 테스트는 기존 컨벤션을 따른다: `scripts/` 하위, `test_*_property.py`(PBT) / `test_*.py`(단위), `ai_engine/.venv/bin/python scripts/test_*.py` 직접 실행.
- 하위 호환(요구사항 5.2)과 폴백 격리(요구사항 9)는 templateId 가드 안쪽에만 분기를 두어 보존한다.
- 모든 LLM 호출은 Bedrock Gateway 경유 유지(gateway.md). Style_Profile 추출은 python-pptx 로컬 XML 파싱이며 신규 추론 호출이 없다. 색/폰트 토큰은 Mermaid 프롬프트/Vertex 프롬프트에 데이터로만 주입된다.
- 저장은 `userData/templates/{templateId}/` 하위로만(요구사항 2.8), IPC 핸들러는 electron/main.js에만 등록(security.md).
- 검증 명령어 예: `ai_engine/.venv/bin/python -c "import ast; ast.parse(open('ai_engine/style_profile.py').read())"`, `node --check src/components/template-panel.js`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "7.1", "9.1", "13.1", "13.3", "14.1"] },
    { "id": 1, "tasks": ["1.2", "3.1", "7.2", "9.2", "13.2", "14.2"] },
    { "id": 2, "tasks": ["1.3", "3.2", "7.3", "13.4", "14.3"] },
    { "id": 3, "tasks": ["2.2", "1.4", "1.5"] },
    { "id": 4, "tasks": ["4.1", "8.1", "2.3", "2.4", "7.4", "9.3"] },
    { "id": 5, "tasks": ["5.1", "8.2", "8.3", "3.3", "15.1"] },
    { "id": 6, "tasks": ["10.1", "4.2", "15.2"] },
    { "id": 7, "tasks": ["11.1", "10.2"] },
    { "id": 8, "tasks": ["11.2"] },
    { "id": 9, "tasks": ["11.3"] },
    { "id": 10, "tasks": ["11.4", "15.3"] }
  ]
}
```
