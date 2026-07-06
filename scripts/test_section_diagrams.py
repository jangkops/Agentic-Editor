"""사용자 시나리오 재현 — '프로젝트 구조 분석 보고서' 같은 다중 섹션 문서가
섹션별로 다른 다이어그램(tree/architecture/stack/flow)을 갖는지 검증.
"""
import sys, os, asyncio, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_engine'))

from server import (
    _force_generate_from_text,
    _classify_section_diagram,
    _resolve_local_root,
    _resolve_relative_for_verify,
)

PROJECT = os.path.join(tempfile.gettempdir(), 'ae_section_diag_test')
os.makedirs(PROJECT + '/.generated', exist_ok=True)


async def main():
    # 분류기 단위 테스트
    print('=== 섹션 분류기 ===')
    cases = [
        ("프로젝트 개요", "본 프로젝트는 웹 애플리케이션입니다."),
        ("디렉토리 구조 분석", "/src/components, /src/services, /config, /docs"),
        ("시스템 아키텍처", "프레젠테이션 계층은 React 기반.\n애플리케이션 계층은 Express.\n데이터 계층은 PostgreSQL."),
        ("기술 스택", "| 계층 | 기술 |\n|---|---|\n| 프론트엔드 | React 18 |\n| 백엔드 | Node.js |"),
        ("개발 가이드라인", "코드 품질 관리. 브랜치 전략. 테스트 전략."),
    ]
    for h, b in cases:
        kind, _ = _classify_section_diagram(h, b)
        print(f'  "{h}" → {kind or "(none)"}')

    # 통합 — _force_generate_from_text가 섹션별 다른 이미지를 생성하는지
    print('\n=== _force_generate_from_text 통합 ===')
    final_text = """# 프로젝트 구조 분석 보고서

## 프로젝트 개요
본 프로젝트는 현대적인 웹 애플리케이션입니다.

## 디렉토리 구조 분석
프로젝트의 디렉토리 구조는 `/src`, `/config`, `/docs`, `/tests`로 구분됩니다.
`/src` 디렉토리는 `/components`, `/services`, `/utils`, `/models` 으로 세분화됩니다.

## 시스템 아키텍처
프레젠테이션 계층: React 기반 SPA, Redux Toolkit
애플리케이션 계층: Node.js + Express RESTful API
데이터 계층: PostgreSQL + MongoDB
캐시 계층: Redis 분산 캐시

## 기술 스택
| 계층 | 기술 | 버전 |
|------|------|------|
| 프론트엔드 | React | 18.2.0 |
| 백엔드 | Node.js | 18.17.0 |
| 데이터베이스 | PostgreSQL | 15.3 |
"""

    out = await _force_generate_from_text(
        primary_tool="generate_pdf",
        target_files=[".generated/test-multi-section.pdf"],
        title="프로젝트 구조 분석 보고서 PDF 생성",
        description="프로젝트 구조 분석 — 다이어그램, 흐름도, 도식화 포함",
        final_text=final_text,
        project_path=PROJECT,
        aws_profile="bedrock-gw",
        bedrock_user="",
    )
    print(f'\n생성된 파일 수: {len(out)}')
    for rel, info in out:
        abs_p = info.get('absPath') or _resolve_relative_for_verify(rel, PROJECT)
        if os.path.isfile(abs_p):
            print(f'  {rel} ({info.get("size", 0)} bytes, model={info.get("model","?")})')

    # 생성된 다이어그램 PNG 종류 확인
    gen_dir = os.path.join(_resolve_local_root(PROJECT), '.generated')
    print(f'\n.generated/ 안 native 이미지:')
    diag_files = sorted([f for f in os.listdir(gen_dir) if f.startswith('native-')])
    for f in diag_files:
        path = os.path.join(gen_dir, f)
        # 파일명에서 종류 추출
        kind = f.split('-')[1]
        print(f'  {f} ({os.path.getsize(path)} bytes) — kind={kind}')

    # 종류 다양성 검증
    kinds = set(f.split('-')[1] for f in diag_files)
    print(f'\n다이어그램 종류 다양성: {len(kinds)}종 — {sorted(kinds)}')
    if len(kinds) >= 2:
        print('OK: 섹션별로 다른 다이어그램이 생성됨')
    else:
        print('WARN: 모든 섹션에 같은 종류만 생성 — 분류기 점검 필요')


asyncio.run(main())
