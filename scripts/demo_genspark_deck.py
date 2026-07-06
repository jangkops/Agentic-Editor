"""신규 레이아웃(kpi_summary, status_table, objective_detail, process_flow)으로
젠스파크 OKR 덱을 재현해 .generated/genspark-demo-*.html 로 저장.

브라우저로 열어 시각 품질을 직접 확인하기 위한 데모(테스트 아님).
실행: python scripts/demo_genspark_deck.py
"""
from __future__ import annotations
import sys, os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "ai_engine"))

from ai_engine import slide_templates as st

PROFILE = {"primaryColor": "#3B82C4", "secondaryColor": "#2E8B57",
           "accentColor": "#F5A623", "textColor": "#1A2332"}
D = st.design_tokens_for_profile(PROFILE)

slides = []

slides.append(st.render_kpi_summary(
    title="2026 상반기 OKR 진척 보고",
    eyebrow="1:1 PROGRESS REPORT · H1 2026",
    subtitle="Infra Management · 6개 핵심 목표 진행 사항 정리",
    metrics=[
        {"value": "5", "label": "DONE", "sublabel": "목표 완료", "tone": "secondary"},
        {"value": "1", "label": "PROCESSING", "sublabel": "상시 운영 중", "tone": "accent"},
        {"value": "≈92%", "label": "WEIGHTED", "sublabel": "가중치 기준 달성률", "tone": "primary"},
        {"value": "6", "label": "TOTAL", "sublabel": "개인 목표 항목", "tone": "dark"},
    ],
    footer="Confidential: for authorized personnel only", design=D))

slides.append(st.render_status_table(
    title="상반기 OKR 전체 진척 현황",
    subtitle="6개 목표 · 가중치 합계 100% · 5건 완료 / 1건 상시 운영",
    columns=["#", "OBJECTIVE", "가중치", "진척률 / 결과"],
    rows=[
        {"cells": ["01", "과제별 비용 자동 집계 체계 구축", "20%", "대시보드 운영 / 리포트 자동화"],
         "progress": 100, "status": "Done", "status_tone": "secondary"},
        {"cells": ["02", "핵심 인프라 통합 모니터링 체계 구축", "20%", "3개 영역 정상 운영 / Grafana"],
         "progress": 100, "status": "Done", "status_tone": "secondary"},
        {"cells": ["03", "계정 온·오프보딩 자동화", "20%", "MOGAM Account Manager 19단계"],
         "progress": 100, "status": "Done", "status_tone": "secondary"},
        {"cells": ["04", "사용자·모델 단위 AI 사용량 집계", "15%", "Bedrock Agent 아키텍처 + 집계"],
         "progress": 100, "status": "Done", "status_tone": "secondary"},
        {"cells": ["05", "신규 연구 프로젝트 인프라 지원", "10%", "matamouse, bi 운영 전환 완료"],
         "progress": 100, "status": "Done", "status_tone": "secondary"},
        {"cells": ["06", "연구 영향 인프라 이슈 대응", "15%", "상시 운영 · 누적 추적 중"],
         "progress": 50, "status": "Processing", "status_tone": "accent"},
    ],
    footer="Confidential: for authorized personnel only", design=D))

slides.append(st.render_objective_detail(
    title="과제별 비용 자동 집계 체계 구축",
    number="01",
    subtitle="AWS EC2 GPU/CPU 인스턴스 비용을 프로젝트·사용자·인스턴스별로 시각화",
    status="100% DONE", status_tone="secondary",
    meta=[
        {"label": "OBJECTIVE 방향", "value": "비용 예측 · 의사결정 가시성"},
        {"label": "가중치", "value": "20%", "tone": "primary"},
        {"label": "KR 목표치", "value": "월간 비용 리포트 발행"},
    ],
    blocks=[
        {"title": "ETL 파이프라인 + 메트릭 수집 Agent",
         "items": ["인스턴스별 GPU/CPU 사용량 수집 Agent", "사용자/프로젝트 라벨링 로직", "Cost Explorer 연동 ETL"]},
        {"title": "웹 기반 비용 대시보드",
         "items": ["프로젝트/사용자/인스턴스 3축 분해", "월 ~$25,000+ 운영비 가시화", "월간 리포트 생성"]},
    ],
    evidence={"title": "증빙 · 완료 기준",
              "items": ["비용산출 대시보드 URL", "월간 비용 리포트 생성", "9개 챕터 문서화 완료"],
              "note": "수기 취합 없이 과제별 비용 조회 + 월 리포트 자동 생성 → 충족"},
    design=D))

slides.append(st.render_process_flow(
    title="표준 대응 프로세스",
    subtitle="연구 영향 인프라 이슈 대응 · 재발 방지 조치",
    steps=[
        {"title": "요청 접수", "caption": "연구/기획팀 요청 수신"},
        {"title": "원인 분석", "caption": "구성/권한/성능 분류"},
        {"title": "조치 완료", "caption": "설정 변경 · 튜닝"},
        {"title": "사용자 확인", "caption": "Teams/메일 확인"},
        {"title": "재발 방지 기록", "caption": "운영 가이드 반영"},
    ],
    note="증빙: 요청 · 조치 내역 · 사용자 확인 Teams/메일 기록 · 재발 방지 조치 — 누적 건수 집계",
    design=D))

gen = _ROOT / ".generated"
gen.mkdir(parents=True, exist_ok=True)
names = ["01-kpi_summary", "02-status_table", "03-objective_detail", "04-process_flow"]
for name, html in zip(names, slides):
    out = gen / f"genspark-demo-{name}.html"
    out.write_text(html, encoding="utf-8")
    print("wrote", out, f"({len(html)} bytes)")
print("\n브라우저로 위 HTML 파일들을 열어 시각 품질을 확인하세요 (1920x1080 풀블리드).")
