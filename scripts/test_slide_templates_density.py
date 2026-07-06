"""Density-augmentation tests for ai_engine/slide_templates.py.

These cover the *additive, preservation-first* density upgrades layered on top
of the completed `pptx-quality-vertex-images` spec. The upgrades add OPTIONAL
information-hierarchy chrome (numbered STEP badges, secondary caption/meta
lines, column badges/metrics, step status colors, process step numbers) that
renders ONLY when the corresponding optional field is supplied.

Two guarantees are asserted:

  (A) PRESERVATION — when NO new optional field is supplied, the render output
      is byte-identical to a call that explicitly sets every new optional field
      to its documented no-op default, AND the output contains none of the
      density-only markers. (The new params therefore cannot change existing
      callers' bytes — rule #1 of the task.)

  (B) DENSITY — when the optional fields ARE supplied, the corresponding badge /
      meta / connector-color / step-number markup is present, tinted with
      design-token colors so styleProfile inheritance is preserved.

Plus a Hypothesis property-based test (PBT) generalising (A): for ANY content,
the absence of the new optional fields yields output free of density markers and
equal to the explicit-no-op-default call.

Everything is hermetic — pure Python, NO network, NO Electron, NO gateway.

Run (hermetic):
  ./venv/bin/python -m pytest scripts/test_slide_templates_density.py -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import re
import sys

# Make ai_engine importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "ai_engine"))

import pytest  # noqa: E402
from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

import ai_engine.slide_templates as m  # noqa: E402
from ai_engine.slide_templates import design_tokens_for_profile  # noqa: E402


# Density-only markers — each appears ONLY when an optional density field is
# rendered. Written in `class="..."` form so they cannot collide with similar
# substrings (e.g. `.pf-note` must NOT match the `pf-no` step-number marker).
DENSITY_MARKERS = (
    'class="card-badge"',
    'class="card-meta"',
    'class="col-head"',
    'class="col-badge"',
    'class="col-metric"',
    'class="step-meta"',
    'class="od-block-cap"',
    'class="pf-no"',
)


def _assert_valid_html(html: str) -> None:
    assert isinstance(html, str) and html
    assert "<html" in html and "<body" in html and "</html>" in html


def _density_markers_in(html: str):
    return [mk for mk in DENSITY_MARKERS if mk in html]


# ---------------------------------------------------------------------------
# Baseline content used across the preservation cases (NO new optional fields).
# ---------------------------------------------------------------------------
FG_BASE = dict(
    title="핵심 기능",
    features=[
        {"icon": "zap", "title": "헤드리스 캡쳐", "description": "외부 도구 0"},
        {"icon": "shield", "title": "보안", "description": "data: URL 로드"},
        {"icon": "layers", "title": "레이아웃", "description": "11종"},
        {"icon": "code", "title": "매핑", "description": "LLM 자동"},
    ],
    subtitle="설치 의존성 0",
)
TL_BASE = dict(
    title="구현 단계",
    steps=[
        {"label": "01", "title": "IPC", "description": "핸들러"},
        {"label": "02", "title": "브리지", "description": "라우트"},
        {"label": "03", "title": "템플릿", "description": "render_*"},
    ],
    subtitle="5단계",
)
TC_BASE = dict(
    title="현재 vs 새 접근",
    left_content="- matplotlib 캡쳐\n- 정렬 깨짐",
    right_content="- 풀블리드\n- 한글 OK",
    subtitle="같은 입력 다른 결과",
)
OD_BASE = dict(
    title="목표 상세",
    number="01",
    subtitle="단일 목표 심화",
    status="100% DONE",
    meta=[{"label": "방향", "value": "확대"}, {"label": "가중치", "value": "30%"}],
    blocks=[{"title": "산출물", "items": ["a", "b"]}, {"title": "활동", "items": ["c"]}],
    evidence={"title": "증빙", "items": ["e1", "e2"], "note": "완료"},
)
PF_BASE = dict(
    title="처리 흐름",
    steps=[{"title": "접수"}, {"title": "분석"}, {"title": "조치"}, {"title": "완료"}],
    subtitle="5단계 흐름",
    note="증빙 보관",
)


# ===========================================================================
# (A) PRESERVATION — default call == explicit-no-op-default call, no markers
# ===========================================================================
def test_preserve_feature_grid_default_byte_identical():
    base = m.render_feature_grid(**FG_BASE)
    explicit = m.render_feature_grid(**FG_BASE, step_badges=False)
    _assert_valid_html(base)
    assert base == explicit, "step_badges=False는 기본 호출과 byte-identical이어야 함"
    assert _density_markers_in(base) == [], "기본 feature_grid에 밀도 마커가 없어야 함"
    # core markup preserved
    assert 'class="card"' in base and 'class="grid"' in base and 'class="card-icon"' in base


def test_preserve_timeline_horizontal_and_vertical_byte_identical():
    base_h = m.render_timeline(**TL_BASE)
    expl_h = m.render_timeline(**TL_BASE, orientation="auto")
    assert base_h == expl_h
    assert _density_markers_in(base_h) == []
    assert 'class="step-circle"' in base_h and 'class="connector"' in base_h

    tl_v = dict(title="t", steps=[{"label": str(i), "title": f"S{i}",
                                   "description": "d"} for i in range(6)])
    base_v = m.render_timeline(**tl_v)
    assert _density_markers_in(base_v) == []
    assert 'class="v-circle"' in base_v and 'class="v-connector"' in base_v


def test_preserve_two_column_default_byte_identical():
    base = m.render_two_column(**TC_BASE)
    explicit = m.render_two_column(**TC_BASE, left_badge="", right_badge="",
                                   left_metric="", right_metric="")
    _assert_valid_html(base)
    assert base == explicit
    assert _density_markers_in(base) == []
    assert 'class="col left"' in base and 'class="col right"' in base


def test_preserve_objective_detail_default_no_markers():
    base = m.render_objective_detail(**OD_BASE)
    _assert_valid_html(base)
    assert _density_markers_in(base) == []
    # core markup preserved
    assert 'class="od-block"' in base and 'class="od-num"' in base and 'class="od-ev"' in base


def test_preserve_process_flow_default_byte_identical():
    base = m.render_process_flow(**PF_BASE)
    explicit = m.render_process_flow(**PF_BASE, step_numbers=False)
    _assert_valid_html(base)
    assert base == explicit, "step_numbers=False는 기본 호출과 byte-identical이어야 함"
    assert _density_markers_in(base) == []
    # core markup preserved — and the `.pf-note` class must remain (regression
    # guard for the pf-no substring collision).
    assert 'class="pf-box"' in base and 'class="pf-note"' in base


# ===========================================================================
# (B) DENSITY — optional fields render the extra hierarchy chrome
# ===========================================================================
def test_density_feature_grid_explicit_badge_and_meta():
    feats = [
        {"icon": "zap", "title": "A", "description": "d", "badge": "필수", "meta": "담당: 김"},
        {"icon": "shield", "title": "B", "description": "d"},
        {"icon": "layers", "title": "C", "description": "d", "meta": "기한: Q3"},
    ]
    html = m.render_feature_grid(title="기능", features=feats)
    _assert_valid_html(html)
    assert 'class="card-badge"' in html and "필수" in html
    assert 'class="card-meta"' in html and "담당: 김" in html and "기한: Q3" in html


def test_density_feature_grid_step_badges_autonumber():
    html = m.render_feature_grid(title="기능", features=FG_BASE["features"],
                                 step_badges=True)
    assert 'class="card-badge"' in html
    assert "STEP 1" in html and "STEP 2" in html


def test_density_feature_grid_explicit_badge_beats_autonumber():
    feats = [{"icon": "zap", "title": "A", "description": "d", "badge": "핵심"}]
    html = m.render_feature_grid(title="기능", features=feats, step_badges=True)
    assert "핵심" in html
    assert "STEP 1" not in html, "명시 badge가 자동 STEP 번호보다 우선해야 함"


def test_density_timeline_status_tone_colors_circle():
    profile = {"primaryColor": "#0066FF", "secondaryColor": "#00C896",
               "accentColor": "#FF6B35"}
    d = design_tokens_for_profile(profile)
    steps = [
        {"label": "01", "title": "S1", "description": "d", "status": "done", "meta": "100%"},
        {"label": "02", "title": "S2", "description": "d", "tone": "accent"},
        {"label": "03", "title": "S3", "description": "d"},
    ]
    html = m.render_timeline(title="t", steps=steps, design=d)
    assert 'class="step-meta"' in html and "100%" in html
    # secondary token ("done"→secondary) and accent token color the step circles
    assert d["secondary"] in html and d["accent"] in html


def test_density_timeline_vertical_meta_renders():
    steps = [{"label": str(i), "title": f"S{i}", "description": "d",
              "meta": f"m{i}", "tone": "primary"} for i in range(6)]
    html = m.render_timeline(title="t", steps=steps)
    assert 'class="step-meta"' in html
    assert "m0" in html and "m5" in html
    # vertical layout marker present (6 steps → vertical)
    assert 'class="v-circle"' in html


def test_density_two_column_badges_and_metrics():
    d = design_tokens_for_profile({"primaryColor": "#112233",
                                   "secondaryColor": "#445566"})
    html = m.render_two_column(
        title="비교", left_content="- a", right_content="- x",
        left_badge="AS-IS", right_badge="TO-BE",
        left_metric="32%", right_metric="91%", design=d)
    assert 'class="col-head"' in html
    assert 'class="col-badge"' in html and "AS-IS" in html and "TO-BE" in html
    assert 'class="col-metric"' in html and "32%" in html and "91%" in html
    # tinted with the design tokens (primary for left, secondary for right)
    assert d["primary"] in html and d["secondary"] in html


def test_density_objective_detail_block_caption():
    od = dict(OD_BASE)
    od["blocks"] = [
        {"title": "산출물", "items": ["a", "b"], "caption": "핵심 결과물 정의"},
        {"title": "활동", "items": ["c"]},
    ]
    html = m.render_objective_detail(**od)
    assert 'class="od-block-cap"' in html and "핵심 결과물 정의" in html


def test_density_process_flow_explicit_step_no_and_auto():
    # explicit step_no
    steps = [{"title": "접수", "step_no": "S1"}, {"title": "분석"}, {"title": "완료"}]
    html = m.render_process_flow(title="흐름", steps=steps)
    assert 'class="pf-no"' in html and "S1" in html
    # auto numbering
    html2 = m.render_process_flow(title="흐름",
                                  steps=[{"title": "접수"}, {"title": "완료"}],
                                  step_numbers=True)
    assert 'class="pf-no"' in html2 and "01" in html2 and "02" in html2


def test_density_uses_profile_tokens_not_hardcoded():
    """Density chrome must inherit styleProfile via design tokens, not hardcode."""
    profile = {"primaryColor": "#AB12CD", "secondaryColor": "#12CD34"}
    d = design_tokens_for_profile(profile)
    html = m.render_feature_grid(title="t",
                                 features=[{"icon": "zap", "title": "A",
                                            "description": "d", "badge": "X"}],
                                 design=d)
    # the first card uses the primary token as its accent → badge is tinted with it
    assert "#AB12CD" in html


# ===========================================================================
# (PBT) PROPERTY — absence of new optional fields ⇒ no density markers AND
#        byte-equal to the explicit-no-op-default call, for ANY content.
# ===========================================================================
_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=0, max_size=40,
)


@settings(max_examples=60, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=_text,
    titles=st.lists(_text, min_size=1, max_size=6),
    descs=st.lists(_text, min_size=1, max_size=6),
    subtitle=_text,
    use_step_badges_default=st.booleans(),
)
def test_pbt_feature_grid_preservation(title, titles, descs, subtitle,
                                       use_step_badges_default):
    """For ANY feature content lacking badge/meta, output has no density markers
    and equals the explicit step_badges=False call."""
    features = [
        {"icon": "circle", "title": t, "description": d}
        for t, d in zip(titles, descs)
    ] or [{"icon": "circle", "title": "x", "description": "y"}]

    base = m.render_feature_grid(title=title, features=features, subtitle=subtitle)
    # explicit no-op default must match the implicit default exactly
    explicit = m.render_feature_grid(title=title, features=features,
                                     subtitle=subtitle, step_badges=False)
    assert base == explicit
    assert _density_markers_in(base) == []
    _assert_valid_html(base)
    # the core grid markup always survives
    assert 'class="card"' in base and 'class="card-icon"' in base


@settings(max_examples=40, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
@given(
    title=_text,
    labels=st.lists(_text, min_size=1, max_size=7),
    titles=st.lists(_text, min_size=1, max_size=7),
)
def test_pbt_timeline_preservation(title, labels, titles):
    """For ANY timeline steps lacking tone/status/meta, output has no density
    markers (both horizontal and vertical orientations)."""
    n = min(len(labels), len(titles))
    if n == 0:
        return
    steps = [{"label": labels[i], "title": titles[i], "description": ""}
             for i in range(n)]
    html = m.render_timeline(title=title, steps=steps)
    assert _density_markers_in(html) == []
    _assert_valid_html(html)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
