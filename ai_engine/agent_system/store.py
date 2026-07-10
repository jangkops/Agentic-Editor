"""JsonFileStore — 파일 영속 LangGraph Store (세션 간 장기 메모리).

LangGraph 의 ``BaseStore`` 는 cross-thread(세션 간) 장기 메모리를 제공한다. 기본
``InMemoryStore`` 는 프로세스 재시작 시 휘발되므로, 이 모듈은 InMemoryStore 를 상속해
``.json`` 파일 영속만 얹는다(프로젝트 제약: SQLite 미사용, 파일 기반).

설계:
- InMemoryStore 가 batch/abatch/get/put/search 를 모두 구현하므로 그대로 재사용한다.
  put(=PutOp) 이 포함된 batch/abatch 실행 후에만 디스크에 저장한다(불필요 I/O 방지).
- 저장 형식: ``[{"namespace": [...], "key": "...", "value": {...}}]`` (Item.value 는 dict).
- 로드 시 put 으로 복원(created_at 은 로드 시점으로 갱신됨 — 값/키/네임스페이스는 보존).

보안(요구사항 8.x): value 에는 자격증명을 저장하지 않는다(호출자 책임). Store 는
사용자가 명시적으로 남긴 사실/선호(예: 이름, 선호 언어)만 담는다.
"""

from __future__ import annotations

import json
import os
from typing import Any, Iterable

from langgraph.store.memory import InMemoryStore
from langgraph.store.base import PutOp


def _default_store_dir() -> str:
    """Store 파일 디렉토리. AE_GENERATED_ROOT(userData) 하위 우선, 없으면 ~/.agentic-editor."""
    env_root = os.environ.get("AE_GENERATED_ROOT", "").strip()
    base = env_root or os.path.expanduser("~/.agentic-editor")
    return os.path.join(base, "store", "langgraph")


class JsonFileStore(InMemoryStore):
    """InMemoryStore + JSON 파일 영속. compile(store=...) 에 그대로 주입 가능.

    Precondition:  base_dir 는 쓰기 가능(없으면 생성). 없으면 기본 경로 사용.
    Postcondition: put(값 변경)을 포함한 batch/abatch 후 단일 ``store.json`` 파일에 반영.
    Invariant:     저장은 .json 파일만 사용(SQLite 미사용). 로드/저장 실패는 비차단(로그).
    """

    def __init__(self, base_dir: str = "", **kwargs: Any):
        super().__init__(**kwargs)
        self._base_dir = (base_dir or "").strip() or _default_store_dir()
        self._file = os.path.join(self._base_dir, "store.json")
        self._loading = False
        self._load()

    # ── 파일 I/O ──
    def _load(self) -> None:
        if not os.path.isfile(self._file):
            return
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"[Store] 로드 실패(무시): {e}")
            return
        self._loading = True
        try:
            for rec in records or []:
                if not isinstance(rec, dict):
                    continue
                ns = rec.get("namespace")
                key = rec.get("key")
                value = rec.get("value")
                if isinstance(ns, list) and isinstance(key, str) and isinstance(value, dict):
                    try:
                        self.put(tuple(ns), key, value)
                    except Exception:
                        continue
        finally:
            self._loading = False

    def _save(self) -> None:
        try:
            os.makedirs(self._base_dir, exist_ok=True)
            records = []
            # InMemoryStore 내부 구조: _data[namespace][key] = Item(value/key/namespace).
            for ns, items in list(getattr(self, "_data", {}).items()):
                for key, item in list(items.items()):
                    val = getattr(item, "value", None)
                    if isinstance(val, dict):
                        records.append({"namespace": list(ns), "key": key, "value": val})
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._file)  # 원자적 교체
        except OSError as e:
            print(f"[Store] 저장 실패(무시): {e}")

    # ── batch 오버라이드: put 발생 시에만 영속 ──
    def batch(self, ops: Iterable[Any]) -> list:
        ops = list(ops)
        result = super().batch(ops)
        if not self._loading and any(isinstance(o, PutOp) for o in ops):
            self._save()
        return result

    async def abatch(self, ops: Iterable[Any]) -> list:
        ops = list(ops)
        result = await super().abatch(ops)
        if not self._loading and any(isinstance(o, PutOp) for o in ops):
            self._save()
        return result
