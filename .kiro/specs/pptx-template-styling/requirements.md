# 요구사항 문서: PPTX 템플릿 스타일링 (pptx-template-styling)

## 소개

현재 AI 에디터는 이미지 생성 품질은 확보했으나, PPTX 생성 시 사용할 "틀(템플릿)" 개념이 없다.
`_tool_generate_pptx`는 빈 `Presentation()`에서 시작해 슬라이드 마스터·레이아웃·테마를 상속하지 않으며,
배경은 단계별 파이프라인(HTML→Vertex 이미지→Mermaid→matplotlib→native)으로 매번 새로 채운다.

이 기능은 사용자가 프레젠테이션 템플릿을 한 번 등록해 두면, 이후 생성되는 PPTX가 그 템플릿의
배경·색상·폰트·레이아웃 스타일을 상속하도록 한다. 등록된 템플릿이 없을 때는 현재 동작을 그대로 유지한다.

이 문서는 다음 핵심 결정에 대해 합리적 기본값을 채택하고, 검토가 필요한 지점을 "결정 노트"로 명시한다.
- 템플릿 형식: 사용자가 업로드한 `.pptx` 파일을 1차 산출물로 사용하고, 그 테마에서 스타일 토큰(Style_Profile)을 파생한다.
- 업로드 방식: Template_Panel 상단의 '+' 버튼 → OS 네이티브 파일 선택창(`.pptx` 필터, 기존 `fs:open-file` 재사용) → 이름 입력 → 목록 등록. 패널로의 드래그&드롭은 보조 진입점으로 지원한다.
- 저장 위치: `userData/templates/` 하위(OS 사용자별 자동 격리) — 프로젝트 데이터 영속화 규칙 준수.
- 스타일 범위: 슬라이드 마스터/레이아웃/테마 상속에 더해, 색/폰트 토큰을 배경 파이프라인(HTML·Mermaid·matplotlib)과 Vertex 프롬프트 미학에 전파.
- 선택 방식: 생성 시 활성 템플릿을 선택하며, 미선택(기본값)은 현재 무템플릿 동작을 보존.
- 다중 사용자(~30명): 템플릿은 사용자별로 격리되며 공유 저장소는 사용하지 않는다.

## 용어 정의

- **AI_Engine**: Python FastAPI 기반 백엔드 서버 (`ai_engine/server.py`)
- **PPTX_Generator**: python-pptx 기반으로 프레젠테이션 파일을 생성하는 도구 (`_tool_generate_pptx`)
- **Template_Manager**: 템플릿의 등록·조회·삭제를 처리하는 백엔드 구성요소
- **Presentation_Template**: 등록된 단위 템플릿. 기준 `.pptx` 파일, 파생된 Style_Profile, 메타데이터(templateId, name, 생성 시각)로 구성된다
- **Template_Store**: 등록된 템플릿이 저장되는 `userData/templates/` 디렉토리
- **Style_Profile**: 템플릿에서 파생된 스타일 토큰 집합(JSON). 주 색상(primary), 보조/강조 색상, 텍스트 색상, 배경 색상, 제목/본문 폰트 패밀리를 포함한다
- **Style_Profile_Serializer**: Style_Profile 객체를 JSON으로 직렬화하고 JSON에서 Style_Profile 객체로 역직렬화하는 구성요소
- **Background_Pipeline**: 슬라이드 배경을 단계별로 생성하는 백엔드 파이프라인 (`_force_generate_from_text`: HTML / Vertex 이미지 / Mermaid / matplotlib / native)
- **Slide_Design_Tokens**: HTML 배경 렌더링의 단일 소스 디자인 토큰 (`slide_templates.SLIDE_DESIGN`)
- **Template_Panel**: 템플릿 등록·목록·미리보기·삭제 UI를 제공하는 프론트엔드 Web Component
- **Template_Upload_Control**: Template_Panel 상단의 '+' (템플릿 추가) 버튼. 클릭 시 OS 네이티브 파일 선택창(`fs:open-file`, `.pptx` 필터)을 열어 템플릿 파일을 선택하는 진입점
- **Native_File_Dialog**: Electron `dialog.showOpenDialog` 기반 OS 파일 선택창 (기존 `fs:open-file` IPC 핸들러)
- **Template_Selector**: 생성 시 활성 Presentation_Template을 선택하는 UI 컨트롤
- **Active_Template**: 다음 PPTX 생성에 적용될 사용자가 선택한 Presentation_Template
- **Generated_Folder**: 생성된 파일이 저장되는 `.generated/` 디렉토리
- **userData**: Electron `app.getPath('userData')`가 가리키는 사용자별 데이터 루트
- **AE_GENERATED_ROOT**: Electron이 백엔드에 주입하는 사용자별 쓰기 가능 루트 환경변수

## 요구사항

### 요구사항 1: 템플릿 등록 (.pptx 업로드)

**사용자 스토리:** 발표자로서, 보유한 PowerPoint 템플릿 파일을 등록하여 이후 생성물이 그 틀을 따르게 하고 싶다.

#### 인수 조건

1. WHEN 사용자가 `.pptx` 파일과 템플릿 이름을 제출하여 템플릿 등록을 요청하면, THE Template_Manager SHALL 해당 파일을 Template_Store에 복사하고 templateId(UUID v4), 이름, 등록 시각(ISO 8601)을 포함한 메타데이터를 저장한다
2. THE Template_Manager SHALL 앞뒤 공백을 제거한 후의 템플릿 이름이 1자 이상 100자 이하일 것을 요구한다
3. IF 제출된 파일의 확장자가 `.pptx`가 아니거나 python-pptx로 열리지 않으면, THEN THE Template_Manager SHALL 해당 파일을 Template_Store에 저장하지 않고 "invalid-template" 에러와 원인 설명(최대 200자)을 포함한 JSON을 반환한다
4. IF 제출된 파일 크기가 50MB를 초과하면, THEN THE Template_Manager SHALL 해당 파일을 Template_Store에 저장하지 않고 "template-too-large" 에러와 최대 허용 크기를 포함한 JSON을 반환한다
5. WHEN 템플릿 등록이 성공하면, THE Template_Manager SHALL templateId, 이름, 기준 `.pptx` 상대 경로, 슬라이드 레이아웃 개수를 포함한 JSON을 반환한다
6. IF 등록 요청의 이름이 앞뒤 공백 제거 및 대소문자 무시 비교 시 이미 존재하는 템플릿 이름과 일치하면, THEN THE Template_Manager SHALL 기존 템플릿 산출물을 보존한 채 새 파일을 저장하지 않고 "duplicate-name" 에러를 포함한 JSON을 반환한다
7. IF 앞뒤 공백을 제거한 템플릿 이름이 1자 미만이거나 100자를 초과하면, THEN THE Template_Manager SHALL 해당 파일을 저장하지 않고 "invalid-name" 에러와 허용 길이 범위(1–100)를 포함한 JSON을 반환한다

### 요구사항 2: 템플릿 저장 및 사용자별 격리

**사용자 스토리:** 운영자로서, 약 30명이 사용하는 환경에서 각 사용자의 템플릿이 서로 섞이지 않고 안전하게 보관되기를 원한다.

#### 인수 조건

1. THE Template_Manager SHALL 모든 템플릿 산출물을 `userData/templates/{templateId}/` 하위에만 저장하며, 어떤 산출물 경로도 해당 `{templateId}` 디렉토리 밖으로 벗어나지 않도록 한다
2. WHEN 템플릿 등록이 성공하면, THE Template_Manager SHALL 해당 `userData/templates/{templateId}/` 디렉토리에 기준 `.pptx` 파일, `style_profile.json`, `metadata.json`을 저장한다
3. WHERE 백엔드가 Electron으로부터 AE_GENERATED_ROOT를 주입받은 경우, THE Template_Manager SHALL Template_Store 루트를 해당 사용자별 경로 기준으로 결정한다
4. IF AE_GENERATED_ROOT가 주입되지 않았고 userData 루트를 결정할 수 없으면, THEN THE Template_Manager SHALL 템플릿을 저장하지 않고 "no-storage-root" 에러를 포함한 JSON을 반환한다
5. IF Template_Store 디렉토리가 존재하지 않으면, THEN THE Template_Manager SHALL 최초 등록 시 해당 디렉토리를 생성한다
6. IF 디렉토리 생성 또는 산출물 쓰기 중 실패가 발생하면, THEN THE Template_Manager SHALL 부분적으로 생성된 산출물을 남기지 않고 "template-store-write-failed" 에러와 원인 설명(최대 200자)을 포함한 JSON을 반환한다
7. IF templateId가 1자 미만 또는 128자 초과이거나 경로 구분자(`/`, `\`) 또는 상위 참조(`..`)를 포함하면, THEN THE Template_Manager SHALL 해당 요청을 거부하고 "invalid-template-id" 에러를 반환한다
8. THE Template_Manager SHALL 템플릿 산출물을 프로젝트 작업 폴더나 애플리케이션 설치 폴더가 아닌 userData 하위에만 기록한다

### 요구사항 3: 템플릿 스타일 프로파일 추출

**사용자 스토리:** 발표자로서, 등록한 템플릿의 색과 폰트가 자동으로 인식되어 생성물 전반에 일관되게 적용되기를 원한다.

#### 인수 조건

1. WHEN 템플릿 등록이 성공하면, THE Template_Manager SHALL 기준 `.pptx`의 테마에서 주 색상, 강조 색상, 텍스트 색상, 배경 색상, 제목 폰트 패밀리, 본문 폰트 패밀리의 6개 항목을 추출하여 6개 항목이 모두 비어 있지 않게 채워진 Style_Profile을 구성하며, 각 폰트 패밀리 값은 1자 이상 64자 이하 문자열로 한다
2. THE Style_Profile SHALL 모든 색상을 대문자 6자리 16진수 RGB 문자열(`#RRGGBB`, 예: `#1E1E1E`) 형식으로 표현한다
3. IF 테마에 특정 색상 또는 폰트 항목이 존재하지 않거나, 그 값이 색상의 경우 `#RRGGBB` 형식으로 또는 폰트의 경우 1자 이상 64자 이하 문자열로 해석되지 않으면, THEN THE Template_Manager SHALL 해당 항목에 Slide_Design_Tokens의 대응 기본값을 사용한다
4. THE Template_Manager SHALL 구성된 Style_Profile을 해당 템플릿 디렉토리의 `style_profile.json`에 저장한다
5. WHILE 템플릿이 등록된 상태에서, WHEN 동일한 templateId로 Style_Profile 조회가 요청되면, THE Template_Manager SHALL 매 호출마다 바이트 단위로 동일한 Style_Profile JSON 내용을 반환한다

### 요구사항 4: 스타일 프로파일 직렬화·역직렬화 (왕복 보존)

**사용자 스토리:** 개발자로서, 저장된 Style_Profile이 손실 없이 읽고 쓰여 생성 결과가 재현 가능하기를 원한다.

#### 인수 조건

1. WHEN Style_Profile 객체에 대해 직렬화가 요청되면, THE Style_Profile_Serializer SHALL 해당 객체를 UTF-8 인코딩 JSON 문자열로 직렬화한다
2. THE Style_Profile_Serializer SHALL 동일한 Style_Profile 객체에 대해 매 호출마다 키 순서가 고정된 바이트 단위로 동일한 JSON 문자열을 출력한다(결정론적 직렬화)
3. WHEN 유효한 Style_Profile JSON 문자열에 대해 역직렬화가 요청되면, THE Style_Profile_Serializer SHALL 이를 Style_Profile 객체로 역직렬화한다
4. FOR ALL 유효한 Style_Profile 객체에 대해, THE Style_Profile_Serializer SHALL `직렬화 = 직렬화 ∘ 역직렬화 ∘ 직렬화` 항등(왕복 보존 속성)을 바이트 단위로 만족한다
5. IF 입력이 구문상 올바른 JSON으로 파싱되지 않으면, THEN THE Style_Profile_Serializer SHALL "invalid-json" 에러를 반환한다
6. IF 파싱된 JSON에 필수 필드(주 색상, 텍스트 색상, 제목 폰트, 본문 폰트) 중 하나 이상이 누락되면, THEN THE Style_Profile_Serializer SHALL 누락된 모든 필드 이름을 포함한 "invalid-style-profile" 에러를 반환하고 부분 객체를 생성하지 않는다
7. IF 색상 필드 값이 `#` 다음 6자리 16진수(대소문자 무관) 형식이 아니면, THEN THE Style_Profile_Serializer SHALL 해당 필드 이름과 함께 "invalid-color" 에러를 반환하고 검증을 중단한다

### 요구사항 5: 템플릿 선택 및 기본 동작 보존

**사용자 스토리:** 발표자로서, 특정 생성 작업에 어떤 템플릿을 쓸지 선택하고, 선택하지 않았을 때는 기존 방식대로 생성되기를 원한다.

#### 인수 조건

1. WHEN 사용자가 Template_Selector에서 "템플릿 없음"이 아닌 한 Presentation_Template을 선택하면, THE AI_Engine SHALL 해당 templateId를 Active_Template으로 설정하여 이후 PPTX 생성 요청에 전달한다
2. WHERE 생성 요청에 templateId가 전달되지 않았거나 "템플릿 없음"이 선택된 경우, THE PPTX_Generator SHALL 무템플릿 생성 경로(빈 `Presentation()` 기반, 단계별 배경 파이프라인)를 호출하며, 그 산출물은 동일 입력에 대한 기존(baseline) 산출물과 슬라이드 수·레이아웃 매핑·배경 파이프라인 단계가 동일해야 한다
3. WHERE 생성 요청에 유효한(= Template_Store에 존재하며 기준 `.pptx`와 Style_Profile에 모두 접근 가능한) templateId가 전달된 경우, THE PPTX_Generator SHALL 해당 템플릿의 기준 `.pptx`와 Style_Profile을 생성에 적용한다
4. IF 전달된 templateId에 해당하는 템플릿이 Template_Store에 존재하지 않으면, THEN THE PPTX_Generator SHALL "template-not-found" 에러를 호출자에게 반환하고 부분 산출물을 적용하지 않은 채 무템플릿 생성 경로로 진행한다
5. IF templateId는 존재하나 기준 `.pptx` 또는 Style_Profile 로드에 실패하면, THEN THE PPTX_Generator SHALL 원인(최대 200자)을 로그로 남기고 무템플릿 생성 경로로 진행한다
6. THE Template_Selector SHALL 등록된 0개 이상의 템플릿 목록과 함께 "템플릿 없음" 옵션을 제공하며, "템플릿 없음"을 기본 선택값으로 한다

### 요구사항 6: PPTX 생성 시 템플릿 마스터·레이아웃·테마 상속

**사용자 스토리:** 발표자로서, 생성된 PPTX가 등록한 템플릿의 배경·레이아웃·테마를 그대로 물려받기를 원한다.

#### 인수 조건

1. WHEN Active_Template이 지정된 상태에서 PPTX 생성이 요청되면, THE PPTX_Generator SHALL 빈 `Presentation()` 대신 템플릿의 기준 `.pptx`를 기준 프레젠테이션으로 열어 슬라이드 마스터·레이아웃·테마를 변경 없이 상속한다
2. THE PPTX_Generator SHALL 슬라이드의 layout 필드("title", "content", "two-column")를 템플릿이 제공하는 대응 슬라이드 레이아웃에 매핑한다
3. IF 템플릿에 요청된 layout에 대응하는 슬라이드 레이아웃이 없으면, THEN THE PPTX_Generator SHALL 템플릿의 첫 번째 콘텐츠 레이아웃을 대체로 사용한다
4. IF 템플릿에 콘텐츠 레이아웃이 하나도 없으면, THEN THE PPTX_Generator SHALL 템플릿 레이아웃 목록의 인덱스 0번 레이아웃을 사용한다
5. WHERE 단계별 배경 파이프라인이 좌상단(0,0)에서 시작해 슬라이드 폭·높이의 100%를 덮는 전체 배경 이미지를 생성한 경우, THE PPTX_Generator SHALL 해당 전체 배경 이미지를 템플릿 마스터 배경보다 위 레이어에 배치하여 마스터 배경이 보이지 않도록 한다
6. WHERE 단계별 배경 파이프라인이 전체 배경 이미지를 생성하지 않은 경우, THE PPTX_Generator SHALL 템플릿의 슬라이드 마스터 배경을 변경 없이 유지한다
7. WHEN 템플릿 기반 PPTX 생성이 성공하면, THE PPTX_Generator SHALL 적용된 templateId, 상대 경로(path), 슬라이드 수(slideCount, 표지 포함), 0보다 큰 파일 크기(sizeBytes)를 포함한 JSON을 반환한다
8. THE PPTX_Generator SHALL 템플릿 적용 여부와 무관하게 슬라이드의 제목·본문 텍스트를 PowerPoint에서 편집 가능한 텍스트 셰이프로 유지한다
9. IF 템플릿 기준 `.pptx`를 열 수 없으면(파일 없음·손상·형식 불일치), THEN THE PPTX_Generator SHALL 원인(최대 200자)을 로그로 남기고 부분 출력을 생성하지 않은 채 요구사항 9의 폴백 경로로 전환한다

### 요구사항 7: 템플릿 스타일의 배경 파이프라인 전파

**사용자 스토리:** 발표자로서, 자동 생성되는 배경·다이어그램·차트가 템플릿의 색과 폰트와 어울리기를 원한다.

#### 인수 조건

1. WHILE Active_Template이 지정된 상태에서 Background_Pipeline이 HTML 배경을 렌더링할 때, THE Background_Pipeline SHALL 렌더링에 사용하는 주 색상·텍스트 색상·배경 색상·폰트 패밀리 값이 Active_Template Style_Profile의 대응 값과 일치하고 Slide_Design_Tokens 기본값이 아니도록 한다
2. WHILE Active_Template이 지정된 상태에서 Background_Pipeline이 Mermaid 다이어그램을 렌더링할 때, THE Background_Pipeline SHALL Mermaid 테마의 주 색상과 텍스트 색상을 Style_Profile의 주 색상과 텍스트 색상 값으로 설정한다
3. WHILE Active_Template이 지정된 상태에서 Background_Pipeline이 matplotlib 차트를 렌더링할 때, THE Background_Pipeline SHALL Style_Profile의 주 색상을 첫 번째 항목으로 하는 2색 이상의 색상 팔레트를 차트 색상으로 적용한다
4. WHERE Active_Template이 지정되고 Vertex 이미지 생성이 활성화된 경우, THE Background_Pipeline SHALL Style_Profile의 주 색상·강조 색상(`#RRGGBB`)과 제목·본문 폰트 패밀리 이름을 이미지 생성 프롬프트에 포함한다
5. WHERE Active_Template이 지정되지 않은 경우, THE Background_Pipeline SHALL 배경·다이어그램·차트의 모든 스타일 토큰을 기존 Slide_Design_Tokens 기본값으로 렌더링한다
6. IF Style_Profile의 특정 토큰이 부재하거나 색상 토큰이 `#RRGGBB` 형식으로 해석되지 않으면, THEN THE Background_Pipeline SHALL 해당 토큰만 Slide_Design_Tokens 기본값으로 대체하고 나머지 토큰은 Style_Profile 값으로 적용하며 렌더링을 중단 없이 계속한다

### 요구사항 8: 템플릿 관리 UI

**사용자 스토리:** 발표자로서, 등록한 템플릿을 한눈에 보고 미리보기하거나 삭제하고 싶다.

#### 인수 조건

1. WHEN Template_Panel이 활성화되면, THE Template_Panel SHALL 등록된 모든 템플릿(최대 200개)을 등록 시각 기준 내림차순으로 정렬하여 각 항목의 이름과 등록 시각(YYYY-MM-DD HH:mm, 24시간 표기)과 함께 활성화 후 1초 이내에 목록으로 표시한다
2. THE Template_Panel SHALL 상단에 Template_Upload_Control('+' 템플릿 추가 버튼)을 표시한다
3. WHEN 사용자가 Template_Upload_Control을 클릭하면, THE Template_Panel SHALL `.pptx` 확장자로 필터링된 Native_File_Dialog를 열고, 사용자가 파일을 선택하면 템플릿 이름 입력을 받아 등록 요청을 Template_Manager에 전달한다
4. WHEN 사용자가 Native_File_Dialog를 취소하면, THE Template_Panel SHALL 등록 요청을 전송하지 않고 패널 상태를 변경하지 않는다
5. WHERE 사용자가 `.pptx` 파일을 Template_Panel 영역에 드래그&드롭한 경우, THE Template_Panel SHALL 보조 진입점으로서 클릭 업로드와 동일하게 이름 입력 후 등록 요청을 전달한다
6. THE Template_Panel SHALL VS Code 스타일 다크 테마 디자인 토큰(variables.css)을 사용하는 Web Component(`<template-panel>`)로 구현된다
7. WHEN 사용자가 목록에서 한 템플릿을 선택하면, THE Template_Panel SHALL 선택 후 0.5초 이내에 해당 템플릿 Style_Profile의 주 색상·강조 색상·텍스트 색상·배경 색상을 각각 `#RRGGBB` 값 라벨이 붙은 색상 견본으로, 제목 폰트와 본문 폰트 패밀리 이름을 텍스트로 미리보기 영역에 표시한다
8. WHEN 사용자가 한 템플릿에 대해 삭제를 확정하면, THE Template_Manager SHALL `userData/templates/{templateId}/` 디렉토리와 그 하위 모든 파일을 제거하고 제거 결과를 호출자에게 반환한다
9. IF 등록된 템플릿이 하나도 없으면, THEN THE Template_Panel SHALL "등록된 템플릿이 없습니다" 안내 메시지와 함께 Template_Upload_Control을 표시한다
10. WHEN 사용자가 한 템플릿에 대해 삭제를 요청하면, THE Template_Panel SHALL 대상 템플릿 이름과 확정·취소 선택지를 포함한 확인 단계를 표시하고, 사용자가 확정을 선택하기 전에는 해당 디렉토리를 제거하지 않으며 사용자가 취소를 선택하면 삭제를 중단하고 확인 단계를 닫는다
11. WHEN 템플릿 디렉토리 제거가 성공하면, THE Template_Panel SHALL 해당 항목을 목록에서 제거하고 갱신된 목록을 0.5초 이내에 표시한다
12. IF 템플릿 디렉토리 제거 중 예외가 발생하면, THEN THE Template_Manager SHALL 해당 디렉토리를 보존한 채 원인 설명(최대 200자)을 포함한 "template-delete-failed" 에러를 반환한다
13. WHEN 삭제 요청이 "template-delete-failed" 에러를 반환하면, THE Template_Panel SHALL 해당 항목을 목록에 유지하고 삭제 실패를 나타내는 에러 메시지를 표시한다

### 요구사항 9: 오류 처리 및 폴백

**사용자 스토리:** 발표자로서, 템플릿에 문제가 있어도 생성이 중단되지 않고 결과물을 얻기를 원한다.

#### 인수 조건

1. IF 템플릿 기준 `.pptx`를 여는 작업이 예외를 발생시키거나 10초 이내에 완료되지 않으면, THEN THE PPTX_Generator SHALL 원인(최대 200자)을 로그로 남기고 무템플릿 생성으로 전환하여 모든 슬라이드 콘텐츠를 포함한 유효한 PPTX 파일 산출을 완료한다
2. IF `style_profile.json`이 손상되어 역직렬화에 실패하면, THEN THE PPTX_Generator SHALL 원인(최대 200자)을 로그로 남기고 Slide_Design_Tokens 기본값으로 대체하여 생성을 계속한다
3. IF python-pptx 라이브러리가 설치되지 않았으면, THEN THE Template_Manager SHALL error 필드에 "missing-dep", lib 필드에 "python-pptx", hint 필드에 설치 명령을 포함한 JSON을 반환한다
4. IF 템플릿 적용 중 일부 스타일 토큰 적용이 실패하면, THEN THE Background_Pipeline SHALL 실패한 토큰만 기본값으로 대체하고 나머지 토큰 적용을 계속한다
5. THE PPTX_Generator SHALL 템플릿 처리 단계의 실패를 슬라이드 콘텐츠 생성 경로로 전파하지 않도록 격리하여, 제목·본문·이미지 생성을 중단 없이 완료한다
6. WHILE 하나 이상의 템플릿 처리 단계(기준 `.pptx` 열기, Style_Profile 로드, 토큰 적용)가 실패한 상태에서, THE PPTX_Generator SHALL 모든 슬라이드 콘텐츠를 포함한 유효한 PPTX 파일 산출을 완료한다
