"""/api/models 자격증명 해석 — 렌더러가 비밀을 보내지 않아도 메인이 /api/reset-cache 로 주입한
카탈로그 자격증명을 쓰고, body 의 명시 자격증명이 있으면 그것을 우선하며, 둘 다 없으면 None
(boto3 프로파일 폴백)이어야 한다.
"""
import ai_engine.server as s


def test_body_credentials_take_precedence(monkeypatch):
    monkeypatch.setattr(s, "_CATALOG_CREDS", {"p": {"accessKeyId": "INJ", "secretAccessKey": "x", "sessionToken": "", "region": "us-west-2"}})
    body = {"profile": "p", "accessKeyId": "BODY", "secretAccessKey": "y"}
    profile, creds = s._resolve_catalog_credentials(body, "default")
    assert profile == "p" and creds is body


def test_injected_catalog_credentials_used_when_body_has_none(monkeypatch):
    inj = {"accessKeyId": "INJ", "secretAccessKey": "x", "sessionToken": "t", "region": "us-west-2"}
    monkeypatch.setattr(s, "_CATALOG_CREDS", {"bedrock-gw": inj})
    profile, creds = s._resolve_catalog_credentials({"profile": "bedrock-gw", "bedrockUser": "alice"}, "default")
    assert profile == "bedrock-gw" and creds is inj
    # GET(body 없음) 도 query profile 로 주입 자격증명을 찾는다
    profile, creds = s._resolve_catalog_credentials(None, "bedrock-gw")
    assert creds is inj


def test_no_credentials_anywhere_falls_back_to_profile(monkeypatch):
    monkeypatch.setattr(s, "_CATALOG_CREDS", {})
    profile, creds = s._resolve_catalog_credentials({"profile": "q"}, "default")
    assert profile == "q" and creds is None
    profile, creds = s._resolve_catalog_credentials("not-a-dict", "default")
    assert profile == "default" and creds is None
