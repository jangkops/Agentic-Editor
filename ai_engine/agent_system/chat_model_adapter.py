"""GatewayChatModel — 정식 LangChain BaseChatModel 어댑터 (Bedrock Gateway 경유).

이 모듈은 LangGraph의 ``bind_tools`` / ``ToolNode`` / ``astream_events`` 와 정합하는
정식 ``langchain_core`` ``BaseChatModel`` 구현을 제공한다. 모든 LLM 호출은
``GatewayClient`` (SigV4 / assume-role)를 경유하며, 직접 boto3 / Anthropic / OpenAI
SDK 를 import 하지 않는다(요구사항 2.2, Property 1).

Bedrock converse 의 ``toolUse`` 블록과 LangChain ``ToolCall`` 을 상호 변환하여 표준
LangGraph 도구 호출 루프를 구성할 수 있게 한다.

실측 근거: API_NOTES.md (langchain-core 1.3.0 / langgraph 1.1.9) 및
``ai_engine/gateway_module.py`` 의 실제 메서드 시그니처.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any, AsyncIterator, Callable, Optional, Sequence

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import Runnable
from pydantic import PrivateAttr


class GatewayModelError(RuntimeError):
    """Gateway 레벨 오류(DENY / ERROR / 타임아웃)를 그래프로 전파한다."""


# ─────────────────────────────────────────────────────────────────────────────
# 변환 헬퍼 (모듈 레벨 — 단위/속성 테스트에서 직접 import 가능)
# ─────────────────────────────────────────────────────────────────────────────
def _lc_tool_to_bedrock_toolspec(tool: Any) -> dict:
    """LangChain 도구 → Bedrock ``toolSpec``.

    반환 형식::

        {"toolSpec": {"name": str, "description": str,
                      "inputSchema": {"json": <JSON Schema>}}}

    dict(이미 toolSpec / 평문 정의) / ``BaseTool`` / pydantic 타입 / Callable 을
    방어적으로 처리한다.
    """
    # 이미 Bedrock toolSpec 형태
    if isinstance(tool, dict) and "toolSpec" in tool:
        return tool

    # 평문 dict 정의
    if isinstance(tool, dict):
        name = tool.get("name") or tool.get("title") or ""
        description = tool.get("description", "") or ""
        schema = None
        input_schema = tool.get("inputSchema")
        if isinstance(input_schema, dict):
            schema = input_schema.get("json")
        schema = (
            schema
            or tool.get("input_schema")
            or tool.get("parameters")
            or {"type": "object", "properties": {}}
        )
        return {
            "toolSpec": {
                "name": name,
                "description": description,
                "inputSchema": {"json": schema},
            }
        }

    # BaseTool / pydantic / Callable → langchain 표준 변환기 사용
    try:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        oai = convert_to_openai_tool(tool)
        fn = oai.get("function", {}) if isinstance(oai, dict) else {}
        return {
            "toolSpec": {
                "name": fn.get("name", ""),
                "description": fn.get("description", "") or "",
                "inputSchema": {
                    "json": fn.get("parameters", {"type": "object", "properties": {}})
                },
            }
        }
    except Exception:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", "tool")
        description = getattr(tool, "description", "") or (getattr(tool, "__doc__", "") or "")
        return {
            "toolSpec": {
                "name": name,
                "description": description,
                "inputSchema": {"json": {"type": "object", "properties": {}}},
            }
        }


def _image_block_from_lc(part: dict) -> Optional[dict]:
    """LangChain 멀티모달 이미지 파트 → Bedrock ``{"image": {...}}`` 블록.

    지원: ``{"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}``
    및 ``{"type":"image","source":{"data"/"bytes",...}}`` 형태.
    변환 불가면 None.
    """
    fmt = "png"
    raw: Optional[bytes] = None

    if part.get("type") == "image_url":
        url_obj = part.get("image_url")
        url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
        if isinstance(url, str) and url.startswith("data:"):
            try:
                header, b64 = url.split(",", 1)
                # data:image/png;base64
                if "/" in header and ";" in header:
                    fmt = header.split("/", 1)[1].split(";", 1)[0] or "png"
                raw = base64.b64decode(b64)
            except Exception:
                return None
    elif part.get("type") == "image":
        source = part.get("source", {})
        fmt = part.get("format") or source.get("media_type", "image/png").split("/")[-1] or "png"
        data = source.get("bytes") or source.get("data")
        if isinstance(data, bytes):
            raw = data
        elif isinstance(data, str):
            try:
                raw = base64.b64decode(data)
            except Exception:
                return None

    if raw is None:
        return None
    if fmt == "jpg":
        fmt = "jpeg"
    return {"image": {"format": fmt, "source": {"bytes": raw}}}


def _lc_content_to_blocks(content: Any) -> list:
    """LangChain 메시지 content(str | list) → Bedrock content 블록 리스트."""
    blocks: list = []
    if isinstance(content, str):
        if content:
            blocks.append({"text": content})
        return blocks
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                if part:
                    blocks.append({"text": part})
            elif isinstance(part, dict):
                ptype = part.get("type")
                if ptype == "text" or ("text" in part and ptype is None):
                    txt = part.get("text", "")
                    if txt:
                        blocks.append({"text": txt})
                elif ptype in ("image_url", "image"):
                    img = _image_block_from_lc(part)
                    if img:
                        blocks.append(img)
                elif "text" in part:
                    blocks.append({"text": part["text"]})
        return blocks
    # 기타 타입 → 문자열화
    if content:
        blocks.append({"text": str(content)})
    return blocks


def _lc_messages_to_bedrock(messages: Sequence[BaseMessage]) -> tuple[list, str]:
    """LangChain 메시지 → (Bedrock converse messages, system_text).

    - SystemMessage 는 병합되어 system_text 로 반환(messages 에는 미포함).
    - HumanMessage → user 역할(멀티모달 이미지 첨부 포함).
    - AIMessage.tool_calls → assistant 역할 ``toolUse`` 블록(+ 텍스트).
    - ToolMessage → user 역할 ``toolResult`` 블록.
    - user/assistant 교대 규칙 준수(연속 동일 role 병합, 선두 assistant 방지).
    """
    system_parts: list[str] = []
    raw: list[dict] = []  # {"role", "content": [...]}

    for msg in messages:
        if isinstance(msg, SystemMessage):
            text = msg.content if isinstance(msg.content, str) else _joined_text(msg.content)
            if text:
                system_parts.append(text)
            continue

        if isinstance(msg, HumanMessage):
            raw.append({"role": "user", "content": _lc_content_to_blocks(msg.content)})
            continue

        if isinstance(msg, AIMessage):
            blocks: list = []
            text = msg.content if isinstance(msg.content, str) else _joined_text(msg.content)
            if text:
                blocks.append({"text": text})
            for tc in (msg.tool_calls or []):
                blocks.append(
                    {
                        "toolUse": {
                            "toolUseId": tc.get("id") or "",
                            "name": tc.get("name") or "",
                            "input": tc.get("args") or {},
                        }
                    }
                )
            if not blocks:
                blocks.append({"text": ""})
            raw.append({"role": "assistant", "content": blocks})
            continue

        if isinstance(msg, ToolMessage):
            tool_content = msg.content
            if isinstance(tool_content, str):
                result_blocks = [{"text": tool_content}]
            else:
                result_blocks = _lc_content_to_blocks(tool_content) or [{"text": ""}]
            raw.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "toolResult": {
                                "toolUseId": msg.tool_call_id or "",
                                "content": result_blocks,
                            }
                        }
                    ],
                }
            )
            continue

        # 알 수 없는 메시지 타입 → user 텍스트로 폴백
        raw.append({"role": "user", "content": _lc_content_to_blocks(getattr(msg, "content", str(msg)))})

    merged = _enforce_alternation(raw)
    return merged, "\n\n".join(system_parts)


def bedrock_messages_to_lc(messages: Any) -> list:
    """Bedrock converse messages(dict) → LangChain BaseMessage 리스트 (역변환).

    ConversationMemory.build_messages 가 만드는 Bedrock 형식
    ``[{"role": "user"/"assistant", "content": [{"text": ...} | {"image": ...}]}]`` 을
    LangGraph 초기 상태의 messages(LangChain 메시지)로 되돌린다. 멀티턴 대화 맥락 복원
    (요구사항: graph-stream 멀티턴 회귀 수정)에 사용한다.

    - text 블록은 이어붙여 content(str)로.
    - image 블록(과거 첨부)은 텍스트 맥락 복원 목적상 ``[이미지 첨부됨]`` placeholder 로
      대체한다(현재 턴 프롬프트의 실제 이미지 처리와 무관 — 히스토리 텍스트 맥락만 복원).
    - role=assistant → AIMessage, 그 외 → HumanMessage.
    - 입력이 비었거나 형식이 어긋나면 빈 리스트를 반환(비차단).
    """
    out: list = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        text_parts: list[str] = []
        has_image = False
        if isinstance(content, str):
            if content:
                text_parts.append(content)
        elif isinstance(content, list):
            for b in content:
                if isinstance(b, dict):
                    if isinstance(b.get("text"), str) and b["text"]:
                        text_parts.append(b["text"])
                    elif "image" in b:
                        has_image = True
                elif isinstance(b, str) and b:
                    text_parts.append(b)
        text = "\n".join(text_parts)
        if has_image and role == "user":
            text = (text + "\n[이미지 첨부됨]").strip()
        if role == "assistant":
            out.append(AIMessage(content=text))
        else:
            out.append(HumanMessage(content=text))
    return out


def _joined_text(content: Any) -> str:
    """content(list) 중 텍스트 파트만 이어붙인다."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    if isinstance(content, list):
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and "text" in p:
                parts.append(p["text"])
    return "".join(parts)


def _enforce_alternation(raw: list[dict]) -> list[dict]:
    """연속된 동일 role 메시지를 병합하고 선두 assistant 를 방지한다."""
    merged: list[dict] = []
    for item in raw:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"].extend(item["content"])
        else:
            merged.append({"role": item["role"], "content": list(item["content"])})
    # Bedrock 은 첫 메시지가 user 여야 한다 — 선두 assistant 면 빈 user 프리픽스 추가
    if merged and merged[0]["role"] != "user":
        merged.insert(0, {"role": "user", "content": [{"text": ""}]})
    return merged


def _bedrock_output_to_ai_message(message: dict) -> AIMessage:
    """Bedrock converse output message → LangChain AIMessage(+tool_calls).

    - 모든 ``{"text"}`` 블록을 이어붙여 content(str).
    - 각 ``{"toolUse":{"toolUseId","name","input"}}`` → ToolCall(id, name, args=input).
    - tool_calls 가 하나라도 있으면 AIMessage.tool_calls 를 채운다(ToolNode 소비용).
    """
    content_blocks = (message or {}).get("content", []) or []
    texts: list[str] = []
    tool_calls: list[dict] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        if "text" in block:
            texts.append(block.get("text", ""))
        elif "toolUse" in block:
            tu = block["toolUse"] or {}
            tool_calls.append(
                {
                    "name": tu.get("name", "") or "",
                    "args": tu.get("input", {}) or {},
                    "id": tu.get("toolUseId", "") or "",
                    "type": "tool_call",
                }
            )
    text = "".join(texts)
    if tool_calls:
        return AIMessage(content=text, tool_calls=tool_calls)
    return AIMessage(content=text)


def _tooluse_delta_to_chunk(partial: dict, index: int = 0) -> AIMessageChunk:
    """스트리밍 toolUse 조각 → tool_call_chunks 누적용 AIMessageChunk.

    partial 은 ``{"toolUseId"?, "name"?, "input"?}`` 형태이며 input 은 JSON 문자열
    조각(부분)일 수 있다.
    """
    args = partial.get("input")
    if args is not None and not isinstance(args, str):
        # 완성된 dict 가 들어오면 문자열로 직렬화
        import json

        args = json.dumps(args)
    chunk_spec = {
        "name": partial.get("name"),
        "args": args,
        "id": partial.get("toolUseId"),
        "index": index,
        "type": "tool_call_chunk",
    }
    return AIMessageChunk(content="", tool_call_chunks=[chunk_spec])


# ─────────────────────────────────────────────────────────────────────────────
# GatewayChatModel
# ─────────────────────────────────────────────────────────────────────────────
class GatewayChatModel(BaseChatModel):
    """Bedrock Gateway 를 경유하는 LangChain 채팅 모델.

    Precondition:  gateway 는 converse / converse_stream_live / stream_sse_realtime
                   메서드를 제공한다. model_id 는 비어있지 않다.
    Postcondition: _generate / _agenerate 는 ChatResult(하나 이상 ChatGeneration)를
                   반환. 반환된 AIMessage 는 tool_calls 를 정확히 반영
                   (Bedrock toolUse ↔ LangChain ToolCall).
    Invariant:     LLM 호출은 반드시 self.gateway 경유(직접 SDK 금지).
    """

    gateway: Any = None  # GatewayClient (arbitrary_types_allowed=True)
    model_id: str = ""
    request_timeout: float = 300.0
    # 비스트리밍 ainvoke(_agenerate) 를 스트리밍 경로(converse_stream_live)로 처리할지 여부.
    # 게이트웨이 /converse 는 일부 모델·toolConfig 호출을 비동기 S3 잡 폴링으로 처리해 느리다
    # (라이브 실측: 동일 select_plan 호출 converse 35s vs stream_live 7.6s, 약 4.6x). 스트리밍
    # 경로는 실시간 반환 + toolUse 를 converse 호환 형태로 조립하므로, 지연이 중요한 reasoning
    # 메타 노드(router/planner/evaluator/aggregate)가 opt-in 으로 켠다. 스트리밍 호출이 실패하면
    # converse 로 자동 폴백해 무회귀를 보장한다(기본 False — 도메인 워커 등 기존 경로 불변).
    prefer_streaming: bool = False

    # 도구 바인딩 상태는 pydantic model field 가 아니라 private attr / self.bind(...) 로 관리.
    # (API_NOTES 5) 실제 도구는 bind_tools 가 self.bind(_bedrock_tool_config=...)로 넘기고
    # _agenerate / _astream 의 **kwargs 에서 읽는다. 아래는 폴백용.
    _bound_tools: Optional[list] = PrivateAttr(default=None)

    def __init__(
        self,
        gateway: Any = None,
        model_id: str = "",
        *,
        gateway_client: Any = None,
        **kwargs: Any,
    ) -> None:
        # 하위 호환: 기존 코드(agent_graph.py)는 GatewayChatModel(gateway_client, model_id)
        # 형태의 위치 인자 및 gateway_client 키워드를 사용한다.
        if gateway is None and gateway_client is not None:
            gateway = gateway_client
        super().__init__(gateway=gateway, model_id=model_id, **kwargs)

    @property
    def _llm_type(self) -> str:
        return "bedrock-gateway"

    # ── 도구 바인딩: LangChain tool → Bedrock toolConfig ──
    def bind_tools(
        self,
        tools: Sequence[dict | type | Callable | Any],
        *,
        tool_choice: Optional[str] = None,
        **kwargs: Any,
    ) -> Runnable[Any, BaseMessage]:
        """LangChain 도구 정의를 Bedrock toolConfig 형식으로 변환해 바인딩.

        반환된 Runnable 은 매 호출 시 ``_bedrock_tool_config`` 를 kwargs 로 주입하며,
        _agenerate / _astream 이 이를 읽어 gateway 에 toolConfig 로 전달한다.
        """
        bedrock_tools = [_lc_tool_to_bedrock_toolspec(t) for t in tools]
        tool_config = {
            "tools": bedrock_tools,
            "toolChoice": _normalize_tool_choice(tool_choice),
        }
        return self.bind(_bedrock_tool_config=tool_config, **kwargs)

    # ── 동기 경로 (LangGraph 는 async 가 기본이나 BaseChatModel 추상 계약 충족) ──
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None

        coro_factory = lambda: self._agenerate(messages, stop, None, **kwargs)

        if running is not None and running.is_running():
            # 이미 실행 중인 이벤트 루프 안에서 동기 호출됨 → 별도 스레드에서 새 루프로 실행
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: asyncio.run(coro_factory()))
                return future.result()
        return asyncio.run(coro_factory())

    # ── 비동기 non-stream ──
    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        bedrock_msgs, system_text = _lc_messages_to_bedrock(messages)
        tool_config = kwargs.get("_bedrock_tool_config") or self._bound_tools

        result = await self._invoke_gateway(bedrock_msgs, system_text, tool_config)
        try:
            _raise_on_gateway_error(result)
        except GatewayModelError as e:
            # 도구 미지원 모델(nemotron 등) → 도구 없이 1회 재시도(graceful degradation).
            # 도구 거부가 아닌 오류(토큰 만료/allowlist 등)는 그대로 전파.
            if tool_config and _is_tool_rejection(str(e)):
                result = await self._invoke_gateway(bedrock_msgs, system_text, None)
                _raise_on_gateway_error(result)
            else:
                raise

        output_message = (result.get("output") or {}).get("message") or {}
        ai_message = _bedrock_output_to_ai_message(output_message)
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    async def _invoke_gateway(self, bedrock_msgs: list, system_text: str, tool_config: Any) -> dict:
        """단발(non-stream) 게이트웨이 호출 — prefer_streaming 이면 스트리밍 경로 우선.

        prefer_streaming=True 이면 converse_stream_live(실시간 SSE, toolUse 를 converse 호환
        output.message 로 조립)를 먼저 시도하고, 그 결과가 ERROR 이거나 예외면 converse 로
        폴백한다(무회귀 안전장치). prefer_streaming=False 면 기존대로 converse 만 사용한다.
        어느 경로든 반환 형태는 동일한 converse 결과 dict 이다.
        """
        use_stream = bool(self.prefer_streaming) and hasattr(self.gateway, "converse_stream_live")
        if use_stream:
            try:
                streamed = await self.gateway.converse_stream_live(
                    model_id=self.model_id,
                    messages=bedrock_msgs,
                    system_prompt=system_text,
                    tool_config=tool_config,
                )
                # 스트리밍이 정상(ALLOW + output) 이면 그대로 사용. ERROR/빈 결과면 converse 폴백.
                if isinstance(streamed, dict) and streamed.get("decision") == "ALLOW" and (streamed.get("output") or {}).get("message"):
                    return streamed
            except Exception:  # noqa: BLE001 — 스트리밍 실패는 비차단, converse 로 폴백.
                pass
        return await self.gateway.converse(
            model_id=self.model_id,
            messages=bedrock_msgs,
            system_prompt=system_text,
            tool_config=tool_config,
        )

    # ── 비동기 스트리밍: astream_events 가 소비 ──
    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        bedrock_msgs, system_text = _lc_messages_to_bedrock(messages)
        tool_config = kwargs.get("_bedrock_tool_config") or self._bound_tools

        tool_index = -1  # content_block_start(toolUse) 마다 증가

        # 주의(API_NOTES CRITICAL 2): 스트림 소비 루프 전체를 asyncio.wait_for 로
        # 감싸면 Python 3.14 에서 취소 시 hang. 개별 await 만 필요 시 감싼다.
        async for evt in self.gateway.stream_sse_realtime(
            model_id=self.model_id,
            messages=bedrock_msgs,
            system_prompt=system_text,
            tool_config=tool_config,
        ):
            etype = evt.get("type", "")

            if etype == "content_block_start":
                cb = evt.get("content_block") or evt.get("contentBlock") or {}
                if isinstance(cb, dict) and "toolUse" in cb:
                    tool_index += 1
                    tu = cb["toolUse"] or {}
                    chunk = _tooluse_delta_to_chunk(
                        {"toolUseId": tu.get("toolUseId"), "name": tu.get("name"), "input": ""},
                        index=tool_index,
                    )
                    yield ChatGenerationChunk(message=chunk)

            elif etype == "content_block_delta":
                delta = evt.get("delta", {}) or {}
                if "text" in delta:
                    text = delta["text"]
                    chunk = AIMessageChunk(content=text)
                    if run_manager:
                        await run_manager.on_llm_new_token(
                            text, chunk=ChatGenerationChunk(message=chunk)
                        )
                    yield ChatGenerationChunk(message=chunk)
                elif "toolUse" in delta:
                    tu = delta["toolUse"] or {}
                    frag = tu.get("input", "")
                    idx = tool_index if tool_index >= 0 else 0
                    yield ChatGenerationChunk(
                        message=_tooluse_delta_to_chunk({"input": frag}, index=idx)
                    )

            elif etype == "heartbeat":
                # keep-alive — 상위(sse_bridge)에서 heartbeat SSE 로 변환.
                yield ChatGenerationChunk(
                    message=AIMessageChunk(content=""),
                    generation_info={"heartbeat": True},
                )

            elif etype == "error":
                raise GatewayModelError(
                    evt.get("message") or evt.get("error") or "stream error"
                )
            # message_delta / message_stop / settlement 등은 토큰 스트림과 무관 → 무시


def _normalize_tool_choice(tool_choice: Optional[str]) -> dict:
    """LangChain tool_choice → Bedrock toolChoice 형식."""
    if tool_choice is None or tool_choice == "auto":
        return {"auto": {}}
    if tool_choice in ("any", "required"):
        return {"any": {}}
    if isinstance(tool_choice, str):
        return {"tool": {"name": tool_choice}}
    if isinstance(tool_choice, dict):
        return tool_choice
    return {"auto": {}}


def _is_tool_rejection(msg: str) -> bool:
    """게이트웨이 오류 메시지가 '도구(toolConfig/tool_use) 미지원/거부'인지 판정.

    도구와 무관한 오류(토큰 만료/allowlist 거부 등)에는 매칭되지 않도록 'tool' 언급을
    전제로 한다.
    """
    low = (msg or "").lower()
    if "tool" not in low:
        return False
    return any(k in low for k in (
        "not support", "unsupported", "invalid", "does not support",
        "doesn't support", "not allowed", "cannot use", "toolconfig", "tooluse",
    ))


def _raise_on_gateway_error(result: dict) -> None:
    """decision 이 ERROR/DENY 이거나 error 필드가 있으면 GatewayModelError 발생(요구사항 2.7)."""
    if not isinstance(result, dict):
        raise GatewayModelError(f"unexpected gateway result: {type(result)!r}")
    decision = result.get("decision")
    if decision in ("ERROR", "DENY") or result.get("error"):
        message = (
            result.get("error")
            or result.get("denial_reason")
            or f"gateway decision={decision}"
        )
        raise GatewayModelError(str(message))
