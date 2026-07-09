"""JSON-based LangGraph checkpoint saver for agent workflows.

이 모듈은 ``JsonFileCheckpointSaver`` — 정식 LangGraph ``BaseCheckpointSaver`` 구현체를
제공한다. SQLite를 절대 사용하지 않고 ``.json`` 파일로만 그래프 상태를 영속하며,
자격증명은 어떤 파일에도 저장하지 않는다(방어적 차단 포함).

(구 수동 워크플로우용 레거시 ``CheckpointStore``는 Phase 5 정리에서 제거되었다 —
 dead code로 확인되어 삭제.)

설계 근거는 spec의 ``API_NOTES.md`` 항목 2·3, "⚠️ 설계 보정 필요", design.md 섹션 5 참조.
"""
import os
import json
import base64
import asyncio
from typing import Any, AsyncIterator, Iterator, Optional, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

try:  # RunnableConfig는 타입 힌트 용도(런타임 의존성 아님)
    from langchain_core.runnables import RunnableConfig
except Exception:  # pragma: no cover - 방어
    RunnableConfig = dict  # type: ignore


# ── 보안 방어: 자격증명 문자열이 직렬화 결과에 나타나면 저장 차단 (요구사항 8.3) ──
_FORBIDDEN_CRED_KEYS = ("accessKeyId", "secretAccessKey")


class CredentialLeakError(RuntimeError):
    """직렬화된 체크포인트에 자격증명이 감지되면 발생(방어적 차단)."""


def _default_checkpoint_dir() -> str:
    """기본 체크포인트 디렉터리.

    우선순위: 주입된 base_dir > ``AE_CHECKPOINT_DIR`` env >
    ``~/.agentic-editor/checkpoints/langgraph``.
    데이터는 항상 userData 하위(요구사항 4.3)에 위치해야 하며, 실제
    userData 경로는 상위(server.py)에서 base_dir로 주입한다.
    """
    env_dir = os.environ.get("AE_CHECKPOINT_DIR")
    if env_dir:
        return env_dir
    return os.path.join(
        os.path.expanduser("~"), ".agentic-editor", "checkpoints", "langgraph"
    )


class JsonFileCheckpointSaver(BaseCheckpointSaver):
    """LangGraph ``BaseCheckpointSaver``의 JSON 파일 구현.

    Precondition:  base_dir(또는 기본값)은 쓰기 가능한 디렉터리다.
    Postcondition: 모든 체크포인트는 ``.json`` 파일로만 저장되며 SQLite를 쓰지 않는다.
                   파일 레이아웃은 ``{base_dir}/{thread_id}/{checkpoint_ns}/{checkpoint_id}.json``.
    Invariant:     직렬화 결과에 자격증명(accessKeyId/secretAccessKey)이 포함되면
                   저장을 차단한다(요구사항 8.3, 이중 방어).

    실측 보정(API_NOTES):
      - async 4종(``aput``/``aput_writes``/``aget_tuple``/``alist``)은 기본 구현이
        ``NotImplementedError``이므로 반드시 구현한다. sync 로직을 ``asyncio.to_thread``로 위임.
      - ``put_writes``/``aput_writes``는 ``task_path: str = ''`` 인자를 포함한다.
      - ``JsonPlusSerializer``는 ``dumps_typed``/``loads_typed``만 제공하며 msgpack bytes를
        반환하므로, 파일에는 ``{"type": tag, "data": base64(bytes)}`` 규약으로 래핑해 저장한다.
    """

    def __init__(self, base_dir: str = "", *, serde=None):
        super().__init__(serde=serde or JsonPlusSerializer())
        self.base_dir = base_dir or _default_checkpoint_dir()
        os.makedirs(self.base_dir, exist_ok=True)

    # ─────────────────────────── 경로/직렬화 헬퍼 ───────────────────────────

    def _thread_dir(self, thread_id: str, checkpoint_ns: str) -> str:
        # checkpoint_ns는 빈 문자열일 수 있으므로 "__default__"로 정규화
        ns = checkpoint_ns or "__default__"
        return os.path.join(self.base_dir, thread_id, ns)

    def _checkpoint_path(
        self, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> str:
        return os.path.join(
            self._thread_dir(thread_id, checkpoint_ns), f"{checkpoint_id}.json"
        )

    def _encode(self, obj: Any) -> dict:
        """serde.dumps_typed → {"type": tag, "data": base64(bytes)} 래핑."""
        type_tag, data = self.serde.dumps_typed(obj)
        return {"type": type_tag, "data": base64.b64encode(data).decode("ascii")}

    def _decode(self, wrapped: dict) -> Any:
        """{"type","data"} → serde.loads_typed((tag, bytes))."""
        data = base64.b64decode(wrapped["data"].encode("ascii"))
        return self.serde.loads_typed((wrapped["type"], data))

    @staticmethod
    def _assert_no_credentials(*objs: Any) -> None:
        """직렬화 전 원본 객체에 자격증명 키가 있으면 차단(요구사항 8.3).

        체크포인트 값은 base64(msgpack)로 저장되어 파일 텍스트에는 평문으로 나타나지
        않으므로, 저장 대상 원본 객체의 문자열 표현을 검사해 이중 방어한다.
        """
        haystack = " ".join(repr(o) for o in objs)
        for key in _FORBIDDEN_CRED_KEYS:
            if key in haystack:
                raise CredentialLeakError(
                    f"체크포인트에 자격증명({key})이 감지되어 저장을 차단했습니다."
                )

    @staticmethod
    def _cfg(config: RunnableConfig) -> dict:
        return (config or {}).get("configurable", {}) or {}

    # ─────────────────────────── sync 4종 ───────────────────────────

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """체크포인트 저장. Postcondition: ``.json`` 파일 1개 기록, 갱신된 config 반환."""
        cfg = self._cfg(config)
        thread_id = cfg["thread_id"]
        checkpoint_ns = cfg.get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]

        # 이중 방어: 직렬화 전 원본 객체에서 자격증명 유출 차단
        self._assert_no_credentials(checkpoint, metadata)

        record = {
            "thread_id": thread_id,
            "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": cfg.get("checkpoint_id"),
            "ts": checkpoint.get("ts", ""),
            "checkpoint": self._encode(checkpoint),
            "metadata": self._encode(dict(metadata)),
            "new_versions": self._encode(dict(new_versions)),
            "writes": [],  # put_writes가 append
        }

        serialized = json.dumps(record, ensure_ascii=False)

        path = self._checkpoint_path(thread_id, checkpoint_ns, checkpoint_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(serialized)

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """중간 write 저장(요구사항: task_path 인자 포함 — API_NOTES HIGH)."""
        cfg = self._cfg(config)
        thread_id = cfg["thread_id"]
        checkpoint_ns = cfg.get("checkpoint_ns", "")
        checkpoint_id = cfg.get("checkpoint_id")
        if not checkpoint_id:
            return

        path = self._checkpoint_path(thread_id, checkpoint_ns, checkpoint_id)
        if not os.path.isfile(path):
            # 대응 체크포인트가 아직 없으면 스킵(방어)
            return

        with open(path, "r", encoding="utf-8") as f:
            record = json.load(f)

        self._assert_no_credentials(writes)

        existing = record.get("writes", [])
        for idx, (channel, value) in enumerate(writes):
            existing.append(
                {
                    "task_id": task_id,
                    "task_path": task_path,
                    "idx": idx,
                    "channel": channel,
                    "value": self._encode(value),
                }
            )
        record["writes"] = existing

        serialized = json.dumps(record, ensure_ascii=False)
        with open(path, "w", encoding="utf-8") as f:
            f.write(serialized)

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """thread_id로 체크포인트 조회.

        checkpoint_id가 config에 있으면 해당 체크포인트를, 없으면 최신(mtime/ts 기준)을 반환.
        존재하지 않으면 None(요구사항 4.5).
        """
        cfg = self._cfg(config)
        thread_id = cfg.get("thread_id")
        if not thread_id:
            return None
        checkpoint_ns = cfg.get("checkpoint_ns", "")
        checkpoint_id = cfg.get("checkpoint_id")

        thread_dir = self._thread_dir(thread_id, checkpoint_ns)
        if not os.path.isdir(thread_dir):
            return None

        if checkpoint_id:
            path = self._checkpoint_path(thread_id, checkpoint_ns, checkpoint_id)
            if not os.path.isfile(path):
                return None
        else:
            path = self._latest_checkpoint_path(thread_dir)
            if path is None:
                return None

        return self._load_tuple(path, thread_id, checkpoint_ns)

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        """thread의 체크포인트를 최신순으로 나열."""
        cfg = self._cfg(config) if config else {}
        thread_id = cfg.get("thread_id")
        if not thread_id:
            return
        checkpoint_ns = cfg.get("checkpoint_ns", "")
        thread_dir = self._thread_dir(thread_id, checkpoint_ns)
        if not os.path.isdir(thread_dir):
            return

        paths = self._sorted_checkpoint_paths(thread_dir)  # 최신 → 과거

        before_id = None
        if before:
            before_id = self._cfg(before).get("checkpoint_id")
        seen_before = before_id is None

        count = 0
        for path in paths:
            cid = os.path.basename(path)[:-5]  # ".json" 제거
            if before_id is not None and not seen_before:
                if cid == before_id:
                    seen_before = True
                continue
            tup = self._load_tuple(path, thread_id, checkpoint_ns)
            if tup is None:
                continue
            if filter:
                md = tup.metadata or {}
                if not all(md.get(k) == v for k, v in filter.items()):
                    continue
            yield tup
            count += 1
            if limit is not None and count >= limit:
                return

    # ─────────────────────────── 로드 내부 헬퍼 ───────────────────────────

    def _read_record(self, path: str) -> Optional[dict]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _load_tuple(
        self, path: str, thread_id: str, checkpoint_ns: str
    ) -> Optional[CheckpointTuple]:
        record = self._read_record(path)
        if record is None:
            return None
        try:
            checkpoint = self._decode(record["checkpoint"])
            metadata = self._decode(record["metadata"])
        except Exception:
            return None

        checkpoint_id = record["checkpoint_id"]
        current_config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }
        parent_config = None
        parent_id = record.get("parent_checkpoint_id")
        if parent_id:
            parent_config = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": parent_id,
                }
            }

        pending_writes = []
        for w in record.get("writes", []):
            try:
                pending_writes.append(
                    (w["task_id"], w["channel"], self._decode(w["value"]))
                )
            except Exception:
                continue

        return CheckpointTuple(
            config=current_config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=pending_writes,
        )

    def _sorted_checkpoint_paths(self, thread_dir: str) -> list[str]:
        """thread_dir 내 ``.json`` 체크포인트 파일을 ts(없으면 mtime) 최신순 정렬."""
        entries = []
        for fname in os.listdir(thread_dir):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(thread_dir, fname)
            if not os.path.isfile(path):
                continue
            record = self._read_record(path)
            ts = (record or {}).get("ts", "")
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            entries.append((ts, mtime, path))
        # ts 문자열(ISO8601)은 사전식 정렬이 시간 정렬과 일치. tie-break로 mtime.
        entries.sort(key=lambda e: (e[0], e[1]), reverse=True)
        return [e[2] for e in entries]

    def _latest_checkpoint_path(self, thread_dir: str) -> Optional[str]:
        paths = self._sorted_checkpoint_paths(thread_dir)
        return paths[0] if paths else None

    # ─────────────────────────── async 4종 (필수) ───────────────────────────

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return await asyncio.to_thread(
            self.put, config, checkpoint, metadata, new_versions
        )

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def aget_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        return await asyncio.to_thread(self.get_tuple, config)

    async def alist(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        # sync list를 스레드에서 소진해 리스트로 만든 뒤 async로 재방출
        items = await asyncio.to_thread(
            lambda: list(
                self.list(config, filter=filter, before=before, limit=limit)
            )
        )
        for item in items:
            yield item
