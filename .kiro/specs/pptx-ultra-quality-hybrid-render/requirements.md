# Requirements Document

## Introduction

본 기능은 PPTX 생성의 "초고퀄 고도화(ultra-high-quality enhancement)"를 목표로 한다. 현재 `ai_engine/server.py`의 `_tool_generate_pptx`에는 하드 트레이드오프가 존재한다: HTML-ON 경로는 모든 슬라이드(콘텐츠 + 구조형 포함)를 풀블리드 PNG로 굽기 때문에 시각 품질은 높지만 편집이 불가능하고, HTML-OFF 경로는 편집 가능하지만 시각적 상한이 낮다. 사용자는 둘 다를 원한다 — **초고 시각 품질과, 편집이 중요한 곳에서의 편집 가능성**.

이를 위해 슬라이드 역할별로 렌더 경로를 자동 결정하는 **하이브리드 렌더 규칙**을 도입한다. 표지/히어로/사진 의도 슬라이드는 Vertex 초고품질 풀블리드 배경으로, 고밀도 콘텐츠 슬라이드는 편집 가능 상태를 유지하는 고밀도 레이아웃(선택적으로 이미지 슬롯에 Vertex 히어로/액센트 이미지 합성)으로, 구조형(흐름/트리/아키텍처) 슬라이드는 편집 가능 네이티브 도형으로 렌더하되 생성 이미지를 손실 없이 backdrop으로 보존한다.

본 기능은 이미 완료·검증된 스펙(`pptx-quality-vertex-images`, `pptx-overlay-collision-fix`, `pptx-native-density-render`)의 동작을 **재작업하지 않는다**. 대신 그 동작들(손실-0, 도형/텍스트 겹침 0, 게이트웨이 제약, caller 지정 우선순위, Vertex 비활성/실패 폴백)을 **보존/회귀 방지 요구사항**으로 명시한다. 기본 동작은 명시적 opt-in 플래그가 켜질 때만 바뀐다.

본 기능은 실제 .pptx 감사(`scripts/audit_pptx_overlap.py`, `scripts/audit_pptx_textbox_overlap.py`, `scripts/audit_pptx_native_density.py`)로 증명 가능해야 하며, 실제 Vertex 렌더 품질은 자격증명 환경(`GOOGLE_APPLICATION_CREDENTIALS`)에서만 측정 가능함을 명시한다.

## Glossary

- **Hybrid_Render_System**: 슬라이드 역할(Slide_Role)에 따라 렌더 경로(Vertex 풀블리드 / HTML 고밀도 편집 가능 / 네이티브 도형)를 슬라이드 단위로 자동 결정하는 렌더 규칙. `ai_engine/server.py`의 `_tool_generate_pptx` 내부 결정 로직으로 구현된다.
- **Slide_Role**: 슬라이드의 역할 분류값. 값의 집합은 `{cover, section, structural, content, visual}`이다. `_classify_slide_role(slide, isCover)`가 산출한다.
- **Full_Bleed_Background**: 슬라이드 전체(좌상단 (0,0) 기준 13.333in × 7.5in, 16:9)를 덮는 배경 이미지. python-pptx PICTURE로 임베드되며 편집 불가한 단일 래스터다.
- **Editable_Native**: python-pptx 네이티브 도형(자동도형/텍스트박스/표) 또는 편집 가능 텍스트로 렌더된 슬라이드 요소. PowerPoint에서 텍스트/위치/서식을 직접 편집할 수 있다. 풀블리드 PNG로 굽지 않는다.
- **Loss_Zero**: 생성된 Vertex 이미지(`_vertex_pre[i]`)와 원본 콘텐츠(불릿/구조 표현)가 최종 산출 .pptx의 어떤 렌더 경로에서도 폐기되지 않는 불변식. 이미지는 최소한 backdrop으로 보존되고, 텍스트 콘텐츠는 편집 가능 요소 또는 굽힌 렌더로 보존된다.
- **Style_Profile**: 결정론적 디자인 토큰(팔레트/타이포/여백) 소스. HTML 경로는 `design_tokens_for_profile`, 네이티브 경로는 `_build_palette` → `_tpl_palette_for_native`로 적용된다. 동일 프로파일 입력은 동일 팔레트를 산출한다.
- **Image_Slot**: HTML 고밀도 레이아웃(cover / two_column / objective_detail 등) 안에 선택적으로 주입되는 이미지 필드(`heroImage`/`image`). 값이 있으면 `background-image`/`<img>`로 합성, 없으면 기존 그라디언트/플레이스홀더로 폴백한다.
- **Vertex_Image_Client**: `ai_engine/vertex_image_module.py`의 `VertexImageClient`. `get_vertex_image_client(aws_profile, credentials)`로 취득하며, `.enabled` 속성, `.resolve_model_id(model_class)`, `async .generate(prompt, model_class="image_generation_high_quality", aspect_ratio="16:9", negative_prompt="", num_images=1, timeout=60)` 인터페이스를 제공한다.
- **Bedrock_Gateway**: 모든 LLM/추론/operation JSON 생성의 유일한 경로. `_get_gw`로 취득한다 (steering `gateway.md`).
- **Feature_Flag**: 하이브리드 렌더 활성화 환경변수 `AE_HYBRID_RENDER`. 기본값은 ON(하이브리드가 표준 동작)이며, 값 `"0"`은 명시적 킬스위치로 기존(하이브리드 이전) 렌더로 롤백한다. 파싱 규칙: `"0"` → 비활성(킬스위치), `"1"` → 활성, 미설정/`""` → 활성(기본 ON), 인식 불가 값 → 활성(기본 ON) + 경고 1줄. `ai_engine/server.py`의 `_hybrid_render_enabled(env)` 순수 함수가 판정한다. Vertex 이미지 활성화는 별개로 기존 `AE_ENABLE_VERTEX_IMAGE=1`을 따른다.
- **PPTX_Audit_Suite**: 실제 .pptx 산출물을 python-pptx만으로(네트워크 불필요) 검사하는 감사 스크립트 집합 — `scripts/audit_pptx_overlap.py`(`audit(path)`), `scripts/audit_pptx_textbox_overlap.py`(`main(path)`), `scripts/audit_pptx_native_density.py`(`audit_native_density(pptx_path, tokens)`).

## Requirements

### Requirement 1: 슬라이드 역할 기반 하이브리드 렌더 라우팅

**User Story:** 프레젠테이션을 만드는 사용자로서, 각 슬라이드가 그 역할에 가장 적합한 렌더 경로로 자동 배정되기를 원한다. 그래야 표지는 초고품질로, 콘텐츠는 편집 가능하게, 구조도는 편집 가능한 도형으로 나온다.

#### Acceptance Criteria

1. WHEN `_tool_generate_pptx`가 슬라이드를 처리하고 Feature_Flag가 활성 상태이면, THE Hybrid_Render_System SHALL 각 슬라이드에 대해 `{cover, section, structural, content, visual}` 중 정확히 하나의 Slide_Role을 `_classify_slide_role`로 산출한다.
2. WHERE Slide_Role이 `cover`, `section`, `visual` 중 하나이고 Vertex_Image_Client가 활성 상태이면, THE Hybrid_Render_System SHALL 해당 슬라이드를 Full_Bleed_Background 렌더 경로로 배정한다.
3. WHERE Slide_Role이 `content`이면, THE Hybrid_Render_System SHALL 해당 슬라이드를 Editable_Native 상태를 유지하는 고밀도 레이아웃 경로로 배정한다.
4. WHERE Slide_Role이 `structural`이면, THE Hybrid_Render_System SHALL 해당 슬라이드를 Editable_Native 도형 경로로 배정하고 생성된 Vertex 이미지가 존재할 경우 backdrop으로만 보존한다.
5. WHEN 하나의 슬라이드에 대해 Slide_Role이 산출되면, THE Hybrid_Render_System SHALL 세 렌더 경로(Full_Bleed_Background, Editable_Native 고밀도, Editable_Native 도형) 중 정확히 하나를 주 렌더러로 배정하고 나머지 두 경로는 해당 슬라이드에 배정하지 않는다.
6. IF Slide_Role이 `{cover, section, visual}` 중 하나이지만 Vertex_Image_Client가 비활성 상태이면, THEN THE Hybrid_Render_System SHALL 해당 슬라이드를 Editable_Native 고밀도 경로로 폴백 배정한다.
7. IF Feature_Flag가 비활성 상태이면, THEN THE Hybrid_Render_System SHALL 모든 슬라이드를 Editable_Native 경로로 렌더하고 라우팅 목적으로 `_classify_slide_role`을 호출하지 않는다.
8. IF Slide_Role 분류 결과가 모호하거나 미정의이거나 복수 후보이면, THEN THE Hybrid_Render_System SHALL 해당 슬라이드의 Slide_Role을 결정론적으로 `content`로 확정한다.

### Requirement 2: content 슬라이드의 고밀도 + 편집 가능성 양립

**User Story:** 프레젠테이션을 만드는 사용자로서, KPI/카드/2단/타임라인/표/프로세스 같은 고밀도 콘텐츠 슬라이드가 시각적으로 밀도 높으면서도 편집 가능하기를 원한다. 그래야 발표 직전에 수치나 문구를 직접 고칠 수 있다.

#### Acceptance Criteria

1. WHERE Slide_Role이 `content`이고 Feature_Flag가 활성 상태이면, THE Hybrid_Render_System SHALL 콘텐츠 텍스트를 편집 가능 텍스트 run 개수 ≥ 1개로 렌더하고 슬라이드 전체(13.333in × 7.5in)를 덮는 풀블리드 PICTURE 개수를 0개로 유지한다.
2. WHILE HTML-ON 상태에서 `content` 슬라이드를 렌더하면, THE Hybrid_Render_System SHALL 해당 슬라이드를 풀블리드 PNG로 굽지 않고 편집 가능 텍스트 run 개수 ≥ 1개를 유지한다.
3. WHERE Slide_Role이 `content`이고 Vertex_Image_Client가 활성 상태이며 히어로/액센트 이미지가 생성되었으면, THE Hybrid_Render_System SHALL 해당 이미지를 Image_Slot에 합성하여 합성 이미지 개수 ≥ 1개를 산출한다.
4. IF `content` 슬라이드에 대해 Image_Slot 합성이 불가능한 레이아웃이면, THEN THE Hybrid_Render_System SHALL 생성된 이미지를 backdrop 또는 on-slide 레이어로 보존하여 보존 이미지 개수 ≥ 1개를 유지하고 폐기하지 않는다.
5. WHEN `content` 슬라이드가 렌더되면, THE Hybrid_Render_System SHALL `scripts/audit_pptx_native_density.py`의 `audit_native_density(pptx_path, tokens)` failures 목록 개수가 0개이고 비텍스트 시각 요소 개수 ≥ 2개이며 5개 스타일 품질 검사를 모두 통과하는 산출물을 생성한다.

### Requirement 3: Vertex 프롬프트/합성 품질 업그레이드

**User Story:** 프레젠테이션을 만드는 사용자로서, Full_Bleed_Background 이미지가 젠스파크급의 초고품질이기를 원한다. 그래야 표지와 히어로 슬라이드가 상업용 수준으로 보인다.

#### Acceptance Criteria

1. WHEN Full_Bleed_Background 이미지를 생성하면, THE Hybrid_Render_System SHALL Vertex_Image_Client.generate를 `aspect_ratio="16:9"`로 정확히 1회 호출한다.
2. WHEN 배경(텍스트가 없어야 하는) 이미지를 생성하면, THE Hybrid_Render_System SHALL 길이 ≥ 1인 비어 있지 않은 `negative_prompt`를 Vertex_Image_Client.generate에 전달하고, 해당 `negative_prompt`에 텍스트·문자·워터마크 억제 용어를 포함한다.
3. WHEN Vertex 프롬프트를 구성하면, THE Hybrid_Render_System SHALL 서로 다른 Slide_Role 값에 대해 실제로 서로 다른 프롬프트 문자열을 산출한다.
4. WHEN Vertex 프롬프트의 색상/톤을 결정하면, THE Hybrid_Render_System SHALL 동일한 Style_Profile 입력에 대해 결정론적으로 동일한 색상/톤 표현을 산출한다.
5. WHEN Full_Bleed_Background 이미지 생성을 요청하면, THE Hybrid_Render_System SHALL `model_class="image_generation_high_quality"`로 Vertex_Image_Client.generate를 호출한다.
6. WHEN 프롬프트 빌더 단위 테스트를 실행하면, THE Hybrid_Render_System SHALL 라이브 Vertex 호출 없이 동일 입력에 대해 바이트 단위로 동일한 프롬프트 문자열을 산출한다.
7. WHEN Vertex 프롬프트를 구성하면, THE Hybrid_Render_System SHALL 직접 LLM 호출을 수행하지 않으며, LLM 호출이 필요한 경우 Bedrock_Gateway 경유로만 수행한다.

### Requirement 4: 완료 스펙 동작 보존 (회귀 방지)

**User Story:** 유지보수를 담당하는 개발자로서, 이미 완료·검증된 스펙의 동작이 본 고도화로 인해 깨지지 않기를 원한다. 그래야 손실-0, 겹침 0, 게이트웨이 제약이 계속 지켜진다.

#### Acceptance Criteria

1. THE Hybrid_Render_System SHALL 성공적으로 생성된 Vertex 이미지의 100%를 최종 .pptx에 포함하고 폐기된 이미지 개수를 0개로 유지한다 (Loss_Zero 불변식 보존).
2. WHEN caller가 슬라이드에 대해 `imageFile` 또는 `slideBackground`를 명시하면, THE Hybrid_Render_System SHALL caller 지정 값의 기존 우선순위를 유지하고 Vertex 사전생성을 스킵한다.
3. IF `AE_ENABLE_VERTEX_IMAGE`가 `1`이 아니거나 Vertex_Image_Client.generate 호출이 실패하면, THEN THE Hybrid_Render_System SHALL 콘텐츠 손실 항목 개수 0개로 기존 네이티브/HTML 폴백 경로로 전환하고 폴백이 발생했음을 표시한다.
4. THE Hybrid_Render_System SHALL 모든 LLM 및 operation JSON 생성을 Bedrock_Gateway 경유로만 수행하고, 이미지 생성 경로가 아닌 곳에서의 Vertex_Image_Client 호출 개수를 0개로 유지한다.
5. WHEN `structural` 슬라이드를 렌더하면, THE Hybrid_Render_System SHALL 흐름/트리/아키텍처 다이어그램을 Editable_Native 도형으로 유지하고 구조 요소의 래스터화 개수를 0개로 유지한다.
6. WHEN 어떤 슬라이드를 렌더하면, THE Hybrid_Render_System SHALL 도형-도형 및 텍스트박스-텍스트박스 겹침 면적이 0 EMU인 산출물을 생성한다.

### Requirement 5: 실제 산출물 감사 기반 검증 가능성

**User Story:** 품질을 검증하는 개발자로서, 본 기능의 합격 여부가 실제 .pptx 파일 감사로 증명되기를 원한다. 그래야 "된다고 주장"이 아니라 실측으로 확인할 수 있다.

#### Acceptance Criteria

1. WHEN 하이브리드 렌더로 .pptx를 생성하면, THE Hybrid_Render_System SHALL `scripts/audit_pptx_overlap.py`의 `audit(path)`가 판정한 "텍스트·이미지 겹침 슬라이드" 목록 개수 0개 및 "편집 불가(래스터) 의심 슬라이드" 목록 개수 0개인 산출물을 생성한다.
2. WHEN 하이브리드 렌더로 .pptx를 생성하면, THE Hybrid_Render_System SHALL `scripts/audit_pptx_textbox_overlap.py`의 `main(path)`가 판정한 겹침 면적 0.05in²를 초과하는 텍스트박스 쌍 개수가 0개인 산출물을 생성한다.
3. WHEN `content` 슬라이드를 포함한 .pptx를 생성하면, THE Hybrid_Render_System SHALL `scripts/audit_pptx_native_density.py`의 `audit_native_density(pptx_path, tokens)`가 `AuditReport.passed == True`이고 failures 목록이 비어 있는 산출물을 생성한다.
4. THE Hybrid_Render_System SHALL (a) 헤르메틱하게 증명 가능한 속성(Requirement 1 라우팅, Requirement 2/4 합성·보존, 본 Requirement 5의 5.1–5.3 감사)과 (b) 자격증명 환경에서만 측정 가능한 속성(Requirement 3 Vertex 렌더 품질)을 문서에 각각 열거하여 구분한다.
5. WHERE 테스트 환경이 헤르메틱하면, THE Hybrid_Render_System SHALL 라우팅·합성·보존 로직을 검증 가능하게 한다. 여기서 "헤르메틱"은 `Vertex_Image_Client.enabled == False`, 외부 네트워크 호출 개수 0개, 동일 입력에 대한 결정론적 동일 출력, 그리고 5.1–5.3 감사 통과를 모두 만족하는 상태로 정의한다.

### Requirement 6: 기본 ON 하이브리드 렌더와 킬스위치 롤백

**User Story:** 기존 사용자로서, 별도 설정 없이도 개선된 하이브리드 렌더링을 기본으로 사용하기를 원한다. 그래야 표준 동작이 곧 고품질 렌더가 된다. 동시에 문제가 생겼을 때 즉시 기존 동작으로 되돌릴 수 있는 킬스위치가 존재하기를 원한다.

#### Acceptance Criteria

1. IF Feature_Flag(`AE_HYBRID_RENDER`) 값이 `"0"`이면, THEN THE Hybrid_Render_System SHALL 하이브리드 라우팅을 적용하지 않고 하이브리드 도입 이전의 기존 렌더 동작으로 롤백한다.
2. WHEN Feature_Flag(`AE_HYBRID_RENDER`)가 미설정이거나 값이 `""`이면, THE Hybrid_Render_System SHALL 슬라이드 역할 기반 하이브리드 라우팅(Requirement 1)을 기본값으로 적용한다.
3. WHERE Feature_Flag(`AE_HYBRID_RENDER`) 값이 `"1"`이면, THE Hybrid_Render_System SHALL 슬라이드 역할 기반 하이브리드 라우팅(Requirement 1)을 적용한다.
4. IF Feature_Flag(`AE_HYBRID_RENDER`) 값이 `"0"`과 `"1"` 이외의 인식 불가한 값이면, THEN THE Hybrid_Render_System SHALL 해당 값을 활성(기본 ON)으로 처리하여 하이브리드 라우팅을 적용하고 경고를 정확히 1줄 기록한다.
5. WHERE `AE_ENABLE_VERTEX_IMAGE`가 비활성 상태이면, THE Hybrid_Render_System SHALL Vertex_Image_Client 호출 개수 0개로 Editable_Native/HTML 경로로만 렌더한다.
6. IF Feature_Flag(`AE_HYBRID_RENDER`) 값이 `"0"`이면, THEN THE Hybrid_Render_System SHALL 동일 입력 및 동일 seed/설정에 대해 하이브리드 도입 이전 버전과 바이트 단위로 동일한 .pptx를 산출한다.
