"""Regression test — Amazon 이미지 모델 id 해석 (no us. prefix).

진단 엔드포인트(/api/debug/image-gen-status) 실측 결과:
  - amazon.nova-canvas-v1:0  → 'us.amazon.nova-canvas-v1:0 is not on /invoke allowlist'
  - amazon.titan-image-generator-v2:0 → ResourceNotFoundException

원인: _resolve_callable_model_id 가 list_foundation_models 캐시 누락 시
'알 수 없음' 기본 분기로 빠져 us. prefix 를 붙였다. 두 모델은 Bedrock
ON_DEMAND 라 prefix 가 붙으면 안 된다. 본 테스트는 두 모델이 항상 원본
(un-prefixed) id 로 해석됨을 고정한다 (회귀 가드).

Run:
  ./venv/bin/python -m pytest scripts/test_image_model_id_resolution.py -p no:cacheprovider -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine.server import _resolve_callable_model_id  # noqa: E402


def test_nova_canvas_resolves_without_us_prefix():
    # raw 입력 → raw 유지 (boto3/list_foundation_models 호출 전에 반환)
    assert _resolve_callable_model_id("amazon.nova-canvas-v1:0", "p", "u") == "amazon.nova-canvas-v1:0"
    # us. 가 이미 붙어 들어와도 떼어낸다
    assert _resolve_callable_model_id("us.amazon.nova-canvas-v1:0", "p", "u") == "amazon.nova-canvas-v1:0"


def test_titan_image_resolves_without_us_prefix():
    assert _resolve_callable_model_id("amazon.titan-image-generator-v2:0", "p", "u") == "amazon.titan-image-generator-v2:0"
    assert _resolve_callable_model_id("us.amazon.titan-image-generator-v2:0", "p", "u") == "amazon.titan-image-generator-v2:0"


def test_image_guard_does_not_touch_non_image_models():
    # 비이미지 모델은 가드에 걸리지 않는다 (None/빈 입력 안전성만 확인 — 네트워크 없는 경로).
    assert _resolve_callable_model_id("", "p", "u") == ""
    assert _resolve_callable_model_id(None, "p", "u") is None
