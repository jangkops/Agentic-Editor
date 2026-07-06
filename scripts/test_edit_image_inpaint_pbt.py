"""Property 4: edit_image inpaint 입력 유효성 검증 (hypothesis 기반).

mode='inpaint'일 때, 어떤 입력이든 다음 위반 사항이 있으면 정확한 에러 코드가
반환되어야 한다:
  - mode != 'inpaint'/'outpaint'         → "invalid-mode"
  - image_path 가 존재하지 않는 경로     → "file-not-found"
  - 이미지 포맷이 PNG/JPEG가 아님        → "invalid-image"
  - 파일 크기가 5MB 초과                 → "invalid-image"
  - mask_path 가 존재하지 않는 경로      → "mask-not-found"
  - mask 해상도가 원본과 불일치          → "mask-dimension-mismatch"
  - prompt 가 비어있거나 > 512자         → "invalid-parameter"

**Validates: Requirements 2.2, 2.8, 2.9**

실행:
    ai_engine/.venv/bin/python scripts/test_edit_image_inpaint_pbt.py
"""

import asyncio
import json
import os
import sys
import tempfile

# ai_engine 패키지를 상위 디렉토리에서 import 가능하게
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from hypothesis import HealthCheck, assume, given, settings, strategies as st  # noqa: E402
from PIL import Image  # noqa: E402

from ai_engine.server import _tool_edit_image  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures (module-level: 한번만 생성)
# ---------------------------------------------------------------------------

_FIXTURE_DIR = tempfile.mkdtemp(prefix="edit_image_pbt_")
_VALID_IMG_PNG = os.path.join(_FIXTURE_DIR, "valid_64.png")
_VALID_MASK_PNG = os.path.join(_FIXTURE_DIR, "valid_mask_64.png")
_INVALID_FORMAT_BMP = os.path.join(_FIXTURE_DIR, "invalid.bmp")
_VALID_FORMAT_WEBP = os.path.join(_FIXTURE_DIR, "valid.webp")
_HUGE_PNG_FILE = os.path.join(_FIXTURE_DIR, "huge.png")


def _make_png(path: str, w: int, h: int) -> None:
    Image.new("RGB", (w, h), (255, 0, 0)).save(path, "PNG")


def _make_jpeg(path: str, w: int, h: int) -> None:
    Image.new("RGB", (w, h), (0, 255, 0)).save(path, "JPEG")


def _make_webp(path: str, w: int, h: int) -> None:
    Image.new("RGB", (w, h), (0, 0, 255)).save(path, "WEBP")


def _make_bmp(path: str, w: int, h: int) -> None:
    Image.new("RGB", (w, h), (128, 128, 128)).save(path, "BMP")


def _setup_fixtures() -> None:
    _make_png(_VALID_IMG_PNG, 64, 64)
    _make_png(_VALID_MASK_PNG, 64, 64)
    _make_bmp(_INVALID_FORMAT_BMP, 64, 64)
    _make_webp(_VALID_FORMAT_WEBP, 64, 64)
    # 5MB 초과 파일: PNG 매직 바이트 + 6MB 임의 데이터
    # (서버는 매직만 보고 fmt='png'로 판단, 그 다음 사이즈 체크에서 거부)
    with open(_HUGE_PNG_FILE, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(os.urandom(6 * 1024 * 1024))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_inpaint_input() -> dict:
    """Returns a baseline INPAINT input where every field is valid.

    Each property test mutates exactly one field to violate a single rule.
    """
    return {
        "mode": "inpaint",
        "image_path": _VALID_IMG_PNG,
        "mask_path": _VALID_MASK_PNG,
        "prompt": "make the apple red",
    }


def _call(tool_input: dict) -> dict:
    """Run async _tool_edit_image and parse JSON response."""
    raw = asyncio.run(_tool_edit_image(tool_input, project_path=""))
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# 'inpaint'/'outpaint'가 아닌 임의 mode 문자열
_invalid_mode_strategy = (
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=20)
    .filter(lambda s: s not in ("inpaint", "outpaint"))
)

# 절대경로지만 존재하지 않는 파일 경로 (충돌 방지를 위해 _FIXTURE_DIR 하위 사용)
_nonexistent_filename = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_", min_size=4, max_size=24
).map(lambda s: os.path.join(_FIXTURE_DIR, f"_does_not_exist_{s}.png"))

# 마스크용 width/height — 단, 64x64는 valid 케이스이므로 assume으로 제외
_dim = st.integers(min_value=1, max_value=128)

# prompt 길이 위반 (> 512)
_overlong_prompt = st.integers(min_value=513, max_value=2048).map(lambda n: "a" * n)

# 빈/공백 prompt
_blank_prompt = st.sampled_from(["", " ", "   ", "\t", "\n", "  \t \n  "])


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------

@given(mode=_invalid_mode_strategy)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_invalid_mode_returns_invalid_mode(mode: str) -> None:
    inp = _base_inpaint_input()
    inp["mode"] = mode
    out = _call(inp)
    assert out.get("error") == "invalid-mode", f"mode={mode!r} → {out}"


@given(path=_nonexistent_filename)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_nonexistent_image_returns_file_not_found(path: str) -> None:
    assume(not os.path.exists(path))
    inp = _base_inpaint_input()
    inp["image_path"] = path
    out = _call(inp)
    assert out.get("error") == "file-not-found", f"path={path!r} → {out}"


@given(path=_nonexistent_filename)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_nonexistent_mask_returns_mask_not_found(path: str) -> None:
    assume(not os.path.exists(path))
    inp = _base_inpaint_input()
    inp["mask_path"] = path
    out = _call(inp)
    assert out.get("error") == "mask-not-found", f"mask_path={path!r} → {out}"


@given(prompt=_overlong_prompt)
@settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_overlong_prompt_rejected(prompt: str) -> None:
    assert len(prompt) > 512  # sanity
    inp = _base_inpaint_input()
    inp["prompt"] = prompt
    out = _call(inp)
    assert out.get("error") == "invalid-parameter", f"len={len(prompt)} → {out}"


@given(prompt=_blank_prompt)
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_blank_prompt_rejected(prompt: str) -> None:
    inp = _base_inpaint_input()
    inp["prompt"] = prompt
    out = _call(inp)
    assert out.get("error") == "invalid-parameter", f"prompt={prompt!r} → {out}"


@given(mw=_dim, mh=_dim)
@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def prop_mask_dimension_mismatch(mw: int, mh: int) -> None:
    """원본 64x64 와 다른 해상도 마스크 → mask-dimension-mismatch."""
    assume((mw, mh) != (64, 64))
    mask_path = os.path.join(_FIXTURE_DIR, f"mask_{mw}x{mh}.png")
    if not os.path.exists(mask_path):
        _make_png(mask_path, mw, mh)
    inp = _base_inpaint_input()
    inp["mask_path"] = mask_path
    out = _call(inp)
    assert out.get("error") == "mask-dimension-mismatch", f"mask={mw}x{mh} → {out}"


# ---------------------------------------------------------------------------
# Concrete (non-randomized) cases — 특정 위반 1건당 한 번씩만 검증해도 충분
# ---------------------------------------------------------------------------

def case_invalid_format_bmp() -> None:
    inp = _base_inpaint_input()
    inp["image_path"] = _INVALID_FORMAT_BMP
    out = _call(inp)
    assert out.get("error") == "invalid-image", f"BMP → {out}"


def case_inpaint_rejects_webp() -> None:
    inp = _base_inpaint_input()
    inp["image_path"] = _VALID_FORMAT_WEBP
    out = _call(inp)
    # WEBP는 outpaint에서는 OK지만 inpaint에서는 invalid-image
    assert out.get("error") == "invalid-image", f"WEBP for inpaint → {out}"


def case_image_exceeds_5mb() -> None:
    inp = _base_inpaint_input()
    inp["image_path"] = _HUGE_PNG_FILE
    out = _call(inp)
    assert out.get("error") == "invalid-image", f">5MB → {out}"


def case_jpeg_accepted_through_validation() -> None:
    """JPEG는 inpaint에서 허용되어야 한다(검증 통과 → 모델 호출까지 진행)."""
    jpg_path = os.path.join(_FIXTURE_DIR, "valid_64.jpg")
    _make_jpeg(jpg_path, 64, 64)
    inp = _base_inpaint_input()
    inp["image_path"] = jpg_path
    # 우리는 모델 호출 결과까지 가지 않도록 mask_path를 굳이 valid로 둠
    out = _call(inp)
    # 검증 통과 시 모델 호출 단계로 진입 — 실제 환경에서는 게이트웨이/모델 에러가
    # 발생하므로 'model-unavailable' 또는 유효한 결과가 나와야 한다.
    # 어떤 경우에도 위에서 다룬 검증 에러 코드는 나오면 안 됨.
    forbidden = {
        "invalid-mode",
        "file-not-found",
        "invalid-image",
        "mask-not-found",
        "mask-dimension-mismatch",
        "invalid-parameter",
    }
    assert out.get("error") not in forbidden, (
        f"JPEG inpaint 검증이 잘못 거부됨: {out}"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    _setup_fixtures()

    print("=== Property 4: edit_image inpaint 입력 유효성 검증 ===")
    print(f"fixtures dir: {_FIXTURE_DIR}\n")

    properties = [
        ("invalid mode → invalid-mode", prop_invalid_mode_returns_invalid_mode),
        ("nonexistent image → file-not-found", prop_nonexistent_image_returns_file_not_found),
        ("nonexistent mask → mask-not-found", prop_nonexistent_mask_returns_mask_not_found),
        ("prompt > 512 → invalid-parameter", prop_overlong_prompt_rejected),
        ("blank prompt → invalid-parameter", prop_blank_prompt_rejected),
        ("mask dimension mismatch → mask-dimension-mismatch", prop_mask_dimension_mismatch),
    ]

    cases = [
        ("BMP rejected (invalid-image)", case_invalid_format_bmp),
        ("WEBP rejected for inpaint (invalid-image)", case_inpaint_rejects_webp),
        ("file >5MB rejected (invalid-image)", case_image_exceeds_5mb),
        ("valid JPEG passes validation stage", case_jpeg_accepted_through_validation),
    ]

    failures: list[tuple[str, BaseException]] = []

    for name, fn in properties:
        try:
            fn()
            print(f"  PASS  property: {name}")
        except BaseException as e:  # hypothesis raises Falsified, etc.
            print(f"  FAIL  property: {name}")
            print(f"        {type(e).__name__}: {e}")
            failures.append((name, e))

    for name, fn in cases:
        try:
            fn()
            print(f"  PASS  case: {name}")
        except BaseException as e:
            print(f"  FAIL  case: {name}")
            print(f"        {type(e).__name__}: {e}")
            failures.append((name, e))

    if failures:
        print(f"\n=== {len(failures)} FAILURE(S) ===")
        return 1

    print("\nAll properties hold ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
