# PPTX 이미지 슬롯 배정 결함 수정 Bugfix Requirements

## Introduction

agentic-editor의 PPTX 자동 생성에서, 에디터로 만든 실제 산출물
`cgjang-프로젝트-구조-분석-1782775987352.pptx`(9슬라이드)를 python-pptx로 전수 점검한 결과
**이미지 합성 경로의 슬롯 배정 결함** 3종(D1/D2/D3)이 확정되었다. 결함은 모두
`ai_engine/server.py`의 `_tool_generate_pptx` 슬라이드 루프의 멀티 이미지+텍스트 합성/슬롯
배정 경로(디스크 기준 ~5215~5460행 부근의 `add_picture`/배경/장식 이미지 배치)에서 발생하며,
audit 도구(`scripts/audit_pptx_zorder_break.py`, `scripts/audit_pptx_media_classify.py`)로
재현된다.

이전 두 스펙(`pptx-quality-vertex-images`=손실-0, `pptx-overlay-collision-fix`=텍스트/네이티브
겹침)은 각자 seam을 헤르메틱하게 고쳤지만, 실제 런타임의 멀티 이미지 합성/슬롯 배정 경로는
커버하지 못해 아래 버그가 남았다. 본 수정의 핵심 개선은 통합 검증을 **실제
`_tool_generate_pptx` 합성 경로**를(게이트웨이/Vertex/HTML 렌더 목으로) 구동해 생성된 덱을
audit 도구 기준으로 검사하는 것이다 — 즉 사용자가 본 결함(중복 배경/오버사이즈 슬롯/슬라이드
밖)을 테스트가 실제로 잡아야 한다.

영향: 사용자가 본 산출물의 슬라이드 8·9·1에서 풀블리드 배경이 중복 임베드되고, 4K 배경급
이미지가 0.25인치 아이콘 슬롯에 찌그러져 깨지며, 일러스트가 슬라이드 위 경계 밖으로 잘려나가
시각 품질이 크게 저하된다.

## Bug Analysis

표기 규약: 슬라이드 좌표는 인치, 슬라이드 영역 `SLIDE = (0, 0, 13.333, 7.5)`. PICTURE는
`Rect = (left, top, width, height)` 인치 사각형으로 표현한다. 풀블리드 판정은 audit 도구
기준과 동일: `isFullbleed(r) = r.left<=0.3 AND r.top<=0.3 AND r.width>=SW*0.92 AND r.height>=SH*0.92`
(`SW=13.333`, `SH=7.5`). 이미지 픽셀 크기는 임베드된 blob의 디코드 해상도(예: 3840×2160)를
가리킨다.

### Current Behavior (Defect)

미수정 코드가 버그 조건을 만족하는 슬라이드에서 실제로 보이는 잘못된 동작.

1.1 WHEN 한 슬라이드에 동일/유사 풀블리드 배경 이미지가 2장 이상 임베드될 때(슬라이드 8·9: z=0, z=1에 3840×2160 풀블리드 배경 2장이 둘 다 `(0,0,13.333,7.5)`로 겹쳐 임베드됨) THEN the system 풀블리드 배경 PICTURE를 1장으로 제한하지 않고 중복 임베드한다(파일 비대 + z-order 혼란)
1.2 WHEN 배경급 대형 이미지(예: 3840×2160)가 소형 장식 슬롯(아이콘/로고/액센트, 예: `(0.5, 0.6)`의 0.25×0.25in 박스)에 배정될 때(슬라이드 8·9, z=3) THEN the system 대형 이미지를 0.25인치 박스로 강제 축소 임베드해 이미지가 찌그러지고 깨진다(정상 슬라이드의 동일 슬롯에는 75×100 단색 액센트 스퀘어가 들어감)
1.3 WHEN 부분 이미지의 배치 rect가 슬라이드 경계를 벗어날 때(슬라이드 1, z=3: 900×720 일러스트가 `@(8.11, -1.39, 5.21, 4.17)` → top=-1.39in) THEN the system 음수 top/left 또는 경계 초과 좌표를 클램프/리사이즈 없이 그대로 배치해 이미지가 슬라이드 밖으로 빠져나가 잘린다

### Expected Behavior (Correct)

각 결함 조건에서 수정 후 시스템이 보여야 하는 올바른 동작(1.x와 1:1 대응).

2.1 WHEN 한 슬라이드에 풀블리드 배경 후보 이미지가 여럿 있을 때 THEN the system SHALL 풀블리드 배경 PICTURE를 최대 1장만 임베드한다(중복 풀블리드 배경 금지; 나머지 후보는 비주얼/콘텐츠 슬롯으로 재배정하거나 생략하되 손실-0 불변식을 위반하지 않음)
2.2 WHEN 배경급 대형 이미지와 소형 장식 슬롯(아이콘/로고/액센트)이 함께 있을 때 THEN the system SHALL 소형 장식 슬롯에 슬롯 크기에 맞는 이미지(단색 액센트/아이콘급)만 배정하고, 대형 콘텐츠/배경 이미지는 풀블리드 또는 콘텐츠 영역 슬롯으로 배정한다(슬롯-이미지 크기 정합)
2.3 WHEN 어떤 PICTURE의 배치 rect가 슬라이드 경계를 벗어나는 좌표를 가질 때 THEN the system SHALL 모든 PICTURE를 슬라이드 경계 `(0,0,13.333,7.5)` 안에 위치시킨다(음수 top/left 및 경계 초과 금지; 필요 시 좌표 클램프 또는 리사이즈)

### Unchanged Behavior (Regression Prevention)

버그 조건이 아닌 입력에서 반드시 보존되어야 하는 기존 동작.

3.1 WHEN 슬라이드가 D1/D2/D3 어느 조건에도 해당하지 않을 때(풀블리드 배경 ≤1장, 슬롯-이미지 크기 정합, 모든 PICTURE가 경계 안) THEN the system SHALL CONTINUE TO 기존 배치/임베드 좌표를 바이트 동일하게 유지한다(비결함 슬라이드 출력 불변)
3.2 WHEN Vertex 이미지가 생성되었을 때 THEN the system SHALL CONTINUE TO 이전 스펙 `pptx-quality-vertex-images`의 손실-0 불변식을 보존한다(생성된 Vertex 이미지를 폐기하지 않고 배경/비주얼/장식 슬롯에 보존·임베드)
3.3 WHEN 텍스트-텍스트/번호 배지 배치를 검사할 때 THEN the system SHALL CONTINUE TO 이전 스펙 `pptx-overlay-collision-fix`의 겹침 < 10% 불변식을 회귀 없이 유지한다
3.4 WHEN LLM/operation JSON 생성 또는 이미지 생성이 호출될 때 THEN the system SHALL CONTINUE TO 게이트웨이 제약을 유지한다(LLM/operation은 Bedrock Gateway 경유, Vertex는 이미지 생성 경로 `ai_engine/vertex_image_module.py`에서만 호출)
3.5 WHEN Vertex 이미지 생성이 비활성/실패(쿼터/서킷브레이커)일 때 THEN the system SHALL CONTINUE TO 네이티브 다이어그램/카드 폴백으로 진행하고 기존 회귀 스위트를 통과한다

## Bug Condition Formalization

audit 도구와 동일한 기준으로 버그 조건을 형식화한다. `S`는 한 슬라이드의 PICTURE 집합 상태.

### 보조 술어

```pascal
FUNCTION isFullbleed(r)
  INPUT: r of type Rect   // 인치
  OUTPUT: boolean
  RETURN r.left <= 0.3 AND r.top <= 0.3
         AND r.width >= 13.333 * 0.92 AND r.height >= 7.5 * 0.92
END FUNCTION

FUNCTION isLargeImage(p)
  INPUT: p of type Picture  // 임베드 blob 디코드 해상도 보유
  OUTPUT: boolean
  // 배경/콘텐츠급 대형 이미지(예: 3840x2160). 임계는 설계 단계에서 확정.
  RETURN p.pixelWidth >= LARGE_PX OR p.pixelHeight >= LARGE_PX   // 예: LARGE_PX = 1024
END FUNCTION

FUNCTION isSmallSlot(r)
  INPUT: r of type Rect   // 인치
  OUTPUT: boolean
  // 아이콘/로고/액센트 등 소형 장식 슬롯(예: 0.25x0.25in)
  RETURN r.width <= SMALL_SLOT_IN AND r.height <= SMALL_SLOT_IN   // 예: SMALL_SLOT_IN = 0.5
END FUNCTION

FUNCTION withinBounds(r, slide)
  INPUT: r, slide of type Rect
  OUTPUT: boolean
  RETURN r.left >= -EPS AND r.top >= -EPS
         AND r.left + r.width  <= slide.width  + EPS
         AND r.top  + r.height <= slide.height + EPS   // 예: EPS = 0.05
END FUNCTION
```

### 결함별 버그 조건

```pascal
FUNCTION isBugCondition(S)
  INPUT: S = SlideMediaState {
           pictures : list of Picture {   // z-order 순서
             rect       : Rect,           // 배치 사각형(인치)
             pixelWidth : int,            // 임베드 이미지 디코드 해상도
             pixelHeight: int
           }
         }
  OUTPUT: boolean

  // D1: 풀블리드 배경 중복 임베드 (count > 1)
  defectD1 := count({ p IN S.pictures : isFullbleed(p.rect) }) > 1

  // D2: 대형 이미지가 소형 장식 슬롯에 배정
  defectD2 := EXISTS p IN S.pictures SUCH THAT
                isLargeImage(p) AND isSmallSlot(p.rect)

  // D3: PICTURE rect 가 슬라이드 경계 밖
  defectD3 := EXISTS p IN S.pictures SUCH THAT
                NOT withinBounds(p.rect, SLIDE)   // SLIDE = (0,0,13.333,7.5)

  RETURN defectD1 OR defectD2 OR defectD3
END FUNCTION
```

### Property (Fix Checking) — 버그 입력에 대한 기대 동작

`F`는 원본(미수정) 합성 경로, `F'`는 수정된 합성 경로. `F'(S)`는 생성된 슬라이드의 PICTURE
집합 상태.

```pascal
// Property P1: 풀블리드 배경은 최대 1장
FOR ALL S WHERE isBugCondition(S) DO
  S' ← F'(S)
  ASSERT count({ p IN S'.pictures : isFullbleed(p.rect) }) <= 1
END FOR

// Property P2: 소형 장식 슬롯에 대형 이미지 없음
FOR ALL S WHERE isBugCondition(S) DO
  S' ← F'(S)
  ASSERT NOT EXISTS p IN S'.pictures SUCH THAT
           isLargeImage(p) AND isSmallSlot(p.rect)
END FOR

// Property P3: 모든 PICTURE 가 슬라이드 경계 안
FOR ALL S WHERE isBugCondition(S) DO
  S' ← F'(S)
  ASSERT FOR ALL p IN S'.pictures: withinBounds(p.rect, SLIDE)
END FOR
```

### Preservation (Preservation Checking) — 비버그 입력 보존

```pascal
// 비버그 입력은 원본과 동일한 출력(좌표/바이트 보존)
FOR ALL S WHERE NOT isBugCondition(S) DO
  ASSERT F(S) = F'(S)
END FOR

// 손실-0 불변식: 생성된 Vertex 이미지는 폐기되지 않음(이전 스펙)
FOR ALL media_state WHERE has_vertex_image DO
  ASSERT vertex_slot(F'(media_state)) != "none"
END FOR
```

**핵심 정의**
- **C(X) = isBugCondition(S)**: D1(풀블리드 중복 count>1), D2(대형 이미지가 소형 슬롯에 배정),
  D3(PICTURE rect가 경계 밖) 중 하나라도 참.
- **P(result)**: P1∧P2∧P3 — 풀블리드 ≤1, 소형 슬롯에 대형 이미지 없음, 모든 PICTURE 경계 안.
- **F**: 원본(미수정) `_tool_generate_pptx` 합성 경로. **F'**: 수정된 합성 경로.
- **임계 상수(`LARGE_PX`, `SMALL_SLOT_IN`, `EPS`)**: audit 도구의 실제 관측값에 맞춰 design.md
  에서 확정한다.
