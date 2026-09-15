"""SSRF 가드 — fetch_url_raw 가 사설·루프백·메타데이터·차단 호스트로는 네트워크를 호출하지
않고, 리다이렉트도 hop 마다 다시 검사하는지 확인한다. httpx.AsyncClient 는 가짜로 대체해
실제 네트워크는 0이다.
"""
import asyncio

import pytest

from ai_engine.research import backend


def _resolver(mapping):
    def _res(host, port, *a, **k):
        if host not in mapping:
            raise OSError("nxdomain")
        return [(2, 1, 6, "", (ip, port)) for ip in mapping[host]]
    return _res


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8765/health",          # 이 앱의 사이드카
    "http://169.254.169.254/latest/meta-data",  # 클라우드 메타데이터
    "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.9/",
    "http://[::1]/", "http://[fe80::1]/", "http://0.0.0.0/",
    "http://localhost/", "http://svc.internal/", "http://box.local/",
    "ftp://example.com/", "file:///etc/passwd", "javascript:alert(1)", "",
])
def test_blocked_targets(url):
    allowed, reason = backend.url_egress_allowed(url, resolver=_resolver({}))
    assert allowed is False
    assert reason in {"unsupported_scheme", "blocked_host", "blocked_address", "invalid_url"}


def test_public_literal_and_public_dns_allowed():
    assert backend.url_egress_allowed("https://93.184.216.34/", resolver=_resolver({})) == (True, "")
    res = _resolver({"example.com": ["93.184.216.34"]})
    assert backend.url_egress_allowed("https://example.com/x", resolver=res) == (True, "")


def test_dns_to_private_is_blocked_and_dns_failure_is_allowed():
    res = _resolver({"evil.example": ["93.184.216.34", "10.0.0.7"]})
    assert backend.url_egress_allowed("https://evil.example/", resolver=res) == (False, "blocked_address")
    # 해석 실패는 "알 수 없음" → 허용(httpx 도 같은 이유로 실패해 우회는 열리지 않는다).
    assert backend.url_egress_allowed("https://nxdomain.example/", resolver=_resolver({})) == (True, "")


class _FakeResp:
    def __init__(self, status, headers=None, body=b"<html><body>hi</body></html>"):
        self.status_code = status
        self.headers = headers or ({"content-type": "text/html"} if status == 200 else {})
        self.content = body
        self.text = body.decode("utf-8", "replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise backend.httpx.HTTPStatusError("err", request=None, response=None)


class _FakeClient:
    """상태코드 스크립트를 순서대로 돌려주는 가짜 AsyncClient. get() 호출 URL 을 기록한다."""
    calls = []
    script = []

    def __init__(self, *a, **k):
        assert k.get("follow_redirects") is False, "리다이렉트는 수동 검증을 위해 꺼야 한다"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        _FakeClient.calls.append(url)
        return _FakeClient.script.pop(0)


@pytest.fixture
def fake_httpx(monkeypatch):
    monkeypatch.setattr(backend, "web_research_enabled", lambda *a, **k: True)
    monkeypatch.setattr(backend.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(backend, "_RESOLVER", _resolver({"example.com": ["93.184.216.34"], "evil.example": ["10.0.0.7"]}))
    _FakeClient.calls.clear()
    _FakeClient.script.clear()
    return _FakeClient


def _run(coro):
    return asyncio.run(coro)


def test_fetch_blocked_url_makes_no_request(fake_httpx):
    r = _run(backend.fetch_url_raw("http://127.0.0.1:8765/health", timeout=1.0, max_chars=1000))
    assert r.ok is False and r.error == "egress_blocked"
    assert fake_httpx.calls == []


def test_fetch_public_url_ok(fake_httpx):
    fake_httpx.script.append(_FakeResp(200))
    r = _run(backend.fetch_url_raw("https://example.com/a", timeout=1.0, max_chars=1000))
    assert r.ok is True and "hi" in r.text
    assert fake_httpx.calls == ["https://example.com/a"]


def test_redirect_to_private_is_blocked_at_the_hop(fake_httpx):
    fake_httpx.script.append(_FakeResp(302, {"location": "http://evil.example/"}))
    r = _run(backend.fetch_url_raw("https://example.com/a", timeout=1.0, max_chars=1000))
    assert r.ok is False and r.error == "egress_blocked"
    assert fake_httpx.calls == ["https://example.com/a"]      # 내부 주소로는 요청이 나가지 않았다


def test_redirect_chain_is_followed_and_capped(fake_httpx):
    fake_httpx.script.extend([_FakeResp(301, {"location": "/b"}), _FakeResp(200)])
    r = _run(backend.fetch_url_raw("https://example.com/a", timeout=1.0, max_chars=1000))
    assert r.ok is True
    assert fake_httpx.calls == ["https://example.com/a", "https://example.com/b"]

    fake_httpx.calls.clear()
    fake_httpx.script.extend([_FakeResp(302, {"location": f"/{i}"}) for i in range(7)])
    r = _run(backend.fetch_url_raw("https://example.com/a", timeout=1.0, max_chars=1000))
    assert r.ok is False and r.error == "too_many_redirects"
