"""/api/media/pptx-render base64 폴백 — 임시 파일 정리와 스모크 샘플 왕복.

2026-09-22: base64 폴백이 만든 ``ae_pptx_preview_*.pptx`` 를 지우지 않아 미리보기 폴백마다 시스템 임시
폴더에 파일이 쌓였다. 릴리스 CI 스모크가 이 경로로 동결 바이너리의 python-pptx 파서를 검사하게 되면서
함께 고쳤다. 여기서는 (1) 성공·열기 실패 두 경우 모두 임시 파일이 남지 않음 (2) 스모크가 만드는 샘플이
이 엔드포인트에서 제목 그대로 왕복함을 고정한다.
"""

from __future__ import annotations

import base64
import glob
import os
import sys
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import ai_engine.server as s  # noqa: E402
import smoke_frozen_backend as smoke  # noqa: E402


@pytest.fixture()
def isolated_tmp(monkeypatch, tmp_path):
    """NamedTemporaryFile 이 쓰는 임시 폴더를 테스트 전용으로 격리한다(다른 프로세스의 파일과 섞이지 않게)."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def _leftovers(d: Path) -> list[str]:
    return sorted(glob.glob(os.path.join(str(d), "ae_pptx_preview_*.pptx")))


def test_base64_preview_roundtrips_title_and_leaves_no_temp_file(isolated_tmp):
    b64 = smoke.build_sample_pptx_b64("TMP-CLEANUP-TITLE")
    assert b64, "python-pptx 가 venv 에 있어야 한다"

    r = TestClient(s.app).post("/api/media/pptx-render", json={"base64": b64})

    assert r.status_code == 200, r.text
    slides = r.json()["slides"]
    assert slides and slides[0]["title"] == "TMP-CLEANUP-TITLE"
    assert _leftovers(isolated_tmp) == []


def test_unparseable_base64_payload_returns_500_and_leaves_no_temp_file(isolated_tmp):
    garbage = base64.b64encode(b"this is not a zip package").decode("ascii")

    r = TestClient(s.app).post("/api/media/pptx-render", json={"base64": garbage})

    assert r.status_code == 500
    assert "pptx 열기 실패" in r.json()["error"]
    assert _leftovers(isolated_tmp) == []


def test_path_input_is_never_deleted(tmp_path):
    # 사용자가 가리킨 실제 파일은 임시 파일이 아니므로 절대 지우면 안 된다.
    src = tmp_path / "user_deck.pptx"
    src.write_bytes(base64.b64decode(smoke.build_sample_pptx_b64("USER-FILE")))

    r = TestClient(s.app).post("/api/media/pptx-render", json={"path": str(src)})

    assert r.status_code == 200, r.text
    assert r.json()["slides"][0]["title"] == "USER-FILE"
    assert src.exists()


def test_smoke_probe_uses_generated_sample_and_checks_title(monkeypatch):
    """probe_pptx 는 디스크 샘플이 없어도 skip 하지 않고, 응답 제목이 다르면 실패로 판정한다."""
    monkeypatch.setattr(smoke, "_find_sample_pptx", lambda: None)
    sent: list[dict] = []

    def fake_post(url, payload, timeout=30.0):
        sent.append(payload)
        return 200, '{"slides": [{"title": "%s", "bullets": [], "images": []}]}' % smoke.SMOKE_PPTX_TITLE

    monkeypatch.setattr(smoke, "_http_post", fake_post)
    ok = smoke.probe_pptx("http://127.0.0.1:1", creds=False)
    assert ok["ok"] is True and not ok.get("skipped"), ok
    assert "base64" in sent[0] and "path" not in sent[0]

    monkeypatch.setattr(smoke, "_http_post", lambda url, payload, timeout=30.0: (200, '{"slides": [{"title": "WRONG"}]}'))
    bad = smoke.probe_pptx("http://127.0.0.1:1", creds=False)
    assert bad["ok"] is False and not bad.get("skipped")
    assert "제목 왕복 불일치" in bad["error"]
