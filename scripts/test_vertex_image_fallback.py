"""Regression — generate_image 직접 호출이 Bedrock 실패 시 Vertex로 폴백.

대상: ai_engine.server._tool_generate_image / _try_vertex_image_single
배경: Vertex 고품질 폴백이 _force_generate_from_text에만 있어, GPT가 직접 부른
      generate_image는 Bedrock 실패 시 이미지를 못 만들었다. 이제 직접 도구도
      Vertex(Nano Banana/Imagen)로 폴백한다.

실행: pytest scripts/test_vertex_image_fallback.py -q
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

server = pytest.importorskip("ai_engine.server")

# 1x1 PNG (유효 바이트)
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class _FakeVertexClient:
    enabled = True

    async def generate(self, prompt, model_class="image_generation_high_quality",
                       aspect_ratio="16:9", negative_prompt="", num_images=1, timeout=60):
        return {"images": [base64.b64encode(_PNG_1x1).decode()], "model": "vertex/nano-banana-pro"}


def _install_fake_vertex(monkeypatch):
    import ai_engine.vertex_image_module as vmod
    monkeypatch.setattr(vmod, "get_vertex_image_client", lambda aws_profile="": _FakeVertexClient())


def test_vertex_fallback_on_circuit_broken(monkeypatch, tmp_path):
    _install_fake_vertex(monkeypatch)
    # 회로를 강제로 차단 상태로
    import time as _t
    server._IMAGE_GEN_CIRCUIT["disabled_at"] = _t.time()
    server._IMAGE_GEN_CIRCUIT["ttl"] = 300
    try:
        out = asyncio.run(server._tool_generate_image(
            {"prompt": "project flow diagram"}, str(tmp_path), aws_profile="bedrock-gw"))
        obj = json.loads(out)
        assert obj.get("via") == "vertex", f"Vertex 폴백 안 됨: {obj}"
        assert obj.get("path", "").endswith(".png")
        assert os.path.isfile(obj["absPath"]) and os.path.getsize(obj["absPath"]) > 0
    finally:
        server._IMAGE_GEN_CIRCUIT["disabled_at"] = 0


def test_no_vertex_when_disabled(monkeypatch, tmp_path):
    # Vertex 비활성(enabled=False) → 폴백 안 하고 기존 회로 차단 에러 반환
    import ai_engine.vertex_image_module as vmod

    class _Disabled:
        enabled = False
        async def generate(self, *a, **k):
            return {"error": "vertex-disabled"}

    monkeypatch.setattr(vmod, "get_vertex_image_client", lambda aws_profile="": _Disabled())
    import time as _t
    server._IMAGE_GEN_CIRCUIT["disabled_at"] = _t.time()
    server._IMAGE_GEN_CIRCUIT["ttl"] = 300
    try:
        out = asyncio.run(server._tool_generate_image(
            {"prompt": "x"}, str(tmp_path), aws_profile="bedrock-gw"))
        obj = json.loads(out)
        assert obj.get("error") == "circuit-breaker"
    finally:
        server._IMAGE_GEN_CIRCUIT["disabled_at"] = 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
