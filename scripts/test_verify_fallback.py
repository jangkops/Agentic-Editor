"""verified_files lookup이 도구가 fallback 경로(_resolve_local_root)에 저장한 파일을
정확히 찾아내는지 검증. 5명 에이전트 0건 실패 버그 회귀 방지용.
"""
import sys, os, asyncio, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_engine'))

from server import (
    _tool_generate_pdf,
    _tool_generate_pptx,
    _tool_generate_xlsx,
    _tool_generate_docx,
    _resolve_local_root,
    _resolve_relative_for_verify,
)


async def main():
    # project_path를 일부러 쓰기 불가능한 곳으로 — _resolve_local_root가 fallback 경로를 사용하도록 강제
    fake_project = '/private/var/empty'  # 읽기 전용
    real_root = _resolve_local_root(fake_project)
    print(f'project_path(fake)={fake_project}')
    print(f'_resolve_local_root → {real_root}')

    # PDF 생성
    res = await _tool_generate_pdf({
        'title': '검증 테스트',
        'sections': [{'heading': '테스트', 'body': '경로 검증 테스트입니다.'}],
    }, fake_project)
    parsed = json.loads(res)
    if 'error' in parsed:
        print(f'FAIL: PDF generation error: {parsed}')
        return
    rel_path = parsed['path']
    print(f'PDF saved with rel_path: {rel_path}')

    # 검증 헬퍼가 같은 fake project_path로도 파일을 찾을 수 있어야 함
    found_abs = _resolve_relative_for_verify(rel_path, fake_project)
    print(f'verify helper resolved to: {found_abs}')
    print(f'file exists: {os.path.isfile(found_abs)}')

    if os.path.isfile(found_abs):
        print(f'OK: verify helper finds files saved in fallback root')
    else:
        print(f'FAIL: verify helper missed the file')

    # cleanup
    try:
        if os.path.isfile(found_abs):
            os.unlink(found_abs)
    except Exception:
        pass


asyncio.run(main())
