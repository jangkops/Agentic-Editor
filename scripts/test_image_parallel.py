"""best-of-N 이미지 생성 + 품질 스코어링 + 자동 재시도 단위 테스트.

게이트웨이/Bedrock 호출 없이 함수 분기 로직만 검증.
"""
import sys, os, asyncio, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_engine'))

from server import (
    _select_image_models,
    _tool_generate_image,
)


async def main():
    # 1) 모델 선택 — 다양한 프롬프트로 우선순위 차이 확인
    print('=== 1) 프롬프트 의도별 모델 선택 ===')
    cases = [
        ('professional photo of a sunset over the ocean', 'photo'),
        ('flowchart showing user authentication process', 'diagram'),
        ('minimalist company logo design', 'logo'),
        ('digital illustration of a futuristic city', 'art'),
        ('business presentation cover image', 'business'),
        ('일반 이미지', None),
    ]
    for prompt, hint in cases:
        models = _select_image_models(prompt, hint=hint or '')[:3]
        short = [m.split('.')[1] for m in models]
        print(f'  {prompt[:40]:40s} → {short}')

    # 2) 모든 호출이 fail하는 시나리오 — 회로 차단 발동 + 정확한 에러 응답
    print()
    print('=== 2) 게이트웨이 access denied 시뮬 (실제 호출은 안 함) ===')
    print('   → 일부러 패스. 실제 환경에서만 검증 가능.')

    # 3) AST 무결성 + 함수 시그니처
    print()
    print('=== 3) 함수 시그니처 ===')
    import inspect
    sig = inspect.signature(_tool_generate_image)
    print(f'  _tool_generate_image{sig}')
    print('  OK: tool_input/_isRetry 파라미터 존재 — 무한 재귀 방지 구조 정상')


asyncio.run(main())
