"""FastAPI server — AI Editor backend."""
import os
import json
import uuid
import asyncio
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Editor Engine", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# GatewayClient 캐시 — 동일 profile+user 조합은 재사용
_gw_cache = {}


def _is_code_related(prompt: str) -> bool:
    """프롬프트가 코드/프로젝트 관련인지 판단."""
    p = prompt.lower().strip()
    # 코드 관련 키워드
    code_keywords = [
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

    # 기본 컨텍스트: 프로젝트 경로 + 열린 파일명만 (내용은 RAG에서)
    if project_path and not system_prompt:
        system_prompt = f"사용자의 프로젝트 경로: {project_path}"
        if open_file:
            system_prompt += f"\n현재 열린 파일: {open_file}"

    # RAG 컨텍스트 주입 — 코드/프로젝트 관련 질문에만
    if project_path and _is_code_related(prompt):
        try:
            from ai_engine.rag.context_builder import build_system_prompt
            system_prompt = build_system_prompt(
                project_path=project_path,
                query=prompt,
                open_file=open_file,
                open_file_content=open_file_content,
                base_system_prompt=system_prompt,
                aws_profile=aws_profile,
                bedrock_user=bedrock_user,
                gateway_client=gw,
            )
        except Exception as e:
            print(f"[RAG] 컨텍스트 빌드 실패 (무시): {e}")

    messages = _build_messages(body.get("chatHistory", []), prompt, body.get("sessionId", "default"))
    try:
        # converse-stream Lambda Function URL (HTTP 호출)
        result = await gw.converse_stream_live(model_id=model, messages=messages, system_prompt=system_prompt)
        if result.get("decision") == "ERROR":
            print(f"[Stream] fallback: {result.get('error', '')[:100]}")
            result = await gw.converse(model_id=model, messages=messages, system_prompt=system_prompt)
        asyncio.create_task(_maybe_summarize(body.get("sessionId", "default"), body.get("chatHistory", []), gw))
    except Exception as e:
        err_str = str(e)
        # ValidationException (토큰 초과) → 히스토리 없이 재시도
        if "ValidationException" in err_str or "too many" in err_str.lower():
            try:
                messages_retry = [{"role": "user", "content": [{"text": prompt}]}]
                result = await gw.converse(model_id=model, messages=messages_retry, system_prompt=system_prompt)
            except Exception as e2:
                result = {"decision": "ERROR", "error": str(e2)}
        else:
            result = {"decision": "ERROR", "error": err_str}

    async def event_stream():
        decision = result.get("decision", "")
        # quota 정보 캐시
        if result.get("remaining_quota"):
            rq = result["remaining_quota"]
            _quota_cache["remaining_krw"] = rq.get("cost_krw", 0)
            _quota_cache["last_updated"] = datetime.utcnow().isoformat()
        if result.get("estimated_cost_krw"):
            _quota_cache["used_krw"] += result["estimated_cost_krw"]
            # 한도 추정 (remaining + used)
            if _quota_cache["remaining_krw"] > 0:
                _quota_cache["limit_krw"] = _quota_cache["remaining_krw"] + _quota_cache["used_krw"]
        if decision == "ALLOW":
            output = result.get("output", {}).get("message", {}).get("content", [])
            for c in output:
                if "text" in c:
                    # JSON으로 감싸서 줄바꿈 이스케이프
                    yield f"data: {json.dumps({'text': c['text']}, ensure_ascii=False)}\n\n"
        elif decision == "ACCEPTED":
            job_id = result.get("job_id", "")
            if job_id:
                text = await gw._poll_job_result(job_id)
                if text:
                    yield f"data: {json.dumps({'text': text}, ensure_ascii=False)}\n\n"
                else:
                    yield f"data: {json.dumps({'error': f'작업 {job_id[:12]}... 결과 대기 시간 초과'})}\n\n"
            else:
                yield f"data: {json.dumps({'error': 'ACCEPTED — job_id 없음'})}\n\n"
        elif decision == "DENY":
            yield f"data: {json.dumps({'error': result.get('denial_reason', 'DENIED') + ' (model: ' + model + ')'})}\n\n"
        else:
            yield f"data: {json.dumps({'error': result.get('error', f'Unknown: {decision}')})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
            # RAG 컨텍스트를 시스템 프롬프트에 추가
            if rag_context:
                sp = (sp + "\n\n" + rag_context) if sp else rag_context
            try:
                # converse-stream 우선 시도, 실패 시 기존 /converse fallback
                result = await asyncio.wait_for(
                    gw.converse_stream_live(model_id=model_id, messages=messages, system_prompt=sp),
                    timeout=120
                )
                if result.get("decision") == "ERROR":
                    result = await asyncio.wait_for(
                        gw.converse(model_id=model_id, messages=messages, system_prompt=sp),
                        timeout=120
                    )
                decision = result.get("decision", "")
                if decision == "ALLOW":
                    output = result.get("output", {}).get("message", {}).get("content", [])
                    text = "\n".join(c.get("text", "") for c in output if "text" in c)
                    return {"slotId": slot_id, "modelId": model_id, "status": "done", "content": text}
                elif decision == "ACCEPTED":
                    job_id = result.get("job_id", "")
                    if job_id:
                        text = await gw._poll_job_result(job_id)
                        if text:
                            return {"slotId": slot_id, "modelId": model_id, "status": "done", "content": text}
                    return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": "ACCEPTED — 결과 대기 시간 초과"}
                elif decision == "DENY":
                    return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": result.get("denial_reason", "DENIED")}
                else:
                    return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": result.get("error", f"Unknown: {decision}")}
            except asyncio.TimeoutError:
                return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": "90초 타임아웃"}
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


_quota_cache = {"used_krw": 0, "remaining_krw": 0, "limit_krw": 0, "last_updated": "", "user": ""}

@app.get("/api/quota")
async def get_quota(request: Request):
    profile = request.query_params.get("profile", os.environ.get("AWS_PROFILE", "default"))
    user = request.query_params.get("user", "")
    print(f"[Quota] 요청: profile={profile}, user={user}, cache={_quota_cache}")
    # 첫 호출이면 실제 Gateway에서 quota 조회
    if _quota_cache["remaining_krw"] == 0 and user:
        try:
            gw = _get_gw(profile, user)
            print(f"[Quota] Gateway 호출 시도...")
            # 간단한 호출로 quota 정보 획득
            result = await gw.converse(
                model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
                messages=[{"role": "user", "content": [{"text": "hi"}]}],
            )
            print(f"[Quota] Gateway 응답: decision={result.get('decision')}, remaining_quota={result.get('remaining_quota')}")
            if result.get("remaining_quota"):
                rq = result["remaining_quota"]
                _quota_cache["remaining_krw"] = rq.get("cost_krw", 0)
                _quota_cache["limit_krw"] = rq.get("cost_krw", 0) + _quota_cache["used_krw"]
                _quota_cache["user"] = user
                _quota_cache["last_updated"] = datetime.utcnow().isoformat()
                print(f"[Quota] 캐시 업데이트: {_quota_cache}")
            if result.get("estimated_cost_krw"):
                _quota_cache["used_krw"] += result["estimated_cost_krw"]
        except Exception as e:
            print(f"[Quota] Gateway 호출 실패: {e}")
    return {
        "user": _quota_cache["user"] or user,
        "used_krw": round(_quota_cache["used_krw"], 2),
        "remaining_krw": round(_quota_cache["remaining_krw"], 2),
        "limit_krw": round(_quota_cache["limit_krw"], 2),
        "last_updated": _quota_cache["last_updated"],
    }
