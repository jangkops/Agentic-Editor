"""Property test — 모델 선택 보존/복구 로직.

Feature: gateway-openai-models
대상(JS 포팅): src/main.js 의 resolveSelection / catalogSignature 순수 함수.

이 파일은 JS 순수 함수의 동작 계약을 Python으로 충실히 포팅해 Property 8을
검증한다(tasks.md 9.3가 허용하는 "Python 포팅 선택 로직"). 포팅 로직이 JS와
어긋나지 않도록, 규칙을 src/main.js 주석과 1:1로 맞춘다.

Property:
  - Property 8: 모델 갱신은 선택 상태를 보존하거나 유효하게 복구한다 (Req 4.3, 4.5, 4.6)
    · 갱신 후 selectedModel은 항상 다음 카탈로그의 멤버(또는 카탈로그가 비면 None)
    · prevId가 다음 목록에 존재하면 유지
    · 부재 시 채팅 가능 모델(있으면) 선택
    · 동일 카탈로그면 선택 불변

실행: pytest scripts/test_model_selection_property.py -q
"""
from __future__ import annotations

import os
import sys

from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

_HSET = settings(max_examples=200, deadline=None)


# ── src/main.js 순수 함수 포팅 ────────────────────────────────────
def _flatten_catalog(catalog):
    out = []
    if not isinstance(catalog, dict):
        return out
    for p, ms in catalog.items():
        if not isinstance(ms, list):
            continue
        for m in ms:
            if isinstance(m, dict) and m.get("id") is not None:
                mm = dict(m)
                mm["provider"] = p
                out.append(mm)
    return out


def catalog_signature(catalog):
    ids = []
    if isinstance(catalog, dict):
        for ms in catalog.values():
            if not isinstance(ms, list):
                continue
            for m in ms:
                if isinstance(m, dict) and m.get("id") is not None:
                    ids.append(str(m["id"]))
    return "\u0001".join(sorted(set(ids)))


def resolve_selection(prev_id, prev_catalog, next_catalog):
    next_models = _flatten_catalog(next_catalog)
    if catalog_signature(prev_catalog) == catalog_signature(next_catalog):
        keep = next((m for m in next_models if m["id"] == prev_id), None)
        if keep:
            return keep
    still = next((m for m in next_models if m["id"] == prev_id), None)
    if still:
        return still
    chat = [m for m in next_models if isinstance(m.get("capabilities"), dict) and m["capabilities"].get("chat")]
    if chat:
        return chat[0]
    if next_models:
        return next_models[0]
    return None


# ── 전략 ──────────────────────────────────────────────────────────
_id = st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8)


def _model(chat):
    return st.builds(lambda i: {"id": i, "name": i.upper(), "capabilities": {"chat": chat}}, _id)


def _catalog():
    return st.dictionaries(
        keys=st.sampled_from(["OpenAI", "Anthropic", "Amazon"]),
        values=st.lists(st.one_of(_model(True), _model(False)), max_size=4),
        max_size=3,
    )


def _ids(catalog):
    return {m["id"] for m in _flatten_catalog(catalog)}


# Feature: gateway-openai-models, Property 8: 모델 갱신은 선택 상태를 보존하거나 유효하게 복구한다
@_HSET
@given(prev=_catalog(), nxt=_catalog(), prev_id=st.one_of(st.none(), _id))
def test_property8_selection_is_member_or_none(prev, nxt, prev_id):
    sel = resolve_selection(prev_id, prev, nxt)
    if not _flatten_catalog(nxt):
        assert sel is None
    else:
        assert sel is not None
        assert sel["id"] in _ids(nxt)


@_HSET
@given(prev=_catalog(), nxt=_catalog())
def test_property8_existing_prev_id_preserved(prev, nxt):
    next_ids = list(_ids(nxt))
    if not next_ids:
        return
    prev_id = next_ids[0]
    sel = resolve_selection(prev_id, prev, nxt)
    assert sel["id"] == prev_id


@_HSET
@given(cat=_catalog(), prev_id=st.one_of(st.none(), _id))
def test_property8_identical_catalog_is_stable(cat, prev_id):
    # 동일 카탈로그 + prevId가 멤버면 불변
    if prev_id is not None and prev_id in _ids(cat):
        sel = resolve_selection(prev_id, cat, cat)
        assert sel["id"] == prev_id


@_HSET
@given(nxt=_catalog())
def test_property8_absent_prev_picks_chat_model(nxt):
    # prevId가 다음 목록에 없으면 채팅 가능 모델 우선 선택
    flat = _flatten_catalog(nxt)
    if not flat:
        return
    sel = resolve_selection("___absent___", {}, nxt)
    chat = [m for m in flat if m["capabilities"].get("chat")]
    if chat:
        assert sel["capabilities"].get("chat") is True


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
