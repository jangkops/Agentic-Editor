# Requirements Document

요구사항 문서: PPTX 디자인 밀도 패리티 (pptx-design-density-parity)

## Introduction

사용자는 우리 에디터가 생성한 슬라이드 출력을 Genspark가 생성한 고품질 참조 PDF
("신규 입사자 노트북 세팅 온보딩 매뉴얼", 표지 1 + 본문 3, 총 4페이지)와 비교했다.
참조 자료는 표지와 본문 모두에서 높은 **디자인 밀도**(요소 다양성·정보 계층·시각 장식)를 보인다.

사용자의 합격 기준(수용 한계선)은 명확하다. **우리 슬라이드 출력은 디자인 밀도와 디테일에서
Genspark 참조와 최소한 동률이어야 하며, 가능하면 이를 초과해야 한다.**

현재 렌더러 `ai_engine/slide_templates.py`는 `render_layout(layout, data)`로 디스패치되는 11종 레이아웃을
제공하지만, 참조 대비 다음 밀도 요소가 비어 있다(파일 검증 완료).
- `render_two_column`은 left_content/right_content를 줄바꿈 텍스트(불릿/단락)로만 받고, 좌/우 badge·metric과
  단일 배경 이미지만 지원한다. 섹션별로 **담당자(CONTACT) 박스 / 스크린샷 카드 / 참고(NOTE) 콜아웃 박스 /
  하이퍼링크 칩 / 원형 번호 배지 리스트 아이템**을 임베드할 수 없다.
- `render_cover_slide`에는 **표지 위 STEP 카드 그리드** 옵션이 없다.
- 캡션이 달린 **다중 스크린샷(그림) 슬롯**이 없다.
- 제목의 **부분(인라인) 강조색** 표현이 없다.

이 기능은 위 격차를 메우되, 이 코드베이스의 강한 회귀 테스트 문화에 맞춰 **가산적(additive)·바이트 보존(byte-preserving)**
방식으로만 추가한다. 즉 새 필드를 생략하면 기존 출력이 바이트 단위로 동일해야 한다. 또한 "최소 동률" 합격 여부를
사람의 주관이 아니라 **객관적 요소 커버리지 체크리스트/점수**로 검증 가능하게 만든다.

이 문서는 요구사항만 정의한다(설계·태스크는 다루지 않는다). 모든 인수 조건은 구현 중심이고 테스트 가능하도록 작성한다.

### 비목표 (Out of Scope)

- LLM 자동 매핑 품질(콘텐츠를 어떤 레이아웃/필드로 자동 채울지)은 본 스펙의 범위가 아니다. 본 스펙은 **템플릿 자체의 디자인 천장**을 다룬다.
- 새 레이아웃 종류를 무한정 추가하는 것이 아니라, 기존 레이아웃에 밀도 요소를 가산하는 것이 우선이다.
- 클릭 가능한 실제 하이퍼링크 동작(PPTX 하이퍼링크 삽입)은 본 스펙 범위가 아니며, 렌더링되는 칩의 시각 표현만 다룬다.

## Glossary

- **Slide_Renderer**: HTML 슬라이드 템플릿 모듈 (`ai_engine/slide_templates.py`). `render_*` 함수와 `render_layout` 디스패처를 포함한다.
- **Layout_Dispatcher**: 레이아웃 이름 문자열을 받아 해당 `render_*` 함수를 `fn(**data)`로 호출하는 함수 (`render_layout`). 알 수 없는 키워드가 전달되면 TypeError로 빈 문자열을 반환한다.
- **Cover_Renderer**: 표지 레이아웃 렌더 함수 (`render_cover_slide`).
- **Two_Column_Renderer**: 2단 본문 레이아웃 렌더 함수 (`render_two_column`).
- **Section_Card**: 2단 본문 한 컬럼을 구성하는 밀도 단위. 번호 배지가 포함된 다크 헤더 바, 본문 단락, 그리고 선택적 밀도 요소(CONTACT 박스·NOTE 콜아웃·스크린샷 슬롯·하이퍼링크 칩·번호 배지 리스트)를 포함할 수 있다.
- **Contact_Box**: 담당자 정보를 담는 박스. 틴트 배경과 좌측 강조 보더를 가지며 이름과 내선번호 항목을 포함한다.
- **Note_Callout**: 참고/주의 사항을 담는 콜아웃 박스. 노랑 계열 틴트 배경과 좌측 강조 보더를 가지며 멀티라인 텍스트를 포함한다.
- **Link_Chip**: 하이퍼링크를 시각적으로 표현하는 칩. SVG 링크/첨부 아이콘과 라벨 텍스트, 진행 화살표 표식으로 구성된다(데코 이모지 미사용).
- **Numbered_List_Item**: 원형 번호 배지(1/2/3…)와 본문 텍스트로 구성된 리스트 항목.
- **Figure_Slot**: 캡션이 달린 스크린샷/그림 임베드 슬롯. 슬라이드당 다중 슬롯을 가질 수 있다.
- **Accent_Headline**: 제목 텍스트의 일부 구간만 강조색으로 표시하는 부분 강조 헤드라인.
- **Step_Card_Grid**: 표지 위에 임베드되는 STEP 카드 격자(예: STEP 01/02/03-04/05-07). 각 카드는 STEP 라벨과 설명을 가진다.
- **Notice_Chip**: 표지 상단의 "공지사항 NOTICE" 형태 eyebrow 칩/필.
- **Icon_Badge**: 표지의 원형 아이콘 배지(틴트 원 안의 SVG 기능 아이콘).
- **Notice_Tab**: 본문 슬라이드 우상단의 "공지사항 NOTICE" 형태 코너 탭.
- **Slide_Footer**: 슬라이드 하단의 러닝 타이틀과 페이지 번호(예: 1/3)를 포함하는 푸터.
- **Parity_Checklist**: Genspark 참조에서 도출한 디자인 밀도 요소 목록. 표지용·본문용으로 구분되며 각 항목은 우리 출력 HTML에서 기계 판정 가능한 마커로 검증된다.
- **Density_Score**: 특정 슬라이드 카테고리에서 Parity_Checklist 항목 중 우리 출력에 존재하는 항목 수.
- **Reference_Score**: 동일 Parity_Checklist에 대해 Genspark 참조가 충족하는 항목 수(고정 기준값).
- **Parity_Scorer**: 렌더된 HTML/출력을 입력받아 Density_Score를 계산하고 Reference_Score와 비교하는 검증 구성요소.
- **Visual_Comparator**: 우리 출력과 참조 출력을 나란히 PNG로 렌더해 시각 비교 산출물을 만드는 도구.
- **Density_Marker**: 특정 밀도 요소가 렌더되었음을 나타내는 출력 내 고유 식별 문자열(예: `class="contact-box"`). 밀도 요소가 비활성일 때는 출력에 존재하지 않는다.
- **Generated_Folder**: 생성 산출물이 저장되는 `.generated/` 디렉토리.
- **Headless_Capture**: HTML → Chrome 헤드리스 → 1920×1080 PNG 캡처 파이프라인.

## Requirements

### Requirement 1: 표지 디자인 밀도 패리티

**User Story:** 발표자로서, 표지가 Genspark 참조 표지처럼 아이콘 배지·공지 칩·부분 강조 제목·STEP 카드·푸터 라벨을 갖추기를 원한다. 그래야 첫 화면부터 디자인 밀도에서 동률 이상을 달성한다.

#### Acceptance Criteria

1. WHERE Icon_Badge 입력이 제공되면, THE Cover_Renderer SHALL 표지에 틴트 원 안의 SVG 기능 아이콘으로 구성된 원형 아이콘 배지를 렌더한다.
2. WHERE Notice_Chip 텍스트가 제공되면, THE Cover_Renderer SHALL 제목 위에 해당 텍스트를 담은 eyebrow 칩을 렌더하며, 텍스트 길이가 40자를 초과하면 40자까지만 표시하고 말줄임 표식을 덧붙인다.
3. WHERE 제목의 부분 강조 구간이 제공되면, THE Cover_Renderer SHALL 해당 구간만 강조색으로 표시하고 나머지 구간은 기본 제목 색으로 표시하는 Accent_Headline을 렌더한다.
4. WHERE Step_Card_Grid 항목 목록이 제공되면, THE Cover_Renderer SHALL 각 항목의 STEP 라벨과 설명을 담은 STEP 카드 격자를 표지 안에 렌더하되, 최소 1개에서 최대 6개 항목을 렌더하고 6개를 초과하는 항목은 렌더하지 않는다.
5. WHERE Slide_Footer 라벨이 제공되면, THE Cover_Renderer SHALL 표지 하단에 해당 라벨을 렌더하며, 라벨 길이가 80자를 초과하면 80자까지만 표시하고 말줄임 표식을 덧붙인다.
6. WHEN 표지가 좌측 수직 강조 바 입력과 함께 렌더되면, THE Cover_Renderer SHALL 좌측 수직 강조 바와 코너 장식 요소를 함께 렌더한다.
7. IF Step_Card_Grid 항목 수가 0이거나 목록이 제공되지 않으면, THEN THE Cover_Renderer SHALL STEP 카드 격자 마크업을 생성하지 않는다.
8. WHERE 부분 강조 구간 입력에 강조 대상이 제목 본문에 존재하지 않는 텍스트가 포함되면, THE Cover_Renderer SHALL 제목 전체를 기본 제목 색으로 렌더하고 강조 마크업을 생성하지 않는다.
9. WHEN Icon_Badge, eyebrow 칩, 좌측 강조 바를 모두 활성화한 표지가 렌더되면, THE Cover_Renderer SHALL Icon_Badge를 정확히 1개, eyebrow 칩을 정확히 1개, 좌측 강조 바를 정확히 1개 렌더한다.
10. IF Icon_Badge 입력은 제공되었으나 사용할 SVG 아이콘이 없으면, THEN THE Cover_Renderer SHALL 아이콘 배지 마크업을 생성하지 않고 표지의 나머지 요소를 정상적으로 렌더한다.

### Requirement 2: 본문 섹션 카드 밀도 패리티

**User Story:** 발표자로서, 2단 본문의 각 섹션이 번호 배지 헤더·담당자 박스·참고 콜아웃·하이퍼링크 칩·번호 배지 리스트를 담을 수 있기를 원한다. 그래야 본문 슬라이드가 참조 본문과 동률 이상의 정보 밀도를 갖는다.

#### Acceptance Criteria

1. WHERE 섹션 번호와 섹션 제목이 제공되면, THE Two_Column_Renderer SHALL 해당 컬럼 상단에 번호 배지와 섹션 제목을 담은 다크 헤더 바를 렌더하며, 섹션 제목은 공백 제외 1자 이상 40자 이하로 표시한다.
2. WHERE Contact_Box 입력(담당자 이름·내선 항목)이 제공되면, THE Two_Column_Renderer SHALL 틴트 배경과 좌측 강조 보더를 가진 Contact_Box를 해당 컬럼에 렌더하며, 각 항목 라벨은 30자 이하로, 항목은 최대 5개까지 렌더한다.
3. WHERE Note_Callout 텍스트가 제공되면, THE Two_Column_Renderer SHALL 노랑 계열 틴트 배경과 좌측 강조 보더를 가진 멀티라인 Note_Callout을 해당 컬럼에 렌더하며, 텍스트는 1자 이상 300자 이하로 표시한다.
4. WHERE Link_Chip 항목 목록이 제공되면, THE Two_Column_Renderer SHALL 각 항목마다 SVG 링크/첨부 아이콘과 라벨 텍스트, 진행 화살표 표식을 가진 Link_Chip을 1개 이상 6개 이하로 렌더하며, 각 라벨은 30자 이하로 표시한다.
5. WHERE Numbered_List_Item 목록이 제공되면, THE Two_Column_Renderer SHALL 각 항목마다 원형 번호 배지와 본문 텍스트를 가진 리스트 항목을 1개 이상 8개 이하로 렌더하며, 번호는 1부터 시작하여 1씩 증가하는 순차 번호로 표시한다.
6. WHERE Notice_Tab 입력이 제공되면, THE Two_Column_Renderer SHALL 슬라이드 우상단에 코너 탭을 렌더하며, 탭 라벨은 20자 이하로 표시한다.
7. WHERE Slide_Footer의 러닝 타이틀과 페이지 번호가 제공되면, THE Two_Column_Renderer SHALL 슬라이드 하단에 40자 이하의 러닝 타이틀과 "현재/전체" 형식(예: 1/3)의 페이지 번호를 렌더한다.
8. THE Two_Column_Renderer SHALL 요구사항 2의 각 밀도 요소(번호 헤더 바, Contact_Box, Note_Callout, Link_Chip, Numbered_List_Item, Notice_Tab, Slide_Footer)를 해당 입력 제공 여부에 따라 서로 독립적으로 활성화하며, 입력이 제공되지 않은 요소는 렌더하지 않는다.
9. WHERE Link_Chip 라벨이 제공되면, THE Two_Column_Renderer SHALL 데코 이모지 없이 SVG 기능 아이콘만 사용하여 칩을 렌더한다.
10. IF 어떤 밀도 요소의 항목 수 또는 텍스트 길이가 정의된 최대값을 초과하면, THEN THE Two_Column_Renderer SHALL 초과분을 최대값까지 잘라내고 잘림 표식을 표시하며 렌더 생성을 중단하지 않는다.
11. IF 컬럼의 밀도 요소 총 높이가 컬럼 가용 높이를 초과하면, THEN THE Two_Column_Renderer SHALL 콘텐츠가 슬라이드 경계를 벗어나지 않도록 오버플로를 방지한다.

### Requirement 3: 캡션이 달린 스크린샷/그림 슬롯

**User Story:** 발표자로서, 슬라이드에 ITSM 요청 화면·앱스토어 검색·Teams 폴더 트리 같은 캡처를 캡션과 함께 여러 개 넣고 싶다. 그래야 참조 본문처럼 실제 화면 근거를 보여줄 수 있다.

#### Acceptance Criteria

1. WHERE 하나 이상의 Figure_Slot 입력(이미지 참조와 캡션)이 제공되면, THE Two_Column_Renderer SHALL 각 슬롯을 이미지와 캡션이 결합된 카드로 1개 이상 10개 이하로 렌더한다.
2. WHEN 다중 Figure_Slot이 제공되면, THE Slide_Renderer SHALL 모든 슬롯을 슬라이드 경계 1920×1080 안에 배치한다.
3. THE Slide_Renderer SHALL Figure_Slot 이미지를 텍스트 콘텐츠 영역과 해당 이미지 면적 기준 10% 미만으로만 겹치도록 배치한다.
4. WHERE Figure_Slot 이미지 참조가 로컬 파일 경로 또는 `data:image/` URI이면, THE Slide_Renderer SHALL 해당 이미지를 인라인 data URI로 임베드한다.
5. IF Figure_Slot 이미지 참조가 `http://`, `https://`, 프로토콜 상대(`//`), 또는 `file://`로 시작하면, THEN THE Slide_Renderer SHALL 해당 외부 참조를 거부하고 이미지를 임베드하지 않으며 나머지 슬롯과 슬라이드를 정상적으로 렌더한다.
6. IF Figure_Slot 이미지 참조가 비어 있거나 읽을 수 없으면, THEN THE Slide_Renderer SHALL 해당 슬롯의 이미지를 생략하고 나머지 슬롯과 슬라이드를 정상적으로 렌더한다.
7. WHERE Figure_Slot 캡션 텍스트가 제공되면, THE Slide_Renderer SHALL 캡션을 해당 슬롯 이미지에 인접하게 렌더하며 다른 슬롯과 0픽셀로 겹치도록(겹침 없이) 배치한다.
8. WHEN 다중 Figure_Slot이 렌더되면, THE Slide_Renderer SHALL 각 Figure_Slot 카드를 다른 Figure_Slot 카드와 0픽셀로 겹치도록(겹침 없이) 배치한다.
9. IF Figure_Slot 입력 항목 수가 10개를 초과하면, THEN THE Slide_Renderer SHALL 10개까지만 렌더하고 초과 항목은 렌더하지 않는다.

### Requirement 4: 가산적·바이트 보존 보장

**User Story:** 유지보수자로서, 새 밀도 필드를 추가해도 기존 호출자의 출력이 한 바이트도 바뀌지 않기를 원한다. 그래야 기존 회귀 테스트와 산출물이 깨지지 않는다.

#### Acceptance Criteria

1. WHEN 어떤 `render_*` 함수가 본 스펙에서 추가된 밀도 필드를 전혀 받지 않고 호출되면, THE Slide_Renderer SHALL 모든 밀도 필드를 문서화된 무동작(no-op) 기본값으로 명시한 호출과 0바이트 차이의 동일한 바이트 시퀀스 출력을 생성한다.
2. WHEN 어떤 `render_*` 함수가 밀도 필드 없이 호출되면, THE Slide_Renderer SHALL 출력에 포함된 Density_Marker 개수를 0으로 유지한다.
3. THE Slide_Renderer SHALL 본 스펙에서 추가하는 모든 밀도 필드를 무동작(no-op) 값에 해당하는 기본값을 가진 선택적(optional) 키워드 인자로 정의한다.
4. WHEN Layout_Dispatcher가 본 스펙에서 추가된 밀도 필드를 포함한 `data`로 호출되면, THE Layout_Dispatcher SHALL TypeError 없이 해당 필드를 대상 `render_*` 함수로 전달한다.
5. THE Slide_Renderer SHALL 각 밀도 요소에 대해, 그 요소가 렌더될 때만 출력에 존재하고 비활성일 때는 존재하지 않으며 동일 출력 내에서 고유한 Density_Marker를 생성한다.
6. WHEN 동일한 입력으로 어떤 `render_*` 함수가 여러 번 호출되면, THE Slide_Renderer SHALL 매 호출마다 동일한 바이트 시퀀스 출력을 생성한다.
7. IF 지원하지 않는 밀도 필드가 전달되면, THEN THE Slide_Renderer SHALL TypeError 없이 해당 필드를 제외 처리하고 바이트 보존 출력을 유지한다.

### Requirement 5: 측정 가능한 밀도 패리티 합격 기준

**User Story:** 의사결정자로서, "최소 동률" 달성 여부를 주관이 아니라 객관적 점수로 확인하고 싶다. 그래야 합격선을 검증 가능하게 관리할 수 있다.

#### Acceptance Criteria

1. THE Parity_Scorer SHALL Genspark 참조에서 도출한 표지용 Parity_Checklist와 본문용 Parity_Checklist를 각각 고정 항목 집합으로 정의한다.
2. WHEN 렌더된 출력이 Parity_Scorer에 입력되면, THE Parity_Scorer SHALL 해당 카테고리의 Parity_Checklist 항목 중 출력에 존재하는 항목 수를 0 이상 해당 카테고리 항목 총수 이하의 정수 Density_Score로 산출한다.
3. THE Parity_Scorer SHALL 각 Parity_Checklist 카테고리에 대해 Reference_Score를 0 이상 해당 카테고리 항목 총수 이하의 정수 고정 기준값으로 보관한다.
4. WHEN 표지 카테고리의 Density_Score가 산출되면, THE Parity_Scorer SHALL Density_Score가 표지 Reference_Score 이상이면 합격으로 판정한다.
5. WHEN 본문 카테고리의 Density_Score가 산출되면, THE Parity_Scorer SHALL Density_Score가 본문 Reference_Score 이상이면 합격으로 판정한다.
6. THE Parity_Scorer SHALL 각 Parity_Checklist 항목의 충족 여부와 카테고리별 Density_Score·Reference_Score·합격 판정을 보고한다.
7. WHEN Visual_Comparator가 실행되면, THE Visual_Comparator SHALL 우리 출력과 참조 출력을 나란히 배치한 시각 비교 PNG를 Generated_Folder에 생성한다.
8. IF 어떤 카테고리의 Density_Score가 해당 Reference_Score 미만이면, THEN THE Parity_Scorer SHALL 불합격으로 판정하고 미충족 항목을 보고한다.
9. IF Visual_Comparator의 우리 출력 또는 참조 출력 입력이 누락되면, THEN THE Visual_Comparator SHALL 시각 비교 PNG를 생성하지 않고 오류를 반환한다.

### Requirement 6: 헤르메틱 렌더링·한글 폰트·회귀 안전성

**User Story:** 유지보수자로서, 밀도 패리티 작업이 네트워크 없이 검증 가능하고, 한글이 올바르게 렌더되며, 직전에 완료한 수정들을 회귀시키지 않기를 원한다.

#### Acceptance Criteria

1. THE Slide_Renderer SHALL 게이트웨이·Vertex·외부 네트워크 호출 없이 모든 `render_*` 함수와 `render_layout`을 실행하여 0바이트를 초과하는 출력 파일을 생성한다.
2. THE Slide_Renderer SHALL `http://` 또는 `https://` URL을 참조하지 않는 자기완결적 HTML 문서를 생성한다.
3. THE Slide_Renderer SHALL 한글(CJK) 텍스트가 누락 글리프(두부 문자) 0건으로 렌더되도록 CJK 인지 폰트 스택을 적용한다.
4. WHEN 밀도 요소가 포함된 슬라이드가 렌더되면, THE Slide_Renderer SHALL 텍스트와 이미지의 겹침을 결합 바운딩박스 면적 대비 10% 미만으로 유지한다.
5. WHEN 밀도 요소가 포함된 슬라이드가 렌더되면, THE Slide_Renderer SHALL 풀블리드(전면) 이미지 수를 0 이상 1 이하로 유지한다.
6. WHEN 밀도 요소가 포함된 슬라이드가 렌더되면, THE Slide_Renderer SHALL 모든 콘텐츠 요소를 좌표 (0,0)부터 (1920,1080)까지의 슬라이드 경계 안에 100% 포함되도록 배치한다.
7. THE Slide_Renderer SHALL 슬라이드 출력에 SVG 기능 아이콘만 사용하고 유니코드 데코 이모지를 0건으로 유지한다.
8. IF `render_*` 함수 또는 `render_layout` 실행 중 네트워크 호출이 시도되면, THEN THE Slide_Renderer SHALL 해당 시도를 오류로 처리한다.
9. WHEN 본 스펙의 변경이 적용되면, THE Slide_Renderer SHALL 선행 스펙의 회귀 테스트를 100% 통과한다.

### Requirement 7: 스타일 토큰 일관성

**User Story:** 발표자로서, 새 밀도 요소도 등록된 템플릿의 색·폰트를 따르기를 원한다. 그래야 표지와 본문의 디자인이 일관된다.

#### Acceptance Criteria

1. WHEN 밀도 요소가 per-call 디자인 토큰 입력과 함께 렌더되면, THE Slide_Renderer SHALL 해당 요소의 주색·텍스트색·배경색과 제목·본문 폰트 패밀리를 전달된 디자인 토큰에서 가져온다.
2. WHERE per-call 디자인 토큰이 제공되지 않으면(None 또는 빈 dict이면), THE Slide_Renderer SHALL 모듈 기본 디자인 토큰(SLIDE_DESIGN)을 사용한다.
3. THE Slide_Renderer SHALL 밀도 요소의 색과 폰트를 적용된 디자인 토큰의 대응값과 일치시키고, 디자인 토큰을 경유하지 않는 고정값을 포함하지 않는다.
4. IF 특정 색 토큰이 `#RRGGBB` 형식으로 해석되지 않거나 폰트 토큰이 1자 이상 64자 이하의 문자열이 아니면, THEN THE Slide_Renderer SHALL 해당 토큰만 SLIDE_DESIGN 기본값으로 대체하고 나머지 토큰은 전달값을 유지하며 렌더를 중단하지 않는다.
