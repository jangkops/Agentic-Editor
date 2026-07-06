# PPTX 풀블리드-네이티브 오버레이 충돌 Bugfix Design

## Overview

"템플릿 + 이미지 슬라이드" 생성 경로에서 동일 콘텐츠가 두 레이어로 합성되는 결함을 수정한다.
풀블리드 HTML→PNG 배경(slideBackground)이 슬라이드의 제목/본문/다이어그램 카드를 **구워서(baked-in)**
렌더하는데, 파이프라인이 이를 장식용 배경으로 취급하면서 동일 콘텐츠의 네이티브 제목 텍스트박스 +
네이티브 본문 텍스트박스 + (슬라이드 4의 경우) 네이티브 다이어그램 카드를 함께 방출한다. 그 결과
네이티브 텍스트가 구워진 배경 위에 약 100% 겹치고, 제목이 두 번 표시되며, 표지에는 경계 밖(음수-top)
도형이 남는다.

제품 결정(사용자): **네이티브 편집 가능 텍스트/다이어그램이 슬라이드의 콘텐츠**이며, 풀블리드 배경은
**장식 전용**(제목/본문/카드 미포함)이어야 한다. 따라서 수정 방향은:

- 네이티브 제목/본문/다이어그램 콘텐츠가 배치되는 슬라이드에서 배경이 **콘텐츠가 구워진 HTML 렌더가
  되지 않게** 한다(장식/플레인 배경만 허용). 또는 콘텐츠가 구워진 풀블리드 이미지를 그 슬라이드의 표현으로
  채택한 경우 네이티브 오버레이를 억제한다.
- 중복 네이티브 제목 텍스트박스를 제거한다.
- 모든 도형을 슬라이드 경계 안으로 클램프하거나 제거한다.

수정은 **가산적/바이트 보존** 지향으로, 검증된 정상 경로(직접 네이티브, slideBackground 미설정)와 선행
스펙(pptx-quality-vertex-images, pptx-overlay-collision-fix, pptx-image-slot-placement-fix,
pptx-design-density-parity, slide_templates 밀도)을 회귀시키지 않는다. server.py 에디터 버퍼는 STALE
이므로 디스크 패치로만 수정한다.

## Glossary

- **Bug_Condition (C)**: 한 슬라이드가 (A) 콘텐츠가 구워진 풀블리드 배경 PICTURE + 같은 콘텐츠의
  네이티브 텍스트/다이어그램 레이어를 동시에 가져 면적 겹침 ≥ 임계(0.10)이거나, (B) 슬라이드 경계
  (0,0,13.333,7.5) 밖 도형(예: 음수 top)을 가지는 조건.
- **Property (P)**: 수정 후 슬라이드에서 (1) 네이티브 텍스트↔콘텐츠 구워진 풀블리드 배경 겹침 < 10%,
  (2) 제목 1회 표시, (3) 모든 도형이 슬라이드 경계 안.
- **Preservation**: 결함 조건이 아닌 입력(직접 네이티브 경로, 명시 imageFile/장식 배경, 풀블리드 없는
  네이티브 텍스트 슬라이드 등)에서 원본 F 와 수정 F' 의 산출물이 동일해야 함.
- **baked-in(구워진) 콘텐츠**: 슬라이드 제목/본문/카드 텍스트가 PNG 픽셀로 렌더되어 PowerPoint에서
  편집 불가한 상태. 풀블리드 HTML 렌더가 이에 해당.
- **decorative(장식) 배경**: 제목/본문/카드 텍스트를 포함하지 않는 배경(사진/그라데이션/추상 패턴).
- **_safe_set_title**: `ai_engine/server.py:3999` — title placeholder가 없으면
  `ai_engine/server.py:4018`에서 `add_textbox(0.6,0.3,12.1,1.0)`로 네이티브 제목을 폴백 생성.
- **slideBackground**: 슬라이드 전체(0,0,13.333×7.5)를 덮는 풀블리드 이미지 경로. HTML 게이트
  `ai_engine/server.py:5126`가 `5149`에서 설정.
- **_native_over_bg / _eff_bg**: `ai_engine/server.py:5266 / 5278` — 풀블리드 배경을 back-most로
  깔고 그 위에 네이티브 다이어그램을 그리는 경로.
- **within_bounds / clamp_into_bounds**: `ai_engine/layout_geometry.py:345 / 360` — 경계 검사·보정 헬퍼.

## Bug Details

### Bug Condition

결함은 "템플릿 + 이미지 슬라이드" 요청에서 한 슬라이드에 (A) 콘텐츠가 구워진 풀블리드 HTML PNG 배경과
(B) 동일 콘텐츠의 네이티브 제목/본문/다이어그램 레이어가 동시에 존재할 때, 또는 슬라이드 경계 밖에
도형이 배치될 때 나타난다. 파이프라인은 콘텐츠가 구워진 배경을 장식 배경처럼 취급하면서 네이티브
오버레이를 함께 방출하거나(중복), 장식 도형을 음수 top으로 경계 밖에 둔다.

**Formal Specification:**
```
FUNCTION isBugCondition(slide)
  INPUT: slide of type GeneratedSlide
  OUTPUT: boolean

  hasBakedFullbleed := EXISTS pic IN slide.pictures
                         WHERE isFullBleed(pic)             // (0,0)~13.33×7.5 근사 (margin<=0.3, w/h>=92%)
                           AND hasBakedTextualContent(pic)  // 제목/본문/카드가 그림에 구워짐
  hasNativeOverlap := EXISTS shp IN slide.shapes
                         WHERE isNativeTextOrDiagram(shp)
                           AND areaOverlapRatio(shp, fullbleedPic) >= 0.10
  collision := hasBakedFullbleed AND hasNativeOverlap

  offSlide := EXISTS shp IN slide.shapes
                WHERE NOT within_bounds(rect(shp), (0,0,13.333,7.5))

  RETURN collision OR offSlide
END FUNCTION
```

```
FUNCTION expectedBehavior(result)
  INPUT: result of type GeneratedSlide  // F'(slide)
  OUTPUT: boolean

  RETURN maxTextPictureOverlapRatio(result) < 0.10
     AND titleAppearsExactlyOnce(result)
     AND allShapesWithinBounds(result, (0,0,13.333,7.5))
END FUNCTION
```

### Examples

- **슬라이드 1~3 (기대 vs 실제)**: 기대 — 제목 1회 + 편집 가능 네이티브 본문/다이어그램(또는 장식 배경
  위 네이티브 콘텐츠, 겹침 없음). 실제 — 콘텐츠가 구워진 `PICTURE(0,0,13.33×7.5)` + 네이티브 제목
  `TEXT_BOX(0.6,0.3,12.1,1.0)`(제목 중복) + 네이티브 본문 `TEXT_BOX(0.6,1.6,12.1,5.4)`가 구워진
  카드 위에 ~100% 겹침.
- **슬라이드 4 (기대 vs 실제)**: 기대 — 다이어그램 카드 1세트. 실제 — 풀블리드 배경에 구워진 카드 +
  네이티브 `AUTO_SHAPE` 카드 이중 합성.
- **표지 슬라이드 0 (기대 vs 실제)**: 기대 — 모든 장식 도형이 슬라이드 경계 안. 실제 —
  `AUTO_SHAPE top=-1.50`(장식 원), `PICTURE top=-1.39`가 경계 밖.
- **엣지 케이스(정상 유지)**: 직접 네이티브 경로(slideBackground 미설정) 슬라이드 — 겹침/경계밖 없음
  (현재 정상, 수정 후에도 동일해야 함).

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `_tool_generate_pptx` 직접 네이티브 경로(slideBackground 미설정)의 깨끗한 편집 가능 슬라이드 생성.
- 생성된 Vertex 이미지의 손실-0 임베드(pptx-quality-vertex-images 불변식).
- caller가 명시한 imageFile/slideBackground(사진/장식) 슬라이드의 명시 이미지 주 렌더러 유지.
- 풀블리드 배경이 없는 네이티브 텍스트 슬라이드의 기존 레이아웃·밀도·여백.
- 슬라이드당 풀블리드 1회 보장(D1) 및 z-order(텍스트가 이미지 위) 규칙.

**Scope:**
결함 조건(isBugCondition=true)이 아닌 모든 입력은 이 수정으로 완전히 영향받지 않아야 한다. 여기에는
다음이 포함된다.
- 직접 네이티브 경로로 생성된 슬라이드(콘텐츠가 구워진 풀블리드 배경 부재).
- caller가 명시한 장식/사진 배경(콘텐츠가 구워지지 않은 이미지) 위 네이티브 콘텐츠.
- 경계 안에 이미 있는 장식 도형.

**Note:** 기대되는 올바른 동작 자체는 아래 Correctness Properties(Property 1)에 정의된다. 본 절은
**변하면 안 되는 것**에 초점을 둔다.

## Hypothesized Root Cause

디스크(`grep_search`로 확인) 라인 인용. 수정은 디스크 패치로만 수행한다.

1. **네이티브 제목 중복**: `ai_engine/server.py:5113` `_safe_set_title(s, …)` (HTML 게이트보다 먼저
   실행) → placeholder 부재 시 `3999`의 폴백이 `4018`에서 `add_textbox(0.6,0.3,12.1,1.0)`로 제목
   추가. 풀블리드 PNG에 구워진 제목과 중복.

2. **네이티브 본문 오버레이**: `ai_engine/server.py:5170` `body_shape = s.placeholders[1] …`,
   `5171` `if body_shape is None and bullets:` → `5174` `add_textbox(0.6,1.6,12.1,5.4)`. HTML 게이트의
   slideBackground 설정 여부와 무관하게 본문을 채워 구워진 배경 위에 겹침.

3. **콘텐츠가 구워진 풀블리드 배경**: `ai_engine/server.py:5126` 게이트가 섹션을 HTML로 렌더해 `5149`
   `sd["slideBackground"] = _sec_rel`로 설정. 이 HTML 렌더는 제목/본문/카드를 그대로 포함 →
   장식이 아닌 콘텐츠 배경(슬라이드 1~3에 네이티브 카드가 없고 카드가 PNG에 구워짐이 입증).
   `_html_enabled`는 `4592/4621`에서 결정.

4. **네이티브 다이어그램 중첩**: `ai_engine/server.py:5278` `_eff_bg = slide_bg …`, `5279`
   `if native_diag and … _eff_bg …:` 및 `5266/5291` `_native_over_bg` 경로 → 풀블리드 배경 위에
   네이티브 카드를 그려 슬라이드 4 카드 이중 합성.

5. **표지 경계 밖 도형**: `ai_engine/native_diagram_pptx.py:1388` `build_native_cover` 의 `1597`
   `c1 = add_shape(MSO_SHAPE.OVAL, Inches(SW - 2.7), Inches(-1.5), …)` (top=-1.5). 표지 배경 임베드
   경로 `ai_engine/server.py:4719`~`4754`의 `PICTURE top=-1.39`도 경계 밖. 보정 헬퍼
   `ai_engine/layout_geometry.py:345`(within_bounds), `360`(clamp_into_bounds) 재사용.

## Correctness Properties

Property 1: Bug Condition - 풀블리드-네이티브 충돌 및 경계밖 제거

_For any_ 슬라이드 입력에서 결함 조건이 성립할 때(isBugCondition이 true: 콘텐츠가 구워진 풀블리드
배경 위 네이티브 콘텐츠 겹침 ≥10%, 또는 경계 밖 도형 존재), 수정된 파이프라인 F'은 (1) 네이티브
텍스트와 콘텐츠가 구워진 풀블리드 배경의 면적 겹침을 10% 미만으로 만들고, (2) 제목을 정확히 한 번만
표시하며, (3) 모든 도형을 슬라이드 경계(0,0,13.333,7.5) 안에 위치시켜야 한다.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Preservation - 비결함 입력 동작 보존

_For any_ 결함 조건이 성립하지 않는 입력에 대해(isBugCondition이 false: 직접 네이티브 경로, 명시
장식/사진 배경, 풀블리드 없는 네이티브 텍스트 슬라이드, 경계 안 장식 도형), 수정된 파이프라인 F'은
원본 F 와 동일한 산출물을 만들어 기존 레이아웃·밀도·손실-0 임베드·z-order·D1 불변식을 보존해야 한다.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

루트 원인 분석이 맞다는 가정 하에, **가산적/바이트 보존** 원칙으로 다음을 변경한다(디스크 패치, 앵커
유일성 검증 후 적용).

**File**: `ai_engine/server.py`

1. **콘텐츠가 구워진 풀블리드 배경 ↔ 네이티브 콘텐츠 상호배제 (Defect 1.1/1.3)**
   - `5126`/`5149` HTML 풀블리드 배경 경로와 `5170`~`5176` 네이티브 본문, `5278`~ `_eff_bg`
     네이티브 다이어그램 경로 사이에 단일 결정 게이트를 둔다.
   - 결정: 한 슬라이드에 네이티브 본문/다이어그램 콘텐츠를 배치하면, 그 슬라이드 배경은 콘텐츠가
     구워진 HTML 렌더로 설정하지 않는다(장식 배경만 허용). 역으로 콘텐츠가 구워진 풀블리드 PNG를
     그 슬라이드의 표현으로 채택하면 네이티브 본문/다이어그램 방출을 건너뛴다.
   - 제품 결정상 선호: 네이티브 콘텐츠 우선 → HTML 배경은 장식 전용(텍스트/카드 미포함)으로 렌더하거나
     배경 미설정으로 폴백.

2. **네이티브 제목 중복 제거 (Defect 1.2)**
   - 슬라이드 배경이 콘텐츠가 구워진 풀블리드일 때 `5113` `_safe_set_title` 네이티브 제목 폴백
     (`4018` add_textbox)을 억제하여 제목을 1회만 표시. 또는 배경을 장식 전용으로 만들어 구워진 제목을
     제거하고 네이티브 제목만 유지.

3. **본문 텍스트박스 가드 (Defect 1.1)**
   - `5171` `if body_shape is None and bullets:` 블록(`5174` add_textbox)에 "콘텐츠가 구워진 풀블리드
     배경이 이미 본문을 포함" 조건을 추가해 중복 본문 방출을 막는다.

4. **경계 밖 도형 클램프/제거 (Defect 1.4)**
   - PPTX 조립 마지막 단계에 슬라이드별 후처리 패스를 추가: 모든 도형 rect를 `within_bounds`로 검사하고
     경계 밖이면 `clamp_into_bounds`로 보정(또는 순수 장식이면 제거).

**File**: `ai_engine/native_diagram_pptx.py`

5. **표지 장식 원 경계 보정 (Defect 1.4)**
   - `1597` `c1` OVAL의 `Inches(-1.5)` 음수 top을 경계 안으로 클램프하거나, 표지 후처리 패스에서
     일괄 보정되도록 한다(시각 효과 유지하되 경계 밖 미발생).

## Testing Strategy

### Validation Approach

두 단계 접근: 먼저 수정 전(UNFIXED) 코드에서 결함을 드러내는 반례를 수집하고, 그다음 수정이 올바르게
동작하며 기존 동작을 보존하는지 검증한다. 모든 테스트는 hermetic(네트워크 0)이며 게이트웨이/Vertex/
HTML 렌더를 mock 하고, 생성된 in-memory/temp pptx를 `scripts/audit_pptx_textbox_overlap.py`,
`scripts/audit_pptx_zorder_break.py`(경계밖), 그리고 구워진-콘텐츠 검사로 감사한다. heredoc/stdin
없이 파일로 작성하고 `./venv/bin/python -m pytest <file> -p no:cacheprovider -q` 로 실행하며, Chrome이
필요하면 skip 하고 명령이 hang 되지 않게 timeout 을 둔다.

### Exploratory Bug Condition Checking

**Goal**: 수정 전 코드에서 결함을 드러내는 반례를 수집하고 루트 원인을 확인/반박한다. 반박되면
재가설한다.

**Test Plan**: "템플릿 + 이미지 슬라이드" 경로(HTML 풀블리드 배경 + 네이티브 제목/본문/다이어그램을
동시에 생성하는 경로)를 실제 `_tool_generate_pptx`로 구동하고(HTML 섹션 렌더는 제목/본문/카드를
PNG에 굽도록 mock), 생성된 덱을 감사해 다음을 단언한다: 콘텐츠가 구워진 풀블리드 배경 위 네이티브
텍스트 겹침 < 10%, 제목 1회, 경계 밖 도형 0. 수정 전 코드에서는 이 단언이 실패(겹침≈100%, 제목 2회,
음수-top 존재)해야 한다.

**Test Cases**:
1. **본문 오버레이 반례**: 본문 슬라이드에서 네이티브 본문(0.6,1.6,12.1,5.4)이 구워진 배경 위에 겹침
   (will fail on unfixed code)
2. **제목 중복 반례**: 네이티브 제목 텍스트박스(0.6,0.3,12.1,1.0)와 구워진 제목 중복 (will fail)
3. **다이어그램 이중 합성 반례**: 슬라이드 4 네이티브 카드 + 구워진 카드 (will fail)
4. **표지 경계밖 반례**: `AUTO_SHAPE top=-1.50` / `PICTURE top=-1.39` (will fail)

**Expected Counterexamples**:
- 네이티브 텍스트↔구워진 풀블리드 배경 면적 겹침 ≈ 100%, 제목 2회, 경계 밖 음수-top 도형 존재.
- 가능한 원인: server.py 5113/5174 네이티브 방출이 5126/5149 HTML 콘텐츠 배경과 상호배제되지 않음,
  native_diagram_pptx 1597 음수 top.

### Fix Checking

**Goal**: 결함 조건이 성립하는 모든 입력에서 수정된 함수가 기대 동작을 산출함을 검증.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := generate_pptx_fixed(input)
  ASSERT expectedBehavior(result)  // 겹침<10% AND 제목 1회 AND 경계 안
END FOR
```

### Preservation Checking

**Goal**: 결함 조건이 성립하지 않는 모든 입력에서 수정 함수가 원본과 동일한 산출물을 냄을 검증.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT generate_pptx_original(input) = generate_pptx_fixed(input)
END FOR
```

**Testing Approach**: 보존 검증에는 property-based testing 을 권장한다:
- 입력 도메인 전반에 걸쳐 다수 케이스를 자동 생성한다.
- 수동 단위 테스트가 놓치는 엣지를 잡는다.
- 비결함 입력 전체에 대해 동작 불변의 강한 보장을 제공한다.

**Test Plan**: 수정 전 코드에서 비결함 입력(직접 네이티브 경로, 명시 장식 배경, 풀블리드 없는 네이티브
텍스트 슬라이드)의 산출물을 먼저 관측하고, 그 관측된 동작을 property-based 테스트로 포착한다.

**Test Cases**:
1. **직접 네이티브 경로 보존**: slideBackground 미설정 슬라이드가 수정 전 정상임을 관측 → 수정 후에도
   동일(겹침/경계밖 없음) 유지.
2. **손실-0 임베드 보존**: 생성된 Vertex 이미지가 media에 임베드됨을 관측 → 수정 후에도 보존.
3. **명시 장식 배경 보존**: caller가 준 imageFile/slideBackground가 주 렌더러로 유지됨을 관측 → 보존.
4. **밀도/레이아웃 보존**: 풀블리드 없는 네이티브 텍스트 슬라이드의 레이아웃/여백이 동일.

### Unit Tests

- 콘텐츠가 구워진 풀블리드 배경 ↔ 네이티브 본문 상호배제 게이트 단위 검증.
- 네이티브 제목 폴백 억제(배경이 콘텐츠 구워짐일 때 제목 1회) 단위 검증.
- 경계 밖 도형 클램프/제거 후처리 패스 단위 검증(음수 top → 경계 안).

### Property-Based Tests

- 무작위 덱(역할: 표지/콘텐츠/구조형/사진 혼합)을 생성해 결함 조건 입력에서 겹침<10% AND 제목 1회 AND
  경계 안을 단언.
- 무작위 비결함 입력(직접 네이티브/명시 배경)에서 원본 대비 산출물 보존을 단언.
- 다수 시나리오에서 손실-0 임베드 불변식 유지를 단언.

### Integration Tests

- "템플릿 + 이미지 슬라이드" 전체 흐름을 실제 `_tool_generate_pptx`(필요 시 자율 빌더 경로)로 구동하고
  생성된 덱을 3종 감사 스크립트로 검증.
- 표지 + 본문(콘텐츠/구조형) 혼합 덱에서 슬라이드 수·배경 임베드·겹침·경계 동시 검증.
- 컨텍스트 전환(표지→본문→다이어그램)에서 충돌·경계밖 미발생 확인.
