"""단위 테스트: _resolve_active_template 활성 템플릿 해석 폴백.

Feature: pptx-template-styling, Task 10.2 (활성 템플릿 해석 폴백 단위 테스트)
설계 §구성요소 6 / 요구사항 5.2, 5.3, 5.4, 5.5

대상: ai_engine/server.py 의 이미 구현된
  _resolve_active_template(template_id, store_root) → (template_path, style_profile, used)

검증 대상 분기(설계 §구성요소 6):
  - template_id 없음/""              → (None, None, False)            (요구사항 5.2)
  - template-not-found(미등록 유효 ID) → (None, None, False)            (요구사항 5.4)
  - base.pptx 부재(로드 실패)         → (None, None, False)            (요구사항 5.5)
  - style_profile.json 손상(로드 실패) → (None, None, False)            (요구사항 5.5)
  - 정상                              → (abs_path, profile_dict, True) (요구사항 5.3)

fixture: python-pptx로 최소 유효 .pptx를 만들고 register_template로 임시 store_root에
등록한다. python-pptx 또는 server import 불가 시 전체 SKIP 한다. store_root는 모두
임시 디렉토리를 사용하며 _resolve_active_template에 직접 전달한다(resolve_template_store_root
미사용 — 테스트 격리).

실행:
  ai_engine/.venv/bin/python scripts/test_resolve_active_template.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

# repo 루트를 import 경로에 추가 (기존 scripts/ 테스트 컨벤션 미러링).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# python-pptx 가용 여부 — 없으면 유효 .pptx fixture를 만들 수 없으므로 전체 SKIP.
try:
    from pptx import Presentation  # noqa: E402

    _HAS_PPTX = True
except ImportError:  # pragma: no cover - 환경 의존
    _HAS_PPTX = False

if not _HAS_PPTX:
    print("SKIP: python-pptx 미설치 — 유효 .pptx fixture 생성 불가")
    sys.exit(0)

# server import 실패 시(무거운 의존성 등) 전체 SKIP — 기존 테스트 컨벤션과 동일.
try:
    from ai_engine.server import _resolve_active_template  # noqa: E402
except Exception as e:  # pragma: no cover - server import 실패 시 skip
    print(f"SKIP: ai_engine.server import 실패 ({e})")
    sys.exit(0)

from ai_engine.template_manager import (  # noqa: E402
    TEMPLATES_SUBDIR,
    register_template,
)
from ai_engine.style_profile import STYLE_PROFILE_KEY_ORDER  # noqa: E402


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _make_valid_pptx(path: str) -> str:
    """python-pptx로 최소 유효 .pptx를 생성해 경로를 반환한다."""
    Presentation().save(path)
    return path


def _register_valid_template(store_root: str, name: str = "표준 템플릿") -> str:
    """임시 store_root에 유효 .pptx를 등록하고 templateId를 반환한다."""
    src = _make_valid_pptx(os.path.join(store_root, "src.pptx"))
    res = register_template(src, name, store_root=store_root)
    assert "error" not in res, f"fixture 등록 실패: {res!r}"
    return res["templateId"]


def _artifact_path(store_root: str, template_id: str, fname: str) -> str:
    return os.path.join(store_root, TEMPLATES_SUBDIR, template_id, fname)


# ---------------------------------------------------------------------------
# 1. 빈/None template_id (요구사항 5.2)
# ---------------------------------------------------------------------------

def test_empty_template_id_no_template() -> None:
    """template_id == "" → (None, None, False). store_root 사용조차 하지 않는다."""
    store_root = tempfile.mkdtemp(prefix="tmpl_empty_id_")
    try:
        result = _resolve_active_template("", store_root)
        assert result == (None, None, False), f'"" → {result!r} ((None,None,False) 기대)'
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


def test_none_template_id_no_template() -> None:
    """template_id is None → (None, None, False) (무템플릿 — 기존 동작 보존)."""
    store_root = tempfile.mkdtemp(prefix="tmpl_none_id_")
    try:
        result = _resolve_active_template(None, store_root)
        assert result == (None, None, False), f"None → {result!r} ((None,None,False) 기대)"
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. template-not-found — 미등록 유효 형식 ID (요구사항 5.4)
# ---------------------------------------------------------------------------

def test_template_not_found_no_template() -> None:
    """등록되지 않은 유효 형식 templateId → (None, None, False), 예외 없음."""
    store_root = tempfile.mkdtemp(prefix="tmpl_not_found_")
    try:
        # UUID v4 형식이지만 Template_Store에 등록되지 않은 ID.
        unknown_id = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        result = _resolve_active_template(unknown_id, store_root)
        assert result == (None, None, False), (
            f"미등록 ID → {result!r} ((None,None,False) 기대)"
        )
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. 로드 실패 (요구사항 5.5)
# ---------------------------------------------------------------------------

def test_missing_base_pptx_falls_back() -> None:
    """등록 후 base.pptx 삭제 → 기준 .pptx 로드 실패 → (None, None, False)."""
    store_root = tempfile.mkdtemp(prefix="tmpl_no_base_")
    try:
        template_id = _register_valid_template(store_root)
        base_path = _artifact_path(store_root, template_id, "base.pptx")
        assert os.path.isfile(base_path), "fixture: base.pptx 가 존재해야 함"
        os.remove(base_path)  # 기준 .pptx 부재 상황 구성
        result = _resolve_active_template(template_id, store_root)
        assert result == (None, None, False), (
            f"base.pptx 부재 → {result!r} ((None,None,False) 기대)"
        )
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


def test_corrupt_style_profile_falls_back() -> None:
    """등록 후 style_profile.json 손상 → Style_Profile 로드 실패 → (None, None, False)."""
    store_root = tempfile.mkdtemp(prefix="tmpl_corrupt_sp_")
    try:
        template_id = _register_valid_template(store_root)
        sp_path = _artifact_path(store_root, template_id, "style_profile.json")
        assert os.path.isfile(sp_path), "fixture: style_profile.json 이 존재해야 함"
        # 손상된(파싱 불가) 내용으로 덮어쓴다 → get_style_profile 이 invalid-json 반환.
        with open(sp_path, "w", encoding="utf-8") as fh:
            fh.write("{ 이건 valid json 이 아닙니다 ::: ")
        result = _resolve_active_template(template_id, store_root)
        assert result == (None, None, False), (
            f"손상 style_profile → {result!r} ((None,None,False) 기대)"
        )
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. 정상 (요구사항 5.3)
# ---------------------------------------------------------------------------

def test_valid_template_returns_path_and_profile() -> None:
    """정상 등록 → (abs_path, profile_dict, True). abs_path 절대경로+실존, profile 7토큰 dict."""
    store_root = tempfile.mkdtemp(prefix="tmpl_valid_")
    try:
        template_id = _register_valid_template(store_root)
        template_path, profile, used = _resolve_active_template(template_id, store_root)

        # used 플래그.
        assert used is True, f"정상 템플릿 → used={used!r} (True 기대)"

        # 절대 경로 + 실제 파일 존재.
        assert isinstance(template_path, str) and template_path, "templatePath 가 비어 있음"
        assert os.path.isabs(template_path), f"절대 경로가 아님: {template_path!r}"
        assert os.path.isfile(template_path), f"실제 파일이 아님: {template_path!r}"
        assert template_path.endswith("base.pptx"), (
            f"base.pptx 를 가리켜야 함: {template_path!r}"
        )

        # profile 은 7토큰 dict.
        assert isinstance(profile, dict), f"profile 이 dict 가 아님: {type(profile)!r}"
        assert "error" not in profile, f"profile 에 error 가 있음: {profile!r}"
        for key in STYLE_PROFILE_KEY_ORDER:
            assert key in profile, f"profile 에 '{key}' 키 누락: {profile!r}"
            assert isinstance(profile[key], str) and profile[key], (
                f"profile['{key}'] 가 비어 있음: {profile!r}"
            )
        assert len(STYLE_PROFILE_KEY_ORDER) == 7, "Style_Profile 은 7토큰이어야 함"
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 러너
# ---------------------------------------------------------------------------

def _run_all() -> int:
    tests = [
        ("empty template_id → no-template (5.2)", test_empty_template_id_no_template),
        ("None template_id → no-template (5.2)", test_none_template_id_no_template),
        ("template-not-found → no-template (5.4)", test_template_not_found_no_template),
        ("missing base.pptx → no-template (5.5)", test_missing_base_pptx_falls_back),
        ("corrupt style_profile → no-template (5.5)", test_corrupt_style_profile_falls_back),
        ("valid template → (abs_path, profile, True) (5.3)",
         test_valid_template_returns_path_and_profile),
    ]
    failures = 0
    for label, fn in tests:
        try:
            fn()
            print(f"PASS: {label}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL: {label}\n      {exc}")
        except Exception as exc:  # noqa: BLE001 - 예기치 못한 오류도 실패로 보고
            failures += 1
            print(f"ERROR: {label}\n      {type(exc).__name__}: {exc}")
    total = len(tests)
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
