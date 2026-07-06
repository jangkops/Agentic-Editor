# Bugfix Requirements Document

## Introduction

에디터의 `generate_pptx` 경로가 두 가지 결함을 보인다. 첫째, 같은 작업에서 함께 생성된
Vertex 고품질 이미지가 최종 PPTX 슬라이드에 임베드되지 않고 폐기되거나 누락된다(생성됐으나
미사용). 둘째, 산출된 PPTX의 시각적 퀄리티·밀도가 낮아, 업로드한 참고 자료(목암생명과학연구소
신규 입사자 IT 온보딩 매뉴얼 — STEP별 카드 레이아웃, 2단 구성, 스크린샷·아이콘·배지 등 고밀도
디자인) 수준에 미치지 못한다.

가장 최근 산출물 `프로젝트-폴더-구조-분석-1782368486281.pptx`에서 이 두 결함이 함께
관찰된다. 기대 동작은 "고퀄리티 PPT + 생성된 Vertex 이미지의 올바른 활용"이나, 실제로는
"저퀄리티 + 이미지 미활용" 상태다.

근본 메커니즘(조사 결과): `_tool_generate_pptx`의 슬라이드 임베드 루프는 슬라이드에
`nativeDiagram`이 없을 때만(`not native_diag and not img_file and not slide_bg`) 사전
생성된 Vertex 이미지(`_vertex_pre[i]`)를 임베드한다. 그러나 그 직전 단계의 LLM 구조화와
결정론적 카드 폴백이 본문 슬라이드 거의 전부에 `nativeDiagram`을 부여하기 때문에, 생성된
Vertex 이미지가 임베드 가드에 걸려 사용되지 않는다. 이로 인해 이미지는 누락되고 슬라이드는
단조로운 텍스트/박스 위주로 남아 저품질이 된다.

## Bug Analysis

### Current Behavior (Defect)

현재 `generate_pptx`가 Vertex 이미지를 생성하면서도 그 결과를 최종 덱에 반영하지 못하고,
참고 자료 수준의 디자인 밀도에 못 미치는 산출물을 만든다.

1.1 WHEN 어떤 슬라이드에 대해 Vertex 이미지가 생성되었고(`_vertex_pre[i]` 존재) 동시에 그
슬라이드에 `nativeDiagram`이 부여되어 있으면 THEN the system 임베드 가드(`not native_diag`)에
의해 생성된 Vertex 이미지를 폐기한다(생성됐으나 미사용).

1.2 WHEN LLM 구조화 또는 결정론적 카드 폴백이 본문 슬라이드 대부분에 `nativeDiagram`을
부여하면 THEN the system 해당 슬라이드들의 Vertex 이미지를 임베드하지 않아 최종 PPTX에서
이미지가 누락된다.

1.3 WHEN 시각형 슬라이드(표지/주요 섹션)에 `imagePrompt`가 주어졌더라도 THEN the system
이미지가 임베드되지 않거나 텍스트·박스 위주의 단조로운 슬라이드로 렌더한다.

1.4 WHEN 최종 PPTX가 생성되면 THEN the system 참고 매뉴얼 수준의 밀도·디테일(STEP 카드
레이아웃, 2단 구성, 스크린샷/아이콘/배지)에 못 미치는 저품질·저밀도 슬라이드를 산출한다.

### Expected Behavior (Correct)

1.x의 각 조건에 대해, 시스템은 생성된 Vertex 이미지를 올바르게 활용하고 참고 자료 수준의
고품질 슬라이드를 산출해야 한다.

2.1 WHEN 어떤 슬라이드에 대해 Vertex 이미지가 생성되었으면 THEN the system SHALL 그 이미지를
최종 PPTX의 해당 슬라이드에 임베드하여 "생성됐으나 미사용" 이미지가 남지 않도록 한다.

2.2 WHEN 한 슬라이드가 이미지(사진/일러스트)와 구조 다이어그램 양쪽 후보로 처리될 수 있으면
THEN the system SHALL 생성된 Vertex 이미지를 폐기하지 않고 활용하는 결정 규칙을 적용한다
(이미지와 구조 표현이 모두 손실되지 않도록 한다).

2.3 WHEN 시각형 슬라이드(표지/주요 섹션)에 `imagePrompt`가 주어지면 THEN the system SHALL
해당 슬라이드에 고품질 이미지를 임베드한다.

2.4 WHEN 최종 PPTX가 생성되면 THEN the system SHALL 참고 매뉴얼 수준의 디테일·밀도(STEP/카드
레이아웃, 2단 구성, 시각요소 활용)를 갖춘 고품질 슬라이드를 산출한다.

### Unchanged Behavior (Regression Prevention)

이미지 활용·품질 개선이 기존의 편집 가능 다이어그램, HTML 풀블리드 경로, 폴백 동작, 게이트웨이
제약을 깨뜨려서는 안 된다.

3.1 WHEN 슬라이드가 진짜 구조형 다이어그램(흐름/트리/아키텍처)이면 THEN the system SHALL
CONTINUE TO 편집 가능한 네이티브 도형으로 렌더한다.

3.2 WHEN HTML 풀블리드 슬라이드 경로(`_html_enabled`)가 활성이면 THEN the system SHALL
CONTINUE TO 해당 경로로 슬라이드를 렌더한다.

3.3 WHEN Vertex 이미지 생성이 비활성이거나 실패(쿼터·서킷브레이커)하면 THEN the system SHALL
CONTINUE TO 네이티브 다이어그램/카드로 폴백한다(media-output-quality 회귀 방지).

3.4 WHEN LLM/operation JSON 생성이 필요하면 THEN the system SHALL CONTINUE TO Bedrock
Gateway 경유로만 호출하고 Vertex는 이미지 생성에 한해서만 사용한다.

3.5 WHEN 템플릿(`styleProfile`/`templatePath`)이 주어지면 THEN the system SHALL CONTINUE TO
템플릿 스타일을 상속해 적용한다.
