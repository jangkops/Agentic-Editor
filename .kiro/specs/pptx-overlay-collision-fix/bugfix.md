# Bugfix Requirements Document

## Introduction

agentic-editor의 PPTX 자동 생성 기능에서, 에디터로 만든 실제 산출물
`프로젝트-뎁스별-흐름도-1782713951826.pptx`(7슬라이드)를 python-pptx로 전수 점검한 결과
세 가지 오버레이/충돌 결함이 확정되었다. 이 버그 클래스는 이전 스펙
`pptx-quality-vertex-images`(완료, 생성된 Vertex 이미지를 폐기하지 않는 손실-0 결정 규칙
`_select_render_plan` 도입)의 반작용이다. 이미지를 폐기하지 않게 되면서 이번엔 이미지가
**과채택**되어 텍스트와 같은 영역에 겹쳐 배치되고, 동시에 텍스트 오버레이 박스끼리도
좌표 계산 결함으로 겹친다.

세 결함은 모두 `ai_engine/server.py`의 `_tool_generate_pptx` 텍스트/배경/배지 placement
좌표 계산과, 구조형/흐름도 슬라이드의 렌더 경로 선택(`_classify_slide_role` /
`_select_render_plan`)에서 발생한다. 영향: 산출 덱의 슬라이드 대부분이 (a) 편집 텍스트
박스끼리 겹쳐 읽기/편집 불가, (b) 흐름도/구조 콘텐츠가 텍스트 구워진 AI 이미지로 렌더되어
편집 불가 + 본문 박스와 충돌, (c) 번호 배지가 라벨 위에 100% 포개진다.

전수 점검 도구(이미 작성됨, 재현·검증에 재사용): `scripts/audit_pptx_overlap.py`,
`scripts/audit_pptx_images.py`, `scripts/audit_pptx_baked_text.py`,
`scripts/audit_pptx_textbox_overlap.py`.

## Bug Analysis

### Current Behavior (Defect)

세 결함을 각각 결함 A(표지 텍스트 박스 겹침), 결함 B(흐름도/구조 슬라이드의 구워진 텍스트
+ 본문 오버레이 겹침), 결함 C(번호 배지–라벨 겹침)로 구분한다.

**결함 A — 표지(슬라이드1) 텍스트 박스끼리 겹침**

1.1 WHEN 표지 슬라이드의 제목 텍스트 박스 @(1.15, 2.8) 11.18×2.0in 와 부제 텍스트 박스
@(1.17, 3.85) 10.58×1.0in 가 함께 배치될 때 THEN the system 두 박스를 10.05in²(작은 박스
면적의 95%)만큼 수직으로 겹쳐 배치하여 제목과 부제가 서로 포개진다.

1.2 WHEN 표지에서 제목 박스의 (top + height) 좌표가 부제 박스의 top 좌표보다 클 때
THEN the system 두 편집 텍스트 박스의 세로 구간이 겹치는데도 충돌을 감지/회피하지 않고
그대로 출력한다.

**결함 B — 흐름도/구조 슬라이드의 구워진 텍스트 + 본문 오버레이 겹침**

1.3 WHEN 구조형/흐름도 역할 슬라이드(슬라이드3 `Depth 0 루트 구조`, 슬라이드6
`실제 디렉토리 흐름도`)를 렌더할 때 THEN the system 구조/흐름도 콘텐츠를 네이티브 편집
다이어그램이나 텍스트-as-텍스트 HTML이 아니라 텍스트가 구워진 4K(3840×2160) AI 배경
이미지로 렌더한다(슬라이드3 텍스트추정행 7.7%·~6줄, 슬라이드6 9.3%·~4줄; 한글 철자
부정확·편집 불가).

1.4 WHEN 풀블리드 4K AI 배경 이미지가 깔린 콘텐츠 슬라이드에 본문 텍스트 박스
@(0.5, 1.75) 9.0×4.95in 가 배치될 때 THEN the system 본문 박스를 배경 이미지의 거의 전체
영역(이미지에 구워진 텍스트 포함)에 겹쳐 올려 구워진 텍스트와 편집 본문 텍스트가
이중으로 겹친다.

**결함 C — 기술스택(슬라이드7) 번호 배지가 라벨 텍스트와 겹침**

1.5 WHEN 기술스택 슬라이드에서 원형 번호 배지 `1`~`6`을 각 라벨 박스(예: `언어`
@(0.9, 1.75) 5.45×1.14in) 기준으로 배치할 때 THEN the system 배지의 인셋 좌표를 라벨 박스
내부로 계산하여 배지가 라벨 텍스트와 0.46in²(배지 면적의 100%)만큼 완전히 포개진다.

### Expected Behavior (Correct)

**결함 A — 표지 텍스트 박스 분리**

2.1 WHEN 표지 슬라이드의 제목 박스와 부제 박스가 함께 배치될 때 THEN the system SHALL
두 편집 텍스트 박스의 겹침 면적이 더 작은 박스 면적의 정해진 임계(10%) 미만이 되도록
좌표를 산출한다(겹침 사실상 0).

2.2 WHEN 표지에서 제목 박스의 세로 구간(top ~ top+height)이 산출될 때 THEN the system SHALL
부제 박스의 top이 제목 박스의 (top + height) 이상이 되도록 수직 간격을 확보하여 두 박스가
겹치지 않게 한다.

**결함 B — 구조/흐름도는 편집 텍스트로 렌더, 본문은 배경과 분리**

2.3 WHEN 구조형/흐름도 역할 슬라이드를 렌더할 때 THEN the system SHALL 구조/흐름도
콘텐츠를 네이티브 편집 다이어그램 또는 텍스트-as-텍스트 HTML로 렌더하고, 텍스트가 구워진
AI 이미지를 본문 콘텐츠 캐리어로 사용하지 않는다.

2.4 WHEN 풀블리드 이미지를 슬라이드 배경(장식)으로 사용할 때 THEN the system SHALL 동일
영역에 큰 본문 텍스트 박스를 겹쳐 배치하지 않고, 본문은 배경과 분리된 안전 영역(겹침이
임계 미만)에 배치한다.

**결함 C — 번호 배지를 라벨 밖으로 분리**

2.5 WHEN 기술스택 슬라이드에서 번호 배지 `1`~`6`을 라벨 박스 기준으로 배치할 때
THEN the system SHALL 배지를 라벨 박스 바깥(또는 라벨 텍스트와 겹치지 않는 거터)에 배치하여
배지와 라벨 텍스트의 겹침 면적이 배지 면적의 임계(10%) 미만이 되게 한다.

### Unchanged Behavior (Regression Prevention)

3.1 WHEN 슬라이드가 결함 A/B/C 어느 조건에도 해당하지 않을 때 THEN the system SHALL CONTINUE TO
기존 배치/렌더 좌표와 동일한 결과(바이트 보존)를 산출한다.

3.2 WHEN Vertex 이미지가 생성되어 있고 슬라이드 역할이 비구조형(cover/content/visual)일 때
THEN the system SHALL CONTINUE TO 이전 스펙 `pptx-quality-vertex-images`의 손실-0 불변식대로
생성된 Vertex 이미지를 폐기하지 않고 슬라이드 역할에 맞는 위치(슬라이드 비주얼/배경/HTML
이미지 슬롯)에 보존·임베드한다.

3.3 WHEN Vertex 이미지 생성이 비활성이거나 실패(쿼터/서킷브레이커)할 때 THEN the system SHALL
CONTINUE TO 네이티브 다이어그램/카드로 폴백하고 `pptx-quality-vertex-images`의 회귀
스위트를 통과한다.

3.4 WHEN LLM/operation JSON 생성을 호출할 때 THEN the system SHALL CONTINUE TO Bedrock
Gateway 경유로만 호출하고, Vertex AI는 이미지 생성 경로(`ai_engine/vertex_image_module.py`)
에서만 호출한다(gateway.md 예외 조항 준수, 이미지 외 작업에서 Vertex 호출 0).

3.5 WHEN 텍스트 박스끼리 겹치지 않고 본문이 배경과 분리된 정상 슬라이드를 렌더할 때
THEN the system SHALL CONTINUE TO 템플릿 스타일 상속(`styleProfile`/`templatePath`)과 HTML
풀블리드 고밀도 레이아웃 경로를 그대로 적용한다.

3.6 WHEN 진짜 구조형 다이어그램(흐름/트리/아키텍처)이 네이티브 편집 도형으로 렌더 가능할 때
THEN the system SHALL CONTINUE TO 편집 가능한 네이티브 도형으로 렌더한다.

## Bug Condition Derivation

세 결함은 단일한 충돌(collision) 술어로 통합 형식화한다. 입력은 한 슬라이드의 배치 요소
집합과 역할/렌더 상태다.

### Bug Condition Function

```pascal
FUNCTION isBugCondition(S)
  INPUT: S = SlideLayoutState {
           role        : enum,      // cover | section | structural | content | visual
           textBoxes   : list of Rect,   // 편집 텍스트 박스 (제목/부제/라벨/본문)
           badges      : list of Rect,   // 번호 배지 등 장식 텍스트 요소
           bgImage     : optional Image, // 풀블리드 배경 이미지
           bgHasBakedText : bool,        // 배경 이미지에 텍스트가 구워져 있음
           bodyBox     : optional Rect   // 큰 본문 텍스트 박스
         }
  OUTPUT: boolean   // True = 이 슬라이드가 오버레이/충돌 결함을 가짐

  // 결함 A: 편집 텍스트 박스끼리 의미있게 겹침
  defectA := EXISTS (a, b) IN pairs(textBoxes) SUCH THAT
               overlapArea(a, b) >= 0.10 * min(area(a), area(b))

  // 결함 C: 번호 배지가 라벨 텍스트 박스와 의미있게 겹침
  defectC := EXISTS badge IN badges, label IN textBoxes SUCH THAT
               overlapArea(badge, label) >= 0.10 * area(badge)

  // 결함 B: 구조형/흐름도 콘텐츠가 구워진 텍스트 이미지로 렌더되거나,
  //         풀블리드 배경 위에 큰 본문 박스가 겹침
  defectB := (role IN { structural } AND bgImage != NULL AND bgHasBakedText)
             OR (bgImage != NULL AND bodyBox != NULL
                 AND overlapArea(bodyBox, bgImage.rect) >= 0.10 * area(bodyBox))

  RETURN defectA OR defectB OR defectC
END FUNCTION
```

여기서 `overlapArea(p, q)`는 두 사각형의 교집합 면적이며, `area(r)`는 사각형 면적이다.
임계 10%는 "의미있는 겹침"의 경계로, 기대 동작 2.1/2.4/2.5와 일치한다.

### Property Specification (Fix Checking)

```pascal
// Property: Fix Checking — 충돌 제거
FOR ALL S WHERE isBugCondition(S) DO
  S' ← layoutSlide'(S)              // 수정된 배치/렌더 경로
  // 결함 A/C: 편집 요소끼리 임계 미만 겹침
  ASSERT FOR ALL (a,b) IN pairs(S'.textBoxes ∪ S'.badges) :
           overlapArea(a,b) < 0.10 * min(area(a), area(b))
  // 결함 B: 구조/흐름도는 구워진 텍스트 이미지 캐리어가 아님
  ASSERT NOT (S'.role = structural AND S'.bgImage != NULL AND S'.bgHasBakedText)
  // 결함 B: 본문 박스가 배경 이미지와 임계 미만 겹침(배경은 장식)
  ASSERT (S'.bodyBox = NULL OR S'.bgImage = NULL
          OR overlapArea(S'.bodyBox, S'.bgImage.rect) < 0.10 * area(S'.bodyBox))
END FOR
```

**Key Definitions:**
- **F** (`layoutSlide`): 원본(미수정) 배치/렌더 함수 — 현재 코드.
- **F'** (`layoutSlide'`): 수정된 배치/렌더 함수.

### Preservation Goal (Preservation Checking)

```pascal
// Property: Preservation Checking — 비버그 입력 동작 보존
FOR ALL S WHERE NOT isBugCondition(S) DO
  ASSERT F(S) = F'(S)
END FOR
```

비버그 입력(텍스트 박스가 겹치지 않고, 구조형이 구워진 이미지가 아니며, 본문이 배경과
분리된 슬라이드, 그리고 Vertex 손실-0/게이트웨이 제약 경로)에 대해 수정 코드는 원본과
동일하게 동작해야 한다. 특히 3.2(Vertex 손실-0 불변식)와 3.4(게이트웨이 제약)는 본 수정의
영향을 받지 않아야 한다.
