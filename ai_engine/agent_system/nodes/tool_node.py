"""GatewayToolNode — LangGraph ToolNode 역할 노드.

Task 1.10 산출물. design.md의 `GatewayToolNode` 섹션 + API_NOTES.md(항목 4/7)를 근거로 한다.

책임 (요구사항 3.7 / 6.2 / 7.5):
- 마지막 AIMessage의 `tool_calls`(각 tc는 dict: id/name/args — API_NOTES 항목 7)를 순회하며
  각 도구를 실행하고, **각 tool_call 당 ToolMessage 1개**를 반환한다
  (`ToolMessage.tool_call_id == tc["id"]` — API_NOTES 항목 4).
- 원격 세션(`state["is_remote"]` and 브리지가 원격)이면서 브리지가 처리 가능한 도구는
  server.py의 `_call_bridge`로 라우팅(+`_format_bridge_result`), 그 외에는 server.py의
  `_execute_tool`(로컬 통합 디스패처)로 실행한다. **기존 자산 재구현 금지**(요구사항 7.5).
- 로컬/브리지 실행 함수는 모두 **동기**이므로 `asyncio.to_thread`로 감싸고,
  `asyncio.wait_for(..., self.timeout)`으로 도구 1회 실행에 타임아웃을 강제한다(요구사항 6.2).
  타임아웃 시 `[도구 시간 초과: name (Ns)]` 내용의 ToolMessage를 반환한다.
- 파일 생성 도구의 산출물 경로는 `_resolve_relative_for_verify`로 절대경로를 해석한 뒤
  **디스크 실측**(`os.path.isfile` and `getsize > 0`)을 통과한 항목만 verified_files에 추가한다
  (요구사항 3.7 / Property 3). verified_files는 GraphState의 dedup 병합 reducer가 처리한다.

무한대기 차단(과거 hang 이력 대응):
- 모든 도구 실행은 `asyncio.wait_for`로 감싼다. 이 노드는 스트리밍이 아니므로
  (API_NOTES 항목 5의 스트리밍 제너레이터 hang 위험과 무관) wait_for로 안전하게 감쌀 수 있다.

⚠️ 순환 참조 방지 (지연 import):
- `ai_engine.server`는 상단에서 import하지 않는다. server.py가 agent_system 모듈을 import할 수
  있어 순환이 발생하기 때문이다. server 함수는 `__call__`/헬퍼 **내부에서** `import ai_engine.server`
  로 지연 import하고, `_srv._execute_tool`처럼 **모듈 속성으로 접근**한다(테스트 monkeypatch 정합).
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, List, Optional

from langchain_core.messages import ToolMessage

# 원격 SSH 브리지가 처리할 수 있는 도구 집합. server.py `_REMOTE_TOOLS`와 동일하게 유지.
# 이 집합 외의 도구(예: generate_pptx/pdf/image 등 미디어 생성)는 원격 세션이라도
# 항상 로컬 `_execute_tool`로 실행한다(브리지는 파일시스템/명령 계열만 지원).
_BRIDGE_TOOLS = frozenset(
    {"read_file", "write_file", "list_directory", "search_files", "run_command"}
)

# 미디어 생성 도구군 — 이미지 생성(Vertex/Bedrock) + HTML→PNG 렌더 + 문서 조립이
# 포함돼 본질적으로 무겁다(슬라이드/이미지 수에 비례해 수 분까지 소요 가능). 일반 도구의
# 120초 상한으로는 정상 산출물이 timeout 되므로(예: 3장 PPT = 이미지 3장 생성+렌더),
# 이 집합은 더 긴 상한(AE_MEDIA_TOOL_TIMEOUT, 기본 600초)을 적용한다. 상한이 존재하고
# SSE heartbeat 로 연결이 유지되므로 무한대기는 발생하지 않는다.
_MEDIA_TOOLS = frozenset(
    {
        "generate_image",
        "generate_pdf",
        "generate_pptx",
        "generate_docx",
        "generate_xlsx",
        "edit_image",
        "generate_native_diagram",
    }
)


def _default_timeout() -> float:
    """TOOL_NODE_TIMEOUT 기본값(초). 요구사항 6.2: 기본 120초, env AE_TOOL_NODE_TIMEOUT."""
    try:
        return float(os.environ.get("AE_TOOL_NODE_TIMEOUT", "120"))
    except (TypeError, ValueError):
        return 120.0


def _default_media_timeout() -> float:
    """미디어 생성 도구 전용 상한(초). 기본 600초, env AE_MEDIA_TOOL_TIMEOUT."""
    try:
        return float(os.environ.get("AE_MEDIA_TOOL_TIMEOUT", "600"))
    except (TypeError, ValueError):
        return 600.0


# ── 전역 미디어 동시성 세마포어 (부하 폭주 방지) ──
# 병렬 fan-out(Send)에서 여러 워커가 무거운 미디어 도구(이미지/PPTX/PDF 생성 = CPU/IO/
# 게이트웨이 집약)를 동시에 실행하면 로컬 워크스테이션이 포화되어 프로세스 생성 불가 상태에
# 빠질 수 있다(실측된 회귀). 미디어 생성만 전역 세마포어로 직렬화(기본 동시 1개)해 안정화한다.
# 텍스트 도메인(chat/coding/research/ops)의 게이트웨이 호출은 제한하지 않아 병렬성을 유지한다.
def _media_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("AE_MEDIA_CONCURRENCY", "1")))
    except (TypeError, ValueError):
        return 1


_MEDIA_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_media_semaphore() -> asyncio.Semaphore:
    """미디어 동시성 세마포어를 지연 생성(실행 중 이벤트루프에 바인딩)."""
    global _MEDIA_SEMAPHORE
    if _MEDIA_SEMAPHORE is None:
        _MEDIA_SEMAPHORE = asyncio.Semaphore(_media_concurrency())
    return _MEDIA_SEMAPHORE


def _tool_name(t: Any) -> str:
    """도구 정의에서 이름 추출 — dict / BaseTool / Callable 모두 방어적으로 처리.

    API_NOTES 항목 4/6: 표준 ToolNode는 dict를 받지 않지만, 커스텀 노드는 자체 규약이므로
    dict(name 키) / .name 속성 / __name__ 를 순서대로 시도한다.
    """
    if isinstance(t, dict):
        return str(t.get("name", ""))
    name = getattr(t, "name", None)
    if name:
        return str(name)
    return str(getattr(t, "__name__", t))


def _extract_rel_paths(tool_name: str, args: dict, raw: Any) -> List[str]:
    """도구 실행 결과(raw)에서 생성 파일의 (프로젝트) 상대/절대 경로 후보를 추출.

    server.py의 run-agent 경로가 사용하는 추출 규약과 동일:
      - 결과가 JSON dict이고 "error"가 없고 "path"가 있으면 → [path]
      - 결과가 JSON dict이고 "images": [{"path": ...}, ...] 형태면 → 각 항목의 path
      - write_file 도구는 결과가 평문이므로 args["path"]를 사용
    추출 실패/비파일 도구(read_file/run_command 등)는 빈 리스트.
    """
    paths: List[str] = []

    parsed = None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = None
    elif isinstance(raw, dict):
        parsed = raw

    if isinstance(parsed, dict) and "error" not in parsed:
        if isinstance(parsed.get("path"), str) and parsed["path"]:
            paths.append(parsed["path"])
        imgs = parsed.get("images")
        if isinstance(imgs, list):
            for it in imgs:
                if isinstance(it, dict) and isinstance(it.get("path"), str) and it["path"]:
                    paths.append(it["path"])

    # write_file은 평문 결과 → 입력 경로를 산출물로 간주.
    if tool_name == "write_file" and isinstance(args, dict):
        p = args.get("path")
        if isinstance(p, str) and p:
            paths.append(p)

    return paths


class GatewayToolNode:
    """LangGraph `ToolNode` 역할 — 마지막 AIMessage.tool_calls를 실행해 ToolMessage 반환.

    Precondition:  state["messages"][-1]에 `tool_calls`(list[dict])가 존재.
    Postcondition: 각 tool_call 당 ToolMessage 1개(tool_call_id 매칭)를 생성하고,
                   파일 생성 도구는 디스크 실측 통과 항목만 verified_files에 담아 반환.
                   반환 형식: {"messages": [ToolMessage,...], "verified_files": [VerifiedFile,...]}.
    Invariant:     server.py의 `_execute_tool`/`_call_bridge`를 재사용(재구현 금지),
                   각 도구 실행은 asyncio.wait_for(self.timeout)로 감싼다.
    """

    def __init__(self, tools, deps=None, timeout: Optional[float] = None):
        self.deps = deps
        self.timeout = float(timeout) if timeout is not None else _default_timeout()
        # 미디어 생성 도구 전용 상한(일반 도구보다 길다). timeout 을 명시 주입한 경우에도
        # 미디어 상한은 max(주입값, media_default)로 두어 최소한의 여유를 보장한다.
        self.media_timeout = max(self.timeout, _default_media_timeout())
        self.tool_names = {_tool_name(t) for t in (tools or [])}

    # ── 개별 실행 헬퍼 (동기 server 함수를 감쌈; asyncio.to_thread에서 호출) ──

    def _run_local(self, name: str, args: dict, state) -> str:
        """로컬 통합 디스패처 `_execute_tool` 호출(동기)."""
        import ai_engine.server as _srv  # 지연 import (순환 참조 방지)

        return _srv._execute_tool(
            name,
            args,
            project_path=state.get("project_path", "") or "",
            aws_profile=state.get("aws_profile", "") or "",
            bedrock_user=state.get("bedrock_user", "") or "",
            template_id=state.get("template_id", "") or "",
        )

    def _run_bridge(self, name: str, args: dict) -> str:
        """원격 브리지 라우팅 — `_call_bridge` + `_format_bridge_result`(동기)."""
        import ai_engine.server as _srv  # 지연 import (순환 참조 방지)

        br = _srv._call_bridge(name, args, self.timeout)
        if br is None:
            return f"[원격 브리지 연결 실패: {name}]"
        return _srv._format_bridge_result(name, br)

    def _verify_files(self, name: str, args: dict, raw: Any, state) -> List[dict]:
        """산출물 경로를 절대경로로 해석 후 디스크 실측 통과 항목만 VerifiedFile로 반환.

        요구사항 3.7 / Property 3: 실제 디스크에 존재(size > 0)하는 파일만 verified_files에 포함.
        """
        import ai_engine.server as _srv  # 지연 import (순환 참조 방지)

        project_path = state.get("project_path", "") or ""
        out: List[dict] = []
        seen = set()
        for rel in _extract_rel_paths(name, args, raw):
            try:
                abs_path = _srv._resolve_relative_for_verify(rel, project_path)
            except Exception:
                continue
            if abs_path in seen:
                continue
            try:
                if os.path.isfile(abs_path) and os.path.getsize(abs_path) > 0:
                    seen.add(abs_path)
                    out.append({"path": rel, "absPath": abs_path, "tool": name})
            except OSError:
                continue
        return out

    async def __call__(self, state) -> dict:
        # ⚠️ 순차 실행(의도적 트레이드오프): 공식 LangGraph ToolNode 는 tool_calls 를 병렬
        # 실행하지만, 우리 도구는 파일 생성/셸 등 부작용(side-effect)이 있어 동일 경로/자원
        # 경합을 피하기 위해 아래 루프에서 tool_call 을 순차 처리한다(동작 변경 금지).
        import ai_engine.server as _srv  # 지연 import (순환 참조 방지)

        messages = state.get("messages") or []
        if not messages:
            return {"messages": [], "verified_files": []}
        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None) or []

        # 원격 여부는 도구 루프 진입 전 1회만 판정(브리지 status 왕복 최소화).
        is_remote_session = bool(state.get("is_remote"))
        bridge_remote = False
        if is_remote_session:
            try:
                bridge_remote = bool(_srv._bridge_is_remote())
            except Exception:
                bridge_remote = False

        tool_messages: List[ToolMessage] = []
        new_files: List[dict] = []

        for tc in tool_calls:
            name = tc.get("name", "")
            args = tc.get("args") or {}
            tc_id = tc.get("id")

            # MCP 도구(LangChain BaseTool)는 deps.mcp_tool_map 을 통해 ainvoke 로 실행한다.
            mcp_map = getattr(self.deps, "mcp_tool_map", None) or {}
            is_media = name in _MEDIA_TOOLS
            eff_timeout = self.media_timeout if is_media else self.timeout
            if name in mcp_map:
                try:
                    _mcp_tool = mcp_map[name]
                    raw = await asyncio.wait_for(_mcp_tool.ainvoke(args), timeout=eff_timeout)
                except asyncio.TimeoutError:
                    raw = f"[MCP 도구 시간 초과: {name} ({eff_timeout}s)]"
                except Exception as e:  # noqa: BLE001 — MCP 실패는 비차단
                    raw = f"[MCP 도구 오류: {name} — {str(e)[:300]}]"
                # MCP 도구 결과는 파일 산출물이 아니므로 verified_files 미대상.
                tool_messages.append(ToolMessage(content=str(raw), tool_call_id=tc_id))
                continue

            use_bridge = is_remote_session and bridge_remote and name in _BRIDGE_TOOLS
            # 미디어 생성 도구는 긴 상한 적용(이미지 생성 + 렌더 + 조립).
            try:
                if use_bridge:
                    raw = await asyncio.wait_for(
                        asyncio.to_thread(self._run_bridge, name, args),
                        timeout=eff_timeout,
                    )
                elif is_media:
                    # 미디어 생성은 전역 세마포어로 직렬화(부하 폭주 방지). 세마포어 대기는
                    # 타임아웃에 포함하지 않고, 실제 실행만 wait_for 로 감싼다.
                    async with _get_media_semaphore():
                        raw = await asyncio.wait_for(
                            asyncio.to_thread(self._run_local, name, args, state),
                            timeout=eff_timeout,
                        )
                else:
                    raw = await asyncio.wait_for(
                        asyncio.to_thread(self._run_local, name, args, state),
                        timeout=eff_timeout,
                    )
            except asyncio.TimeoutError:
                raw = f"[도구 시간 초과: {name} ({eff_timeout}s)]"
            except Exception as e:  # noqa: BLE001 — 도구 실패는 비차단, ToolMessage로 전달
                raw = f"[도구 실행 오류: {name} — {str(e)[:300]}]"

            # verified_files 디스크 실측 (요구사항 3.7)
            new_files.extend(self._verify_files(name, args, raw, state))

            tool_messages.append(
                ToolMessage(content=str(raw), tool_call_id=tc_id)
            )

        return {"messages": tool_messages, "verified_files": new_files}
