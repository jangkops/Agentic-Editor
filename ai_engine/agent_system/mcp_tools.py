"""MCP 도구 통합 — MCP 서버의 tool 을 LangChain BaseTool 로 로드해 도메인 서브그래프에 병합.

langchain-mcp-adapters(MultiServerMCPClient)로 MCP 서버에 연결하고 `get_tools()` 로 얻은
LangChain BaseTool 을 반환한다. 이 도구들은 GatewayChatModel.bind_tools 로 Bedrock toolSpec
으로 변환되어 model 에 노출되고, GatewayToolNode 가 `ainvoke` 로 실행한다.

설계 원칙(안전 최우선):
- **옵트인(기본 off):** `AE_MCP_ENABLED` 가 on 일 때만 동작. 기본은 완전 no-op(무영향).
  MCP 서버는 외부 프로세스(docker/stdio/http)라 부팅 실패·지연 위험이 있으므로 옵트인.
- **allowlist:** 각 서버의 `autoApprove` 에 명시된 도구만 노출한다(보안 — LLM 이 호출 가능한
  API 를 최소화). `AE_MCP_ALLOW_ALL=1` 이면 전체 노출(개발용).
- **disabled 제외:** mcp.json 의 `disabled: true` 서버는 로드하지 않는다.
- **비차단:** 설정 없음/연결 실패/타임아웃이면 빈 리스트를 반환해 그래프 진행을 막지 않는다.
- **캐시:** 서버 연결/도구 로드는 비싸므로 TTL(기본 300초) 캐시.

설정 소스 우선순위:
  1) env `AE_MCP_CONFIG`(파일 경로)
  2) workspace `.kiro/settings/mcp.json`
  3) user `~/.kiro/settings/mcp.json`
kiro mcp.json 형식({"mcpServers": {name: {command,args,env,disabled,autoApprove}}})을
langchain-mcp-adapters connections 형식으로 변환한다.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple


def _mcp_enabled() -> bool:
    return os.environ.get("AE_MCP_ENABLED", "").strip().lower() in ("1", "true", "on", "yes")


def _mcp_allow_all() -> bool:
    return os.environ.get("AE_MCP_ALLOW_ALL", "").strip().lower() in ("1", "true", "on", "yes")


def _mcp_timeout() -> float:
    try:
        return float(os.environ.get("AE_MCP_LOAD_TIMEOUT", "30"))
    except (TypeError, ValueError):
        return 30.0


def _mcp_cache_ttl() -> float:
    try:
        return float(os.environ.get("AE_MCP_CACHE_TTL", "300"))
    except (TypeError, ValueError):
        return 300.0


def _config_paths() -> List[str]:
    paths: List[str] = []
    env_cfg = os.environ.get("AE_MCP_CONFIG", "").strip()
    if env_cfg:
        paths.append(env_cfg)
    # workspace(현재 작업 폴더 기준) + user 홈
    paths.append(os.path.join(os.getcwd(), ".kiro", "settings", "mcp.json"))
    paths.append(os.path.expanduser("~/.kiro/settings/mcp.json"))
    return paths


def _load_config() -> Dict[str, dict]:
    """mcp.json 들을 읽어 서버 정의 dict 를 병합 반환({name: server_def}). 실패는 무시."""
    merged: Dict[str, dict] = {}
    for p in _config_paths():
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict):
            for name, sdef in servers.items():
                if isinstance(sdef, dict) and name not in merged:
                    merged[name] = sdef
    return merged


def _to_connection(sdef: dict) -> Optional[dict]:
    """kiro 서버 정의 → langchain-mcp-adapters connection dict. 지원 못 하면 None."""
    if sdef.get("disabled") is True:
        return None
    # HTTP/SSE transport 명시
    url = sdef.get("url")
    if isinstance(url, str) and url:
        transport = sdef.get("transport") or ("sse" if url.rstrip("/").endswith("/sse") else "streamable_http")
        conn = {"transport": transport, "url": url}
        if isinstance(sdef.get("headers"), dict):
            conn["headers"] = sdef["headers"]
        return conn
    # stdio (command/args/env)
    command = sdef.get("command")
    if isinstance(command, str) and command:
        conn = {"transport": "stdio", "command": command, "args": list(sdef.get("args") or [])}
        if isinstance(sdef.get("env"), dict):
            conn["env"] = sdef["env"]
        return conn
    return None


def _allowed_names(sdef: dict) -> Optional[set]:
    """서버의 autoApprove allowlist. allow-all 이면 None(전체 허용)."""
    if _mcp_allow_all():
        return None
    aa = sdef.get("autoApprove")
    if isinstance(aa, list):
        return {str(x) for x in aa}
    return set()  # autoApprove 없으면 아무 도구도 노출 안 함(보안 기본)


# ── 캐시 ──
_CACHE: Dict[str, Any] = {"ts": 0.0, "tools": [], "names": set()}


async def _load_tools_uncached() -> Tuple[List[Any], set]:
    """MCP 서버들에 연결해 allowlist 통과 도구(BaseTool)와 이름 집합을 반환."""
    config = _load_config()
    if not config:
        return [], set()

    # allowlist 를 서버별로 계산하되, MultiServerMCPClient 는 서버 단위 연결만 하므로
    # 전체 도구를 로드한 뒤 이름 기준으로 필터한다. (도구 name 은 서버 간 유일하다고 가정)
    connections: Dict[str, dict] = {}
    global_allow: Optional[set] = set()  # None 이면 전체 허용
    any_allow_all = False
    for name, sdef in config.items():
        conn = _to_connection(sdef)
        if conn is None:
            continue
        connections[name] = conn
        allow = _allowed_names(sdef)
        if allow is None:
            any_allow_all = True
        else:
            global_allow |= allow
    if not connections:
        return [], set()

    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(connections)
    tools = await client.get_tools()

    out_tools: List[Any] = []
    out_names: set = set()
    for t in tools:
        tname = getattr(t, "name", None)
        if not tname:
            continue
        if any_allow_all or tname in global_allow:
            out_tools.append(t)
            out_names.add(tname)
    return out_tools, out_names


async def get_mcp_tools() -> Tuple[List[Any], set]:
    """MCP 도구(BaseTool 리스트)와 이름 집합을 반환. 비활성/실패/타임아웃이면 ([], set()).

    TTL 캐시. 옵트인(AE_MCP_ENABLED). 어떤 예외에도 raise 하지 않는다(비차단).
    """
    if not _mcp_enabled():
        return [], set()
    now = time.time()
    if (now - _CACHE["ts"]) < _mcp_cache_ttl() and _CACHE["tools"]:
        return list(_CACHE["tools"]), set(_CACHE["names"])
    try:
        tools, names = await asyncio.wait_for(_load_tools_uncached(), timeout=_mcp_timeout())
    except Exception as e:  # noqa: BLE001 — MCP 연결 실패는 비차단
        print(f"[MCP] 도구 로드 실패(무시): {str(e)[:200]}")
        return [], set()
    _CACHE["ts"] = now
    _CACHE["tools"] = list(tools)
    _CACHE["names"] = set(names)
    if names:
        print(f"[MCP] 도구 {len(names)}개 로드: {sorted(names)[:20]}")
    return tools, names
