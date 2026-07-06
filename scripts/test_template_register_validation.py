"""단위 테스트: Template_Manager.register_template 검증 순서 + 산출물 미생성.

Feature: pptx-template-styling, Task 2.3 (등록 검증 순서 단위 테스트)
설계 §구성요소 1(등록 검증 순서) / 요구사항 1.2, 1.3, 1.4, 1.6, 1.7

register_template(file_path, name, store_root)는 짧은 검사를 먼저 수행하고 디스크
쓰기를 마지막에 한 번에 수행한다(부분 산출물 방지). 본 테스트는 각 에러 분기가
올바른 error 코드를 반환하고, *검증 실패 시 Template_Store
(`{store_root}/templates/`)에 어떤 산출물 디렉토리도 생성되지 않음*을 확인한다.

실제 코드(ai_engine/template_manager.py)의 검증 순서:
  1. python-pptx import 불가          → missing-dep        (9.3)
  2. 이름 trim 길이 ∉ [1,100]         → invalid-name       (1.2, 1.7)
  3. 파일 크기 > 50MB                 → template-too-large (1.4)
  4. 확장자 .pptx AND 열림 실패       → invalid-template   (1.3)
  5. store_root 결정 불가             → no-storage-root    (2.4)
  6. 이름 중복(trim + casefold)       → duplicate-name     (1.6)
  7. 디스크 쓰기 → 성공 {templateId, name, path, layoutCount}

50MB 파일은 실제로 50MB를 쓰지 않고 sparse 파일(seek + 1바이트 write)로 만든다.
register_template은 os.path.getsize()로만 크기를 보므로 logical size만 크면 된다.
no-storage-root는 유효 .pptx + 유효 이름이 5번 검사까지 통과해야 도달하므로,
resolve_template_store_root를 None 반환으로 patch해 그 분기를 격리한다.

python-pptx 미설치 시 전체 테스트를 SKIP 한다(유효 .pptx fixture를 만들 수 없으므로).

실행:
  ai_engine/.venv/bin/python scripts/test_template_register_validation.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from unittest.mock import patch

# repo 루트를 import 경로에 추가 (기존 scripts/ 테스트 컨벤션 미러링).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine.template_manager import (  # noqa: E402
    TEMPLATES_SUBDIR,
    _MAX_TEMPLATE_BYTES,
    register_template,
)

# python-pptx 가용 여부 — 없으면 유효 .pptx fixture를 만들 수 없으므로 전체 SKIP.
try:
    from pptx import Presentation  # noqa: E402

    _HAS_PPTX = True
except ImportError:  # pragma: no cover - 환경 의존
    _HAS_PPTX = False


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

def _make_valid_pptx(path: str) -> str:
    """python-pptx로 최소 유효 .pptx를 생성해 경로를 반환한다."""
    Presentation().save(path)
    return path


def _make_sparse_oversize_pptx(path: str) -> str:
    """50MB를 초과하는 sparse .pptx를 만든다(실제 디스크 사용 없이 logical size만 큼)."""
    with open(path, "wb") as fh:
        fh.seek(_MAX_TEMPLATE_BYTES)  # 마지막 바이트 위치로 이동
        fh.write(b"\0")  # → 파일 크기 = _MAX_TEMPLATE_BYTES + 1 (> 50MB)
    assert os.path.getsize(path) > _MAX_TEMPLATE_BYTES
    return path


def _templates_dir(store_root: str) -> str:
    return os.path.join(store_root, TEMPLATES_SUBDIR)


def _artifact_dir_count(store_root: str) -> int:
    """`{store_root}/templates/` 하위 디렉토리(=등록 산출물) 개수. 없으면 0."""
    tdir = _templates_dir(store_root)
    if not os.path.isdir(tdir):
        return 0
    return sum(
        1 for e in os.listdir(tdir) if os.path.isdir(os.path.join(tdir, e))
    )


def _assert_no_artifacts(store_root: str, msg: str) -> None:
    """검증 실패 후 Template_Store에 어떤 산출물 디렉토리도 없어야 한다."""
    count = _artifact_dir_count(store_root)
    assert count == 0, f"{msg}: 산출물 디렉토리가 {count}개 생성됨 (0이어야 함)"


# ---------------------------------------------------------------------------
# 1. invalid-name (요구사항 1.2, 1.7)
# ---------------------------------------------------------------------------

def test_invalid_name_too_short() -> None:
    """trim 후 길이 0(공백만/빈 문자열) → invalid-name, 산출물 미생성."""
    store_root = tempfile.mkdtemp(prefix="tmpl_inv_name_short_")
    try:
        valid = _make_valid_pptx(os.path.join(store_root, "src.pptx"))
        for bad_name in ("", "   ", "\t\n  "):
            res = register_template(valid, bad_name, store_root=store_root)
            assert res.get("error") == "invalid-name", (
                f"name={bad_name!r} → {res!r} (invalid-name 기대)"
            )
            assert res.get("allowed") == [1, 100], res
            _assert_no_artifacts(store_root, f"invalid-name(short, {bad_name!r})")
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


def test_invalid_name_too_long() -> None:
    """trim 후 길이 100 초과(101자) → invalid-name, 산출물 미생성."""
    store_root = tempfile.mkdtemp(prefix="tmpl_inv_name_long_")
    try:
        valid = _make_valid_pptx(os.path.join(store_root, "src.pptx"))
        # 앞뒤 공백을 둬도 trim 후 101자이면 위반이어야 한다.
        long_name = "  " + ("가" * 101) + "  "
        res = register_template(valid, long_name, store_root=store_root)
        assert res.get("error") == "invalid-name", res
        assert res.get("allowed") == [1, 100], res
        _assert_no_artifacts(store_root, "invalid-name(long)")
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


def test_valid_name_boundary_100_registers() -> None:
    """경계값: trim 후 정확히 100자는 유효 → 등록 성공(검증 분기가 과하게 막지 않음 확인)."""
    store_root = tempfile.mkdtemp(prefix="tmpl_name_100_")
    try:
        valid = _make_valid_pptx(os.path.join(store_root, "src.pptx"))
        name_100 = "a" * 100
        res = register_template(valid, name_100, store_root=store_root)
        assert "error" not in res, f"100자 이름은 유효해야 함: {res!r}"
        assert res.get("name") == name_100, res
        assert _artifact_dir_count(store_root) == 1, "성공 시 산출물 1개 생성"
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. template-too-large (요구사항 1.4)
# ---------------------------------------------------------------------------

def test_template_too_large() -> None:
    """50MB 초과 파일 → template-too-large, 산출물 미생성."""
    store_root = tempfile.mkdtemp(prefix="tmpl_too_large_")
    try:
        big = _make_sparse_oversize_pptx(os.path.join(store_root, "big.pptx"))
        res = register_template(big, "큰 템플릿", store_root=store_root)
        assert res.get("error") == "template-too-large", res
        assert res.get("maxBytes") == _MAX_TEMPLATE_BYTES, res
        _assert_no_artifacts(store_root, "template-too-large")
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 3. invalid-template (요구사항 1.3)
# ---------------------------------------------------------------------------

def test_invalid_template_wrong_extension() -> None:
    """확장자가 .pptx가 아니면 → invalid-template, 산출물 미생성."""
    store_root = tempfile.mkdtemp(prefix="tmpl_inv_ext_")
    try:
        txt = os.path.join(store_root, "deck.txt")
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write("나는 pptx가 아닙니다")
        res = register_template(txt, "확장자 위반", store_root=store_root)
        assert res.get("error") == "invalid-template", res
        _assert_no_artifacts(store_root, "invalid-template(extension)")
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


def test_invalid_template_disguised_pptx() -> None:
    """.pptx 확장자지만 python-pptx로 열 수 없는 텍스트 위장 파일 → invalid-template."""
    store_root = tempfile.mkdtemp(prefix="tmpl_disguised_")
    try:
        fake = os.path.join(store_root, "fake.pptx")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write("이건 진짜 pptx(zip) 구조가 아닌 평문입니다")
        res = register_template(fake, "위장 템플릿", store_root=store_root)
        assert res.get("error") == "invalid-template", res
        _assert_no_artifacts(store_root, "invalid-template(disguised)")
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 4. duplicate-name (요구사항 1.6)
# ---------------------------------------------------------------------------

def test_duplicate_name_preserves_existing_and_adds_none() -> None:
    """동일 이름(trim+casefold) 재등록 → duplicate-name, 기존 산출물 보존, 새 산출물 미생성."""
    store_root = tempfile.mkdtemp(prefix="tmpl_dup_")
    try:
        src = _make_valid_pptx(os.path.join(store_root, "src.pptx"))

        # 1차 등록 성공.
        first = register_template(src, "회사 표준 템플릿", store_root=store_root)
        assert "error" not in first, f"1차 등록은 성공해야 함: {first!r}"
        first_id = first["templateId"]
        assert _artifact_dir_count(store_root) == 1, "1차 등록 후 산출물 1개"

        # 2차 등록: trim + casefold 동일(앞뒤 공백 + 대소문자만 다른 변형 포함).
        for dup_name in ("  회사 표준 템플릿  ", "회사 표준 템플릿"):
            res = register_template(src, dup_name, store_root=store_root)
            assert res.get("error") == "duplicate-name", (
                f"name={dup_name!r} → {res!r} (duplicate-name 기대)"
            )
            # 기존 산출물 보존 + 새 산출물 미생성: 디렉토리 개수가 여전히 1.
            assert _artifact_dir_count(store_root) == 1, (
                f"중복 등록 후 산출물 개수 변동 ({dup_name!r})"
            )
            # 기존 templateId 디렉토리가 그대로 존재.
            assert os.path.isdir(
                os.path.join(_templates_dir(store_root), first_id)
            ), "기존 템플릿 디렉토리가 보존되어야 함"
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


def test_duplicate_name_casefold_ascii() -> None:
    """ASCII 대소문자 차이만 있는 이름도 casefold 비교로 중복 처리된다."""
    store_root = tempfile.mkdtemp(prefix="tmpl_dup_case_")
    try:
        src = _make_valid_pptx(os.path.join(store_root, "src.pptx"))
        first = register_template(src, "MyTemplate", store_root=store_root)
        assert "error" not in first, first
        res = register_template(src, "mytemplate", store_root=store_root)
        assert res.get("error") == "duplicate-name", res
        assert _artifact_dir_count(store_root) == 1, "casefold 중복 후 산출물 1개 유지"
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. no-storage-root (요구사항 2.4)
# ---------------------------------------------------------------------------

def test_no_storage_root() -> None:
    """store_root 결정 불가 → no-storage-root.

    유효 .pptx + 유효 이름은 5번 검사(store_root 결정)까지 통과해야 이 분기에 도달한다.
    register_template은 store_root 인자가 falsy면 resolve_template_store_root()를
    호출하므로, 그 함수를 None 반환으로 patch해 '결정 불가' 상황을 격리한다.
    store_root가 None이면 어떤 경로도 조립되지 않으므로 산출물 디렉토리도 생기지 않는다.
    """
    src_root = tempfile.mkdtemp(prefix="tmpl_no_root_src_")
    try:
        src = _make_valid_pptx(os.path.join(src_root, "src.pptx"))
        with patch(
            "ai_engine.template_manager.resolve_template_store_root",
            return_value=None,
        ):
            # store_root=None → 내부에서 patch된 resolver 호출 → None → no-storage-root.
            res = register_template(src, "정상 이름", store_root=None)
        assert res.get("error") == "no-storage-root", res
        # src_root(원본 디렉토리)에는 templates/ 산출물이 생기면 안 된다.
        _assert_no_artifacts(src_root, "no-storage-root")
    finally:
        shutil.rmtree(src_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

TESTS = [
    ("invalid-name: trim 길이 0", test_invalid_name_too_short),
    ("invalid-name: trim 길이 >100", test_invalid_name_too_long),
    ("name 경계 100자는 등록 성공", test_valid_name_boundary_100_registers),
    ("template-too-large: >50MB", test_template_too_large),
    ("invalid-template: 확장자 비-.pptx", test_invalid_template_wrong_extension),
    ("invalid-template: 텍스트 위장 .pptx", test_invalid_template_disguised_pptx),
    ("duplicate-name: 보존 + 새 산출물 미생성", test_duplicate_name_preserves_existing_and_adds_none),
    ("duplicate-name: ASCII casefold", test_duplicate_name_casefold_ascii),
    ("no-storage-root: store_root 결정 불가", test_no_storage_root),
]


def main() -> int:
    print("=== Task 2.3: register_template 검증 순서 단위 테스트 ===")
    if not _HAS_PPTX:
        print("SKIP: python-pptx 미설치 — 유효 .pptx fixture를 만들 수 없어 전체 스킵")
        return 0

    failures = []
    for label, fn in TESTS:
        print(f"[test] {label} ...", end=" ", flush=True)
        try:
            fn()
            print("OK")
        except AssertionError as e:
            print("FAIL")
            failures.append((label, e))
        except Exception as e:  # noqa: BLE001 - 예기치 못한 오류도 표면화
            print("ERROR")
            failures.append((label, e))

    print()
    if failures:
        print(f"FAILED: {len(failures)} of {len(TESTS)} tests")
        for label, e in failures:
            print(f"  - {label}: {e}")
        return 1
    print(f"PASSED: all {len(TESTS)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
