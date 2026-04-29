"""FastAPI server — AI Editor backend."""
import os
import json
import uuid
import asyncio
import subprocess
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Editor Engine", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ===== Agent Tool Definitions =====
AGENT_TOOLS = {
    "tools": [
        {
            "toolSpec": {
                "name": "read_file",
                "description": "파일 내용을 읽습니다. 프로젝트 내 모든 파일을 읽을 수 있습니다.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "읽을 파일의 절대 경로 또는 프로젝트 상대 경로"}
                        },
                        "required": ["path"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "write_file",
                "description": "파일에 내용을 씁니다. 새 파일 생성 또는 기존 파일 덮어쓰기.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "쓸 파일의 절대 경로"},
                            "content": {"type": "string", "description": "파일에 쓸 내용"}
                        },
                        "required": ["path", "content"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "list_directory",
                "description": "디렉토리의 파일/폴더 목록을 반환합니다.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "탐색할 디렉토리 경로"}
                        },
                        "required": ["path"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "run_command",
                "description": "터미널 명령어를 실행하고 결과를 반환합니다. git, npm, pip 등 모든 CLI 도구 사용 가능.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "실행할 셸 명령어"},
                            "cwd": {"type": "string", "description": "작업 디렉토리 (선택)"}
                        },
                        "required": ["command"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "search_files",
                "description": "프로젝트 내 파일에서 텍스트를 검색합니다 (grep).",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "검색할 텍스트 또는 정규식"},
                            "path": {"type": "string", "description": "검색할 디렉토리 경로"},
                            "file_pattern": {"type": "string", "description": "파일 패턴 (예: *.py, *.js)"}
                        },
                        "required": ["query", "path"]
                    }
                }
            }
        }
    ]
}


def _execute_tool(tool_name: str, tool_input: dict, project_path: str = "") -> str:
    """도구를 실행하고 결과를 문자열로 반환."""
    try:
        if tool_name == "read_file":
            path = tool_input["path"]
            if not os.path.isabs(path) and project_path:
                path = os.path.join(project_path, path)
            if not os.path.exists(path):
                return f"파일 없음: {path}"
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            if len(content) > 30000:
                content = content[:30000] + f"\n... (총 {len(content)}자, 30000자까지 표시)"
            return content

        elif tool_name == "write_file":
            path = tool_input["path"]
            if not os.path.isabs(path) and project_path:
                path = os.path.join(project_path, path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(tool_input["content"])
            return f"파일 저장 완료: {path} ({len(tool_input['content'])}자)"

        elif tool_name == "list_directory":
            path = tool_input["path"]
            if not os.path.isabs(path) and project_path:
                path = os.path.join(project_path, path)
            if not os.path.isdir(path):
                return f"디렉토리 없음: {path}"
            entries = os.listdir(path)
            result = []
            for e in sorted(entries):
                if e.startswith('.') and e not in ('.env', '.gitignore'):
                    continue
                fp = os.path.join(path, e)
                kind = "DIR" if os.path.isdir(fp) else "FILE"
                result.append(f"  {kind}  {e}")
            return f"{path}/ ({len(result)}개)\n" + "\n".join(result[:100])

        elif tool_name == "run_command":
            cmd = tool_input["command"]
            cwd = tool_input.get("cwd", project_path or os.getcwd())
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=cwd,
                env={**os.environ, "PATH": os.environ.get("PATH", "")},
            )
            output = result.stdout + result.stderr
            if len(output) > 10000:
                output = output[:10000] + "\n... (출력 잘림)"
            return output or "(출력 없음)"

        elif tool_name == "search_files":
            query = tool_input["query"]
            path = tool_input["path"]
            if not os.path.isabs(path) and project_path:
                path = os.path.join(project_path, path)
            pattern = tool_input.get("file_pattern", "")
            include = f"--include='{pattern}'" if pattern else ""
            cmd = f"grep -rn {include} --color=never '{query}' '{path}' 2>/dev/null | head -50"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            return result.stdout or "검색 결과 없음"

        else:
            return f"알 수 없는 도구: {tool_name}"
    except subprocess.TimeoutExpired:
        return "명령 실행 시간 초과 (30초)"
    except Exception as e:
        return f"도구 실행 오류: {str(e)}"


# GatewayClient 캐시 — 동일 profile+user 조합은 재사용
_gw_cache = {}


def _is_code_related(prompt: str) -> bool:
    """프롬프트가 코드/프로젝트 관련인지 판단."""
    p = prompt.lower().strip()
    # 코드 관련 키워드
    code_keySwords = [
        'code', 'function', 'class', 'import', 'error', 'bug', 'fix',
        'implement', 'refactor', 'test', 'deploy', 'build', 'compile',
        '코드', '함수', '클래스', '에러', '버그', '수정', '구현', '리팩토링',
        '파일', '모듈', '컴포넌트', '테스트', '배포', '빌드', '변수',
        'api', 'endpoint', 'database', 'query', 'schema', 'migration',
        'this file', 'this project', '이 파일', '이 프로젝트', '현재',
        '경로', '폴더', '디렉토리', '열린', '오픈', 'path', 'directory',
        '에디터', 'editor', 'project', '프로젝트',
        '.js', '.py', '.ts', '.css', '.html', '.json',
    ]
    for kw in code_keywords:
        if kw in p:
            return True
    # 200자 이상이면 코드 관련일 가능성 높음
    if len(p) > 200:
        return True
    return False


def _build_messages(chat_history: list, current_prompt: str, session_id: str = "") -> list:
    """ConversationMemory를 통해 messages 구성."""
    from ai_engine.rag.conversation_memory import get_memory
    mem = get_memory()
    messages, _ = mem.build_messages(session_id or "default", chat_history, current_prompt)
    return messages

async def _maybe_summarize(session_id: str, chat_history: list, gw):
    """대화가 길어지면 비동기로 요약 체크포인트 생성."""
    try:
        from ai_engine.rag.conversation_memory import get_memory
        mem = get_memory()
        _, needs = mem.build_messages(session_id, chat_history, "")
        if needs:
            await mem.summarize_and_checkpoint(session_id, chat_history, gw)
    except Exception as e:
        print(f"[Memory] 요약 트리거 실패: {e}")


def _get_gw(aws_profile, bedrock_user):
    key = f"{aws_profile}:{bedrock_user}"
    if key not in _gw_cache:
        from ai_engine.gateway_module import GatewayClient
        _gw_cache[key] = GatewayClient(
            gateway_url=os.environ.get("GATEWAY_URL", "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1"),
            aws_profile=aws_profile,
            region=os.environ.get("AWS_REGION", "us-west-2"),
            bedrock_user=bedrock_user,
        )
    gw = _gw_cache[key]
    # 주입된 자격증명이 있으면 캐시 만료하지 않음
    if not hasattr(gw, '_injected_creds') or not gw._injected_creds:
        gw._cred_time = 0
    return gw


def _is_expired_error(result):
    """응답이 토큰 만료 에러인지 판단."""
    err = ""
    if isinstance(result, dict):
        err = result.get("error", "")
    elif isinstance(result, str):
        err = result
    low = err.lower()
    return "expired" in low or "security token" in low


async def _refresh_and_retry_gw(gw, aws_profile, bedrock_user):
    """토큰 만료 시 자격증명을 다시 assume role하여 주입."""
    try:
        import boto3 as b3
        from botocore.credentials import Credentials as BotoCreds
        # boto3 세션 캐시 초기화
        b3.DEFAULT_SESSION = None
        gw.force_refresh_creds()
        session = b3.Session(profile_name=aws_profile)
        sts = session.client("sts")
        account = sts.get_caller_identity()["Account"]
        if bedrock_user:
            assumed = sts.assume_role(
                RoleArn=f"arn:aws:iam::{account}:role/BedrockUser-{bedrock_user}",
                RoleSessionName="ai-editor-refresh",
            )
            c = assumed["Credentials"]
            gw.inject_credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
            print(f"[AutoRefresh] BedrockUser-{bedrock_user} 자격증명 재주입 성공")
            return True
        else:
            fc = session.get_credentials().get_frozen_credentials()
            gw.inject_credentials(fc.access_key, fc.secret_key, fc.token)
            print(f"[AutoRefresh] 프로파일 {aws_profile} 자격증명 재주입 성공")
            return True
    except Exception as e:
        print(f"[AutoRefresh] 자격증명 재주입 실패: {e}")
        return False


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "0.3.0",
    }


@app.post("/api/reset-cache")
async def reset_cache(request: Request):
    """Gateway 클라이언트 캐시 초기화 + 선택적 자격증명 주입."""
    _gw_cache.clear()
    _quota_cache["used_krw"] = 0
    _quota_cache["remaining_krw"] = 0
    _quota_cache["limit_krw"] = 0
    _quota_cache["last_updated"] = ""
    _quota_cache["user"] = ""
    try:
        import boto3
        boto3.DEFAULT_SESSION = None
    except Exception:
        pass
    # 자격증명 직접 주입 (Electron에서 전달)
    try:
        body = await request.json()
        creds = body.get("credentials")
        if creds and creds.get("AWS_ACCESS_KEY_ID"):
            profile = body.get("profile", "bedrock-gw")
            user = body.get("bedrockUser", "")
            # 새 GatewayClient 생성 후 자격증명 주입
            from ai_engine.gateway_module import GatewayClient
            gw = GatewayClient(
                gateway_url=os.environ.get("GATEWAY_URL", "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1"),
                aws_profile=profile,
                region=os.environ.get("AWS_REGION", "us-west-2"),
                bedrock_user=user,
            )
            # SSO 기본 자격증명으로 BedrockUser assume role 시도
            try:
                import boto3 as b3
                from botocore.credentials import Credentials as BotoCreds
                # 전달받은 SSO 자격증명으로 임시 세션 생성
                tmp_session = b3.Session()
                sts = tmp_session.client(
                    "sts",
                    aws_access_key_id=creds["AWS_ACCESS_KEY_ID"],
                    aws_secret_access_key=creds["AWS_SECRET_ACCESS_KEY"],
                    aws_session_token=creds.get("AWS_SESSION_TOKEN", ""),
                    region_name=creds.get("AWS_DEFAULT_REGION", "us-west-2"),
                )
                account = sts.get_caller_identity()["Account"]
                if user:
                    assumed = sts.assume_role(
                        RoleArn=f"arn:aws:iam::{account}:role/BedrockUser-{user}",
                        RoleSessionName="ai-editor",
                    )
                    c = assumed["Credentials"]
                    gw.inject_credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
                    print(f"[Cache] BedrockUser-{user} assume role 성공")
                else:
                    gw.inject_credentials(
                        creds["AWS_ACCESS_KEY_ID"],
                        creds["AWS_SECRET_ACCESS_KEY"],
                        creds.get("AWS_SESSION_TOKEN", ""),
                    )
                key = f"{profile}:{user}"
                _gw_cache[key] = gw
            except Exception as e:
                print(f"[Cache] assume role 실패: {e}")
    except Exception:
        pass
    return {"status": "ok", "message": "cache cleared"}


@app.post("/api/rag/index")
async def rag_index(request: Request):
    """프로젝트 인덱싱 수동 트리거."""
    body = await request.json()
    project_path = body.get("projectPath", "")
    if not project_path or not os.path.isdir(project_path):
        return JSONResponse(content={"error": "Invalid project path"}, status_code=400)
    from ai_engine.rag.context_builder import get_indexer
    idx = get_indexer(project_path)
    count = idx.index_project(project_path)
    return {"status": "ok", "chunks": count, "files": len(set(c.file_path for c in idx.chunks))}


@app.get("/api/rag/status")
async def rag_status(request: Request):
    """RAG 인덱싱 상태 조회."""
    project_path = request.query_params.get("projectPath", "")
    if not project_path:
        return {"indexed": False, "chunks": 0}
    from ai_engine.rag.context_builder import _indexer_cache
    if project_path in _indexer_cache:
        idx = _indexer_cache[project_path]
        return {"indexed": True, "chunks": len(idx.chunks), "files": len(set(c.file_path for c in idx.chunks))}
    return {"indexed": False, "chunks": 0}


@app.get("/api/models")
@app.post("/api/models")
async def list_models(request: Request):
    """Return available models. POST로 자격증명을 직접 전달 가능."""
    profile = request.query_params.get("profile", os.environ.get("AWS_PROFILE", "default"))
    
    # POST body에서 자격증명 직접 받기
    creds_override = None
    if request.method == "POST":
        try:
            body = await request.json()
            if body.get("accessKeyId"):
                creds_override = body
                profile = body.get("profile", profile)
        except Exception:
            pass
    
    try:
        import boto3

        if creds_override:
            # 전달받은 자격증명으로 직접 클라이언트 생성
            client = boto3.client(
                "bedrock",
                aws_access_key_id=creds_override["accessKeyId"],
                aws_secret_access_key=creds_override["secretAccessKey"],
                aws_session_token=creds_override.get("sessionToken", ""),
                region_name=creds_override.get("region", os.environ.get("AWS_REGION", "us-west-2")),
            )
        else:
            session = boto3.Session(
                profile_name=profile,
                region_name=os.environ.get("AWS_REGION", "us-west-2"),
            )
            client = session.client("bedrock")
        
        resp = client.list_foundation_models()
        catalog = {}
        skip = ["IMAGE", "VIDEO", "EMBEDDING"]
        for m in resp.get("modelSummaries", []):
            modes = m.get("outputModalities", [])
            if any(s in str(modes) for s in skip):
                continue
            if m.get("modelLifecycle", {}).get("status") in ["LEGACY", "EOL"]:
                continue
            provider = m.get("providerName", "Unknown")
            if provider not in catalog:
                catalog[provider] = []
            catalog[provider].append({
                "id": m["modelId"],
                "name": m.get("modelName", m["modelId"]),
            })
        return JSONResponse(content={
            "models": catalog,
            "count": sum(len(v) for v in catalog.values()),
        })
    except Exception as e:
        return JSONResponse(content={"models": {}, "error": str(e)})


@app.post("/api/agents/run-stream")
async def run_agent_stream(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-sonnet-4-6")
    system_prompt = body.get("systemPrompt", "")
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    project_path = body.get("projectPath", "")
    open_file = body.get("openFile", "")
    open_file_content = body.get("openFileContent", "")

    gw = _get_gw(aws_profile, bedrock_user)

    if project_path and not system_prompt:
        system_prompt = f"사용자의 프로젝트 경로: {project_path}"
        if open_file:
            system_prompt += f"\n현재 열린 파일: {open_file}"

    if project_path and _is_code_related(prompt):
        try:
            from ai_engine.rag.context_builder import build_system_prompt
            system_prompt = build_system_prompt(
                project_path=project_path, query=prompt,
                open_file=open_file, open_file_content=open_file_content,
                base_system_prompt=system_prompt,
                aws_profile=aws_profile, bedrock_user=bedrock_user, gateway_client=gw,
            )
        except Exception as e:
            print(f"[RAG] 컨텍스트 빌드 실패 (무시): {e}")

    messages = _build_messages(body.get("chatHistory", []), prompt, body.get("sessionId", "default"))
    stream_model = model if model.startswith("us.") or model.startswith("eu.") else f"us.{model}"

    async def realtime_stream():
        """Lambda SSE를 실시간으로 프론트엔드에 중계 — ChatGPT처럼 글자가 써지는 효과."""
        try:
            async for evt in gw.stream_sse_realtime(model_id=stream_model, messages=messages, system_prompt=system_prompt):
                evt_type = evt.get("type", "")
                if evt_type == "content_block_delta":
                    delta = evt.get("delta", {})
                    if "text" in delta:
                        yield f"data: {json.dumps({'text': delta['text']}, ensure_ascii=False)}\n\n"
                elif evt_type == "settlement":
                    rq = {"cost_krw": evt.get("remaining_quota_krw", 0)}
                    _extract_quota({"remaining_quota": rq, "estimated_cost_krw": evt.get("estimated_cost_krw", 0)}, _quota_cache.get("user", ""))
                elif evt_type == "error":
                    yield f"data: {json.dumps({'error': evt.get('message', str(evt))}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        asyncio.create_task(_maybe_summarize(body.get("sessionId", "default"), body.get("chatHistory", []), gw))

    return StreamingResponse(realtime_stream(), media_type="text/event-stream")


@app.post("/api/agents/run-agent")
async def run_agent_with_tools(request: Request):
    """에이전트 모드 — 도구 실행 루프 포함. 모델이 tool_use로 응답하면 실행 후 재호출."""
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-sonnet-4-6")
    system_prompt = body.get("systemPrompt", "")
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    project_path = body.get("projectPath", "")
    open_file = body.get("openFile", "")
    open_file_content = body.get("openFileContent", "")

    gw = _get_gw(aws_profile, bedrock_user)
    stream_model = model if model.startswith("us.") or model.startswith("eu.") else f"us.{model}"

    # 시스템 프롬프트 구성
    if project_path and not system_prompt:
        system_prompt = f"사용자의 프로젝트 경로: {project_path}"
        if open_file:
            system_prompt += f"\n현재 열린 파일: {open_file}"
    if project_path and _is_code_related(prompt):
        try:
            from ai_engine.rag.context_builder import build_system_prompt
            system_prompt = build_system_prompt(
                project_path=project_path, query=prompt,
                open_file=open_file, open_file_content=open_file_content,
                base_system_prompt=system_prompt,
                aws_profile=aws_profile, bedrock_user=bedrock_user, gateway_client=gw,
            )
        except Exception as e:
            print(f"[Agent] RAG 실패 (무시): {e}")

    messages = _build_messages(body.get("chatHistory", []), prompt, body.get("sessionId", "default"))

    async def agent_stream():
        nonlocal messages
        max_turns = 10

        for turn in range(max_turns):
            print(f"[Agent] turn={turn}, realtime stream + toolConfig")
            text_parts = []
            tool_use_blocks = []
            current_tool = {}
            stop_reason = ""

            try:
                async for evt in gw.stream_sse_realtime(
                    model_id=stream_model, messages=messages,
                    system_prompt=system_prompt, tool_config=AGENT_TOOLS,
                ):
                    evt_type = evt.get("type", "")
                    if evt_type == "content_block_delta":
                        delta = evt.get("delta", {})
                        if "text" in delta:
                            text_parts.append(delta["text"])
                            yield f"data: {json.dumps({'text': delta['text']}, ensure_ascii=False)}\n\n"
                        elif "toolUse" in delta:
                            if current_tool:
                                current_tool["_input_json"] = current_tool.get("_input_json", "") + delta["toolUse"].get("input", "")
                    elif evt_type == "content_block_start":
                        cb = evt.get("content_block") or evt.get("contentBlock") or {}
                        if "toolUse" in cb:
                            tu = cb["toolUse"]
                            current_tool = {"toolUseId": tu.get("toolUseId", ""), "name": tu.get("name", ""), "_input_json": ""}
                    elif evt_type == "content_block_stop":
                        if current_tool and current_tool.get("name"):
                            try:
                                inp = json.loads(current_tool.get("_input_json", "{}"))
                            except json.JSONDecodeError:
                                inp = {}
                            tool_use_blocks.append({
                                "toolUse": {"toolUseId": current_tool["toolUseId"], "name": current_tool["name"], "input": inp}
                            })
                            current_tool = {}
                    elif evt_type in ("message_delta", "message_stop"):
                        stop_reason = evt.get("delta", {}).get("stopReason", "") or evt.get("stop_reason", "") or evt.get("stopReason", "")
                    elif evt_type == "settlement":
                        _extract_quota({"remaining_quota": {"cost_krw": evt.get("remaining_quota_krw", 0)}, "estimated_cost_krw": evt.get("estimated_cost_krw", 0)}, _quota_cache.get("user", ""))
                    elif evt_type == "error":
                        yield f"data: {json.dumps({'error': evt.get('message', str(evt))}, ensure_ascii=False)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
                break

            # content_blocks 조합
            content_blocks = []
            if text_parts:
                content_blocks.append({"text": "".join(text_parts)})
            content_blocks.extend(tool_use_blocks)

            print(f"[Agent] turn={turn}, stopReason={stop_reason}, text={len(text_parts)}parts, tools={len(tool_use_blocks)}")

            if not content_blocks:
                break

            messages.append({"role": "assistant", "content": content_blocks})

            if not tool_use_blocks:
                # max_tokens로 끊긴 경우 → 이어서 생성
                if stop_reason == "max_tokens" and turn < max_turns - 1:
                    print(f"[Agent] max_tokens 도달 — 이어서 생성 (turn {turn+1})")
                    messages.append({"role": "user", "content": [{"text": "계속 이어서 작성해주세요."}]})
                    continue
                break

            # 도구 실행
            tool_results = []
            for block in tool_use_blocks:
                tu = block["toolUse"]
                tool_name = tu.get("name", "")
                tool_id = tu.get("toolUseId", "")
                tool_input = tu.get("input", {})
                yield f"data: {json.dumps({'tool': tool_name, 'input': tool_input, 'status': 'running'}, ensure_ascii=False)}\n\n"
                tool_output = _execute_tool(tool_name, tool_input, project_path)
                print(f"[Agent] 도구 실행: {tool_name} → {len(tool_output)}자")
                yield f"data: {json.dumps({'tool': tool_name, 'output': tool_output[:500], 'status': 'done'}, ensure_ascii=False)}\n\n"
                tool_results.append({"toolResult": {"toolUseId": tool_id, "content": [{"text": tool_output[:15000]}]}})

            messages.append({"role": "user", "content": tool_results})

        yield "data: [DONE]\n\n"

    return StreamingResponse(agent_stream(), media_type="text/event-stream")


@app.post("/api/agents/run-parallel")
async def run_agent_parallel(request: Request):
    """병렬 모델 호출 — 서버에서 동시 실행, SSE로 각 모델 결과 전달."""
    body = await request.json()
    prompt = body.get("prompt", "")
    models = body.get("models", [])
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    project_path = body.get("projectPath", "")
    open_file = body.get("openFile", "")
    open_file_content = body.get("openFileContent", "")

    gw = _get_gw(aws_profile, bedrock_user)

    # RAG 컨텍스트 — 코드/프로젝트 관련 질문에만
    rag_context = ""
    if project_path and _is_code_related(prompt):
        try:
            from ai_engine.rag.context_builder import build_system_prompt
            rag_context = build_system_prompt(
                project_path=project_path,
                query=prompt,
                open_file=open_file,
                open_file_content=open_file_content,
                aws_profile=aws_profile,
                bedrock_user=bedrock_user,
                gateway_client=gw,
            )
        except Exception as e:
            print(f"[RAG] 컨텍스트 빌드 실패 (무시): {e}")

    messages = [{"role": "user", "content": [{"text": prompt}]}]

    async def parallel_stream():
        async def call_model(slot):
            model_id = slot.get("modelId", "")
            slot_id = slot.get("slotId", "")
            sp = slot.get("systemPrompt", "")
            # us. prefix 적용
            sid = model_id if model_id.startswith("us.") or model_id.startswith("eu.") else f"us.{model_id}"
            # RAG 컨텍스트를 시스템 프롬프트에 추가
            if rag_context:
                sp = (sp + "\n\n" + rag_context) if sp else rag_context
            try:
                # converse-stream 우선 시도, 실패 시 기존 /converse fallback
                result = await asyncio.wait_for(
                    gw.converse_stream_live(model_id=sid, messages=messages, system_prompt=sp),
                    timeout=300
                )
                if result.get("decision") == "ERROR":
                    result = await asyncio.wait_for(
                        gw.converse(model_id=sid, messages=messages, system_prompt=sp),
                        timeout=300
                    )
                decision = result.get("decision", "")
                if decision == "ALLOW":
                    output = result.get("output", {}).get("message", {}).get("content", [])
                    text = "\n".join(c.get("text", "") for c in output if "text" in c)
                    return {"slotId": slot_id, "modelId": model_id, "status": "done", "content": text}
                elif decision == "ACCEPTED":
                    job_id = result.get("job_id", "")
                    if job_id:
                        text = await gw._poll_job_result(job_id, max_wait=300)
                        if text:
                            return {"slotId": slot_id, "modelId": model_id, "status": "done", "content": text}
                    return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": "ACCEPTED — 결과 대기 시간 초과"}
                elif decision == "DENY":
                    return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": result.get("denial_reason", "DENIED")}
                else:
                    return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": result.get("error", f"Unknown: {decision}")}
            except asyncio.TimeoutError:
                return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": "300초 타임아웃"}
            except Exception as e:
                return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": str(e)}

        # 동시 실행
        tasks = [call_model(slot) for slot in models]
        for coro in asyncio.as_completed(tasks):
            result = await coro
            yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(parallel_stream(), media_type="text/event-stream")


@app.post("/api/agents/run")
async def run_agent(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-sonnet-4-6")
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    try:
        gw = _get_gw(aws_profile, bedrock_user)
        result = await gw.converse(
            model_id=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/agents/workflow")
async def run_workflow(request: Request):
    """Run full agent workflow: Plan → Code → Review → Execute."""
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-3-5-sonnet-20241022-v2:0")

    try:
        from ai_engine.gateway_module import GatewayClient
        from ai_engine.agent_system.agent_graph import build_graph

        gw = GatewayClient(
            gateway_url=os.environ.get(
                "GATEWAY_URL",
                "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1",
            ),
            aws_profile=os.environ.get("AWS_PROFILE", "default"),
        )
        graph = build_graph(gw)
        from ai_engine.agent_system.state import AgentState

        state = AgentState(task=prompt)
        result = await graph.ainvoke(state)
        await gw.close()
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


_quota_cache = {"used_krw": 0, "remaining_krw": 0, "limit_krw": 0, "last_updated": "", "user": "", "fetching": False}

@app.get("/api/quota")
async def get_quota(request: Request):
    profile = request.query_params.get("profile", os.environ.get("AWS_PROFILE", "default"))
    user = request.query_params.get("user", "")
    # 첫 호출이면 백그라운드에서 quota 조회 시작 (즉시 응답 반환)
    if _quota_cache["remaining_krw"] == 0 and user and not _quota_cache["fetching"]:
        _quota_cache["fetching"] = True
        asyncio.create_task(_fetch_quota_background(profile, user))
    return {
        "user": _quota_cache["user"] or user,
        "used_krw": round(_quota_cache["used_krw"], 2),
        "remaining_krw": round(_quota_cache["remaining_krw"], 2),
        "limit_krw": round(_quota_cache["limit_krw"], 2),
        "last_updated": _quota_cache["last_updated"],
    }


async def _fetch_quota_background(profile, user):
    """백그라운드에서 Gateway 호출하여 quota 정보 캐시."""
    try:
        gw = _get_gw(profile, user)
        print(f"[Quota] 백그라운드 quota 조회 시작...")
        # Gateway /converse 직접 호출 — maxTokens:1로 최소 비용
        # us. prefix haiku 4.5 우선, 실패 시 haiku 3 fallback
        quota_models = [
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "us.anthropic.claude-3-haiku-20240307-v1:0",
        ]
        for mid in quota_models:
            try:
                result = await asyncio.wait_for(
                    gw.converse_quota_only(
                        model_id=mid,
                        messages=[{"role": "user", "content": [{"text": "hi"}]}],
                    ),
                    timeout=15
                )
                print(f"[Quota] {mid}: decision={result.get('decision')}, remaining_quota={result.get('remaining_quota')}, error={result.get('error', '')[:100]}")
                _extract_quota(result, user)
                if _quota_cache["remaining_krw"] > 0:
                    print(f"[Quota] 성공! remaining={_quota_cache['remaining_krw']}")
                    return
            except Exception as e:
                print(f"[Quota] {mid} 실패: {e}")
                continue
        print(f"[Quota] 모든 모델 실패 — 첫 채팅 후 자동 갱신")
    except Exception as e:
        print(f"[Quota] 백그라운드 조회 실패: {e}")
    finally:
        _quota_cache["fetching"] = False


def _extract_quota(result, user=""):
    """Gateway 응답에서 quota 정보를 추출하여 캐시에 저장."""
    rq = result.get("remaining_quota", {})
    if rq:
        # 다양한 키 이름 대응
        cost_val = 0
        if isinstance(rq, (int, float)):
            cost_val = rq
        elif isinstance(rq, dict):
            cost_val = rq.get("cost_krw") or rq.get("remaining_cost_krw") or rq.get("remaining_krw") or rq.get("remaining") or 0
            if not cost_val:
                for v in rq.values():
                    if isinstance(v, (int, float)) and v > 0:
                        cost_val = v
                        break
        if cost_val > 0:
            _quota_cache["remaining_krw"] = cost_val
            _quota_cache["limit_krw"] = cost_val + _quota_cache["used_krw"]
            _quota_cache["user"] = user
            _quota_cache["last_updated"] = datetime.utcnow().isoformat()
            print(f"[Quota] 캐시 갱신 성공: remaining={cost_val}")
    if result.get("estimated_cost_krw"):
        _quota_cache["used_krw"] += result["estimated_cost_krw"]
        if _quota_cache["remaining_krw"] > 0:
            _quota_cache["limit_krw"] = _quota_cache["remaining_krw"] + _quota_cache["used_krw"]
