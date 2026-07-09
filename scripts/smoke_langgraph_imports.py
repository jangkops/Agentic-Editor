#!/usr/bin/env python3
"""LangGraph 오케스트레이터 번들 import smoke test (서버 미기동, 유한 시간).

목적
----
Phase 5(정리/번들 검증)의 일부. 정식 LangGraph 런타임으로 마이그레이션하면서 새로
의존하게 된 서드파티 서브모듈과, 신설한 우리 오케스트레이터 모듈 전체가 import
가능한지 빠르게 검증한다. 서버를 기동하지 않고 import만 수행하므로 유한 시간에 종료한다.

이 스크립트는 재빌드(PyInstaller onedir) 이후에도 유사 검증에 재사용할 수 있도록
import 대상을 명시적으로 문서화한다. 동결 산출물(dist)에서 검증할 때는
아래 THIRD_PARTY_MODULES 목록을 그대로 사용하면 된다(우리 모듈은 번들에 collect_submodules로 포함).

사용법
------
    ai_engine/.venv/bin/python scripts/smoke_langgraph_imports.py

종료 코드: 전부 성공 0, 하나라도 실패 1.
"""
import importlib
import os
import sys

# 워크스페이스 루트(이 스크립트의 상위 디렉토리)를 sys.path에 추가해
# 어디서 실행하든 `ai_engine.*` 패키지를 import 가능하게 한다.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ── 서드파티 신규/핵심 서브모듈 ─────────────────────────────────
# PyInstaller spec(ai-engine-server.spec)의 collect_all('langgraph') /
# collect_all('langchain_core')로 번들에 포함되어야 하는 대상.
THIRD_PARTY_MODULES = [
    "langgraph.checkpoint.base",
    "langgraph.checkpoint.serde.jsonplus",
    "langgraph.prebuilt",
    "langchain_core.language_models",
]

# ── 우리 오케스트레이터 모듈 전체 ───────────────────────────────
PROJECT_MODULES = [
    "ai_engine.agent_system.graph_state",
    "ai_engine.agent_system.chat_model_adapter",
    "ai_engine.agent_system.checkpoint_store",
    "ai_engine.agent_system.deps",
    "ai_engine.agent_system.supervisor",
    "ai_engine.agent_system.sse_bridge",
    "ai_engine.agent_system.nodes.tool_node",
    "ai_engine.agent_system.nodes.retrieve",
    "ai_engine.agent_system.nodes.verify",
    "ai_engine.agent_system.subgraphs.coding",
    "ai_engine.agent_system.subgraphs.media",
    "ai_engine.agent_system.subgraphs.research",
    "ai_engine.agent_system.subgraphs.ops",
    "ai_engine.agent_system.subgraphs.chat",
    "ai_engine.server",
]

ALL_MODULES = THIRD_PARTY_MODULES + PROJECT_MODULES


def main() -> int:
    ok, failed = [], []
    for mod in ALL_MODULES:
        try:
            importlib.import_module(mod)
            ok.append(mod)
            print(f"  OK   {mod}")
        except Exception as e:  # noqa: BLE001 - smoke test는 모든 실패를 수집
            failed.append((mod, repr(e)))
            print(f"  FAIL {mod}  ->  {e!r}")

    print(f"\n{len(ok)}/{len(ALL_MODULES)} modules imported successfully.")
    if failed:
        print("\nFailures:")
        for mod, err in failed:
            print(f"  - {mod}: {err}")
        return 1
    print("All imports OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
