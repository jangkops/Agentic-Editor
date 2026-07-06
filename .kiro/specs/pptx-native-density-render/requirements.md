# Requirements Document

## Introduction

이 기능은 AI 에디터의 PPTX 생성에서 "젠스파크급 고밀도 디자인 + PowerPoint 편집가능 + 도형/텍스트 겹침 0"을 **동시에** 달성하는 것을 목적으로 한다. 그동안 빠져 있던 핵심 조각, 즉 **HTML 고밀도 레이아웃을 PNG로 굽지 않고 편집가능한 네이티브 PPTX 도형(텍스트박스/표/오토셰이프)으로 렌더하는 신규 경로**를 만든다.

### 확정된 근본 원인 (코드 추적으로 입증)

본 요구사항은 다음의 입증된 근본 원인을 해소하기 위한 것이다.

1. **HTML 본문이 통짜 이미지로 베이크됨.** `ai_engine/server.py`의 `_tool_generate_pptx`는 `_html_enabled`가 참이면 본문 슬라이드를 `slide_templates.render_layout()`의 HTML로 만들고, 이를 `_render_html_slide_to_png`로 1920×1080 PNG로 구워 `sd["slideBackground"]`(풀블리드)로 깐다. 그 결과 제목/본문/카드가 PNG 픽셀에 묻혀 **PowerPoint에서 선택·편집이 불가능한 통짜 이미지**가 된다.
2. **HTML→네이티브 도형 변환기가 코드베이스에 부재.** `slide_templates.py`의 고밀도 요소(`_section_header_bar`/`_contact_box`/`_note_callout`/`_numbered_list`/`_figure_slots` 등)는 호출되지만 전부 PNG로 래스터화되어 끝난다. 이 HTML 구조를 편집가능 도형으로 옮기는 변환 경로가 존재하지 않는다.
3. **화려함과 편집가능이 배타적.** 네이티브 편집가능 경로(`nativeDiagram`, `build_native_diagram` 등)는 `_html_enabled`가 꺼졌을 때(`not _html_enabled`)만 동작한다. 따라서 HTML(화려함)과 네이티브(편집가능)가 구조적으로 갈려 있다.
4. **직전 버그픽스가 통짜 이미지를 확정.** `_suppress_native_body = bool(_rr_html_bg_set)` 패치는 베이크된 배경이 채택되면 네이티브 본문을 제거하여 통짜 이미지를 확정했다. 동시에 표지/특정 슬라이드는 억제가 적용되지 않아 겹침이 잔존한다.
5. **실제 산출물 audit로 확인.** 7슬라이드 산출물 audit 결과, 일부 슬라이드는 통짜 이미지(편집가능 텍스트 0개), 나머지는 텍스트 겹침이 잔존했다. 즉 hermetic 단위 테스트만으로 합격 판정한 과거 스펙들이 실제 산출물 품질 결함을 놓쳤다.

### 해결 방향

알려진 레이아웃 집합(cover/section_divider/two_column/feature_grid/timeline/comparison/architecture)의 고밀도 디자인을 **PNG로 굽지 않고** 편집가능 네이티브 도형으로 렌더하는 경로를 신설하고, 모든 슬라이드(표지 포함)에 겹침 0·경계 안·제목 1회 규칙을 일관 적용한다. 합격 기준은 hermetic 단위 테스트가 아니라 **실제 생성된 .pptx를 audit하는 통합 검증**으로 정의한다.

## Glossary

- **PPTX_생성기 (PPTX_Generator)**: `ai_engine/server.py`의 `_tool_generate_pptx`를 포함하여 슬라이드 데이터를 PPTX 파일로 만드는 백엔드 생성 경로.
- **네이티브_렌더러 (Native_Renderer)**: 알려진 고밀도 레이아웃을 PowerPoint에서 선택·편집 가능한 네이티브 도형(텍스트박스/표/오토셰이프)으로 변환하는 신규 렌더 경로.
- **베이크_통짜이미지 (Baked_Image)**: HTML 레이아웃을 1920×1080 PNG로 래스터화하여 슬라이드 전체 배경(`slideBackground`, 풀블리드)으로 깐 산출물. 제목·본문 텍스트가 픽셀에 묻혀 PowerPoint에서 편집 불가능한 상태.
- **편집가능_네이티브 (Editable_Native_Shape)**: PowerPoint에서 텍스트 선택·수정·도형 이동이 가능한 네이티브 PPTX 셰이프. 텍스트 런(run)이 도형 트리에 실재한다.
- **장식_배경 (Decorative_Background)**: 콘텐츠 텍스트를 포함하지 않는 풀블리드 배경(사진/그라데이션/추상 이미지). 정보 전달이 아닌 시각 장식만 담당.
- **장식_배경_도형 (Decorative_Background_Shape)**: 콘텐츠 텍스트를 담지 않고 그 위에 놓일 텍스트·요소의 레이어링 배경 컨테이너로만 기능하는 도형(예: 섹션 헤더 막대, 카드 배경 컨테이너, 노트 콜아웃 배경 박스). 풀블리드 여부와 무관하며, 텍스트는 이 도형이 아니라 그 위(z-순서 상위)의 별도 편집가능_네이티브 셰이프가 담는다. 겹침 검사에서 제외되는 대상이다.
- **장식_비주얼 (Decorative_Visual)**: 편집이 불필요한 시각 요소 전체를 가리키는 상위 개념. 장식_배경, 히어로_일러스트, 그림슬롯 채움 이미지, 아이콘·엠블럼을 포함한다. 콘텐츠 텍스트(제목·본문·불릿·카드·다이어그램·표 텍스트)를 담지 않으며 정보 전달이 아닌 시각 장식·예시 목적만 수행한다.
- **히어로_일러스트 (Hero_Illustration)**: 슬라이드 또는 섹션의 주제를 시각적으로 표현하는 대형 일러스트레이션·이미지. 콘텐츠 텍스트를 포함하지 않는 장식_비주얼의 한 종류로, 편집가능_네이티브 콘텐츠와 분리되어 배치된다.
- **그림슬롯 (Figure_Slot)**: `slide_templates.py`의 그림 슬롯(figure_slot) 영역. 편집 불필요한 이미지(사진·일러스트)로 채워지는 자리이며, 콘텐츠 텍스트를 베이크하지 않는다.
- **Vertex_이미지_모듈 (Vertex_Image_Module)**: `ai_engine/vertex_image_module.py` 단일 모듈. 이미지 생성 예외 결정에 따라 Vertex AI 이미지 생성 호출이 발생하는 유일한 지점.
- **밀도_패리티 (Density_Parity)**: 젠스파크 참조 산출물 대비 정보 밀도(텍스트 분량, 시각 요소 개수)가 동률 이상인 상태. `scripts/parity_scorer.py`의 밀도 점수로 측정.
- **밀도_채점기 (Density_Scorer)**: `scripts/parity_scorer.py`. 산출물의 정보 밀도/시각 요소 수를 점수화하는 컴포넌트.
- **산출물_검증기 (Output_Auditor)**: 실제 생성된 .pptx 파일을 입력으로 편집가능 텍스트 존재·겹침률·경계 위반·제목 중복·밀도 점수를 검사하는 통합 검증 컴포넌트. `scripts/audit_pptx_textbox_overlap.py`, `scripts/audit_pptx_zorder_break.py`, `scripts/audit_pptx_baked_text.py`를 활용.
- **겹침률 (Overlap_Ratio)**: 두 셰이프의 면적 교집합을 더 작은 셰이프 면적으로 나눈 비율(%). 임계값은 10%.
- **슬라이드_경계 (Slide_Bounds)**: 좌상단 (0, 0)에서 우하단 (13.333, 7.5) 인치까지의 16:9 슬라이드 영역.
- **알려진_레이아웃 (Known_Layout)**: `slide_templates.py`의 `LAYOUT_REGISTRY`에 등록된 레이아웃 집합 — cover, section_divider, two_column, feature_grid, timeline, comparison, architecture.
- **고밀도_요소 (Dense_Element)**: 섹션 헤더, 연락처 박스, 노트 콜아웃, 번호 목록, 그림 슬롯, 카드 그리드, 다이어그램 등 고밀도 디자인을 구성하는 콘텐츠 단위.
- **Bedrock_게이트웨이 (Bedrock_Gateway)**: 모든 LLM·operation JSON 생성을 경유해야 하는 게이트웨이. `gateway.md` 참조.
- **비결함_입력 (Non_Defective_Input)**: 이미 편집가능·겹침 없음·경계 안 규칙을 만족하거나, 이 기능의 처리 대상이 아닌 입력.

## Requirements

### Requirement 1: 본문 콘텐츠의 편집가능 네이티브 렌더

**User Story:** 발표자로서, 생성된 PPTX의 본문 콘텐츠를 PowerPoint에서 직접 선택·수정하고 싶다. 그래야 통짜 이미지가 아닌 실제 편집가능 슬라이드로 활용할 수 있다.

#### Acceptance Criteria

1. WHEN PPTX_생성기가 알려진_레이아웃의 본문 콘텐츠(제목·본문·불릿·카드·번호목록·연락처·노트·그림슬롯·다이어그램)를 렌더할 때, THE 네이티브_렌더러 SHALL 해당 콘텐츠를 편집가능_네이티브 셰이프로 생성한다.
2. WHEN 본문 콘텐츠가 렌더될 때, THE 네이티브_렌더러 SHALL 콘텐츠 텍스트를 베이크_통짜이미지로 래스터화하지 않는다.
3. WHEN 고밀도_요소(섹션 헤더·연락처 박스·노트 콜아웃·번호 목록·그림 슬롯·카드 그리드)가 입력될 때, THE 네이티브_렌더러 SHALL 각 요소의 시각 구조(테두리·배지·강조색·정렬)를 편집가능_네이티브 셰이프로 보존한다.
4. IF 알려진_레이아웃의 콘텐츠를 네이티브_렌더러가 변환할 수 없는 경우, THEN THE PPTX_생성기 SHALL 콘텐츠 텍스트를 편집가능_네이티브 셰이프로 출력하는 폴백을 적용한다.
5. WHEN 슬라이드가 PPTX_생성기에 의해 생성될 때, THE PPTX_생성기 SHALL 본문 콘텐츠를 베이크_통짜이미지로 대체하는 경로를 사용하지 않는다.

### Requirement 2: 셰이프 겹침 0

**User Story:** 발표자로서, 슬라이드의 텍스트와 도형이 서로 겹치지 않기를 원한다. 그래야 내용이 가려지지 않고 읽을 수 있다.

#### Acceptance Criteria

1. WHEN PPTX_생성기가 슬라이드를 생성할 때, THE PPTX_생성기 SHALL 모든 편집가능_네이티브 텍스트 셰이프 쌍에 대해 겹침률(두 셰이프 경계 사각형 면적의 교집합을 두 셰이프 중 더 작은 면적으로 나눈 백분율)을 10% 미만으로 유지한다.
2. WHEN PPTX_생성기가 슬라이드를 생성할 때, THE PPTX_생성기 SHALL 각 편집가능_네이티브 텍스트 셰이프와 모든 이미지·도형 셰이프 사이의 겹침률(두 셰이프 경계 사각형 면적의 교집합을 두 셰이프 중 더 작은 면적으로 나눈 백분율)을 10% 미만으로 유지한다.
3. IF 두 셰이프의 겹침률이 10% 이상으로 계산되는 경우, THEN THE PPTX_생성기 SHALL 셰이프의 위치 또는 크기를 조정하여 해당 쌍의 겹침률을 10% 미만으로 낮춘다.
4. WHEN PPTX_생성기가 셰이프의 위치 또는 크기를 조정할 때, THE PPTX_생성기 SHALL 조정 후 모든 셰이프의 경계 사각형을 슬라이드 영역(폭 13.333인치, 높이 7.5인치, 좌상단 원점 기준 0인치 이상) 안에 완전히 포함되도록 유지한다.
5. IF 위치 또는 크기 조정 후에도 두 셰이프의 겹침률을 10% 미만으로 낮추지 못하는 경우, THEN THE PPTX_생성기 SHALL 슬라이드 생성을 실패로 처리하고 해당 슬라이드 식별자와 겹침 위반 셰이프 쌍을 나타내는 오류를 호출자에게 반환한다.

> **명확화 (겹침 검사 범위, 의미 한정):** 위 1–5의 겹침 검사는 **텍스트를 포함하지 않는 장식_배경_도형(예: 섹션 헤더 막대, 카드 배경 컨테이너)을 검사 대상에서 제외한다.** 즉 겹침률 10% 미만 규칙은 텍스트↔텍스트 셰이프 쌍, 그리고 텍스트↔비배경 이미지/도형 쌍에만 적용한다. 장식_배경_도형 위에 텍스트·요소를 의도적으로 겹쳐 올리는 레이어드 디자인은 정상으로 허용한다. 텍스트가 이미지/도형에 가려지는 경우(가림)는 본 겹침 규칙이 아니라 Requirement 6.2의 z-순서 규칙으로 별도 보장한다.

### Requirement 3: 슬라이드 경계 안 배치

**User Story:** 발표자로서, 모든 도형이 슬라이드 안에 들어오기를 원한다. 그래야 잘리거나 화면 밖으로 나가는 내용이 없다.

#### Acceptance Criteria

1. WHEN PPTX_생성기가 셰이프를 배치할 때, THE PPTX_생성기 SHALL 모든 셰이프의 좌단·상단·우단·하단을 슬라이드_경계 (0, 0)–(13.333, 7.5) 인치 안에 0.05 인치 이하의 허용오차 내로 배치한다.
2. IF 셰이프가 슬라이드_경계를 0.05 인치 허용오차를 초과하여 벗어나는 경우, THEN THE PPTX_생성기 SHALL 먼저 해당 셰이프를 평행이동하여 슬라이드_경계 안으로 들어오게 한다.
3. IF 셰이프의 폭 또는 높이가 슬라이드_경계 크기(13.333 × 7.5 인치)를 초과하여 평행이동만으로 경계 안에 들어올 수 없는 경우, THEN THE PPTX_생성기 SHALL 해당 셰이프를 슬라이드_경계 크기 이하로 축소한 뒤 슬라이드_경계 안에 배치한다.
4. WHILE 셰이프가 이미 슬라이드_경계 안(0.05 인치 허용오차 내)에 있는 동안, THE PPTX_생성기 SHALL 해당 셰이프의 위치와 크기를 변경 없이 보존한다.
5. WHEN PPTX_생성기가 셰이프의 위치 또는 크기 조정을 완료할 때, THE PPTX_생성기 SHALL 조정 후 해당 셰이프가 슬라이드_경계 안(0.05 인치 허용오차 내)에 있음을 보장한다.

### Requirement 4: 제목 1회 표시

**User Story:** 발표자로서, 슬라이드마다 제목이 한 번만 나타나기를 원한다. 그래야 중복 제목으로 인한 혼란과 겹침이 없다.

#### Acceptance Criteria

1. WHEN PPTX_생성기가 제목 텍스트를 가진 한 슬라이드를 생성할 때, THE PPTX_생성기 SHALL 그 슬라이드의 제목 텍스트를 정확히 1개의 편집가능_네이티브 셰이프로만 표시한다.
2. IF 한 슬라이드에 동일 제목 텍스트(앞뒤 공백 제거·대소문자 정규화 후 문자열 일치)가 2개 이상의 편집가능_네이티브 셰이프로 방출되려는 경우, THEN THE PPTX_생성기 SHALL 편집가능_네이티브 셰이프 1개만 남기고 나머지 중복 제목 셰이프를 제거한다.
3. IF 한 슬라이드의 제목 텍스트가 베이크_통짜이미지 또는 장식_배경에 픽셀로 구워진 동시에 편집가능_네이티브 셰이프로도 존재하는 경우, THEN THE PPTX_생성기 SHALL 편집가능_네이티브 셰이프 1개만 유지하고 베이크된 제목이 채택되지 않도록 그 배경을 제거하거나 미채택한다.
4. WHEN PPTX_생성기가 제목 텍스트가 없는 슬라이드를 생성할 때, THE PPTX_생성기 SHALL 제목용 편집가능_네이티브 셰이프를 생성하지 않는다.

### Requirement 5: 밀도/디자인 패리티 (편집가능 상태에서)

**User Story:** 발표자로서, 편집가능한 슬라이드라도 젠스파크급 정보 밀도와 시각 완성도를 유지하기를 원한다. 그래야 편집가능성을 얻으면서 디자인 품질을 잃지 않는다.

#### Acceptance Criteria

1. WHEN PPTX_생성기가 슬라이드 덱을 생성할 때, THE PPTX_생성기 SHALL 베이크_통짜이미지 없이 편집가능_네이티브 셰이프만으로 각 슬라이드의 콘텐츠를 구성하고, 덱의 모든 슬라이드가 밀도_채점기 판정에서 합격(density_score >= reference_score)이 되도록 한다.
2. WHEN 밀도_채점기가 생성된 .pptx의 슬라이드(카테고리 cover 또는 body)를 채점할 때, THE 밀도_채점기 SHALL 그 슬라이드의 밀도점수 대 참조점수 비율(density_score / reference_score)을 산출하고, 비율이 1.0 이상이면 합격(passed=true)으로, 1.0 미만이면 불합격(passed=false)으로 보고한다.
3. WHEN 밀도_채점기가 슬라이드를 불합격으로 판정할 때, THE 밀도_채점기 SHALL 미충족 항목 목록(missing)을 합격 판정 결과와 함께 보고한다.
4. WHERE 슬라이드가 고밀도_요소를 포함하는 경우, THE 네이티브_렌더러 SHALL 새 디자인 토큰을 신설하지 않고 기존 design_tokens의 색·여백·타이포그래피 토큰만 사용하여 각 고밀도_요소의 시각 구조를 구성한다.
5. IF 밀도_채점기에 빈 입력(None 또는 빈 문자열) 또는 미지원 카테고리(cover·body 외)가 전달되는 경우, THEN THE 밀도_채점기 SHALL 점수를 산출하지 않고 입력이 유효하지 않음을 나타내는 오류(ValueError)를 발생시킨다.

### Requirement 6: 장식 배경과 콘텐츠 분리

**User Story:** 발표자로서, 풀블리드 배경은 장식으로만 쓰고 콘텐츠는 항상 편집가능하기를 원한다. 그래야 배경 때문에 텍스트가 편집 불가능해지지 않는다.

#### Acceptance Criteria

1. WHERE 슬라이드가 슬라이드_경계 (0, 0)–(13.333, 7.5) 인치 전체를 덮는 풀블리드 배경을 사용하는 경우, THE PPTX_생성기 SHALL `scripts/audit_pptx_baked_text.py`의 베이크 텍스트 판정(텍스트추정행 비율 6% 미만 AND 추정 텍스트줄 수 6개 미만)을 만족하는 장식_배경(콘텐츠 텍스트 없는 사진·그라데이션·추상 이미지)만 허용한다.
2. WHEN 슬라이드에 장식_배경이 적용될 때, THE PPTX_생성기 SHALL 그 슬라이드의 모든 콘텐츠 텍스트를 장식_배경보다 앞선 z-순서의 편집가능_네이티브 셰이프로 배치한다.
3. IF 배경 후보 이미지가 베이크 텍스트 판정 임계(텍스트추정행 비율 6% 이상 OR 추정 텍스트줄 수 6개 이상)를 초과하는 경우, THEN THE PPTX_생성기 SHALL 그 이미지를 풀블리드 배경으로 채택하지 않고 해당 슬라이드의 콘텐츠를 편집가능_네이티브 셰이프로 렌더한다.
4. IF 배경 후보 이미지가 베이크 텍스트 판정으로 풀블리드 배경에서 제외되는 경우, THEN THE PPTX_생성기 SHALL 제외 사유와 해당 슬라이드 위치를 검증 보고에 기록한다.

### Requirement 7: 표지·모든 슬라이드 일관 적용

**User Story:** 발표자로서, 본문뿐 아니라 표지·구조형·다이어그램 슬라이드도 동일한 품질 규칙을 따르기를 원한다. 그래야 덱 전체가 일관되게 편집가능하고 겹침이 없다.

#### Acceptance Criteria

1. WHEN PPTX_생성기가 표지 슬라이드를 생성할 때, THE PPTX_생성기 SHALL 표지 콘텐츠를 편집가능_네이티브 셰이프로 생성하고 겹침률 10% 미만·슬라이드_경계 안 규칙을 적용한다.
2. WHEN PPTX_생성기가 구조형·다이어그램 슬라이드(timeline·comparison·architecture·feature_grid)를 생성할 때, THE PPTX_생성기 SHALL 해당 콘텐츠를 편집가능_네이티브 셰이프로 생성하고 겹침률 10% 미만·슬라이드_경계 안·제목 1회 규칙을 적용한다.
3. THE PPTX_생성기 SHALL 알려진_레이아웃 전체에 대해 편집가능·겹침 10% 미만·경계 안·제목 1회 규칙을 동일하게 적용한다.

### Requirement 8: 합격 게이트 = 실제 산출물 audit

**User Story:** 품질 책임자로서, 합격 판정이 실제 생성된 .pptx 산출물에 대한 검증으로 이루어지기를 원한다. 그래야 hermetic 단위 테스트가 놓친 실제 결함이 다시 통과되지 않는다.

#### Acceptance Criteria

1. WHEN 산출물_검증기가 실제 생성된 .pptx를 검사할 때, THE 산출물_검증기 SHALL 장식_배경을 제외한 모든 슬라이드 각각에 대해 텍스트 프레임에 비어있지 않은 텍스트 런을 가진 편집가능_네이티브 텍스트 셰이프가 1개 이상 존재함을 확인한다.
2. WHEN 산출물_검증기가 실제 생성된 .pptx를 검사할 때, THE 산출물_검증기 SHALL 장식_배경(풀블리드)을 제외한 모든 셰이프 쌍에 대해 겹침률(두 셰이프 면적 교집합을 더 작은 셰이프 면적으로 나눈 비율)이 10% 미만임을 확인한다.
3. WHEN 산출물_검증기가 실제 생성된 .pptx를 검사할 때, THE 산출물_검증기 SHALL 모든 셰이프가 슬라이드_경계 (0, 0)–(13.333, 7.5) 인치 안에 있음을 확인한다.
4. WHEN 산출물_검증기가 실제 생성된 .pptx를 검사할 때, THE 산출물_검증기 SHALL 슬라이드마다 제목 텍스트를 담은 편집가능_네이티브 셰이프가 정확히 1개임을 확인한다.
5. WHEN 산출물_검증기가 실제 생성된 .pptx를 검사할 때, THE 산출물_검증기 SHALL 밀도_채점기가 산출한 카테고리(cover·body)별 밀도 점수가 각 카테고리의 젠스파크 참조 점수 이상임을 확인한다.
6. IF 산출물_검증기의 검사 항목 중 하나라도 실패하는 경우, THEN THE 산출물_검증기 SHALL 합격 판정을 거부하고, 실패한 검사 항목명·해당 슬라이드 번호(1부터 시작)·문제 셰이프 식별자를 포함한 실패 보고를 출력한다.
7. THE 산출물_검증기 SHALL hermetic 단위 테스트 통과만으로 합격 판정을 내리지 않는다.
8. IF 풀블리드 배경 이미지에 콘텐츠 텍스트가 베이크된 것으로 검출되는 경우, THEN THE 산출물_검증기 SHALL 합격 판정을 거부하고 해당 슬라이드 번호와 검출 신호를 보고한다.
9. IF 편집가능_네이티브 텍스트 셰이프가 자신과 겹치는 이미지 셰이프보다 아래(z-order상 먼저)에 배치되어 가려지는 경우, THEN THE 산출물_검증기 SHALL 합격 판정을 거부하고 해당 슬라이드 번호와 겹침 셰이프 쌍을 보고한다.
10. WHEN 산출물_검증기의 모든 검사 항목이 통과하는 경우, THE 산출물_검증기 SHALL 합격 판정을 출력한다.

> **명확화 (8.2 겹침 검사 범위, 의미 한정):** 8.2의 겹침률 10% 미만 검사는 **텍스트를 포함하지 않는 장식_배경_도형(예: 섹션 헤더 막대, 카드 배경 컨테이너)을 검사 대상에서 제외한다.** 검사는 텍스트 보유 셰이프 쌍(텍스트↔텍스트) 및 텍스트↔비배경 이미지/도형 쌍에만 적용하며, 장식_배경_도형 위에 텍스트를 의도적으로 올리는 레이어드 디자인은 정상으로 허용한다. 텍스트가 이미지/도형에 가려지는 경우(가림)는 8.2가 아니라 8.9의 z-순서 검사로 별도 보장한다.

### Requirement 9: 회귀 방지 (기존 동작 보존)

**User Story:** 유지보수 담당자로서, 신규 네이티브 렌더 경로가 기존에 정상 동작하던 경로를 깨지 않기를 원한다. 그래야 이미 해결된 결함이 재발하지 않는다.

#### Acceptance Criteria

1. WHEN caller가 imageFile 또는 slideBackground 경로를 명시한 경우, THE PPTX_생성기 SHALL 그 경로를 주 렌더러로 유지한다(명시 우선순위 보존).
2. WHEN Vertex 이미지가 생성된 슬라이드를 렌더할 때, THE PPTX_생성기 SHALL 그 이미지를 손실 0으로 임베드한다(pptx-quality-vertex-images 보존).
3. WHEN AE_PREFER_EDITABLE_DIAGRAM 등 기존 네이티브 다이어그램 경로가 활성인 경우, THE PPTX_생성기 SHALL 그 경로의 기존 동작을 보존한다.
4. THE PPTX_생성기 SHALL 선행 스펙(pptx-overlay-collision-fix, pptx-image-slot-placement-fix, pptx-design-density-parity, slide_templates 밀도)의 검증 항목을 회귀 없이 유지한다.
5. WHERE 입력이 비결함_입력인 경우, THE PPTX_생성기 SHALL 해당 입력에 대해 추가 변형 없이 no-op으로 동작한다.

### Requirement 10: 아키텍처·보안 제약 준수

**User Story:** 플랫폼 책임자로서, 신규 경로가 프로젝트의 게이트웨이·스택·디스크 제약을 준수하기를 원한다. 그래야 보안·아키텍처 규칙 위반이 발생하지 않는다.

#### Acceptance Criteria

1. WHEN 네이티브_렌더러 또는 PPTX_생성기가 LLM·operation JSON을 생성할 때, THE PPTX_생성기 SHALL 그 호출을 Bedrock_게이트웨이 경유로만 수행한다.
2. WHERE 이미지 생성이 필요한 경우, THE PPTX_생성기 SHALL `ai_engine/vertex_image_module.py` 단일 모듈을 통해서만 Vertex AI를 호출한다(이미지 생성 예외).
3. WHEN 신규 프론트엔드 코드가 추가될 때, THE 네이티브_렌더러 SHALL Electron + Vanilla JavaScript 스택만 사용한다(React·TypeScript 금지).
4. WHEN server.py가 편집 중인 파일을 처리할 때, THE PPTX_생성기 SHALL 에디터 버퍼 대신 디스크 상태를 기준으로 처리한다.
5. WHEN 기존 .pptx에 변경을 적용할 때, THE PPTX_생성기 SHALL 변경을 가산적(additive)으로 적용하고 기존 바이트를 보존한다.

### Requirement 11: Vertex 초고퀄 장식·비주얼 적극 활용 (역할 분리)

**User Story:** 발표자로서, 슬라이드의 장식·비주얼 요소(장식_배경·히어로_일러스트·그림슬롯·아이콘)를 Vertex AI 이미지로 초고퀄 고도화하고 싶다. 단 제목·본문·불릿·카드·다이어그램·표 같은 콘텐츠 텍스트는 항상 편집가능_네이티브로 유지되기를 원한다. 그래야 시각 완성도를 끌어올리면서도 편집가능성을 잃지 않는다.

#### Acceptance Criteria

1. WHEN 슬라이드에 장식_배경 또는 그림슬롯 등 편집이 불필요한 장식_비주얼 요소가 필요할 때, THE PPTX_생성기 SHALL Vertex_이미지_모듈을 통해 고해상도 이미지를 생성하여 해당 장식_비주얼 요소를 채운다.
2. WHEN PPTX_생성기가 Vertex 이미지를 장식_비주얼 요소로 사용할 때, THE PPTX_생성기 SHALL 콘텐츠 텍스트(제목·본문·불릿·카드 텍스트·다이어그램 텍스트·표 텍스트)를 그 이미지에 베이크하지 않고 편집가능_네이티브 셰이프로 별도 생성한다.
3. WHERE Vertex 옵트인이 비활성(환경변수 AE_ENABLE_VERTEX_IMAGE 값이 1이 아니거나 GOOGLE_APPLICATION_CREDENTIALS 자격증명이 부재)인 경우, THE PPTX_생성기 SHALL 장식_비주얼 없이 편집가능_네이티브 콘텐츠만으로 슬라이드를 생성하고 콘텐츠 텍스트 손실 0(입력 콘텐츠 텍스트 런 전수 보존)을 유지한다.
4. WHEN Vertex 이미지가 생성되어 슬라이드에 임베드될 때, THE PPTX_생성기 SHALL 그 이미지를 손실 0으로 임베드하고(pptx-quality-vertex-images 보존), 이미지 경계 사각형을 슬라이드_경계 (0, 0)–(13.333, 7.5) 인치 안에 배치하며, 그 이미지와 각 편집가능_네이티브 콘텐츠 텍스트 셰이프 사이의 겹침률을 10% 미만으로 유지하고, 콘텐츠 텍스트 셰이프를 그 이미지보다 앞선 z-순서에 배치한다.
5. WHEN 장식_비주얼 요소를 위해 Vertex가 활용될 때, THE PPTX_생성기 SHALL 이미지 생성 호출을 Vertex_이미지_모듈 단일 모듈을 통해서만 수행하고, LLM·operation JSON 생성은 Bedrock_게이트웨이 경유로 유지한다(gateway.md 정합).
6. WHERE 슬라이드가 Vertex로 생성된 장식_비주얼을 포함하는 경우, THE 산출물_검증기 SHALL 그 장식_비주얼이 `scripts/audit_pptx_baked_text.py`의 베이크 텍스트 판정(텍스트추정행 비율 6% 미만 AND 추정 텍스트줄 수 6개 미만)을 초과하지 않음을 확인하고, 동일 슬라이드에 비어있지 않은 텍스트 런을 가진 편집가능_네이티브 콘텐츠 셰이프가 1개 이상 공존함을 확인한다.

## Non-Goals (범위 밖)

- 완전한 임의 HTML/CSS 파서 구현(무한 레이아웃 지원)은 범위 밖. 신규 네이티브 렌더는 `LAYOUT_REGISTRY`의 알려진_레이아웃(cover/section_divider/two_column/feature_grid/timeline/comparison/architecture)으로 한정한다.
- 새 디자인 토큰/브랜드 체계 신설은 범위 밖. 기존 design_tokens를 재사용한다.
- **Vertex 이미지에 콘텐츠 텍스트(제목·본문·불릿·카드·다이어그램·표 텍스트)를 베이크하는 것은 명시적으로 금지**되며 범위 밖이다. Vertex 이미지는 편집 불가 래스터이므로 장식_비주얼(장식_배경·히어로_일러스트·그림슬롯·아이콘·엠블럼) 용도로만 사용하고, 콘텐츠 텍스트는 항상 편집가능_네이티브 셰이프로 유지한다(Requirement 1·6·11과 정합).

## 참고 파일

- 구현 대상: `ai_engine/server.py`, `ai_engine/slide_templates.py`, `ai_engine/native_diagram_pptx.py`, `ai_engine/layout_geometry.py`
- 검증·채점: `scripts/parity_scorer.py`, `scripts/audit_pptx_textbox_overlap.py`, `scripts/audit_pptx_zorder_break.py`, `scripts/audit_pptx_baked_text.py`
- 스티어링: `.kiro/steering/project.md`, `.kiro/steering/gateway.md`, `.kiro/steering/security.md`, `.kiro/steering/ui.md`
