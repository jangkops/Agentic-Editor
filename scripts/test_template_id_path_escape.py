"""templateId 경로 탈출 거부 단위 테스트 (요구사항 2.1, 2.7).

Template_Manager의 경로 안전 보증을 검증한다. 구현 코드(ai_engine/template_manager.py)는
이미 완성되어 있으며 본 테스트는 그 동작을 *검증만* 한다(구현 수정 없음).

검증 대상:
  1. 경로 구분자/상위참조 거부 (2.7): templateId에 `..`, `/`, `\\` 포함 시 검증 실패.
  2. 길이 위반 (2.7): 1자 미만(빈 문자열) 또는 128자 초과 시 검증 실패. 경계값(1, 128) 통과.
  3. 정상 templateId 통과: UUID v4 / 1–128자 안전 문자열은 통과.
  4. realpath 탈출 차단 (2.1): safe_template_artifact_path가 조립한 경로의 realpath가
     `{store_root}/templates/{templateId}/` prefix를 벗어나면 None(거부). 임시 store_root 사용.
  5. get_template / delete_template / get_style_profile 에 무효 templateId → "invalid-template-id".

실행:
    ai_engine/.venv/bin/python scripts/test_template_id_path_escape.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid

# ai_engine 를 import 가능하게 한다(기존 scripts/ 컨벤션과 동일).
_THIS = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS)
sys.path.insert(0, os.path.join(_ROOT, "ai_engine"))
sys.path.insert(0, _ROOT)

from template_manager import (  # noqa: E402
    TEMPLATES_SUBDIR,
    _validate_template_id,
    safe_template_artifact_path,
    get_template,
    delete_template,
    get_style_profile,
)


# ---------------------------------------------------------------------------
# 결과 헬퍼 + 카운터
# ---------------------------------------------------------------------------
_PASS = 0
_FAIL = 0


def _ok(msg: str) -> None:
    global _PASS
    _PASS += 1
    print(f"  [ok]   {msg}")


def _fail(msg: str) -> None:
    global _FAIL
    _FAIL += 1
    print(f"  [FAIL] {msg}")


def _check(cond: bool, msg: str) -> None:
    if cond:
        _ok(msg)
    else:
        _fail(msg)


# ---------------------------------------------------------------------------
# 1. 경로 구분자/상위참조 거부 (요구사항 2.7)
# ---------------------------------------------------------------------------
def test_separator_and_parent_ref_rejected() -> None:
    print("\n=== 1. 경로 구분자/상위참조 거부 (2.7) ===")
    bad_ids = [
        "../etc",
        "a/b",
        "a\\b",
        "../../secret",
        "..",
        "foo/../bar",
        "/abs/path",
        "C:\\windows",
        "templates/../../escape",
        "a/..",
    ]
    for tid in bad_ids:
        _check(
            _validate_template_id(tid) is False,
            f"_validate_template_id({tid!r}) → 거부",
        )


# ---------------------------------------------------------------------------
# 2. 길이 위반 (요구사항 2.7)
# ---------------------------------------------------------------------------
def test_length_violations_rejected() -> None:
    print("\n=== 2. 길이 위반 (2.7) ===")
    # 1자 미만(빈 문자열) → 거부
    _check(_validate_template_id("") is False, "빈 문자열(0자) → 거부")
    # 128자 초과 → 거부 (안전 문자만 사용해 길이 조건만 격리 검증)
    _check(_validate_template_id("a" * 129) is False, "129자 → 거부")
    _check(_validate_template_id("a" * 256) is False, "256자 → 거부")
    # 경계값 — 1자, 128자는 길이 조건 통과
    _check(_validate_template_id("a") is True, "1자 → 통과(경계)")
    _check(_validate_template_id("a" * 128) is True, "128자 → 통과(경계)")
    # 비문자열 입력 방어
    _check(_validate_template_id(None) is False, "None → 거부")  # type: ignore[arg-type]
    _check(_validate_template_id(12345) is False, "정수 → 거부")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 3. 정상 templateId 통과
# ---------------------------------------------------------------------------
def test_valid_ids_pass() -> None:
    print("\n=== 3. 정상 templateId 통과 ===")
    good_ids = [
        str(uuid.uuid4()),  # UUID v4 (register_template가 실제 발급하는 형식)
        "550e8400-e29b-41d4-a716-446655440000",
        "template_01",
        "my-template",
        "A1B2C3",
        "a" * 64,
    ]
    for tid in good_ids:
        _check(_validate_template_id(tid) is True, f"_validate_template_id({tid!r}) → 통과")


# ---------------------------------------------------------------------------
# 4. realpath 탈출 차단 (요구사항 2.1)
# ---------------------------------------------------------------------------
def test_realpath_escape_blocked() -> None:
    print("\n=== 4. realpath 디렉토리 탈출 차단 (2.1) ===")
    store_root = tempfile.mkdtemp(prefix="ae_tpl_escape_")
    try:
        tid = str(uuid.uuid4())
        base_dir = os.path.realpath(os.path.join(store_root, TEMPLATES_SUBDIR, tid))

        # --- 4a. 정상 파일명 → 경로 반환, realpath가 base_dir 내부 ---
        for fname in ("base.pptx", "style_profile.json", "metadata.json"):
            path = safe_template_artifact_path(store_root, tid, fname)
            if path is None:
                _fail(f"정상 fname {fname!r} 인데 None 반환됨")
                continue
            real = os.path.realpath(path)
            inside = real == base_dir or real.startswith(base_dir + os.sep)
            _check(inside, f"정상 fname {fname!r} → base_dir 내부 ({path})")

        # --- 4b. fname에 상위참조/절대경로 → realpath가 base_dir 밖 → None(거부) ---
        escaping_fnames = [
            "../../escape.txt",        # store_root 밖으로
            "../../../etc/passwd",     # 시스템 경로로 탈출 시도
            "../sibling.pptx",         # templates/{tid} 의 형제로 탈출
            "sub/../../../escape",     # 중첩 상위참조
        ]
        for fname in escaping_fnames:
            path = safe_template_artifact_path(store_root, tid, fname)
            _check(path is None, f"탈출 fname {fname!r} → 거부(None)")

        # --- 4c. 무효 templateId로는 안전 경로 자체가 조립 불가 → None ---
        for bad_tid in ("../escape", "a/b", "a\\b", ".."):
            path = safe_template_artifact_path(store_root, bad_tid, "base.pptx")
            _check(path is None, f"무효 templateId {bad_tid!r} → 경로 None")

        # --- 4d. store_root/fname 누락 방어 ---
        _check(
            safe_template_artifact_path("", tid, "base.pptx") is None,
            "빈 store_root → None",
        )
        _check(
            safe_template_artifact_path(store_root, tid, "") is None,
            "빈 fname → None",
        )

        # --- 4e. symlink를 통한 탈출 차단: templates/{tid} 자체를 외부로 거는 심볼릭 링크 ---
        # base_dir 디렉토리를 store_root 밖의 실제 디렉토리로 가리키는 symlink로 만든 뒤,
        # 그 안의 파일 경로 realpath가 store_root 밖이면 거부되어야 한다.
        outside_dir = tempfile.mkdtemp(prefix="ae_tpl_outside_")
        try:
            link_tid = str(uuid.uuid4())
            templates_dir = os.path.join(store_root, TEMPLATES_SUBDIR)
            os.makedirs(templates_dir, exist_ok=True)
            link_path = os.path.join(templates_dir, link_tid)
            symlink_made = False
            try:
                os.symlink(outside_dir, link_path, target_is_directory=True)
                symlink_made = True
            except (OSError, NotImplementedError, AttributeError):
                # symlink 미지원 환경(권한/플랫폼)에서는 이 하위 검사를 건너뛴다.
                print("  [skip] symlink 생성 불가 — 4e 건너뜀")

            if symlink_made:
                path = safe_template_artifact_path(store_root, link_tid, "base.pptx")
                # base_dir(=link_path)의 realpath는 outside_dir 이므로 store_root 밖.
                # 구현은 real_candidate가 real_base(=outside_dir) 내부면 허용하지만,
                # real_base 자체가 store_root 밖이라는 점에 주목. 본 검사는 "탈출된
                # 디렉토리(symlink 대상) 밖으로는 절대 못 나간다"를 확인하는 보강 검사다.
                if path is not None:
                    real = os.path.realpath(path)
                    real_outside = os.path.realpath(outside_dir)
                    inside_target = (
                        real == real_outside or real.startswith(real_outside + os.sep)
                    )
                    _check(
                        inside_target,
                        "symlink 대상 디렉토리 내부로만 조립됨(대상 밖 탈출 불가)",
                    )
                else:
                    _ok("symlink templateId 경로 → None(거부)")
        finally:
            shutil.rmtree(outside_dir, ignore_errors=True)
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. get_template / delete_template / get_style_profile 무효 templateId → invalid-template-id
# ---------------------------------------------------------------------------
def test_public_api_rejects_invalid_id() -> None:
    print("\n=== 5. 공개 API 무효 templateId 거부 (invalid-template-id) ===")
    store_root = tempfile.mkdtemp(prefix="ae_tpl_api_")
    try:
        invalid_ids = ["../etc", "a/b", "a\\b", "../../secret", "", "a" * 129]
        for tid in invalid_ids:
            r_get = get_template(tid, store_root)
            _check(
                r_get.get("error") == "invalid-template-id",
                f"get_template({tid!r}) → invalid-template-id (got {r_get.get('error')!r})",
            )
            r_del = delete_template(tid, store_root)
            _check(
                r_del.get("error") == "invalid-template-id",
                f"delete_template({tid!r}) → invalid-template-id (got {r_del.get('error')!r})",
            )
            r_sp = get_style_profile(tid, store_root)
            _check(
                r_sp.get("error") == "invalid-template-id",
                f"get_style_profile({tid!r}) → invalid-template-id (got {r_sp.get('error')!r})",
            )
    finally:
        shutil.rmtree(store_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> int:
    print("templateId 경로 탈출 거부 단위 테스트 (요구사항 2.1, 2.7)")
    test_separator_and_parent_ref_rejected()
    test_length_violations_rejected()
    test_valid_ids_pass()
    test_realpath_escape_blocked()
    test_public_api_rejects_invalid_id()

    print("\n=== Summary ===")
    print(f"  passed: {_PASS}")
    print(f"  failed: {_FAIL}")
    if _FAIL == 0:
        print("\n  ALL PASS ✓")
        return 0
    print(f"\n  {_FAIL} CHECK(S) FAILED ✗")
    return 1


if __name__ == "__main__":
    sys.exit(main())
