"""고품질 벡터 아이콘(Lucide, ISC License) → PNG 렌더/캐시 모듈.

flaticon 등은 라이선스/API 제약이 있어 직접 사용이 어렵다. 동급 퀄리티의
오픈소스 라인 아이콘 세트인 **Lucide**(https://lucide.dev, ISC License — 상업적
사용·재배포 자유)의 SVG path를 번들로 포함해, 로컬 Chrome 헤드리스로 고해상도
PNG(투명 배경)로 렌더해 캐시한다.

- 슬라이드 카드의 아이콘 칩에 흰색 아이콘으로 임베드 → 젠스파크/OpenAI 제안서급 비주얼.
- 칩/카드/텍스트 등 나머지 요소는 전부 편집 가능한 네이티브 도형으로 유지.
- Chrome 미존재/렌더 실패 시 None 반환 → 호출부가 네이티브 autoshape 글리프로 폴백.
- AE_DISABLE_RICH_ICONS=1 로 비활성(autoshape 폴백). 테스트/오프라인 환경 대응.

라이선스 고지: 아이콘은 Lucide(ISC). 산출물에 포함되어도 무방하나, 별도 크레딧을
원하면 "Icons by Lucide (lucide.dev), ISC License"를 표기할 수 있다.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from pathlib import Path

# ── Lucide 24x24 아이콘 inner SVG (stroke 기반). 의미 키 → path 마크업 ──────────
# 출처: Lucide (https://lucide.dev) — ISC License.
_LUCIDE = {
    "zap": '<path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/>',
    "cloud": '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/>',
    "gear": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5V19A9 3 0 0 0 21 19V5"/><path d="M3 12A9 3 0 0 0 21 12"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    "dollar": '<line x1="12" x2="12" y1="2" y2="22"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    "chart": '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"/><path d="M22 12A10 10 0 0 0 12 2v10z"/>',
    "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    "up": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "down": '<polyline points="22 17 13.5 8.5 8.5 13.5 2 7"/><polyline points="16 17 22 17 22 11"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "chevron": '<path d="m9 18 6-6-6-6"/>',
    "box": '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "check": '<path d="M21.801 10A10 10 0 1 1 17 3.335"/><path d="m9 11 3 3L22 4"/>',
    "server": '<rect width="20" height="8" x="2" y="2" rx="2" ry="2"/><rect width="20" height="8" x="2" y="14" rx="2" ry="2"/><line x1="6" x2="6.01" y1="6" y2="6"/><line x1="6" x2="6.01" y1="18" y2="18"/>',
}

_CACHE_DIR = Path(os.path.expanduser("~/.agentic-editor/.icon_cache"))
_CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


def _find_chrome():
    env = os.environ.get("AE_CHROME_PATH", "").strip()
    if env and os.path.isfile(env):
        return env
    for p in _CHROME_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


def available() -> bool:
    """리치 아이콘 사용 가능 여부(미설정/Chrome 존재)."""
    if os.environ.get("AE_DISABLE_RICH_ICONS", "").strip() == "1":
        return False
    return _find_chrome() is not None


def icon_keys() -> list:
    return list(_LUCIDE.keys())


def _svg_markup(name: str, color_hex: str, stroke_w: float = 2.0) -> str:
    inner = _LUCIDE.get(name) or _LUCIDE["chart"]
    col = "#" + color_hex.lstrip("#")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" '
        'viewBox="0 0 24 24" fill="none" stroke="' + col + '" '
        'stroke-width="' + str(stroke_w) + '" stroke-linecap="round" '
        'stroke-linejoin="round">' + inner + '</svg>'
    )


def get_icon_png(name: str, color_hex: str = "FFFFFF", px: int = 240) -> str | None:
    """아이콘 PNG(투명 배경) 절대경로 반환. 캐시 우선, 없으면 Chrome로 렌더.

    실패/비활성 시 None → 호출부가 네이티브 autoshape로 폴백.
    """
    if not available():
        return None
    name = (name or "").lower().strip()
    if name not in _LUCIDE:
        name = "chart"
    color = (color_hex or "FFFFFF").lstrip("#").upper()
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return None
    key = hashlib.md5(f"{name}_{color}_{px}".encode()).hexdigest()[:12]
    out = _CACHE_DIR / f"{name}_{color}_{px}_{key}.png"
    if out.is_file() and out.stat().st_size > 200:
        return str(out)

    chrome = _find_chrome()
    if not chrome:
        return None
    svg = _svg_markup(name, color)
    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<style>html,body{margin:0;padding:0;background:transparent}'
        f'svg{{display:block;width:{px}px;height:{px}px}}</style></head>'
        f'<body>{svg}</body></html>'
    )
    tmp_html = None
    try:
        fd, tmp_html = tempfile.mkstemp(suffix=".html", prefix="ae_icon_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        cmd = [
            chrome, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--hide-scrollbars", "--force-device-scale-factor=1",
            "--default-background-color=00000000",
            f"--screenshot={out}", f"--window-size={px},{px}",
            "--disable-extensions", "--disable-software-rasterizer",
            f"file://{tmp_html}",
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=25, check=False)
    except Exception:
        pass
    finally:
        if tmp_html and os.path.isfile(tmp_html):
            try:
                os.remove(tmp_html)
            except Exception:
                pass
    if out.is_file() and out.stat().st_size > 200:
        return str(out)
    return None


def prewarm(color_hex: str = "FFFFFF") -> int:
    """모든 아이콘을 미리 렌더해 캐시(첫 생성 지연 제거). 성공 개수 반환."""
    ok = 0
    for k in _LUCIDE:
        if get_icon_png(k, color_hex):
            ok += 1
    return ok


if __name__ == "__main__":
    print("chrome:", _find_chrome())
    print("available:", available())
    n = prewarm("FFFFFF")
    print(f"prewarmed {n}/{len(_LUCIDE)} icons → {_CACHE_DIR}")
