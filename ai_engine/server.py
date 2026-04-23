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
        '.js', '.py', '.ts', '.css', '.html', '.json',
    ]
    for kw in code_keywords:
        if kw in p:
            return True
    # 200자 이상이면 코드 관련일 가능성 높음
    if len(p) > 200:
        return True
    return False

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
    # 자격증명 캐시 강제 만료 — 매번 새로 assume role
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
async def reset_cache():
    """Gateway 클라이언트 캐시 + boto3 세션 캐시 완전 초기화."""
    # GatewayClient 캐시 초기화
    _gw_cache.clear()
    # boto3 내부 credential 캐시 초기화
    try:
        import boto3
        import botocore.session as bs
        # 기본 세션의 credential resolver 캐시 리셋
        boto3.DEFAULT_SESSION = None
    except Exception:
        pass
    return {"status": "ok", "message": "all caches cleared"}


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

    messages = [{"role": "user", "content": [{"text": prompt}]}]
    try:
        result = await gw.converse(model_id=model, messages=messages, system_prompt=system_prompt)
    except Exception as e:
        result = {"decision": "ERROR", "error": str(e)}

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
                result = await asyncio.wait_for(
                    gw.converse(model_id=model_id, messages=messages, system_prompt=sp),
                    timeout=90
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
    # 첫 호출이면 실제 Gateway에서 quota 조회
    if _quota_cache["remaining_krw"] == 0 and user:
        try:
            gw = _get_gw(profile, user)
            # 간단한 호출로 quota 정보 획득
            result = await gw.converse(
                model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
                messages=[{"role": "user", "content": [{"text": "hi"}]}],
            )
            if result.get("remaining_quota"):
                rq = result["remaining_quota"]
                _quota_cache["remaining_krw"] = rq.get("cost_krw", 0)
                _quota_cache["limit_krw"] = rq.get("cost_krw", 0) + _quota_cache["used_krw"]
                _quota_cache["user"] = user
                _quota_cache["last_updated"] = datetime.utcnow().isoformat()
            if result.get("estimated_cost_krw"):
                _quota_cache["used_krw"] += result["estimated_cost_krw"]
        except Exception:
            pass
    return {
        "user": _quota_cache["user"] or user,
        "used_krw": round(_quota_cache["used_krw"], 2),
        "remaining_krw": round(_quota_cache["remaining_krw"], 2),
        "limit_krw": round(_quota_cache["limit_krw"], 2),
        "last_updated": _quota_cache["last_updated"],
    }
