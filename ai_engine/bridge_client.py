"""Bridge client — proxies filesystem/exec tool calls to the Electron
bridge HTTP server, which routes them through the active Remote SSH session.

The Electron main process starts a local HTTP bridge server when it boots
(see electron/src/remote/bridge-server.js). It writes a discovery file at
`<tmpdir>/ae-bridge.json` containing `{url, token, pid, ts}`. We poll this
file lazily on first use so an externally-started ai_engine (e.g. via
`npm run dev:python` started before Electron) can find it.

Contract:
- When `is_active()` returns True AND `is_remote_session()` returns True,
  call `read_file/list_directory/run_command/search_files/write_file` —
  these execute on the connected remote host via SSH.
- When the bridge is unavailable, callers should fall back to local
  `os.path` operations (matching legacy behaviour).
"""

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Optional

_DISCOVERY_FILENAME = "ae-bridge.json"
_REFRESH_INTERVAL_SEC = 5.0  # re-read discovery file at most this often

_state = {
    "url": "",
    "token": "",
    "loaded_at": 0.0,
    "remote": False,
    "alias": None,
    "remote_checked_at": 0.0,
}


def _discovery_path() -> str:
    return os.path.join(tempfile.gettempdir(), _DISCOVERY_FILENAME)


def _load_discovery(force: bool = False) -> bool:
    """Load bridge URL/token from discovery file or env vars.

    Env vars (`AE_BRIDGE_URL`, `AE_BRIDGE_TOKEN`) take precedence — these are
    set when ai_engine is started by Electron's ProcessManager.

    Returns True if bridge URL+token are now known.
    """
    now = time.time()
    if not force and _state["url"] and (now - _state["loaded_at"] < _REFRESH_INTERVAL_SEC):
        return True

    # 1) Env vars — set by ProcessManager
    env_url = os.environ.get("AE_BRIDGE_URL", "").strip()
    env_token = os.environ.get("AE_BRIDGE_TOKEN", "").strip()
    if env_url and env_token:
        _state["url"] = env_url
        _state["token"] = env_token
        _state["loaded_at"] = now
        return True

    # 2) Discovery file — written by Electron main.js after bridge starts
    try:
        with open(_discovery_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        url = (data.get("url") or "").strip()
        token = (data.get("token") or "").strip()
        if url and token:
            _state["url"] = url
            _state["token"] = token
            _state["loaded_at"] = now
            return True
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # No bridge available
    if not _state["url"]:
        _state["loaded_at"] = now
    return bool(_state["url"])


def is_active() -> bool:
    """True if the bridge HTTP server is reachable (URL+token known)."""
    return _load_discovery()


def is_remote_session() -> bool:
    """True if the bridge reports an active remote SSH session.

    Cached for a few seconds to avoid hammering the bridge on every tool call.
    """
    if not _load_discovery():
        return False
    now = time.time()
    if now - _state["remote_checked_at"] < 2.0:
        return bool(_state["remote"])
    try:
        resp = _post("/bridge/status", {})
        _state["remote"] = bool(resp.get("remote"))
        _state["alias"] = resp.get("alias")
        _state["remote_checked_at"] = now
        return _state["remote"]
    except Exception:
        # Bridge unreachable — discovery file may be stale (Electron exited)
        _state["url"] = ""
        _state["token"] = ""
        _state["remote"] = False
        return False


def alias() -> Optional[str]:
    """Return the active remote session alias, or None."""
    return _state.get("alias")


def _post(endpoint: str, payload: dict, timeout: float = 30.0) -> dict:
    """POST JSON to bridge endpoint. Raises on transport error or non-200."""
    if not _load_discovery():
        raise RuntimeError("bridge not available")
    url = _state["url"].rstrip("/") + endpoint
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-AE-Bridge-Token": _state["token"],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data) if data else {}
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8")
            err_data = json.loads(err_body)
            raise RuntimeError(err_data.get("error") or f"HTTP {e.code}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RuntimeError(f"HTTP {e.code}")


# ── Public tool proxy methods ───────────────────────────────────────────

def read_file(path: str) -> str:
    """Read a file via the bridge (remote SSH SFTP)."""
    r = _post("/bridge/read_file", {"path": path})
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "read failed")
    return r.get("content") or ""


def write_file(path: str, content: str) -> None:
    """Write a file via the bridge (remote SSH SFTP atomic write)."""
    r = _post("/bridge/write_file", {"path": path, "content": content}, timeout=60.0)
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "write failed")


def list_directory(path: str) -> list:
    """List a directory via the bridge. Returns list of {name, path, isDirectory, size}."""
    r = _post("/bridge/list_directory", {"path": path})
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "list failed")
    return r.get("entries") or []


def run_command(command: str, cwd: str = "") -> dict:
    """Run a shell command on the remote host via SSH. Returns {stdout, stderr, code}."""
    payload = {"command": command}
    if cwd:
        payload["cwd"] = cwd
    r = _post("/bridge/run_command", payload, timeout=60.0)
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "exec failed")
    return {
        "stdout": r.get("stdout") or "",
        "stderr": r.get("stderr") or "",
        "code": r.get("code") if isinstance(r.get("code"), int) else 0,
    }


def search_files(query: str, path: str = ".", file_pattern: str = "") -> str:
    """Grep on the remote host."""
    payload = {"query": query, "path": path}
    if file_pattern:
        payload["file_pattern"] = file_pattern
    r = _post("/bridge/search_files", payload, timeout=30.0)
    if not r.get("ok"):
        raise RuntimeError(r.get("error") or "search failed")
    return r.get("output") or ""
