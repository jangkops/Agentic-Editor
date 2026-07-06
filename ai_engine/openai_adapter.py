"""OpenAI Responses → Bedrock Converse 어댑터 (gateway-openai-models).

게이트웨이의 OpenAI Responses 라우트(/openai/responses, /openai/responses-jobs)
응답을 기존 채팅/도구/사용량 흐름이 소비하는 Bedrock Converse 형식으로 변환한다.

게이트웨이 확정 스키마가 OpenAI Responses 표준과 다를 수 있으므로, 후보 키를
방어적으로 순회해 차이를 흡수한다(요구사항 6).

설계 원칙 — 순수 add: 신규 독립 모듈. 기존 코드 import 의존 없음.
참조: design.md "Components and Interfaces 6절", Requirements 6.1~6.5
"""
from __future__ import annotations

_DETAIL_MAX = 200

# 텍스트 추출 후보 키 (우선순위)
_TEXT_KEYS = ("output_text", "output", "content", "text", "message")
# job_id 추출 후보 키
_JOBID_KEYS = ("job_id", "jobId", "id", "job", "task_id", "taskId")
# 상태 추출 후보 키
_STATUS_KEYS = ("status", "state", "job_status", "jobStatus")
# usage 추출 후보 키
_USAGE_KEYS = ("usage", "token_usage", "usageMetadata", "tokenUsage")

# 상태 판정 집합
_STATUS_COMPLETED = ("completed", "succeeded", "success", "done")
_STATUS_FAILED = ("failed", "cancelled", "canceled", "error", "errored")


class InvalidOpenAIResponse(Exception):
    """OpenAI 응답이 예상 스키마와 불일치(출력 텍스트 부재 등). 부분 텍스트 미전달."""

    def __init__(self, detail: str = ""):
        self.detail = (detail or "")[:_DETAIL_MAX]
        super().__init__(self.detail or "invalid-openai-response")


def extract_job_id(raw: dict) -> str:
    """잡 제출 응답에서 job_id를 방어적으로 추출. 없으면 빈 문자열."""
    if not isinstance(raw, dict):
        return ""
    for k in _JOBID_KEYS:
        v = raw.get(k)
        if isinstance(v, (str, int)) and str(v):
            return str(v)
    return ""


def extract_status(raw: dict) -> str:
    """잡 상태를 방어적으로 추출(소문자). 없으면 빈 문자열."""
    if not isinstance(raw, dict):
        return ""
    for k in _STATUS_KEYS:
        v = raw.get(k)
        if isinstance(v, str) and v:
            return v.lower()
    return ""


def status_is_completed(status: str) -> bool:
    return (status or "").lower() in _STATUS_COMPLETED


def status_is_failed(status: str) -> bool:
    return (status or "").lower() in _STATUS_FAILED


def _unwrap_gateway_envelope(raw):
    """게이트웨이 래퍼 언랩 — 실제 OpenAI Responses 객체를 반환한다.

    게이트웨이는 OpenAI 응답을 다음처럼 감싸서 반환한다(라이브 확인 2026-06):
        {"decision":"ALLOW", "output": {<OpenAI response>}, "usage": {...}}
    여기서 내부 <OpenAI response>는 다시 "output": [ ... ] 배열과 "usage"를 갖는다.
    이 함수는 그 내부 객체를 꺼내 어댑터가 기존 로직으로 파싱할 수 있게 한다.
    래퍼가 아니면(이미 OpenAI 객체이면) 원본을 그대로 반환한다.
    """
    if not isinstance(raw, dict):
        return raw
    inner = raw.get("output")
    # 래퍼 판정: output이 dict이고, 그 안에 OpenAI 응답 시그널이 있을 때.
    if isinstance(inner, dict) and (
        "output" in inner
        or inner.get("object") == "response"
        or "output_text" in inner
    ):
        return inner
    return raw


def _collect_text_from_output(output) -> str:
    """output(배열/문자열/dict)을 순회하며 텍스트를 순서대로 수집."""
    parts: list[str] = []

    def _walk(node):
        if node is None:
            return
        if isinstance(node, str):
            if node:
                parts.append(node)
            return
        if isinstance(node, list):
            for it in node:
                _walk(it)
            return
        if isinstance(node, dict):
            # OpenAI Responses: output[].content[].text, 또는 {type:"output_text", text:...}
            if isinstance(node.get("text"), str):
                if node["text"]:
                    parts.append(node["text"])
                return
            if "content" in node:
                _walk(node.get("content"))
                return
            # 일부 변형: {"output_text": "..."}
            if isinstance(node.get("output_text"), str):
                parts.append(node["output_text"])
                return
        # 그 외 타입은 무시
    _walk(output)
    return "".join(parts)


def extract_text(raw: dict) -> str:
    """OpenAI Responses 응답에서 출력 텍스트를 방어적으로 추출.

    output_text 최우선. 없으면 output 배열을 순회하며 content[].text 수집.
    추출 결과가 비면 빈 문자열 반환(호출자 to_converse가 에러로 변환).
    게이트웨이 래퍼({"output": {<OpenAI 응답>}})는 먼저 언랩한다.
    """
    if not isinstance(raw, dict):
        return ""
    raw = _unwrap_gateway_envelope(raw)
    # 1) output_text 최우선 (문자열 또는 문자열 배열)
    ot = raw.get("output_text")
    if isinstance(ot, str) and ot:
        return ot
    if isinstance(ot, list):
        joined = _collect_text_from_output(ot)
        if joined:
            return joined
    # 2) output 배열/객체 순회
    if "output" in raw:
        joined = _collect_text_from_output(raw.get("output"))
        if joined:
            return joined
    # 3) 기타 후보 키
    for k in ("content", "text", "message"):
        if k in raw:
            joined = _collect_text_from_output(raw.get(k))
            if joined:
                return joined
    return ""


def extract_tool_calls(raw: dict) -> list:
    """OpenAI tool/function call → Converse toolUse 블록 목록. 없으면 빈 목록.

    방어적: output 배열에서 type이 function_call/tool_call인 항목, 또는
    tool_calls 필드를 순회.
    """
    blocks: list = []
    if not isinstance(raw, dict):
        return blocks
    raw = _unwrap_gateway_envelope(raw)

    def _emit(call: dict):
        if not isinstance(call, dict):
            return
        name = call.get("name") or call.get("function", {}).get("name") if isinstance(call.get("function"), dict) else call.get("name")
        if not name and isinstance(call.get("function"), dict):
            name = call["function"].get("name")
        call_id = call.get("call_id") or call.get("id") or call.get("toolUseId") or ""
        args = call.get("arguments")
        if args is None and isinstance(call.get("function"), dict):
            args = call["function"].get("arguments")
        # arguments가 JSON 문자열이면 파싱 시도
        parsed_args = args
        if isinstance(args, str):
            try:
                import json as _j
                parsed_args = _j.loads(args)
            except (ValueError, TypeError):
                parsed_args = {"_raw": args}
        if not isinstance(parsed_args, dict):
            parsed_args = {} if parsed_args is None else {"_value": parsed_args}
        if name:
            blocks.append({"toolUse": {"toolUseId": str(call_id) or str(name), "name": str(name), "input": parsed_args}})

    # tool_calls 최상위
    tc = raw.get("tool_calls")
    if isinstance(tc, list):
        for c in tc:
            _emit(c)
    # output 배열 내 function_call
    out = raw.get("output")
    if isinstance(out, list):
        for it in out:
            if isinstance(it, dict) and (it.get("type") in ("function_call", "tool_call") or "function" in it):
                _emit(it)
    return blocks


def extract_usage(raw: dict) -> dict:
    """토큰 사용량을 방어적으로 추출 → {inputTokens, outputTokens}. 부분 누락 시 0."""
    usage = None
    candidates = []
    if isinstance(raw, dict):
        candidates.append(raw)
        unwrapped = _unwrap_gateway_envelope(raw)
        if unwrapped is not raw:
            candidates.append(unwrapped)
    for src in candidates:
        for k in _USAGE_KEYS:
            if isinstance(src.get(k), dict):
                usage = src[k]
                break
        if usage is not None:
            break
    if not isinstance(usage, dict):
        return {"inputTokens": 0, "outputTokens": 0}

    def _g(*keys):
        for kk in keys:
            v = usage.get(kk)
            if isinstance(v, (int, float)):
                return int(v)
        return 0
    return {
        "inputTokens": _g("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"),
        "outputTokens": _g("output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
    }


def to_converse(raw: dict) -> dict:
    """OpenAI Responses 응답 → Bedrock Converse 형식.

    반환: {"decision":"ALLOW",
           "output":{"message":{"content":[{"text":<텍스트>}, <tool blocks>]}},
           "usage": {...}}
    텍스트 추출 실패 → InvalidOpenAIResponse (부분 텍스트 미전달, 요구사항 6.5).
    """
    text = extract_text(raw)
    tool_blocks = extract_tool_calls(raw)
    if not text and not tool_blocks:
        raise InvalidOpenAIResponse("no output text or tool calls in OpenAI response")
    content: list = []
    if text:
        content.append({"text": text})
    content.extend(tool_blocks)
    return {
        "decision": "ALLOW",
        "output": {"message": {"role": "assistant", "content": content}},
        "usage": extract_usage(raw),
    }


def extract_function_calls(raw: dict) -> list:
    """OpenAI Responses 응답에서 function_call 항목을 추출(도구 실행 루프용).

    게이트웨이 래퍼를 먼저 언랩한 뒤 내부 output 배열에서 type=="function_call"
    (또는 tool_call/function 형태)인 항목을 모아 다음 형식으로 반환한다:
        [{"call_id": str, "name": str, "arguments": str(JSON)}]
    arguments는 OpenAI 표준상 JSON 문자열이다(파싱은 호출자 책임).
    """
    inner = _unwrap_gateway_envelope(raw)
    calls = []
    if not isinstance(inner, dict):
        return calls
    out = inner.get("output")
    items = out if isinstance(out, list) else []
    # 최상위 tool_calls도 함께 본다(변형 방어).
    if isinstance(inner.get("tool_calls"), list):
        items = items + inner["tool_calls"]
    for it in items:
        if not isinstance(it, dict):
            continue
        itype = it.get("type") or ""
        is_fc = (itype in ("function_call", "tool_call")) or ("function" in it) or (
            it.get("name") and it.get("arguments") is not None
        )
        if not is_fc:
            continue
        name = it.get("name")
        args = it.get("arguments")
        if (name is None or args is None) and isinstance(it.get("function"), dict):
            name = name or it["function"].get("name")
            args = args if args is not None else it["function"].get("arguments")
        if not name:
            continue
        call_id = it.get("call_id") or it.get("id") or it.get("toolUseId") or str(name)
        if not isinstance(args, str):
            try:
                import json as _j
                args = _j.dumps(args if args is not None else {})
            except (TypeError, ValueError):
                args = "{}"
        calls.append({"call_id": str(call_id), "name": str(name), "arguments": args})
    return calls
