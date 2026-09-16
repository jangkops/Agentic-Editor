"""/health 와 /api/reset-cache 가 노출하는 boot_id — 메인 프로세스의 SidecarWatcher 가 사이드카
재기동(인스턴스 교체)을 감지해 자격증명을 즉시 재주입하는 근거다. 프로세스 수명 동안 고정이어야 한다.
"""
import re

from starlette.testclient import TestClient

import ai_engine.server as s


def test_health_exposes_a_stable_boot_id():
    client = TestClient(s.app)
    a = client.get("/health").json()
    b = client.get("/health").json()
    assert re.fullmatch(r"[0-9a-f]{32}", a["boot_id"])
    assert a["boot_id"] == b["boot_id"] == s._BOOT_ID
    assert a["status"] == "ok"


def test_reset_cache_reports_the_same_boot_id():
    client = TestClient(s.app)
    r = client.post("/api/reset-cache", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["boot_id"] == s._BOOT_ID
