# Bugfix Requirements Document

## Introduction

에디터 채팅으로 "템플릿을 사용하고, 현재 열린 프로젝트 뎁스 흐름도를 파악하여 pptx 이미지 슬라이드를
제작해줘" 라는 요청을 수행해 생성된 실제 산출물(`cgjang-프로젝트-구조-분석-1782794770613.pptx`,
16:9 13.33×7.5in)을 도형 단위로 감사한 결과, **"템플릿 + 이미지 슬라이드" 경로**에서 동일한 콘텐츠가
두 겹으로 합성되는 결함이 확인되었다.

본문 슬라이드(1~4)마다 다음 세 레이어가 같은 콘텐츠를 중복 표현하며 충돌한다.

1. `PICTURE (0,0,13.33×7.5)` — 슬라이드 제목 + 본문 + 다이어그램 카드가 **구워진(baked-in)**
   풀블리드 HTML 렌더 배경.
2. `TEXT_BOX (0.6,0.3,12.1,1.0)` — 네이티브 제목 텍스트박스(레이어 1에 구워진 제목과 중복).
3. `TEXT_BOX (0.6,1.6,12.1,5.4)` — 네이티브 본문 텍스트박스("Depth 0…/cgjang…")가 레이어 1에
   구워진 다이어그램 카드 **위에** 그려짐 → 면적 약 100% 겹침.

표지 슬라이드(0)는 추가로 슬라이드 경계 밖(음수 top) 도형을 가진다: `PICTURE top=-1.39`,
`AUTO_SHAPE top=-1.50`. 슬라이드 4는 풀블리드 배경 위에 네이티브 `AUTO_SHAPE` 다이어그램 카드를
추가로 겹쳐 그린다.

결과적으로 동일 콘텐츠가 (a) 콘텐츠가 구워진 풀블리드 HTML/PNG 배경 + (b) 같은 콘텐츠의 네이티브
텍스트/다이어그램 오버레이로 이중 합성 → 중복·겹침 콘텐츠가 발생하고, 표지에는 경계 밖 음수-top
도형이 남는다.

참고(범위 한정): `_tool_generate_pptx`의 **직접 네이티브 경로**(slideBackground 미설정)는 hermetic
재현에서 **정상(clean)** 이다. 따라서 본 결함은 **같은 슬라이드에 slideBackground(콘텐츠가 구워진
풀블리드 HTML PNG)와 네이티브 제목/본문/다이어그램을 동시에 설정하는 경로**에 한정해 존재한다.
탐색 테스트는 반드시 두 레이어를 모두 생성하는 경로를 구동하고 생성된 덱을 감사해야 한다.

## Bug Analysis

### Current Behavior (Defect)

현재 "템플릿 + 이미지 슬라이드" 요청을 처리할 때 다음이 발생한다.

1.1 WHEN 한 슬라이드에 콘텐츠가 구워진 풀블리드 HTML 배경(slideBackground)이 깔리고 동시에 같은
콘텐츠의 네이티브 본문 텍스트박스가 그려질 때 THEN the system 은 네이티브 본문을 배경 위에 겹쳐
그려 면적 약 100% 중복 표시한다(텍스트↔이미지 겹침).

1.2 WHEN 콘텐츠가 구워진 풀블리드 HTML 배경이 슬라이드 제목을 이미 포함할 때 THEN the system 은
네이티브 제목 텍스트박스(0.6,0.3,12.1,1.0)를 추가로 그려 제목을 두 번 표시한다.

1.3 WHEN 슬라이드 4처럼 풀블리드 배경 위에 네이티브 다이어그램 카드를 그릴 때 THEN the system 은
배경에 구워진 카드와 네이티브 카드를 이중으로 겹쳐 표시한다.

1.4 WHEN 표지 슬라이드를 구성할 때 THEN the system 은 슬라이드 경계(0,0,13.333,7.5) 밖에 위치한
도형(예: `AUTO_SHAPE top=-1.50`, `PICTURE top=-1.39`)을 남긴다.

### Expected Behavior (Correct)

1.1~1.4 의 각 조건에서 기대되는 올바른 동작은 다음과 같다.

2.1 WHEN 한 슬라이드에 네이티브 본문/다이어그램 콘텐츠가 배치될 때 THEN the system SHALL 그 슬라이드의
배경이 콘텐츠가 구워진 HTML 렌더가 되지 않도록 하여(장식 전용 배경만 허용), 네이티브 텍스트가 콘텐츠가
구워진 풀블리드 배경과 면적 10% 미만으로만 겹치게 한다.

2.2 WHEN 슬라이드 제목이 표시될 때 THEN the system SHALL 제목을 단 한 번만 표시한다(구워진 배경 제목과
네이티브 제목 텍스트박스의 중복 제거).

2.3 WHEN 다이어그램 카드가 표시될 때 THEN the system SHALL 콘텐츠가 구워진 배경 카드와 네이티브 카드
중 하나만 표현하여 카드 콘텐츠의 이중 합성을 제거한다.

2.4 WHEN 표지 및 본문 슬라이드의 모든 도형이 배치될 때 THEN the system SHALL 모든 도형을 슬라이드
경계(0,0,13.333,7.5) 안으로 클램프하거나 제거하여 음수-top 등 경계 밖 도형이 없게 한다.

### Unchanged Behavior (Regression Prevention)

다음 동작은 이 수정으로 영향을 받지 않아야 한다.

3.1 WHEN `_tool_generate_pptx`의 직접 네이티브 경로(slideBackground 미설정)로 슬라이드를 생성할 때
THEN the system SHALL CONTINUE TO 기존처럼 깨끗한 편집 가능 네이티브 슬라이드를 생성한다(현재 정상).

3.2 WHEN Vertex 이미지가 생성되어 슬라이드에 임베드될 때 THEN the system SHALL CONTINUE TO 생성된
이미지를 폐기하지 않고 임베드한다(pptx-quality-vertex-images 손실-0 불변식 보존).

3.3 WHEN caller가 명시적으로 imageFile/slideBackground 경로를 지정한(사진/장식) 슬라이드를 만들 때
THEN the system SHALL CONTINUE TO 그 명시 이미지를 주 렌더러로 유지한다(우선순위 보존).

3.4 WHEN 슬라이드의 본문이 다이어그램형이 아니어서 풀블리드 배경 없이 네이티브 텍스트만 표시할 때
THEN the system SHALL CONTINUE TO 기존 레이아웃·밀도·여백을 그대로 유지한다(pptx-overlay-collision-fix,
pptx-image-slot-placement-fix, pptx-design-density-parity, slide_templates 밀도 보존).

3.5 WHEN 슬라이드당 풀블리드 배경 1회 보장(D1) 및 z-order(텍스트가 이미지 위) 규칙이 적용될 때
THEN the system SHALL CONTINUE TO 기존 불변식을 유지한다.

## Bug Condition (형식화)

```pascal
FUNCTION isBugCondition(slide)
  INPUT: slide of type GeneratedSlide  // python-pptx Slide 의 도형 집합
  OUTPUT: boolean

  // (A) 콘텐츠가 구워진 풀블리드 배경 위에 같은 콘텐츠의 네이티브 레이어가 겹침
  hasBakedFullbleed := EXISTS pic IN slide.pictures
                         WHERE isFullBleed(pic)            // (0,0)~13.33×7.5 근사
                           AND hasBakedTextualContent(pic) // 제목/본문/카드가 그림에 구워짐
  hasNativeContent := EXISTS shp IN slide.shapes
                         WHERE isNativeTextOrDiagram(shp)  // TEXT_BOX/AUTO_SHAPE 텍스트
                           AND areaOverlapRatio(shp, fullbleedPic) >= OVERLAP_THRESHOLD  // 0.10
  collision := hasBakedFullbleed AND hasNativeContent

  // (B) 슬라이드 경계 밖 도형(음수 top 등)
  offSlide := EXISTS shp IN slide.shapes
                WHERE NOT within_bounds(rect(shp), (0,0,13.333,7.5))

  RETURN collision OR offSlide
END FUNCTION
```

```pascal
// Property: Fix Checking — 충돌/경계밖 제거 + 제목 1회
FOR ALL slide WHERE isBugCondition(slide) DO
  result ← F'(slide)   // 수정된 파이프라인이 만든 슬라이드
  ASSERT maxTextPictureOverlapRatio(result) < 0.10   // 네이티브 텍스트↔구워진 배경 겹침 10% 미만
     AND titleAppearsExactlyOnce(result)             // 제목 중복 없음
     AND allShapesWithinBounds(result, (0,0,13.333,7.5))  // 경계 밖 도형 없음
END FOR
```

```pascal
// Property: Preservation Checking — 비결함 입력은 원본과 동일
FOR ALL slide WHERE NOT isBugCondition(slide) DO
  ASSERT F(slide) = F'(slide)
END FOR
```

- **F**: 수정 전(원본) 파이프라인
- **F'**: 수정 후 파이프라인
- **OVERLAP_THRESHOLD**: 0.10 (감사 스크립트 `audit_pptx_zorder_break.py`의 텍스트↔이미지 임계 8%와 정합)

## Hypothesized Root Cause (디스크 라인 인용)

> server.py 에디터 버퍼는 STALE 이므로 아래 라인은 `grep_search`로 디스크에서 확인한 값이며,
> 수정은 디스크 패치로만 수행한다.

같은 콘텐츠가 두 레이어로 합성되는 원인은 다음 두 의도(장식 배경 vs 콘텐츠 오버레이)가 한 슬라이드에서
동시에 적용되기 때문이다.

1. **네이티브 제목 중복** — `ai_engine/server.py:5113` 에서 `_safe_set_title(s, ...)` 가 HTML
   배경 게이트보다 먼저 실행된다. title placeholder 부재 시 `ai_engine/server.py:3999`의
   `_safe_set_title` 폴백이 `ai_engine/server.py:4018` 에서 `add_textbox(0.6,0.3,12.1,1.0)`로
   네이티브 제목을 추가 → 풀블리드 PNG에 구워진 제목과 중복(결함 1.2).

2. **네이티브 본문 오버레이** — `ai_engine/server.py:5170`에서 `body_shape = s.placeholders[1] …`,
   `5171`의 `if body_shape is None and bullets:` 조건에서 `5174` 의
   `add_textbox(0.6,1.6,12.1,5.4)` 로 본문 텍스트박스를 만들어 불릿을 채운다. 이 블록은 HTML
   게이트가 `slideBackground`를 설정했는지와 무관하게 본문을 그린다 → 콘텐츠가 구워진 배경 위에
   본문 중복(결함 1.1). 감사된 본문 박스 좌표(0.6,1.6,12.1,5.4)와 정확히 일치.

3. **HTML 풀블리드 배경이 콘텐츠 렌더** — `ai_engine/server.py:5126`의 게이트
   `if _html_enabled and not sd.get("slideBackground") …:` 에서 섹션을 HTML로 렌더해 `5149`의
   `sd["slideBackground"] = _sec_rel` 로 풀블리드 배경에 설정한다. 이 HTML 렌더는 같은 슬라이드의
   제목/본문/카드를 그대로 렌더하므로(슬라이드 1~3에 네이티브 카드가 없고 카드가 PNG에 구워져 있음이
   이를 입증) **콘텐츠가 구워진 배경**이 된다. `_html_enabled`는 `4592/4621`에서 결정.

4. **네이티브 다이어그램이 배경 위에 중첩** — `ai_engine/server.py:5278`의 `_eff_bg = slide_bg …`,
   `5279`의 `if native_diag and … _eff_bg …:` 및 `5266/5291`의 `_native_over_bg` 경로가 풀블리드
   배경을 back-most로 깔고 그 위에 네이티브 다이어그램 카드를 그린다 → 슬라이드 4의 카드 이중 합성
   (결함 1.3).

5. **표지 경계 밖 도형** — `ai_engine/native_diagram_pptx.py:1388`의 `build_native_cover` 가
   `1597`에서 장식용 대형 원 `c1 = add_shape(MSO_SHAPE.OVAL, Inches(SW - 2.7), Inches(-1.5), …)`
   를 `top=-1.5`(음수)로 배치 → 감사의 `AUTO_SHAPE top=-1.50`와 일치(결함 1.4). 표지 배경 임베드
   경로(`ai_engine/server.py:4719`~`4754`)에서 비롯되는 `PICTURE top=-1.39`도 경계 밖이며 클램프
   대상이다. 경계 보정 헬퍼는 `ai_engine/layout_geometry.py:345`(`within_bounds`),
   `360`(`clamp_into_bounds`)을 재사용한다.

요약: 풀블리드 HTML PNG는 **콘텐츠가 구워진 렌더**인데 파이프라인이 이를 장식용 slideBackground로
취급하면서 동일 콘텐츠의 네이티브 제목/본문/다이어그램을 함께 방출한다. 제품 결정에 따라 네이티브
편집 가능 콘텐츠가 슬라이드의 콘텐츠이며, 풀블리드 배경은 장식 전용(제목/본문/카드 미포함)이어야 한다.
