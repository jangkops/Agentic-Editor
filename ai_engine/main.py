"""AI Editor Backend — main entry point (uses server.py app)."""
import os
import sys

# Re-export the app from server.py for uvicorn
from ai_engine.server import app  # noqa: F401


if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    uvicorn.run(
        "ai_engine.server:app",
        host="127.0.0.1",  # 로컬 전용 — 0.0.0.0 은 LAN 노출(원장 #17)
        port=port,
        reload=False,
        log_level="info",
    )
