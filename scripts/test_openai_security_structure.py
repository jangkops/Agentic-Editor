"""Security structure checks — OpenAI 통합 (요구사항 9.1, 9.2, 9.3, 9.5).

Feature: gateway-openai-models

정적 소스 검사로 보안 제약을 못박는다:
  - OpenAI 호출은 게이트웨이(execute-api) 경유만 — OpenAI/Anthropic SDK 미사용
  - OpenAI 라우트는 self.gateway_url 기반 URL 사용
  - OpenAI_Catalog_File 경로는 userData 하위(AE_GENERATED_ROOT / ~/.agentic-editor)
  - 카탈로그 소스 선택이 자격증명(accessKey/secret)을 요구·저장하지 않음

실행: pytest scripts/test_openai_security_structure.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_AI = os.path.join(_ROOT, "ai_engine")


def _read(name):
    with open(os.path.join(_AI, name), "r", encoding="utf-8") as f:
        return f.read()


def test_no_openai_or_anthropic_sdk_imports():
    for fn in ("openai_adapter.py", "openai_catalog.py"):
        src = _read(fn)
        assert "import openai" not in src, f"{fn}: OpenAI SDK import 금지"
        assert "from openai" not in src, f"{fn}: OpenAI SDK import 금지"
        assert "import anthropic" not in src, f"{fn}: Anthropic SDK import 금지"


def test_openai_routes_use_gateway_url():
    src = _read("gateway_module.py")
    # OpenAI 라우트는 gateway_url 기반으로 구성되어야 한다(직접 openai.com 호출 금지)
    assert '{self.gateway_url}/openai/responses' in src
    assert "api.openai.com" not in src, "OpenAI 직접 엔드포인트 호출 금지"


def test_catalog_file_path_under_userdata():
    from ai_engine import openai_catalog as oc

    path = oc._default_catalog_path()
    root_env = os.environ.get("AE_GENERATED_ROOT", "").strip()
    expected_root = root_env or os.path.expanduser("~/.agentic-editor")
    assert os.path.abspath(path).startswith(os.path.abspath(expected_root)), (
        f"카탈로그 경로가 userData 하위가 아님: {path}"
    )
    assert path.endswith(os.path.join("openai", "openai_catalog.json"))


def test_get_catalog_source_does_not_require_credentials():
    from ai_engine import openai_catalog as oc

    # 자격증명 키 없이도 소스를 만들 수 있어야 하며(소스 B), 자격증명을 읽지 않는다
    src = oc.get_catalog_source({})
    assert hasattr(src, "list_models")
    # 카탈로그 소스 선택 로직 소스에 자격증명 키 참조가 없어야 함
    code = _read("openai_catalog.py")
    for forbidden in ("accessKeyId", "secretAccessKey", "AWS_SECRET", "secret_key"):
        assert forbidden not in code, f"카탈로그 모듈에 자격증명 키({forbidden}) 참조 금지"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
