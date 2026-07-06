# PPTX 이미지 슬롯 배정 결함 수정 Bugfix Design

## Overview

`ai_engine/server.py`의 `_tool_generate_pptx`(디스크 4415행~) 슬라이드 합성 루프는
배경/장식/부분 이미지를 여러 경로에서 `add_picture`로 임베드한다. 이 경로들이 서로의
배치 결과를 모른 채 독립적으로 동작해 세 결함이 발생한다.

- **D1**: 한 슬라이드에 풀블리드 배경이 2장 임베드됨(슬라이드 8·9). 여러 풀블리드 임베드
  경로(`_native_over_bg`의 `_pic_bg`, `bg_path`, 표지의 `cover_bg`/`_embed_fullbleed`)가
  모두 `spTree.insert(2, ...)`로 back-most에 삽입하면서 **이미 배경이 깔렸는지 검사하는
  가드가 없다**.
- **D2**: 배경급 대형 이미지(3840×2160)가 0.25in 소형 장식 슬롯에 배정됨(슬라이드 8·9).
  `_select_render_plan`이 `vertex_slot="visual"`을 내면 슬롯 크기와 무관하게
  `img_file = _pre_rel`(5239행)로 들어가고, 이후 임베드 경로가 **슬롯-이미지 크기 정합을
  검증하지 않는다**.
- **D3**: 부분 이미지의 배치 rect가 슬라이드 경계 밖으로 나감(슬라이드 1, top=-1.39in).
  region에 비해 큰 이미지를 그릴 때 `off_t = region_t + (region_h - draw_h)/2.0`류 계산이
  음수를 낼 수 있고, **슬라이드 경계 클램프가 없는 임베드 경로**가 존재한다.

수정 전략은 좌표/슬롯/중복 결정을 `ai_engine/layout_geometry.py`의 **순수 함수**로 추출해
PBT 가능하게 만들고(이전 스펙 `pptx-overlay-collision-fix`에서 도입한 모듈을 additive하게
확장), `_tool_generate_pptx`의 각 `add_picture` 직전에 이 함수들을 호출해 결정을 위임하는
것이다. 비버그 입력에서는 함수가 입력 좌표를 **그대로 반환(no-op 동등성)**하므로 기존 출력이
바이트 보존된다. 손실-0 불변식(이전 스펙 `pptx-quality-vertex-images`)은 중복/오배정
후보를 **폐기하지 않고** 다른 슬롯으로 재배정·보존하는 방식으로 유지한다.

server.py는 에디터 버퍼가 stale하므로 **디스크 패치**로만 수정한다. heredoc/stdin 금지,
네트워크 0(게이트웨이/Vertex/HTML은 목), 게이트웨이 제약 준수(LLM/operation은 Bedrock
Gateway, Vertex는 `vertex_image_module.py` 경유만).

## Glossary

- **Bug_Condition (C)**: 한 슬라이드의 PICTURE 집합 상태 `S`가 D1∨D2∨D3 중 하나라도 만족하는
  조건. `isBugCondition(S)`(bugfix.md 형식화)와 동일.
- **Property (P)**: 버그 입력에 대한 기대 동작 P1∧P2∧P3 — 풀블리드 ≤1, 소형 슬롯에 대형
  이미지 없음, 모든 PICTURE 경계 안.
- **Preservation**: 비버그 슬라이드(¬C)의 기존 배치/임베드 좌표·바이트, 그리고 이전 두 스펙의
  불변식(손실-0, 겹침 <10%, 게이트웨이 제약)을 변경하지 않음.
- **F / F'**: 원본(미수정) `_tool_generate_pptx` 합성 경로 / 수정된 합성 경로.
- **풀블리드(isFullbleed)**: audit 도구와 동일 — `r.left<=0.3 ∧ r.top<=0.3 ∧ r.width>=13.333*0.92
  ∧ r.height>=7.5*0.92`.
- **`_tool_generate_pptx`**: `ai_engine/server.py` 4415행의 PPTX 합성 도구. 슬라이드 루프에서
  배경/장식/부분 이미지를 `add_picture`로 조립한다.
- **`_eff_bg` / `_native_over_bg`**: 5266행 `_eff_bg = slide_bg or _dp_body_bg`. 네이티브
  다이어그램 위에 풀블리드 배경을 깔 때 쓰는 유효 배경 경로와 그 플래그.
- **`_select_render_plan`**: 3159행. Vertex 이미지의 슬롯(`hero|backdrop|visual|none`)과
  `body_separated`를 결정하는 순수 결정 함수.
- **`layout_geometry`**: `ai_engine/layout_geometry.py`. 좌표·기하 순수 함수 모듈. 본 수정의
  PBT 단일 대상이며 새 함수를 additive로 추가한다.
- **`LARGE_PX` / `SMALL_SLOT_IN` / `EPS`**: 대형 이미지/소형 슬롯/경계 허용오차 임계 상수.

## Bug Details

### Bug Condition

버그는 `_tool_generate_pptx` 슬라이드 루프가 한 슬라이드에 이미지를 임베드하면서 (D1) 풀블리드
배경 임베드 경로 간 중복 가드가 없거나, (D2) 슬롯 크기와 이미지 픽셀 크기의 정합을 검증하지
않거나, (D3) 배치 좌표를 슬라이드 경계로 클램프하지 않을 때 발생한다.

**Formal Specification:**
```
FUNCTION isBugCondition(S)
  INPUT: S = SlideMediaState { pictures : list of Picture { rect:Rect, pixelWidth:int, pixelHeight:int } }  // z-order 순
  OUTPUT: boolean

  // D1: 풀블리드 배경 중복 임베드
  defectD1 := count({ p IN S.pictures : isFullbleed(p.rect) }) > 1

  // D2: 대형 이미지가 소형 장식 슬롯에 배정
  defectD2 := EXISTS p IN S.pictures SUCH THAT isLargeImage(p) AND isSmallSlot(p.rect)

  // D3: PICTURE rect 가 슬라이드 경계 밖
  defectD3 := EXISTS p IN S.pictures SUCH THAT NOT withinBounds(p.rect, SLIDE)  // SLIDE=(0,0,13.333,7.5)

  RETURN defectD1 OR defectD2 OR defectD3
END FUNCTION
```

보조 술어(임계는 본 설계 §임계 상수에서 확정):
```
isFullbleed(r)   := r.left<=0.3 AND r.top<=0.3 AND r.width>=13.333*0.92 AND r.height>=7.5*0.92
isLargeImage(p)  := p.pixelWidth>=LARGE_PX OR p.pixelHeight>=LARGE_PX        // LARGE_PX=1024
isSmallSlot(r)   := r.width<=SMALL_SLOT_IN AND r.height<=SMALL_SLOT_IN        // SMALL_SLOT_IN=0.5
withinBounds(r,s):= r.left>=-EPS AND r.top>=-EPS AND r.left+r.width<=s.width+EPS AND r.top+r.height<=s.height+EPS  // EPS=0.05
```

### Examples

- **D1** (슬라이드 8·9): z=0, z=1에 3840×2160 풀블리드 배경 2장이 둘 다 `(0,0,13.333,7.5)`.
  기대: 풀블리드 1장만 임베드, 두 번째 후보는 콘텐츠/비주얼 슬롯 재배정 또는 미임베드(보존).
- **D2** (슬라이드 8·9, z=3): 3840×2160 이미지가 `(0.5,0.6,0.25,0.25)` 0.25in 슬롯에 찌그러져
  임베드. 정상 슬라이드는 같은 슬롯에 75×100 단색 액센트가 들어감. 기대: 소형 슬롯에는 슬롯
  크기에 맞는 자산만, 대형 이미지는 풀블리드/콘텐츠 영역으로.
- **D3** (슬라이드 1, z=3): 900×720 일러스트가 `(8.11, -1.39, 5.21, 4.17)` → top=-1.39in로
  슬라이드 위로 잘려나감. 기대: 경계 안으로 클램프 또는 region에 맞춰 리사이즈.
- **엣지(비버그)**: 풀블리드 1장 + 소형 슬롯에 75×100 단색 + 모든 PICTURE가 경계 안 →
  `isBugCondition=false`. F'는 F와 바이트 동일 출력.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors (비버그 입력에서 반드시 보존):**
- D1/D2/D3 어느 조건에도 해당하지 않는 슬라이드의 기존 배치/임베드 좌표를 **바이트 동일**하게
  유지(bugfix 3.1).
- 생성된 Vertex 이미지를 폐기하지 않고 배경/비주얼/장식 슬롯에 보존·임베드(손실-0, bugfix 3.2,
  이전 스펙 `pptx-quality-vertex-images`).
- 텍스트-텍스트/번호 배지 겹침 <10% 불변식(bugfix 3.3, 이전 스펙 `pptx-overlay-collision-fix`).
- LLM/operation은 Bedrock Gateway 경유, Vertex는 `vertex_image_module.py`에서만 호출(bugfix 3.4).
- Vertex 비활성/실패 시 네이티브 다이어그램/카드 폴백 진행 및 기존 회귀 스위트 통과(bugfix 3.5).

**Scope:**
풀블리드가 이미 ≤1장이고, 소형 슬롯에 대형 이미지가 없고, 모든 PICTURE가 경계 안인 슬라이드는
본 수정의 어떤 분기도 좌표를 바꾸지 않는다. 새 순수 함수들은 이런 입력에서 **입력을 그대로
반환(no-op 동등성)**하도록 설계한다.

> 버그 입력에 대한 올바른 동작은 아래 Correctness Properties(P1~P3)에 정의한다. 이 절은
> 무엇이 **바뀌지 않아야 하는지**에 집중한다.

## Hypothesized Root Cause

디스크(`ai_engine/server.py`, `ai_engine/native_diagram_pptx.py`) 실측 라인 인용.

### D1 — 풀블리드 배경 중복 임베드

여러 풀블리드 임베드 경로가 서로의 결과를 모른 채 각각 `spTree.insert(2, ...)`로 back-most에
삽입한다. 두 경로가 같은 슬라이드에서 실행되면 둘 다 인덱스 2에 삽입되어 z=0, z=1 두 장의
풀블리드가 남는다.

1. **본문 슬라이드 — `_native_over_bg` 와 `bg_path` 의 동시 실행 가능성** (server.py)
   - 5266행: `_eff_bg = slide_bg or _dp_body_bg`
   - 5269~5278행: `native_diag`가 있고 `_eff_bg`가 있으면 `_pic_bg = s.shapes.add_picture(_cand_bg, ... 13.333×7.5)` 후 `_spt.insert(2, _pic_bg._element)`, `_native_over_bg=True`, `slide_bg=""`.
   - 5349~5355행: `if slide_bg:` → `bg_path` 설정. 5387~5396행: `s.shapes.add_picture(bg_path, ... 13.333×7.5)` + `spTree.insert(2, ...)`.
   - **근본 원인**: `_native_over_bg` 경로가 `add_picture`에 성공하고도 예외 처리/조기 종료
     경로에서 `slide_bg`를 비우지 못하거나, `_dp_body_bg`(서버 공유 배경, 4523행)와
     caller `slideBackground`(5185행)가 동시에 설정되어 한 경로가 `_dp_body_bg`를, 다른 경로가
     잔존 `slide_bg`를 각각 풀블리드로 까는 경우 중복이 발생한다. 어느 경로도 "이미 풀블리드가
     깔렸는가"를 검사하는 가드가 없다.
2. **표지 — `cover_bg` 와 HTML 표지의 이중 임베드** (server.py)
   - 4746~4750행: `cover_bg`(coverBackground)가 있으면 `cover.shapes.add_picture(cand, ...)` + `insert(2)`.
   - 4834행: HTML 표지 렌더 성공 시 `_embed_fullbleed(cover, _cov_abs)`(4656행: add_picture + insert(2)).
   - **근본 원인**: 표지에 coverBackground와 HTML 표지가 모두 있으면 두 풀블리드가 겹쳐 임베드.
3. **공유 배경 전파** (server.py)
   - 4501~4523행: Vertex 공유 배경이 `tool_input["coverBackground"]`와 `_dp_body_bg`에 동시 주입.
   - 11062~11064행, 11111~11113행: force-generate 경로가 `section_backgrounds`/`_shared_body_bg`를
     `sd["slideBackground"]`에 주입. caller bg와 서버 bg가 겹쳐 한 슬라이드에 풀블리드 후보가
     2개 생기는 입력 조건을 만든다.

### D2 — 대형 이미지가 소형 장식 슬롯에 배정

- 5232~5239행: `_slot = _plan["vertex_slot"]`; `if _slot == "visual": img_file = _pre_rel`.
  슬롯이 "visual"이면 이미지 픽셀 크기를 검사하지 않고 `img_file`로 채운다.
- 5398~5440행: `img_path` 임베드 경로가 종횡비로 region fit만 하고 **슬롯/region 크기 자체가
  소형(아이콘급)인지 검사하지 않는다**. region이 소형이면 대형 이미지가 그대로 축소 임베드되어
  찌그러진다.
- `native_diagram_pptx.py` 997행: 카드 아이콘 칩 슬롯에 `add_picture(_ic_png, ...)` — 정상은
  작은 단색 아이콘 PNG. 이 슬롯에 대형 배경 이미지 경로가 오배정되면 D2가 재현된다.
- **근본 원인**: 슬롯 크기(인치)와 이미지 픽셀 해상도의 정합을 검증하는 가드가 없어, 대형
  이미지가 소형 슬롯으로 흘러든다.

### D3 — PICTURE rect가 슬라이드 경계 밖

- 5419~5437행(`img_path` 경로): `draw_w=region_w; draw_h=region_w/ar; if draw_h>region_h: draw_h=region_h; draw_w=region_h*ar`로 region 내 clamp 후 `off_l/off_t = region_* + (region_* - draw_*)/2.0`.
  이 경로는 draw를 region으로 clamp하므로 off가 음수가 되지 않는다(정상). 따라서 슬라이드 1의
  `top=-1.39`는 **이 경로가 아니다**.
- **근본 원인 후보**: region clamp가 없는 다른 부분 이미지 슬롯 배치(히어로/사이드 합성 또는
  네이티브 다이어그램 이미지 슬롯)에서 `off_t = region_t + (region_h - draw_h)/2.0`를 쓰되
  `draw_h > region_h`(이미지가 region보다 큼)인 채로 계산해 음수 top이 나오고, 경계 클램프 없이
  그대로 `add_picture`된다. 즉 **부분 이미지 배치 좌표를 슬라이드 경계로 클램프/리사이즈하는
  공통 가드의 부재**가 근본 원인이다.

> 위 D1/D3 가설은 탐색 테스트(Exploratory Bug Condition Checking)에서 실제 합성 경로를 구동해
> 확인/반증한다. 반증 시 재가설한다. 정확한 버그 덱(`cgjang-…-1782775987352.pptx`)은 디스크에
> 없어, 통합 테스트가 슬라이드 8·9·1 유사 입력으로 D1/D2/D3을 구동해 재현한다.

## Correctness Properties

Property 1: Bug Condition — 풀블리드 배경 ≤ 1장 (D1)

_For any_ 슬라이드 상태 `S`에서 버그 조건이 성립할 때(특히 풀블리드 후보가 여럿일 때), 수정된
합성 경로 `F'`는 풀블리드 배경 PICTURE를 **최대 1장만** 임베드한다
(`count({p : isFullbleed(p.rect)}) <= 1`). 나머지 풀블리드 후보는 폐기하지 않고 콘텐츠/비주얼
슬롯으로 재배정하거나 미임베드하되 생성 이미지 자체는 보존한다.

**Validates: Requirements 2.1**

Property 2: Bug Condition — 소형 슬롯에 대형 이미지 없음 (D2)

_For any_ 슬라이드 상태 `S`에서 버그 조건이 성립할 때, 수정된 합성 경로 `F'`의 결과에는
대형 이미지가 소형 장식 슬롯에 배정된 PICTURE가 존재하지 않는다
(`NOT EXISTS p : isLargeImage(p) AND isSmallSlot(p.rect)`). 대형 이미지는 풀블리드/콘텐츠
영역으로 배정한다.

**Validates: Requirements 2.2**

Property 3: Bug Condition — 모든 PICTURE 경계 안 (D3)

_For any_ 슬라이드 상태 `S`에서 버그 조건이 성립할 때, 수정된 합성 경로 `F'`의 모든 PICTURE는
슬라이드 경계 `(0,0,13.333,7.5)` 안에 위치한다(`FOR ALL p: withinBounds(p.rect, SLIDE)`).
음수 top/left 및 경계 초과를 클램프 또는 리사이즈로 제거한다.

**Validates: Requirements 2.3**

Property 4: Preservation — 비버그 슬라이드 바이트 보존

_For any_ 슬라이드 상태 `S`에서 버그 조건이 성립하지 **않을 때**(`isBugCondition(S)=false`),
수정된 경로는 원본과 동일한 결과를 생성한다(`F(S) = F'(S)`). 좌표/임베드/바이트가 보존된다.
순수 함수는 이런 입력에서 입력을 그대로 반환(no-op 동등성)한다.

**Validates: Requirements 3.1, 3.3**

Property 5: Preservation — 손실-0 + 게이트웨이 제약 보존

_For any_ Vertex 이미지가 생성된 입력에서, 수정된 경로는 그 이미지를 폐기하지 않는다
(`vertex_slot(F'(media_state)) != "none"`; 중복/오배정 후보도 다른 슬롯에 보존). 또한 기하
결정 함수는 어떤 네트워크/모델 호출도 하지 않으며, LLM/operation은 Bedrock Gateway, Vertex는
`vertex_image_module.py` 경유만 유지한다.

**Validates: Requirements 3.2, 3.4, 3.5**

### 임계 상수 (audit 실측 기준 확정)

- `LARGE_PX = 1024` — 배경/콘텐츠급 대형 이미지. audit 실측 대형은 3840×2160으로 1024를 크게
  초과하며, 정상 장식 자산(75×100, 아이콘 PNG 24~40px급)은 1024 미만이라 양자를 명확히 가른다.
- `SMALL_SLOT_IN = 0.5` — 소형 장식 슬롯. audit 실측 결함 슬롯은 0.25in로 0.5 미만, 콘텐츠/
  히어로 region(≥5in)과 명확히 구분된다.
- `EPS = 0.05` — 경계 허용오차. audit 도구(`audit_pptx_zorder_break.py`의 off-slide 판정)와 동일.

## Fix Implementation

### §0. 신규 순수 함수 — `ai_engine/layout_geometry.py` (additive)

기존 모듈을 깨지 않고 함수만 추가한다. 좌표 단위는 인치, `Rect=(left,top,width,height)`.
네트워크/모델 호출 없음(Property 5).

```python
# 임계 상수
LARGE_PX: int = 1024
SMALL_SLOT_IN: float = 0.5
BOUNDS_EPS: float = 0.05
SLIDE_RECT: Rect = (0.0, 0.0, 13.333, 7.5)

def is_fullbleed(r: Rect) -> bool:
    """audit 도구와 동일 판정 — 풀블리드 배경 여부."""

def is_large_image(px_w: int, px_h: int, *, large_px: int = LARGE_PX) -> bool:
    """이미지 픽셀 해상도가 배경/콘텐츠급 대형인지."""

def is_small_slot(r: Rect, *, small_in: float = SMALL_SLOT_IN) -> bool:
    """배치 슬롯이 아이콘/액센트급 소형인지."""

def within_bounds(r: Rect, slide: Rect = SLIDE_RECT, *, eps: float = BOUNDS_EPS) -> bool:
    """rect 가 슬라이드 경계 안인지(음수/초과 없음)."""

def clamp_into_bounds(r: Rect, slide: Rect = SLIDE_RECT) -> Rect:
    """D3: rect 를 슬라이드 경계 안으로 이동/축소.
    - width/height 가 slide 를 넘으면 slide 크기로 축소(종횡비 보존은 fit_within 사용).
    - left/top 음수 또는 초과면 경계 안으로 평행이동.
    - 이미 within_bounds 면 입력 그대로 반환(no-op 동등성, Property 4)."""

def fit_within(region: Rect, natural_w: float, natural_h: float) -> Rect:
    """D3: natural 종횡비를 보존하며 region 안에 fit + 중앙정렬한 rect 반환.
    draw 크기가 region 을 넘지 않음을 보장 → off_t/off_l 음수 불가.
    natural 이 이미 region 안에 들어가면 region 기준 중앙배치(좌표는 region 내)."""

def fullbleed_guard(existing_count: int) -> bool:
    """D1: 이미 풀블리드가 존재하면(existing_count>=1) False(재배경 스킵) 반환.
    호출부는 False 면 풀블리드 임베드를 건너뛰고 후보를 다른 슬롯으로 재배정한다."""

def slot_image_fits(slot: Rect, px_w: int, px_h: int) -> bool:
    """D2: 소형 슬롯(is_small_slot)에 대형 이미지(is_large_image)면 False.
    그 외(정합)면 True. 호출부는 False 면 대형 이미지를 풀블리드/콘텐츠 region 으로 재배정."""
```

핵심 불변식: `clamp_into_bounds`/`fit_within`/`fullbleed_guard`/`slot_image_fits`는 비버그
입력(이미 경계 안·정합·풀블리드 0장)에서 **입력을 그대로 반환/True**해 no-op 동등성을 보장
(Property 4).

### §1. D1 수정 — 슬라이드당 풀블리드 1회 보장 (`server.py`)

**File**: `ai_engine/server.py`  **Function**: `_tool_generate_pptx`

1. 슬라이드 루프 진입 시 슬라이드별 가드 플래그 `_fb_embedded = False`를 둔다(슬라이드 단위 초기화).
2. 풀블리드 임베드 헬퍼를 단일화: `_native_over_bg`(5269행), `bg_path`(5387행), 표지
   `cover_bg`(4746행)/`_embed_fullbleed`(4656/4834행) 모든 경로가 임베드 직전에
   `fullbleed_guard(현재 풀블리드 개수)` 또는 `_fb_embedded`를 검사한다. 이미 풀블리드가 있으면
   **재배경을 스킵**하고 플래그만 True로 둔다.
3. 스킵된 풀블리드 후보(예: 잔존 `slide_bg`/`_dp_body_bg`)는 폐기하지 않는다. 손실-0:
   `_select_render_plan`의 결정에 따라 콘텐츠/비주얼 슬롯으로 재배정하거나, 재배정이 불가하면
   단순 미임베드하되 생성 이미지 파일은 디스크에 보존(다른 슬라이드/슬롯서 재사용 가능).
4. 표지: `cover_bg`가 이미 임베드(`_cover_bg_embedded=True`, 4748행 부근)면 HTML 표지
   `_embed_fullbleed` 호출(4834행)을 가드로 스킵한다.

### §2. D2 수정 — 슬롯-이미지 크기 정합 가드 (`server.py`)

1. `_slot == "visual"`로 `img_file = _pre_rel`(5239행) 배정 후, 대상 region이 소형 슬롯인 경우
   `slot_image_fits(slot_rect, px_w, px_h)`로 검증. 대형 이미지가 소형 슬롯이면 풀블리드 또는
   콘텐츠 region(`region_l,region_t,region_w,region_h`)으로 재배정한다.
2. `img_path` 임베드 직전(5398행 부근): 이미지 픽셀 크기를 PIL로 측정해(이미 5404~5410행에서
   측정) `slot_image_fits`로 검사. 소형 region에 대형 이미지면 콘텐츠 region으로 승격.
3. `native_diagram_pptx.py` 아이콘 칩 슬롯(997행)에는 아이콘 자산(`get_icon_png`)만 전달되도록
   유지하고, 대형 이미지 경로가 흘러들지 않게 호출부에서 자산 종류를 분리(이미 분리돼 있으나
   회귀 방지로 통합 테스트가 검증).

### §3. D3 수정 — 경계 클램프/리사이즈 (`server.py`)

1. 모든 부분 이미지 `add_picture` 직전에 배치 rect를 계산한 뒤
   `clamp_into_bounds(rect)` 또는 `fit_within(region, iw, ih)`를 통과시켜 음수 top/left와 경계
   초과를 제거한다.
2. `img_path` 경로(5419~5437행)의 off 계산을 `fit_within(region, iw, ih)` 호출로 치환(동작
   동등하되 음수 불가 보장). 추가로 최종 `(off_l, off_t, draw_w, draw_h)`에 `clamp_into_bounds`를
   적용해 어떤 region 정의에서도 경계 안을 보장.
3. 히어로/사이드 합성 등 region clamp가 없던 경로가 발견되면(탐색 테스트로 확인) 동일하게
   `fit_within`+`clamp_into_bounds`를 적용.

### §4. 공통

- 좌표/슬롯/중복 결정 로직을 `layout_geometry` 순수 함수로 추출해 PBT 대상화.
- 임계 상수(`LARGE_PX=1024`, `SMALL_SLOT_IN=0.5`, `BOUNDS_EPS=0.05`)를 모듈 상단에 명시.
- 모든 변경은 additive — 비버그 입력에서 호출 결과가 입력과 동일해 바이트 보존.

## Testing Strategy

### Validation Approach

두 단계: (1) 미수정 코드에서 D1/D2/D3을 재현하는 반례를 표면화하고, (2) 수정 후 버그가
사라지고 비버그 동작이 보존됨을 검증한다. seam 단독 PBT(순수 함수)와 실제 덱 audit 통합
테스트를 **모두** 포함하며 전부 헤르메틱(네트워크 0)이다.

### Exploratory Bug Condition Checking

**Goal**: 수정 전 D1/D2/D3을 재현해 근본 원인 가설을 확인/반증한다. 반증 시 재가설.

**Test Plan**: 실제 `_tool_generate_pptx`를 게이트웨이/Vertex/HTML 렌더 목으로 구동해
슬라이드 8·9·1 유사 입력을 합성하고, 생성된 in-memory pptx를
`audit_pptx_zorder_break.py`/`audit_pptx_media_classify.py` 기준으로 검사한다. 미수정 코드에서
실패(반례)를 관측한다.

**Test Cases**:
1. **D1 재현**: coverBackground+HTML 표지, 또는 caller `slideBackground`+서버 `_dp_body_bg`가
   동시 설정된 슬라이드 → 풀블리드 2장 관측(will fail on unfixed).
2. **D2 재현**: 대형(3840×2160) 이미지가 `visual` 슬롯/소형 region에 배정 → 0.25in 슬롯에 대형
   이미지 관측(will fail on unfixed).
3. **D3 재현**: region보다 큰 부분 이미지 → 음수 top 또는 경계 초과 관측(will fail on unfixed).
4. **엣지**: 풀블리드 0장 + 큰 region만 → 클램프 영향 없음(may pass on unfixed).

**Expected Counterexamples**: 풀블리드 count>1 / 소형 슬롯의 대형 PICTURE / off-slide rect.
가능 원인: 중복 가드 부재 / 슬롯-크기 정합 부재 / 경계 클램프 부재.

### Fix Checking

**Goal**: 버그 조건을 만족하는 모든 입력에서 F'가 P1∧P2∧P3을 만족.

```
FOR ALL S WHERE isBugCondition(S) DO
  S' := F'(S)
  ASSERT count({p IN S'.pictures : isFullbleed(p.rect)}) <= 1          // P1
  ASSERT NOT EXISTS p IN S'.pictures: isLargeImage(p) AND isSmallSlot(p.rect)  // P2
  ASSERT FOR ALL p IN S'.pictures: withinBounds(p.rect, SLIDE)         // P3
END FOR
```

### Preservation Checking

**Goal**: 버그 조건이 아닌 모든 입력에서 F'가 F와 동일한 결과.

```
FOR ALL S WHERE NOT isBugCondition(S) DO
  ASSERT F(S) = F'(S)              // 좌표/바이트 보존
END FOR
FOR ALL media_state WHERE has_vertex_image DO
  ASSERT vertex_slot(F'(media_state)) != "none"   // 손실-0
END FOR
```

**Testing Approach**: 순수 함수(`layout_geometry`)에 PBT를 적용한다 — 입력 도메인 전반에서
자동 생성된 다수 케이스로 no-op 동등성(비버그→입력 그대로)과 클램프/정합/가드 속성을 검증한다.

**Test Plan**: 미수정 코드에서 비버그 입력(풀블리드 ≤1, 정합 슬롯, 경계 안)의 출력을 먼저
관측해 캡처하고, 수정 후에도 바이트 동일함을 통합 테스트로 확인한다.

**Test Cases**:
1. **풀블리드 1장 보존**: 풀블리드 1장 슬라이드 → 수정 후에도 1장, 좌표 동일.
2. **정합 슬롯 보존**: 75×100 단색이 0.25in 슬롯 → 그대로 유지.
3. **경계 안 보존**: 모든 PICTURE 경계 안 → 좌표 미변경.
4. **손실-0 보존**: Vertex 이미지 생성 입력 → 슬롯 != none (이전 스펙 회귀 방지).

### Unit Tests

- `clamp_into_bounds`/`fit_within`: 음수 top/left, region 초과, 정상 입력 각각의 경계 처리.
- `fullbleed_guard`: count 0→임베드 허용, ≥1→스킵.
- `slot_image_fits`: (소형 슬롯×대형 이미지)=False, 그 외 True.
- 임계 경계값(1024px, 0.5in, EPS=0.05) 경계 케이스.

### Property-Based Tests

- 무작위 Rect/픽셀 크기 생성 → `clamp_into_bounds` 결과는 항상 `within_bounds` 참(P3).
- 무작위 슬롯/이미지 → `slot_image_fits=False`면 재배정 후 소형 슬롯에 대형 이미지 없음(P2).
- 무작위 풀블리드 후보 다수 → 가드 적용 후 풀블리드 ≤1(P1).
- 비버그 Rect(이미 경계 안/정합/풀블리드 0) → 모든 함수가 입력 그대로 반환(P4, no-op 동등성).

### Integration Tests

실제 `_tool_generate_pptx` 합성 경로를 게이트웨이/Vertex/HTML 렌더 목으로 구동해 생성 덱을
audit 기준으로 검사(`test_pptx_quality_vertex_images_integration.py`의 헤르메틱 목 패턴 재사용:
`_FakeVertexClient`, `_render_html_png_fake`, `_img_gen_disabled`, `patch.object`로 `_call_bridge`/
`_get_gw`/`_render_html_slide_to_png`/`_generate_html_slide_for_section` 목).

- **D1 통합**: 슬라이드 8·9 유사 입력 → 생성 덱의 각 슬라이드 풀블리드 ≤1(audit_pptx_zorder_break/
  media_classify로 검증).
- **D2 통합**: 대형 이미지 입력 → 소형 슬롯에 대형 이미지 없음(audit_pptx_media_classify의 슬롯-
  이미지 정합 분류로 검증).
- **D3 통합**: 큰 부분 이미지 입력 → audit_pptx_zorder_break의 off-slide 검출 0건.
- **보존 통합**: 비버그 덱 → 미수정/수정 출력 슬라이드의 PICTURE rect·media 바이트 동일.
- **손실-0/폴백 통합**: Vertex 이미지 생성 입력 → 모든 생성 이미지가 ppt/media에 임베드(unused=0);
  Vertex 비활성 시 네이티브 폴백 진행.

### 테스트 파일 계획 (신규, 헤르메틱)

- `scripts/test_pptx_image_slot_placement_bug_condition.py` — 탐색: 미수정 코드에서 D1/D2/D3
  반례 표면화(실제 합성 경로 + audit).
- `scripts/test_pptx_image_slot_placement_fix_pbt.py` — 순수 PBT: `layout_geometry` 신규 함수로
  P1/P2/P3 fix checking.
- `scripts/test_pptx_image_slot_placement_preservation_pbt.py` — 순수 PBT: 비버그 입력 no-op
  동등성(P4).
- `scripts/test_pptx_image_slot_placement_integration.py` — 통합: 실제 `_tool_generate_pptx`를
  목으로 구동해 슬라이드 8·9·1 유사 시나리오 합성 + audit_pptx_zorder_break/media_classify로
  P1~P5 검증.

실행(헤르메틱, 예):
`./ai_engine/.venv/bin/python -m pytest scripts/test_pptx_image_slot_placement_*.py -p no:cacheprovider -q`
