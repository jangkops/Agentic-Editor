"""Frozen-binary entry point for the AI Editor backend (PyInstaller).

개발용 scripts/start_server.py와 달리:
- uvicorn reload 비활성(동결 환경 비호환) — app 객체를 직접 전달
- venv site-packages 조작 없음(동결 바이너리에 모든 의존성 포함)
- 포트/호스트는 환경변수로 조정(AE_BACKEND_PORT, 기본 8765)

자격증명은 앱이 로그인 시 /api/reset-cache로 직접 주입하므로 여기서 사전 로드는
선택사항이다(있으면 사용, 없으면 무시). 30명 멀티유저·Windows/macOS 동일 동작.
"""
import os
import sys


def _ensure_package_path():
    """프로즌/스크립트 양쪽에서 `import ai_engine.*`가 동작하도록 경로 보정."""
    try:
        base = getattr(sys, "_MEIPASS", None)  # PyInstaller 번들 루트
    except Exception:
        base = None
    if base and base not in sys.path:
        sys.path.insert(0, base)
    # 스크립트 실행(개발) 시 프로젝트 루트 보정
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, ".."))
    if root not in sys.path:
        sys.path.insert(0, root)


def main():
    _ensure_package_path()
    port = int(os.environ.get("AE_BACKEND_PORT", "8765"))
    host = os.environ.get("AE_BACKEND_HOST", "127.0.0.1")

    import uvicorn
    # app 객체를 직접 import해 전달 — 문자열 import + reload는 동결 환경에서 불안정.
    try:
        from ai_engine.server import app
    except Exception:
        # 스크립트 실행 폴백(패키지 경로가 ai_engine 내부일 때)
        from server import app  # type: ignore

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
