# 요구사항 문서: 미디어 생성·편집 기능

## 소개

AI 에디터의 이미지/PPTX/PDF 생성 도구를 검증하고, 이미지 편집(inpaint/outpaint) 기능을 추가하며, 생성된 미디어를 채팅에 인라인으로 표시하고 미리보기/다운로드 UI를 제공하는 기능이다.

## 용어 정의

- **AI_Engine**: Python FastAPI 기반 백엔드 서버 (`ai_engine/server.py`)
- **Image_Generator**: Bedrock 이미지 모델(SD3.5, Titan Image v2, Nova Canvas)을 호출하여 이미지를 생성하는 도구
- **Image_Editor**: 기존 이미지에 inpaint(부분 수정) 또는 outpaint(확장) 작업을 수행하는 도구
- **PDF_Generator**: reportlab 기반으로 PDF 문서를 생성하는 도구
- **PPTX_Generator**: python-pptx 기반으로 프레젠테이션 파일을 생성하는 도구
- **Chat_Renderer**: 채팅 메시지를 화면에 렌더링하는 프론트엔드 모듈 (`renderMessages` 함수)
- **File_Preview_Panel**: 생성된 파일을 미리보기하고 다운로드할 수 있는 UI 컴포넌트
- **Generated_Folder**: 생성된 파일이 저장되는 `.generated/` 디렉토리
- **Bedrock_Gateway**: AWS Bedrock 모델 호출을 중계하는 게이트웨이 서비스
- **Mask_Image**: inpaint 작업 시 수정할 영역을 지정하는 흑백 마스크 이미지 (흰색=편집 영역, 검정색=보존 영역)
- **Agent_Panel**: 에이전트 채팅이 표시되는 우측 360px 패널

## 요구사항

### 요구사항 1: 이미지 생성 모델 폴백 체인 검증

**사용자 스토리:** 개발자로서, 이미지 생성 시 사용 가능한 모델이 순서대로 시도되어 하나라도 성공하면 결과를 받고 싶다.

#### 인수 조건

1. WHEN 사용자가 generate_image 도구를 prompt 파라미터와 함께 호출하면, THE Image_Generator SHALL stability.sd3-5-large-v1:0 → stability.stable-image-core-v1:1 → amazon.titan-image-generator-v2:0 순서로 모델을 시도하여 첫 번째 성공 결과를 반환한다
2. WHEN 모든 이미지 모델이 실패하면, THE Image_Generator SHALL 마지막 모델의 오류 메시지(최대 200자)를 포함한 JSON 에러 응답을 반환한다
3. WHEN 이미지 생성이 성공하면, THE Image_Generator SHALL `.generated/` 폴더에 PNG 파일로 저장하고 상대 경로(프로젝트 루트 기준), 사용된 모델 ID, 요청 크기를 포함한 JSON 응답을 반환한다
4. THE Image_Generator SHALL 512x512, 1024x1024, 1024x1536, 1536x1024, 2048x2048 크기만 허용하며, size 파라미터가 생략된 경우 1024x1024를 기본값으로 사용한다
5. IF 유효하지 않은 크기가 요청되면, THEN THE Image_Generator SHALL 허용 크기 목록을 포함한 에러 응답을 반환한다
6. IF prompt 파라미터가 비어있거나 누락되면, THEN THE Image_Generator SHALL 프롬프트 필수임을 나타내는 에러 응답을 반환한다
7. THE Image_Generator SHALL prompt 파라미터를 최대 2000자까지 허용한다

### 요구사항 2: 이미지 편집 (Inpaint) 기능

**사용자 스토리:** 개발자로서, 기존 이미지의 특정 영역을 텍스트 프롬프트로 수정하고 싶다.

#### 인수 조건

1. WHEN 사용자가 edit_image 도구를 inpaint 모드로 호출하면, THE Image_Editor SHALL 원본 이미지(PNG 또는 JPEG, 최대 5MB)와 마스크 이미지(흰색=편집 영역, 검정색=보존 영역인 PNG)를 읽어 Bedrock 이미지 모델에 inpaint 요청을 전송한다
2. THE Image_Editor SHALL 원본 이미지 경로, 마스크 이미지 경로, 텍스트 프롬프트(최소 1자, 최대 512자)를 필수 입력으로 요구한다
3. WHEN inpaint 작업이 성공하면, THE Image_Editor SHALL 결과 이미지를 `.generated/` 폴더에 `inpaint_{timestamp}.png` 형식으로 저장하고, 파일 경로와 사용된 모델 ID를 포함한 JSON 객체를 반환한다
4. IF 원본 이미지 파일이 존재하지 않으면, THEN THE Image_Editor SHALL "file-not-found" 에러를 반환한다
5. IF 마스크 이미지 파일이 존재하지 않으면, THEN THE Image_Editor SHALL "mask-not-found" 에러를 반환한다
6. THE Image_Editor SHALL amazon.titan-image-generator-v2:0을 1순위로, amazon.nova-canvas를 2순위로 사용하는 폴백 체인을 적용하며, 1순위 모델 호출이 실패(타임아웃 30초 초과 또는 에러 응답)하면 2순위 모델로 재시도한다
7. IF 폴백 체인의 모든 모델이 실패하면, THEN THE Image_Editor SHALL "model-unavailable" 에러와 함께 마지막 실패 원인을 반환한다
8. IF 마스크 이미지의 해상도가 원본 이미지의 해상도와 일치하지 않으면, THEN THE Image_Editor SHALL "mask-dimension-mismatch" 에러를 반환한다
9. IF 원본 이미지가 PNG 또는 JPEG 형식이 아니거나 파일 크기가 5MB를 초과하면, THEN THE Image_Editor SHALL "invalid-image" 에러를 반환한다

### 요구사항 3: 이미지 편집 (Outpaint) 기능

**사용자 스토리:** 개발자로서, 기존 이미지의 경계를 확장하여 새로운 콘텐츠를 생성하고 싶다.

#### 인수 조건

1. WHEN 사용자가 edit_image 도구를 outpaint 모드로 호출하면, THE Image_Editor SHALL 원본 이미지와 확장 방향 및 확장 크기(방향당 1~1024 픽셀)를 Bedrock 이미지 모델에 outpaint 요청으로 전송한다
2. THE Image_Editor SHALL 원본 이미지 경로, 확장 방향(left, right, top, bottom 중 하나 이상), 확장 크기(픽셀 단위 정수), 텍스트 프롬프트(최대 512자)를 필수 입력으로 요구한다
3. WHEN outpaint 작업이 성공하면, THE Image_Editor SHALL 결과 이미지를 `.generated/` 폴더에 PNG로 저장하고 파일 경로와 최종 이미지 크기(width, height 픽셀)를 JSON으로 반환한다
4. IF 원본 이미지 파일이 존재하지 않으면, THEN THE Image_Editor SHALL "file-not-found" 에러를 반환한다
5. IF 원본 이미지가 지원되지 않는 형식(PNG, JPEG, WEBP 외)이거나 원본 크기가 한 변 4096 픽셀을 초과하면, THEN THE Image_Editor SHALL 원인을 나타내는 "invalid-input" 에러를 반환한다
6. IF Titan Image v2 모델 호출이 실패하면, THEN THE Image_Editor SHALL Nova Canvas 모델로 재시도하고, Nova Canvas도 실패하면 마지막 모델의 에러 원인을 포함한 "model-error" 에러를 반환한다
7. IF 확장 방향 값이 허용 목록(left, right, top, bottom)에 포함되지 않거나 확장 크기가 1~1024 범위를 벗어나면, THEN THE Image_Editor SHALL "invalid-parameter" 에러를 반환한다

### 요구사항 4: PDF 생성 검증

**사용자 스토리:** 개발자로서, 구조화된 섹션과 이미지를 포함한 PDF 리포트를 생성하고 싶다.

#### 인수 조건

1. WHEN 사용자가 generate_pdf 도구를 title과 sections 배열로 호출하면, THE PDF_Generator SHALL 제목을 첫 페이지 헤더로, 각 섹션을 순서대로 배치하여 A4(210×297mm) 크기 PDF를 생성한다
2. THE PDF_Generator SHALL 각 섹션의 heading을 Heading2 스타일(14pt 볼드)로, body를 Normal 스타일(10pt)로 렌더링한다
3. WHEN PDF 생성이 성공하면, THE PDF_Generator SHALL `.generated/` 폴더에 저장하고 상대 경로(path), 페이지 수(pageCount), 파일 크기 바이트(fileSize)를 JSON으로 반환한다
4. IF sections 배열이 비어있거나 누락되면, THEN THE PDF_Generator SHALL "sections is required" 에러를 반환한다
5. IF title이 비어있거나 누락되면, THEN THE PDF_Generator SHALL "title is required" 에러를 반환한다
6. IF reportlab 라이브러리가 설치되지 않았으면, THEN THE PDF_Generator SHALL error 필드에 "missing-dep", lib 필드에 "reportlab", hint 필드에 설치 명령을 포함한 JSON을 반환한다
7. IF PDF 파일 저장 중 예외가 발생하면, THEN THE PDF_Generator SHALL error 필드에 "pdf-generation-failed"와 detail 필드에 오류 설명(최대 200자)을 포함한 JSON을 반환한다

### 요구사항 5: PPTX 생성 검증

**사용자 스토리:** 개발자로서, 슬라이드 데크를 생성하고 각 슬라이드에 이미지를 자동 삽입하고 싶다.

#### 인수 조건

1. WHEN 사용자가 generate_pptx 도구를 title과 slides 배열로 호출하면, THE PPTX_Generator SHALL title을 표지 슬라이드로 추가한 뒤 slides 배열의 각 항목을 순서대로 슬라이드로 구성하여 PPTX 파일을 생성한다
2. WHEN 슬라이드에 imagePrompt가 포함되면, THE PPTX_Generator SHALL 해당 프롬프트로 generate_image를 호출하여 생성된 이미지를 슬라이드 우측 영역에 삽입한다
3. IF 슬라이드의 imagePrompt에 대한 이미지 생성이 실패하면, THEN THE PPTX_Generator SHALL 해당 슬라이드를 이미지 없이 유지하고 PPTX 생성을 계속 진행한다
4. WHEN PPTX 생성이 성공하면, THE PPTX_Generator SHALL `.generated/` 폴더에 저장하고 상대 경로(path)와 슬라이드 수(slideCount, 표지 슬라이드 포함)를 JSON 객체로 반환한다
5. IF slides 배열이 비어있거나 누락되면, THEN THE PPTX_Generator SHALL "slides is required" 에러를 JSON으로 반환한다
6. IF python-pptx 라이브러리가 설치되지 않았으면, THEN THE PPTX_Generator SHALL error 필드에 "missing-dep", lib 필드에 "python-pptx", hint 필드에 설치 명령을 포함한 JSON을 반환한다
7. THE PPTX_Generator SHALL 각 슬라이드의 layout 필드가 "title", "content", "two-column" 중 하나일 때 해당 레이아웃을 적용하며, layout이 생략된 경우 "content" 레이아웃을 기본값으로 사용한다
8. IF PPTX 파일 저장 중 예외가 발생하면, THEN THE PPTX_Generator SHALL error 필드에 "pptx-generation-failed"와 detail 필드에 오류 설명(최대 200자)을 포함한 JSON을 반환한다

### 요구사항 6: 생성된 이미지 채팅 인라인 표시

**사용자 스토리:** 개발자로서, 에이전트가 이미지를 생성하면 채팅 패널에서 바로 결과를 확인하고 싶다.

#### 인수 조건

1. WHEN 에이전트 도구 결과에 이미지 경로(`.generated/*.png`)가 포함되면, THE Chat_Renderer SHALL 해당 이미지를 채팅 메시지 내에 썸네일(최대 너비 320px, 최대 높이 240px, 원본 비율 유지)로 인라인 표시한다
2. WHEN 사용자가 인라인 썸네일을 클릭하면, THE Chat_Renderer SHALL 이미지를 에디터 영역에서 뷰포트에 맞춰(최대 너비·높이 100%, 원본 비율 유지) 표시한다
3. THE Chat_Renderer SHALL 단일 모드, 병렬 모드, 합의 모드 모두에서 동일한 썸네일 크기와 레이아웃으로 이미지를 인라인 표시한다
4. IF 이미지 파일이 존재하지 않거나 로드에 실패하면, THEN THE Chat_Renderer SHALL 썸네일과 동일한 영역(최대 너비 320px, 높이 80px)에 파일 경로 텍스트와 함께 에러 플레이스홀더를 표시한다
5. THE Chat_Renderer SHALL 이미지 아래에 모델명과 생성 크기(너비×높이 px 형식)를 한 줄로 표시한다
6. IF 단일 도구 결과에 2개 이상의 이미지 경로가 포함되면, THEN THE Chat_Renderer SHALL 각 이미지를 순서대로 개별 썸네일로 표시하되 최대 4개까지 표시하고, 초과분은 "+N개 더보기" 링크로 접근할 수 있게 한다

### 요구사항 7: 생성 파일 미리보기/다운로드 UI

**사용자 스토리:** 개발자로서, `.generated/` 폴더의 파일을 목록으로 보고 미리보기하거나 다운로드하고 싶다.

#### 인수 조건

1. WHEN File_Preview_Panel이 활성화되면, THE File_Preview_Panel SHALL `.generated/` 폴더의 파일 목록을 수정 시간 기준 최신순으로 최대 100개까지 표시한다
2. THE File_Preview_Panel SHALL 각 파일의 이름, 생성 시간(YYYY-MM-DD HH:mm 형식), 파일 크기(1024 미만은 bytes, 1024 이상은 KB/MB 단위로 소수점 1자리)를 표시한다
3. WHEN 사용자가 이미지 파일(.png, .jpg, .webp)을 선택하면, THE File_Preview_Panel SHALL 에디터 영역에 이미지 뷰어를 표시한다
4. WHEN 사용자가 PDF 파일을 선택하면, THE File_Preview_Panel SHALL 에디터 영역에 PDF 뷰어를 표시한다
5. WHEN 사용자가 PPTX 파일을 선택하면, THE File_Preview_Panel SHALL 에디터 영역에 PPTX 뷰어를 표시한다
6. IF 선택된 파일의 크기가 50MB를 초과하면, THEN THE File_Preview_Panel SHALL 미리보기 대신 파일 크기 초과 안내 메시지를 표시하고 다운로드만 허용한다
7. WHEN 사용자가 다운로드 버튼을 클릭하면, THE File_Preview_Panel SHALL Electron 저장 다이얼로그를 표시하여 사용자가 선택한 경로에 파일을 저장한다
8. IF 다운로드 중 파일 저장에 실패하면, THEN THE File_Preview_Panel SHALL 실패 원인을 포함한 에러 메시지를 표시한다
9. IF `.generated/` 폴더가 비어있거나 존재하지 않으면, THEN THE File_Preview_Panel SHALL "생성된 파일이 없습니다" 메시지를 표시한다
10. WHEN `.generated/` 폴더에 새 파일이 추가되면, THE File_Preview_Panel SHALL 2초 이내에 파일 목록을 자동으로 갱신한다
11. THE File_Preview_Panel SHALL Web Component(`<file-preview-panel>`)로 구현되며 사이드바 또는 에디터 영역에 배치된다

### 요구사항 8: edit_image 에이전트 도구 등록

**사용자 스토리:** 개발자로서, 에이전트 모드에서 edit_image 도구를 호출하여 이미지 편집을 수행하고 싶다.

#### 인수 조건

1. THE AI_Engine SHALL AGENT_TOOLS 딕셔너리의 tools 배열에 name이 "edit_image"인 toolSpec 항목을 등록하여 에이전트가 호출할 수 있게 한다
2. THE edit_image 도구 스키마 SHALL mode(enum: "inpaint", "outpaint"), image_path(문자열), prompt(문자열, 1~1000자)를 필수 파라미터로 정의한다
3. IF mode가 "inpaint"이면, THEN THE edit_image 도구 스키마 SHALL mask_path(문자열)를 필수 파라미터로 추가 요구한다
4. IF mode가 "outpaint"이면, THEN THE edit_image 도구 스키마 SHALL direction(문자열 배열, 유효 값: "up", "down", "left", "right", 최소 1개 최대 4개)을 필수 파라미터로 추가 요구한다
5. IF 지원하지 않는 mode 값이 전달되면, THEN THE AI_Engine SHALL "invalid-mode" 에러를 포함하는 JSON 객체를 반환한다
6. WHEN edit_image 도구가 성공적으로 실행되면, THE AI_Engine SHALL 편집된 이미지의 저장 경로(path)와 사용된 모델 ID(model)를 포함하는 JSON 객체를 반환한다
7. IF image_path 또는 mask_path가 존재하지 않는 파일을 가리키면, THEN THE AI_Engine SHALL "not-found" 에러와 해당 파일 경로를 포함하는 JSON 객체를 반환한다
