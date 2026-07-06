"""Regression — Genspark급 신규 레이아웃(kpi_summary, status_table)이 유효한
풀블리드 HTML을 생성하고 레지스트리/디스패처에 연결되어 있다.

사용자 요구: 젠스파크가 한 번의 요청으로 만든 OKR 덱(지표 카드 요약 + 진척바·상태배지
테이블)과 동등 이상의 퀄리티. 기존 7개 레이아웃에는 이 두 패턴이 없어 추가했다.

Correctness properties:
  P1. kpi_summary는 1920x1080 슬라이드 + 모든 지표 value/label을 포함한다.
  P2. status_table은 헤더/행/진척바(width:%)/상태 배지를 포함하고 모든 셀 텍스트를 담는다.
  P3. 두 레이아웃 모두 LAYOUT_REGISTRY와 render_layout 디스패처로 호출 가능하다.
  P4. style_profile(디자인 토큰)이 색상으로 전파된다.
  P5. 잘못된 입력(빈 metrics/rows)은 빈 문자열로 안전 폴백한다.

실행: pytest scripts/test_slide_templates_genspark_layouts.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "ai_engine"))

st = pytest.importorskip("ai_engine.slide_templates")


def test_kpi_summary_renders_metrics():
    """P1 — kpi_summary가 풀블리드 + 모든 지표를 렌더."""
    html = st.render_kpi_summary(
        title="2026 상반기 OKR 진척 보고",
        eyebrow="1:1 PROGRESS REPORT",
        subtitle="Infra Management · 6개 핵심 목표",
        metrics=[
            {"value": "5", "label": "DONE", "sublabel": "목표 완료", "tone": "secondary"},
            {"value": "1", "label": "PROCESSING", "sublabel": "상시 운영", "tone": "accent"},
            {"value": "≈92%", "label": "WEIGHTED", "sublabel": "달성률", "tone": "primary"},
            {"value": "6", "label": "TOTAL", "sublabel": "목표 항목", "tone": "dark"},
        ],
        footer="Confidential",
    )
    assert "1920px" in html and "1080px" in html
    for token in ("DONE", "PROCESSING", "≈92%", "달성률", "2026 상반기 OKR 진척 보고"):
        assert token in html, f"누락: {token}"
    # 카드 4개 → grid 4열
    assert "repeat(4,1fr)" in html


def test_status_table_renders_progress_and_badges():
    """P2 — status_table이 진척바(width:%)와 상태 배지를 렌더."""
    html = st.render_status_table(
        title="상반기 OKR 전체 진척 현황",
        subtitle="6개 목표 · 가중치 합계 100%",
        columns=["#", "OBJECTIVE", "가중치", "결과"],
        rows=[
            {"cells": ["01", "과제별 비용 자동 집계", "20%", "대시보드 운영"],
             "progress": 100, "status": "Done", "status_tone": "secondary"},
            {"cells": ["06", "인프라 이슈 대응", "15%", "상시 운영"],
             "progress": 50, "status": "Processing", "status_tone": "accent"},
        ],
    )
    assert "1920px" in html
    # 헤더 + 암묵 열
    for h in ("OBJECTIVE", "가중치", "진척률", "상태"):
        assert f">{h}<" in html, f"헤더 누락: {h}"
    # 진척바 width
    assert "width:100%" in html and "width:50%" in html
    # 상태 배지
    assert "Done" in html and "Processing" in html
    assert "badge" in html
    # 셀 텍스트
    assert "과제별 비용 자동 집계" in html and "인프라 이슈 대응" in html


def test_registered_and_dispatchable():
    """P3 — 레지스트리 등록 + render_layout 디스패처 동작."""
    assert "kpi_summary" in st.LAYOUT_REGISTRY
    assert "status_table" in st.LAYOUT_REGISTRY
    h1 = st.render_layout("kpi_summary", {
        "title": "T", "metrics": [{"value": "9", "label": "X"}]})
    h2 = st.render_layout("status_table", {
        "title": "T", "columns": ["A", "B"],
        "rows": [{"cells": ["1", "2"], "progress": 30}]})
    assert h1 and "1920px" in h1
    assert h2 and "width:30%" in h2


def test_style_profile_color_propagates():
    """P4 — 템플릿 Style_Profile 색이 디자인 토큰으로 전파된다."""
    design = st.design_tokens_for_profile({"primaryColor": "#0B5394"})
    html = st.render_kpi_summary(
        title="T", design=design,
        metrics=[{"value": "1", "label": "L", "tone": "primary"}])
    assert "#0B5394" in html, "Style_Profile primaryColor가 적용되지 않음"


def test_invalid_input_safe_fallback():
    """P5 — 빈/잘못된 입력은 빈 문자열로 안전 폴백(호출부가 네이티브로 폴백)."""
    assert st.render_kpi_summary(title="T", metrics=[]) == ""
    assert st.render_kpi_summary(title="T", metrics="nope") == ""
    assert st.render_status_table(title="T", columns=[], rows=[{"cells": []}]) == ""
    assert st.render_status_table(title="T", columns=["A"], rows=[]) == ""
    # 디스패처도 동일하게 빈 문자열
    assert st.render_layout("kpi_summary", {"title": "T", "metrics": []}) == ""


def test_objective_detail_renders_badge_meta_blocks_evidence():
    """objective_detail — 번호배지/상태/메타/산출물/증빙을 렌더."""
    html = st.render_objective_detail(
        title="과제별 비용 자동 집계 체계 구축",
        number="01",
        subtitle="AWS EC2 GPU/CPU 비용을 프로젝트별로 시각화",
        status="100% DONE",
        status_tone="secondary",
        meta=[
            {"label": "OBJECTIVE 방향", "value": "비용 가시성 확보"},
            {"label": "가중치", "value": "20%", "tone": "primary"},
            {"label": "KR 목표치", "value": "월간 리포트 발행"},
        ],
        blocks=[
            {"title": "ETL 파이프라인 + Agent", "items": ["GPU/CPU 수집 Agent", "Cost Explorer 연동"]},
            {"title": "웹 비용 대시보드", "items": ["3개 축 분해 시각화", "월간 리포트 생성"]},
        ],
        evidence={"title": "증빙 · 완료 기준",
                  "items": ["비용 대시보드 URL", "월간 리포트 자동 생성"],
                  "note": "월 리포트 자동 생성 시 완료 → 충족"},
    )
    assert "1920px" in html
    for token in ("01", "100% DONE", "가중치", "20%", "ETL 파이프라인 + Agent",
                  "웹 비용 대시보드", "증빙 · 완료 기준", "월간 리포트 자동 생성"):
        assert token in html, f"누락: {token}"


def test_process_flow_renders_steps_and_arrows():
    """process_flow — 다크 밴드 + 단계 박스 + 화살표."""
    html = st.render_process_flow(
        title="표준 대응 프로세스",
        steps=[
            {"title": "요청 접수"}, {"title": "원인 분석"},
            {"title": "조치 완료"}, {"title": "사용자 확인"}, {"title": "재발 방지 기록"},
        ],
        note="증빙: 요청·조치 내역 · Teams/메일 기록",
    )
    assert "1920px" in html
    assert html.count('class="pf-box"') == 5
    assert "&#8594;" in html  # arrows between steps
    for token in ("요청 접수", "재발 방지 기록", "표준 대응 프로세스"):
        assert token in html, f"누락: {token}"
    # 문자열 steps도 허용
    h2 = st.render_process_flow(title="T", steps=["A", "B", "C"])
    assert h2 and h2.count('class="pf-box"') == 3


def test_new_detail_layouts_registered_and_safe():
    """objective_detail / process_flow 레지스트리 등록 + 빈 입력 안전 폴백."""
    assert "objective_detail" in st.LAYOUT_REGISTRY
    assert "process_flow" in st.LAYOUT_REGISTRY
    # 디스패처 동작
    assert st.render_layout("process_flow", {"title": "T", "steps": ["A", "B"]})
    assert st.render_layout("objective_detail", {
        "title": "T", "blocks": [{"title": "B", "items": ["x"]}]})
    # 빈 입력 → 빈 문자열
    assert st.render_process_flow(title="T", steps=[]) == ""
    assert st.render_objective_detail(title="T") == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
