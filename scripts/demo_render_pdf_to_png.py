"""before.pdf / after.pdf 를 슬라이드별 PNG 로 렌더링하고 D1/D2/D3 비교 이미지를 만든다.

출력:
  <dir>/before_p1.png ... before_p3.png
  <dir>/after_p1.png  ... after_p3.png
  <dir>/compare_D1.png / compare_D2.png / compare_D3.png  (before|after 좌우 합성)
  <dir>/compare_ALL.png (3개 결함 비교를 한 장에)
"""
from __future__ import annotations

import os
import sys

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DIR = os.path.join(_ROOT, ".generated", "_visual_proof")
ZOOM = 1.6  # 렌더 배율


def _font(sz):
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, sz)
            except Exception:
                pass
    return ImageFont.load_default()


def _render(pdf_path, prefix):
    doc = fitz.open(pdf_path)
    paths = []
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM))
        out = os.path.join(DIR, f"{prefix}_p{i}.png")
        pix.save(out)
        paths.append(out)
    doc.close()
    return paths


def _hcat(left_png, right_png, title, out):
    lo = Image.open(left_png).convert("RGB")
    ro = Image.open(right_png).convert("RGB")
    h = max(lo.height, ro.height)
    gap, top = 24, 70
    W = lo.width + ro.width + gap * 3
    H = h + top + gap
    canvas = Image.new("RGB", (W, H), (245, 245, 245))
    dr = ImageDraw.Draw(canvas)
    dr.text((gap, 18), title, font=_font(30), fill=(20, 20, 20))
    dr.text((gap, top - 26), "BEFORE (결함)", font=_font(22), fill=(196, 40, 40))
    dr.text((gap * 2 + lo.width, top - 26), "AFTER (교정)",
            font=_font(22), fill=(20, 140, 90))
    canvas.paste(lo, (gap, top))
    canvas.paste(ro, (gap * 2 + lo.width, top))
    # 경계 박스
    dr.rectangle([gap, top, gap + lo.width, top + lo.height],
                 outline=(196, 40, 40), width=3)
    dr.rectangle([gap * 2 + lo.width, top, gap * 2 + lo.width + ro.width, top + ro.height],
                 outline=(20, 140, 90), width=3)
    canvas.save(out)
    return out


def main():
    b = _render(os.path.join(DIR, "before.pdf"), "before")
    a = _render(os.path.join(DIR, "after.pdf"), "after")
    titles = {1: "D1 — 풀블리드 배경 중복", 2: "D2 — 대형 이미지 소형 슬롯",
              3: "D3 — 부분 이미지 슬라이드 밖"}
    cmps = []
    for i in range(1, min(len(b), len(a)) + 1):
        out = os.path.join(DIR, f"compare_D{i}.png")
        _hcat(b[i - 1], a[i - 1], titles.get(i, f"슬라이드 {i}"), out)
        cmps.append(out)
        print(f"COMPARE D{i}: {out}")
    # 세로로 합쳐 한 장
    imgs = [Image.open(p).convert("RGB") for p in cmps]
    W = max(im.width for im in imgs)
    H = sum(im.height for im in imgs) + 20 * (len(imgs) + 1)
    allc = Image.new("RGB", (W, H), (255, 255, 255))
    y = 20
    for im in imgs:
        allc.paste(im, (0, y))
        y += im.height + 20
    allout = os.path.join(DIR, "compare_ALL.png")
    allc.save(allout)
    print(f"COMPARE ALL: {allout}")


if __name__ == "__main__":
    main()
