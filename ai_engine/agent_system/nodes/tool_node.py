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

from langchain_core.callbacks import adispatch_custom_event
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

# ── 검색 진행 이벤트 (deep-research-engine 요구사항 18 / design.md "9-1) 방출") ──
# 검색 도구군에 한해 실행 경계에서 검색 진행 이벤트(`search_status`)를 방출한다.
# start 정확히 1회 → end 정확히 1회(P14)를 try/finally 로 코드에서 강제한다. 비검색 도구는
# 방출 대상이 아니므로 기존 동작이 그대로 보존된다(무회귀).
_SEARCH_TOOLS = frozenset({"web_search", "search_papers", "deep_research"})

# 도구명 → 검색 종류(kind) 매핑(요구사항 18.2). Search_Indicator 라벨 근거:
# web=웹 검색 / academic=논문 검색 / deep=딥리서치.
_SEARCH_KIND = {
    "web_search": "web",
    "search_papers": "academic",
    "deep_research": "deep",
}


def _search_outcome_from_result(result: Any) -> dict:
    """검색 도구 결과에서 **실제 산출**을 추출한다(순수·예외 없음).

    왜 필요한가: ``status`` 는 실행 성공(예외/타임아웃 없음)만 뜻한다. 리서치 도구는
    실패를 예외가 아니라 구조화 dict 로 돌려주므로(비차단 철학), "제공자 키가 없어
    0건"인 경우도 ``status="ok"`` 로 방출됐다. 그래서 검색 인디케이터는 "완료"로 보이고
    사용자는 왜 근거가 없는지 알 수 없었다(실측 사고).

    Args:
        result: 도구 반환값. 리서치 도구는 JSON 문자열(``server._execute_tool`` 가
                ``json.dumps`` 한 결과)이며, dict 가 직접 올 수도 있다.

    Returns:
        ``{}`` 또는 다음 키의 부분집합:
          - ``count``           결과 건수(int)
          - ``error``           도구 수준 오류 코드(str)
          - ``provider_errors`` ``[{"provider","error"}]`` 제공자별 실패 사유
        파싱 불가·형식 불일치는 빈 dict 를 반환해 페이로드를 과거 형태로 유지한다.
        자격증명은 어떤 키에도 담지 않는다(P9 — 원본 dict 에도 존재하지 않음).
    """
    try:
        data = result
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8", "ignore")
        if isinstance(data, str):
            s = data.strip()
            if not s.startswith("{"):
                return {}          # 도구 오류 문자열 등 — 추출할 산출이 없다
            data = json.loads(s)
        if not isinstance(data, dict):
            return {}

        out: dict = {}
        count = data.get("count")
        if isinstance(count, bool):
            count = None           # bool 은 int 하위형이라 명시적으로 배제
        if isinstance(count, int):
            out["count"] = count
        elif isinstance(data.get("results"), list):
            out["count"] = len(data["results"])

        code = data.get("error")
        if isinstance(code, str) and code:
            out["error"] = code

        perrs = data.get("provider_errors")
        if isinstance(perrs, list):
            cleaned = [
                {"provider": str(e.get("provider", "")), "error": str(e.get("error", ""))}
                for e in perrs
                if isinstance(e, dict) and e.get("error")
            ]
            if cleaned:
                out["provider_errors"] = cleaned
        return out
    except Exception:  # noqa: BLE001 — 관측 정보 추출 실패는 비차단(P8)
        return {}


def _search_query_summary(args: Any, max_len: int) -> str:
    """검색 도구 인자에서 질의문을 추출해 ``max_len`` 자로 절단(요구사항 18.2/18.7).

    design "RESEARCH_TOOLS"의 질의 키는 ``query``(문자열)이며, 방어적으로 ``queries``
    (리스트)도 허용한다. 추출 실패 시 빈 문자열을 반환한다(비차단). 자격증명은 도구
    인자에 포함되지 않으므로 요약에도 포함되지 않는다(P9).
    """
    q = ""
    if isinstance(args, dict):
        cand = args.get("query")
        if isinstance(cand, str) and cand:
            q = cand
        elif isinstance(args.get("queries"), list):
            q = " ".join(
                str(x) for x in args["queries"] if isinstance(x, (str, int, float))
            )
    q = q.strip()
    try:
        n = int(max_len)
    except (TypeError, ValueError):
        n = 80
    return q[: n if n > 0 else 0]


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
        """로컬 통합 디스패처 `_execute_tool` 호출(동기).

        ``deps``(GraphDeps: gateway/model_*/checkpointer/store)를 함께 전달해, 리서치
        도구(특히 deep_research)가 Bedrock Gateway 기반 Planner/Generator 를 실제로 사용
        하게 한다(Task 18.1 게이트웨이 배선). 다른 도구는 deps 를 무시하므로 무회귀다.
        """
        import ai_engine.server as _srv  # 지연 import (순환 참조 방지)

        return _srv._execute_tool(
            name,
            args,
            project_path=state.get("project_path", "") or "",
            aws_profile=state.get("aws_profile", "") or "",
            bedrock_user=state.get("bedrock_user", "") or "",
            template_id=state.get("template_id", "") or "",
            deps=self.deps,
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

    async def _emit_search_status(
        self,
        phase: str,
        name: str,
        args: dict,
        status: Optional[str] = None,
        result: Any = None,
    ) -> None:
        """검색 도구 실행 경계에서 ``search_status`` 커스텀 이벤트를 방출(P14/P9/P8).

        - ``adispatch_custom_event("search_status", payload)`` 는 astream_events(v2)에서
          ``on_custom_event(name="search_status")`` 로 표면화되어 sse_bridge(task 20.2)가
          ``{searchStatus: payload}`` 로 중계한다(design "9-1)/9-3)").
        - payload: ``phase``/``kind``/``providers``(이름만)/``query_summary`` (+ ``end`` 에서
          ``status``). **자격증명 미포함(P9)** — providers 는 config 의 제공자 "이름" 목록,
          query_summary 는 절단된 질의문이다.
        - 방출 전체를 ``try/except`` 로 감싸 실패해도 도구 실행·답변 생성을 막지 않는다
          (비차단 — 요구사항 18.5, P8).
        """
        try:
            kind = _SEARCH_KIND.get(name, "web")
            # 제공자 "이름"만 취한다(자격증명 절대 미포함 — P9). config 는 env 에서 이름만 로드.
            from ai_engine.research.config import DeepResearchConfig

            cfg = DeepResearchConfig.from_env()
            if kind == "academic":
                providers = list(cfg.academic_providers)
            elif kind == "deep":
                # 딥리서치는 웹+논문 제공자를 모두 사용한다(design "축 B").
                providers = list(cfg.web_providers) + list(cfg.academic_providers)
            else:  # web
                providers = list(cfg.web_providers)

            payload = {
                "phase": phase,
                "kind": kind,
                "providers": providers,
                "query_summary": _search_query_summary(args, cfg.query_summary_max),
            }
            if phase == "end":
                # 성공/실패 구분(요구사항 18.3). start 에는 status 를 싣지 않는다.
                # ⚠️ status 는 **실행** 결과(예외/타임아웃 없음)만 뜻한다. 도구가 정상
                # 반환했지만 "키가 없어 0건" 같은 조용한 실패는 status="ok" 로 나온다.
                # 그래서 도구 결과에서 실제 산출(count / 제공자 오류)을 추출해 함께 싣는다.
                # 이 정보로 검색 인디케이터가 "완료" 대신 사유를 보여줄 수 있고,
                # effect_ledger 가 "선언 vs 실제" 불일치를 판정할 수 있다.
                payload["status"] = status or "ok"
                payload.update(_search_outcome_from_result(result))

            await adispatch_custom_event("search_status", payload)
        except Exception:  # noqa: BLE001 — 방출 실패는 비차단(요구사항 18.5, P8)
            pass

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
            # 검색 도구군(_SEARCH_TOOLS)에 한해 실행 경계에서 검색 진행 이벤트를 방출한다.
            # start 는 실행 직전 1회, end 는 아래 finally 에서 1회 → "시작 1회 → 종료 1회"(P14).
            # 비검색 도구는 is_search=False 로 방출을 건너뛰어 기존 동작이 그대로 보존된다.
            is_search = name in _SEARCH_TOOLS
            search_status = "ok"
            if is_search:
                await self._emit_search_status("start", name, args)
            # 미디어 생성 도구는 긴 상한 적용(이미지 생성 + 렌더 + 조립).
            try:
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
                    search_status = "error"
                except Exception as e:  # noqa: BLE001 — 도구 실패는 비차단, ToolMessage로 전달
                    raw = f"[도구 실행 오류: {name} — {str(e)[:300]}]"
                    search_status = "error"
            finally:
                # 성공/타임아웃/예외 무관하게 end 를 정확히 1회 방출(P14). 검색 도구에만 해당하며,
                # 방출 자체는 비차단이라 실패해도 도구 결과 처리를 막지 않는다.
                if is_search:
                    # raw(도구 결과)를 함께 넘겨 실제 산출(count/제공자 오류)을 싣는다.
                    await self._emit_search_status(
                        "end", name, args, status=search_status, result=raw
                    )

            # verified_files 디스크 실측 (요구사항 3.7)
            new_files.extend(self._verify_files(name, args, raw, state))

            tool_messages.append(
                ToolMessage(content=str(raw), tool_call_id=tc_id)
            )

        return {"messages": tool_messages, "verified_files": new_files}
