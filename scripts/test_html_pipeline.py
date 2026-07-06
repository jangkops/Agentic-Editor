"""HTML→PNG 슬라이드 파이프라인 통합 테스트.

사용자 환경에서 슬라이드 시스템이 실제로 호출되는지 진단.
"""
import sys, os, asyncio, json, tempfile, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_engine'))

import bridge_client as bc
from server import (
    _render_html_slide_to_png,
    _generate_html_slide_for_section,
    _get_gw,
    _specialized_model_for_task,
    _resolve_local_root,
)

PROJECT = os.path.join(tempfile.gettempdir(), 'ae_html_pipeline_test')
os.makedirs(PROJECT + '/.generated', exist_ok=True)


async def main():
    print('=== 1) 브리지 상태 ===')
    print(f'  url: {bc._state.get("url")}')
    print(f'  active: {bc.is_active()}')

    print()
    print('=== 2) 직접 HTML → PNG 캡쳐 ===')
    test_html = """<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;font-family:system-ui;background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);height:100vh;display:flex;align-items:center;justify-content:center;color:#fff}
.card{padding:80px;background:rgba(255,255,255,0.12);border-radius:24px;backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,0.2)}
h1{font-size:64px;margin:0 0 16px;font-weight:800}
p{font-size:24px;margin:0;opacity:.9}
</style></head><body><div class="card"><h1>HTML 슬라이드 테스트</h1><p>Genspark/Gamma 수준 — Electron Chromium</p></div></body></html>"""

    out_path = os.path.join(PROJECT, '.generated', 'test-direct-html.png')
    res = await _render_html_slide_to_png(
        html=test_html,
        output_path=out_path,
        width=1920,
        height=1080,
        timeout=15,
    )
    print(f'  결과: {res}')
    if res and res.get('ok') and os.path.isfile(out_path):
        print(f'  OK: 직접 HTML 캡쳐 정상 ({os.path.getsize(out_path)} bytes)')
    else:
        print(f'  FAIL: HTML 캡쳐 실패 — Electron이 안 돌고 있거나 bridge가 한쪽 방향만 뜸')
        return

    print()
    print('=== 3) LLM 레이아웃 픽 + HTML 렌더 (전체 파이프라인) ===')
    aws_profile = os.environ.get('AWS_PROFILE', 'bedrock-gw')
    bedrock_user = os.environ.get('BEDROCK_USER', '')
    try:
        gw = _get_gw(aws_profile, bedrock_user)
    except Exception as e:
        print(f'  gateway init 실패: {e}')
        return
    model_id = _specialized_model_for_task(
        'file_generation', '',
        aws_profile=aws_profile, bedrock_user=bedrock_user,
    )
    print(f'  사용 모델: {model_id}')

    rel = await _generate_html_slide_for_section(
        gw, model_id,
        section_heading='기술 스택',
        section_body='React 18, TypeScript 5, Node.js 18, Express 4, PostgreSQL 15, Redis 7',
        doc_context='프로젝트 아키텍처 분석 보고서',
        project_path=PROJECT,
    )
    if rel:
        abs_p = os.path.join(_resolve_local_root(PROJECT), rel)
        print(f'  OK: {rel} ({os.path.getsize(abs_p)} bytes)')
        # 프로젝트 .generated/로 복사 — 사용자가 패널에서 확인 가능
        target_dir = '.generated'
        os.makedirs(target_dir, exist_ok=True)
        import shutil
        target = os.path.join(target_dir, 'sample-html-slide-stack.png')
        shutil.copy2(abs_p, target)
        print(f'  → 프로젝트 미리보기: {target}')
    else:
        print(f'  FAIL: LLM이 레이아웃 fit 못 찾았거나 렌더 실패')


asyncio.run(main())
