"""Visual_Comparator — 우리 HTML 출력과 참조 PNG를 나란히 비교하는 PNG를 생성한다.

헤르메틱: Chrome 헤드리스로 로컬 파일만 렌더(네트워크 0)하고, Pillow로 두 이미지를
가로 side-by-side 합성한다. LLM/게이트웨이/Vertex 호출 없음.

`scripts/demo_design_ceiling_vs_genspark.py`의 `_html_to_png` 패턴(Chrome
`--headless=new`, `--window-size=1920,1080`, `--screenshot`)을 재사용한다.

출력: `.generated/_design_compare/<out_png>` (요구사항 5.7)
입력 누락 시: PNG 미생성 + ValueError (요구사항 5.9)
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# 비교 PNG 출력 폴더 (Generated_Folder) — 요구사항 5.7
OUT_DIR = os.path.join(_ROOT, ".generated", "_design_compare")

# Chrome 바이너리 경로 — demo_design_ceiling_vs_genspark.py 와 동일 상수 재사용
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 렌더 캔버스 크기 (1920×1080)
_RENDER_W = 1920
_RENDER_H = 1080


def _html_to_png(html: str, out_png: str) -> str:
    """html 문자열을 임시 .html 파일로 쓰고 Chrome 헤드리스로 1920×1080 스크린샷 렌더.

    임시 파일은 out_png 가 위치한 디렉터리에 생성한다(헤르메틱·로컬 파일 렌더).
    PNG 가 생성되지 않으면 RuntimeError 를 발생시킨다.
    """
    out_dir = os.path.dirname(os.path.abspath(out_png)) or "."
    os.makedirs(out_dir, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", delete=False, dir=out_dir, encoding="utf-8"
    ) as f:
        f.write(html)
        html_path = f.name

    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--force-device-scale-factor=1", f"--window-size={_RENDER_W},{_RENDER_H}",
        f"--screenshot={out_png}", f"file://{html_path}",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    finally:
        try:
            os.unlink(html_path)
        except OSError:
            pass

    if not os.path.exists(out_png):
        raise RuntimeError(f"render failed: {r.stderr[-400:]}")
    return out_png


def _label_image(img: Image.Image, text: str) -> None:
    """이미지 좌상단에 반투명 배경의 라벨 텍스트를 그린다(OURS / REFERENCE)."""
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 40)
    except OSError:
        font = ImageFont.load_default()
    pad = 12
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        tw, th = draw.textsize(text, font=font)
    draw.rectangle([0, 0, tw + pad * 2, th + pad * 2], fill=(0, 0, 0))
    draw.text((pad, pad), text, fill=(255, 255, 255), font=font)


def compare(ours_html: str, reference_png: str, out_png: str) -> str:
    """우리 HTML을 PNG로 렌더 후, 참조 PNG와 가로로 나란히 합성해 out_png에 저장.

    합성 결과는 `.generated/_design_compare/` 아래에 저장한다(요구사항 5.7).
    ours_html 이 비었거나 None, 또는 reference_png 가 없거나 비었거나 파일이 아니면
    PNG 를 생성하지 않고 ValueError 를 발생시킨다(요구사항 5.9).

    반환값: 생성된 비교 PNG 의 절대 경로.
    """
    # 입력 누락 검사 — PNG 미생성 + 오류 (요구사항 5.9)
    if not ours_html:
        raise ValueError("ours_html 이 비어 있습니다 (요구사항 5.9: 누락 입력 → 오류)")
    if not reference_png:
        raise ValueError("reference_png 경로가 비어 있습니다 (요구사항 5.9)")
    if not os.path.isfile(reference_png):
        raise ValueError(
            f"reference_png 가 존재하지 않거나 파일이 아닙니다: {reference_png} (요구사항 5.9)"
        )
    if os.path.getsize(reference_png) <= 0:
        raise ValueError(f"reference_png 가 빈 파일입니다: {reference_png} (요구사항 5.9)")

    os.makedirs(OUT_DIR, exist_ok=True)

    # out_png 는 항상 비교 폴더 아래로 정규화한다.
    out_path = out_png if os.path.isabs(out_png) else os.path.join(OUT_DIR, out_png)
    out_path = os.path.abspath(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    # 우리 HTML → PNG 렌더 (임시 파일로 렌더 후 합성)
    ours_png = os.path.join(OUT_DIR, "_ours_render.png")
    _html_to_png(ours_html, ours_png)

    # 두 PNG 를 가로로 side-by-side 합성 (Pillow)
    with Image.open(ours_png) as a_raw, Image.open(reference_png) as b_raw:
        ours_img = a_raw.convert("RGB")
        ref_img = b_raw.convert("RGB")

        # 높이를 더 큰 쪽으로 맞춰 흰 배경에 정렬
        height = max(ours_img.height, ref_img.height)
        total_w = ours_img.width + ref_img.width

        canvas = Image.new("RGB", (total_w, height), (255, 255, 255))
        canvas.paste(ours_img, (0, 0))
        canvas.paste(ref_img, (ours_img.width, 0))

        # 라벨 부착 (OURS / REFERENCE)
        ours_label = canvas.crop((0, 0, ours_img.width, height))
        _label_image(ours_label, "OURS")
        canvas.paste(ours_label, (0, 0))

        ref_label = canvas.crop((ours_img.width, 0, total_w, height))
        _label_image(ref_label, "REFERENCE")
        canvas.paste(ref_label, (ours_img.width, 0))

        canvas.save(out_path)

    if not os.path.exists(out_path):
        raise RuntimeError(f"비교 PNG 생성 실패: {out_path}")
    return out_path
