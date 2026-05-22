"""Gateway client — httpx + botocore SigV4 + BedrockUser assume role."""
import os
import json
import asyncio
from typing import AsyncIterator, Optional

import httpx
import boto3
from botocore.auth import SigV4Auth as BotocoreSigV4
from botocore.credentials import Credentials
from botocore.awsrequest import AWSRequest


# ─────────────────────────────────────────────────────────────────
# Per-model max_tokens limits (Bedrock Converse / ConverseStream)
# Source: AWS Bedrock model documentation. Update when new models are added.
# Conservative defaults are used — stay under documented limits to avoid
# ValidationException ("maximum tokens you requested exceeds the model limit").
# ─────────────────────────────────────────────────────────────────
_MODEL_MAX_TOKENS_MAP = {
    # Anthropic Claude — most support 64K output, Opus 4 supports up to 64K
    "claude-opus-4":     64000,
    "claude-sonnet-4":   64000,
    "claude-haiku-4":    32000,
    "claude-3-7-sonnet": 64000,
    "claude-3-5-sonnet": 8192,
    "claude-3-opus":     4096,
    "claude-3-haiku":    4096,
    # Amazon Nova
    "nova-pro":          5120,
    "nova-lite":         5120,
    "nova-micro":        5120,
    # DeepSeek — R1 has a 32K cap; "lower than 32768" → use 32767
    "deepseek-r1":       32767,
    "deepseek":          32767,
    # Mistral / Pixtral
    "mistral-large":     8192,
    "mistral-small":     8192,
    "pixtral-large":     8192,
    "pixtral":           8192,
    # Meta Llama
    "llama3-3":          8192,
    "llama3-2":          8192,
    "llama3-1":          8192,
    "llama":             8192,
    # Cohere
    "command-r":         4000,
    "command":           4000,
    # NVIDIA Nemotron
    "nemotron":          16384,
    # Writer Palmyra
    "palmyra":           8192,
    # AI21 Jamba
    "jamba":             4096,
    # Stability/Titan/Nova-Canvas — image models, not used in text mode but
    # safe defaults if accidentally invoked
    "stability":         1024,
    "titan-image":       1024,
    "nova-canvas":       1024,
}

# Absolute fallback when no pattern matches — Bedrock minimum guarantee
_DEFAULT_MAX_TOKENS = 4096


def _resolve_model_max_tokens(model_id: str) -> int:
    """Return the safe max_tokens limit for a model id.

    Matches by case-insensitive substring against `_MODEL_MAX_TOKENS_MAP`.
    Falls back to `_DEFAULT_MAX_TOKENS` (4096) if no pattern matches —
    this guarantees we never send a value above any known Bedrock limit.

    Examples:
        us.deepseek.r1-v1:0           → 32768
        us.anthropic.claude-opus-4-7  → 64000
        amazon.nova-pro-v1:0          → 5120
        unknown-model-id              → 4096
    """
    if not model_id:
        return _DEFAULT_MAX_TOKENS
    mid = model_id.lower()
    # Strip common prefixes
    for pfx in ("us.", "eu.", "global."):
        if mid.startswith(pfx):
            mid = mid[len(pfx):]
            break
    # Longer keys first (e.g., "claude-3-7-sonnet" before "claude-3")
    for key in sorted(_MODEL_MAX_TOKENS_MAP.keys(), key=lambda k: -len(k)):
        if key in mid:
            return _MODEL_MAX_TOKENS_MAP[key]
    return _DEFAULT_MAX_TOKENS


def _is_max_tokens_error(msg: str) -> bool:
    """Detect Bedrock max_tokens validation errors."""
    if not msg:
        return False
    low = str(msg).lower()
    return ("maximum tokens" in low and "exceeds" in low) or \
           ("validationexception" in low and "max" in low and "token" in low)


def _extract_model_token_limit(msg: str) -> int:
    """Extract the model's actual token limit from an error message.

    Bedrock errors like:
        'The maximum tokens you requested exceeds the model limit of 32768.'

    Returns 0 if no number can be extracted.
    """
    import re
    if not msg:
        return 0
    m = re.search(r"model limit of (\d+)", msg, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return 0
    # Generic fallback — find any number after "limit"
    m = re.search(r"limit[^0-9]+(\d{3,6})", msg, re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return 0
    return 0


class GatewayClient:
    STREAM_URL = "https://5kzi5pmk6leqq74cq64jza37lu0qipbk.lambda-url.us-west-2.on.aws/"

    def __init__(self, gateway_url="", aws_profile="default", region="us-west-2", bedrock_user=""):
        self.gateway_url = (gateway_url or os.environ.get(
            "GATEWAY_URL", "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1"
        )).rstrip("/")
        self.aws_profile = aws_profile
        self.region = region
        self.bedrock_user = bedrock_user
        self._creds = None
        self._cred_time = 0

    def _get_creds(self) -> Credentials:
        import time
        if self._creds and (time.time() - self._cred_time) < 300:
            return self._creds
        if hasattr(self, '_injected_creds') and self._injected_creds:
            self._creds = self._injected_creds
            self._cred_time = time.time()
            return self._creds
        session = boto3.Session(profile_name=self.aws_profile)
        if self.bedrock_user:
            sts = session.client("sts")
            account = sts.get_caller_identity()["Account"]
            assumed = sts.assume_role(
                RoleArn=f"arn:aws:iam::{account}:role/BedrockUser-{self.bedrock_user}",
                RoleSessionName="ai-editor",
            )
            c = assumed["Credentials"]
            self._creds = Credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
        else:
            fc = session.get_credentials().get_frozen_credentials()
            self._creds = Credentials(fc.access_key, fc.secret_key, fc.token)
        self._cred_time = time.time()
        return self._creds

    def force_refresh_creds(self):
        """자격증명 강제 갱신 — 토큰 만료 시 호출. 주입된 자격증명도 초기화."""
        self._cred_time = 0
        self._creds = None
        if hasattr(self, '_injected_creds'):
            self._injected_creds = None
        # boto3 기본 세션 캐시도 초기화
        try:
            import boto3 as _b3
            _b3.DEFAULT_SESSION = None
        except Exception:
            pass
        print("[GW] 자격증명 강제 갱신 완료")

    def inject_credentials(self, access_key: str, secret_key: str, session_token: str = ""):
        """Electron에서 가져온 자격증명을 직접 주입 — boto3 SSO 캐시 완전 우회."""
        self._injected_creds = Credentials(access_key, secret_key, session_token)
        self._creds = self._injected_creds
        self._cred_time = __import__("time").time()
        print(f"[GW] 자격증명 주입 완료: {access_key[:8]}...")

    def _sign(self, method, url, body_bytes):
        """botocore SigV4로 서명된 헤더 반환."""
        creds = self._get_creds()
        aws_req = AWSRequest(method=method, url=url, data=body_bytes, headers={"Content-Type": "application/json"})
        BotocoreSigV4(creds, "execute-api", self.region).add_auth(aws_req)
        return dict(aws_req.headers)

    def _build_payload(self, model_id, messages, system_prompt="", tool_config=None):
        # Gateway는 일부 모델에 us. prefix 필요 — 원본 ID 우선, DENY 시 prefix 재시도
        if not model_id.startswith("us.") and not model_id.startswith("eu."):
            self._try_us_prefix = True
            used_id = model_id
        else:
            self._try_us_prefix = False
            used_id = model_id
        # 모델별 max_tokens 한계 자동 조정 — ValidationException 방지
        # 환경변수 AE_MAX_TOKENS는 상한선으로만 작동 (모델 한계가 더 작으면 더 작은 값 사용)
        env_cap = int(os.environ.get("AE_MAX_TOKENS", "64000"))
        model_limit = _resolve_model_max_tokens(used_id)
        # 양쪽의 최소값 사용 — 모델 한계를 절대 초과하지 않음
        effective_max = min(env_cap, model_limit)
        body = {"modelId": used_id, "messages": messages, "inferenceConfig": {"maxTokens": effective_max}}
        if system_prompt:
            body["system"] = [{"text": system_prompt}]
        if tool_config:
            body["toolConfig"] = tool_config
        return body

    def _is_expired_error(self, err_str):
        """토큰 만료 에러인지 판단."""
        low = err_str.lower()
        return "expired" in low or "security token" in low or "not authorized" in low

    async def invoke_model(self, model_id: str, body: dict, timeout: int = 30) -> dict:
        """Bedrock InvokeModel API 호출 (이미지 모델용).

        Args:
            model_id: Bedrock 모델 ID (e.g. amazon.titan-image-generator-v2:0)
            body: 모델별 요청 본문
            timeout: 요청 타임아웃 (초), 기본 30초

        Returns:
            dict: {"images": [...]} 성공 시, {"error": "..."} 실패 시
        """
        url = f"{self.gateway_url}/invoke"
        payload = {"modelId": model_id, "body": body}
        body_bytes = json.dumps(payload).encode()

        for attempt in range(3):
            headers = self._sign("POST", url, body_bytes)

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(float(timeout), connect=10.0)
                ) as client:
                    resp = await client.post(url, content=body_bytes, headers=headers)

                if resp.status_code == 200:
                    data = resp.json()
                    # Gateway 응답에서 이미지 데이터 추출
                    if "images" in data:
                        return {"images": data["images"]}
                    # /invoke 라우트 래핑 응답: {"decision":"ALLOW","output":{...},"usage":{...},"cost_krw":...}
                    if "output" in data and isinstance(data.get("output"), dict):
                        out = data["output"]
                        if "images" in out:
                            return {"images": out["images"]}
                        return out
                    # 모델 응답이 body 안에 래핑된 경우
                    resp_body = data.get("body", data)
                    if isinstance(resp_body, str):
                        resp_body = json.loads(resp_body)
                    if "images" in resp_body:
                        return {"images": resp_body["images"]}
                    # 기타 성공 응답 — 그대로 반환
                    return resp_body

                # HTTP 에러 처리
                err_text = resp.text[:200]
                if self._is_expired_error(err_text):
                    if attempt < 2:
                        print(f"[GW invoke_model] 토큰 만료 감지 (시도 {attempt+1}/3) — 자격증명 갱신 후 재시도")
                        self.force_refresh_creds()
                        headers = self._sign("POST", url, body_bytes)
                        await asyncio.sleep(0.5)
                        continue
                return {"error": f"HTTP {resp.status_code}: {err_text}"}

            except httpx.TimeoutException:
                return {"error": f"타임아웃 ({timeout}초 초과)"}
            except Exception as e:
                err_str = str(e)
                if self._is_expired_error(err_str) and attempt < 2:
                    print(f"[GW invoke_model] 토큰 만료 감지 (시도 {attempt+1}/3) — 자격증명 갱신 후 재시도")
                    self.force_refresh_creds()
                    await asyncio.sleep(0.5)
                    continue
                return {"error": str(e)}

        return {"error": "최대 재시도 횟수 초과"}

    async def converse_quota_only(self, model_id, messages, system_prompt=""):
        """Quota 조회 전용 — maxTokens:1로 최소 비용, ACCEPTED 시 폴링 없이 quota만 반환."""
        url = f"{self.gateway_url}/converse"
        payload = self._build_payload(model_id, messages, system_prompt)
        # maxTokens를 1로 오버라이드 — 최소 비용
        payload["inferenceConfig"]["maxTokens"] = 1
        body_bytes = json.dumps(payload).encode()
        import urllib.request, urllib.error
        loop = asyncio.get_event_loop()

        headers = self._sign("POST", url, body_bytes)
        def _call(h=headers, b=body_bytes):
            req = urllib.request.Request(url, data=b, method="POST")
            for k, v in h.items():
                req.add_header(k, v)
            try:
                resp = urllib.request.urlopen(req, timeout=15)
                return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                return {"decision": "ERROR", "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
            except Exception as e:
                return {"decision": "ERROR", "error": str(e)}
        result = await loop.run_in_executor(None, _call)

        # ACCEPTED도 quota 정보 포함 — 폴링 없이 바로 반환
        if result.get("decision") == "ACCEPTED":
            # job cancel (비용 절약)
            job_id = result.get("job_id", "")
            if job_id:
                asyncio.create_task(self._cancel_job(job_id))
            return {
                "decision": "ALLOW",
                "remaining_quota": result.get("remaining_quota", {}),
                "estimated_cost_krw": result.get("estimated_cost_krw", 0),
            }
        return result

    async def converse(self, model_id, messages, system_prompt="", tool_config=None):
        url = f"{self.gateway_url}/converse"
        payload = self._build_payload(model_id, messages, system_prompt, tool_config)
        body_bytes = json.dumps(payload).encode()
        import urllib.request, urllib.error
        loop = asyncio.get_event_loop()

        for attempt in range(3):
            headers = self._sign("POST", url, body_bytes)
            def _call(h=headers, b=body_bytes):
                req = urllib.request.Request(url, data=b, method="POST")
                for k, v in h.items():
                    req.add_header(k, v)
                try:
                    resp = urllib.request.urlopen(req, timeout=300)
                    return json.loads(resp.read().decode())
                except urllib.error.HTTPError as e:
                    return {"decision": "ERROR", "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
                except Exception as e:
                    return {"decision": "ERROR", "error": str(e)}
            result = await loop.run_in_executor(None, _call)

            # 토큰 만료 → 자격증명 갱신 후 재시도
            err_str = result.get("error", "")
            if self._is_expired_error(err_str):
                if attempt < 2:
                    print(f"[GW] 토큰 만료 감지 (시도 {attempt+1}/3) — 자격증명 갱신 후 재시도")
                    self.force_refresh_creds()
                    payload = self._build_payload(model_id, messages, system_prompt)
                    body_bytes = json.dumps(payload).encode()
                    await asyncio.sleep(0.5)
                    continue

            # 원본 ID로 DENY/ERROR → us. prefix로 재시도
            err_or_deny = result.get("decision") in ("ERROR", "DENY")
            deny_reason = result.get("denial_reason", "") + result.get("error", "")
            if err_or_deny and self._try_us_prefix and "not in allowed" in deny_reason and attempt == 0:
                print(f"[GW] 원본 ID '{model_id}' 거부 → us.{model_id} 로 재시도")
                payload["modelId"] = f"us.{model_id}"
                body_bytes = json.dumps(payload).encode()
                self._try_us_prefix = False  # 한 번만 재시도
                continue

            if result.get("decision") == "ACCEPTED":
                # 비동기 모델 — S3 폴링으로 결과 대기
                job_id = result.get("job_id", "")
                if job_id:
                    text = await self._poll_job_result(job_id, max_wait=300)
                    if text:
                        return {"decision": "ALLOW", "output": {"message": {"content": [{"text": text}]}},
                                "remaining_quota": result.get("remaining_quota", {}),
                                "estimated_cost_krw": result.get("estimated_cost_krw", 0)}
                    return {"decision": "ERROR", "error": f"비동기 작업 시간 초과 (job: {job_id[:12]}...)"}
                if attempt < 2:
                    await asyncio.sleep(2)
                    continue
            return result
        return result

    async def converse_stream_live(self, model_id, messages, system_prompt="", tool_config=None):
        """Lambda Function URL — 토큰 만료 시 자동 갱신 + 재시도 (최대 3회)."""
        result = None
        for _retry in range(3):
            result = await self._converse_stream_live_once(model_id, messages, system_prompt, tool_config)
            err = result.get("error", "")
            if self._is_expired_error(err):
                print(f"[Stream] 토큰 만료 감지 (시도 {_retry+1}/3) — 자격증명 갱신 후 재시도")
                self.force_refresh_creds()
                await asyncio.sleep(0.5)
                continue
            # "not in allowed list" → us. prefix로 재시도
            if "not in allowed" in err and not model_id.startswith("us.") and _retry == 0:
                print(f"[Stream] '{model_id}' 거부 → us.{model_id} 로 재시도")
                model_id = f"us.{model_id}"
                continue
            return result
        return result

    async def stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
        """Lambda Function URL — 실시간 SSE 이터레이터 (httpx 비동기, 스레드 고갈 없음).

        max_tokens 검증 실패 시 자동으로 한계의 50%로 줄여 재시도. 최대 2회 재시도.
        """
        max_retries = 2
        attempt = 0
        # 시작점 — 모델별 사전 한계
        current_max = _resolve_model_max_tokens(model_id)
        # env_cap
        env_cap = int(os.environ.get("AE_MAX_TOKENS", "64000"))
        current_max = min(env_cap, current_max)

        while attempt <= max_retries:
            url = self.STREAM_URL
            # payload 생성 — current_max 사용
            payload = self._build_payload(model_id, messages, system_prompt, tool_config)
            payload["inferenceConfig"]["maxTokens"] = current_max
            body_bytes = json.dumps(payload).encode()
            creds = self._get_creds()
            aws_req = AWSRequest(method="POST", url=url, data=body_bytes,
                                 headers={"Content-Type": "application/json"})
            BotocoreSigV4(creds, "lambda", self.region).add_auth(aws_req)
            signed_headers = dict(aws_req.headers)

            error_event = None
            had_data = False
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(300.0, connect=30.0, read=120.0)
                ) as client:
                    async with client.stream("POST", url, content=body_bytes, headers=signed_headers) as resp:
                        if resp.status_code != 200:
                            body_text = ""
                            async for chunk in resp.aiter_text():
                                body_text += chunk
                                if len(body_text) > 500:
                                    break
                            error_event = {"type": "error", "message": f"Lambda HTTP {resp.status_code}: {body_text[:300]}"}
                        else:
                            buf = ""
                            async for chunk in resp.aiter_text():
                                buf += chunk
                                while '\n' in buf:
                                    line, buf = buf.split('\n', 1)
                                    line = line.strip()
                                    if not line or not line.startswith('data: '):
                                        continue
                                    try:
                                        evt = json.loads(line[6:])
                                    except json.JSONDecodeError:
                                        continue
                                    # max_tokens 검증 실패 감지 → 재시도 (yield하지 않음)
                                    if evt.get("type") == "error":
                                        msg = (evt.get("message") or evt.get("error") or "")
                                        if _is_max_tokens_error(msg):
                                            extracted = _extract_model_token_limit(msg)
                                            if extracted and extracted - 1 < current_max:
                                                # 에러는 "lower than X" 라고 함 → X-1 사용
                                                new_max = max(1024, extracted - 1)
                                            else:
                                                new_max = max(1024, int(current_max * 0.5))
                                            if attempt < max_retries and new_max < current_max:
                                                print(f"[GW] max_tokens 한계 초과 — {current_max} → {new_max}로 재시도 (attempt {attempt+1})")
                                                error_event = None  # 외부 루프에서 재시도
                                                current_max = new_max
                                                attempt += 1
                                                # 안쪽 chunk 루프 탈출 → while 루프 재시작
                                                break
                                    had_data = True
                                    yield evt
                                else:
                                    # inner while 정상 종료 (break 안 일어남) — 다음 chunk 받기
                                    continue
                                # break 발생 — chunk 루프 끝
                                break
            except httpx.ReadTimeout:
                error_event = {"type": "error", "message": "Lambda 응답 타임아웃 (120초 무응답)"}
            except httpx.ConnectTimeout:
                error_event = {"type": "error", "message": "Lambda 연결 타임아웃"}
            except httpx.RemoteProtocolError as e:
                error_event = {"type": "error", "message": f"Lambda 연결 끊김: {e}"}
            except Exception as e:
                error_event = {"type": "error", "error": str(e)}

            # 재시도 케이스 (max_tokens 줄임)
            if error_event is None and not had_data and attempt <= max_retries:
                continue
            # 정상 종료 또는 재시도 불가능한 에러
            if error_event is not None:
                yield error_event
            return

    async def _converse_stream_live_once(self, model_id, messages, system_prompt="", tool_config=None):
        """Lambda Function URL을 통한 스트리밍 (1회 시도, httpx 비동기)."""
        url = self.STREAM_URL
        payload = self._build_payload(model_id, messages, system_prompt, tool_config)
        body_bytes = json.dumps(payload).encode()

        creds = self._get_creds()
        aws_req = AWSRequest(method="POST", url=url, data=body_bytes,
                             headers={"Content-Type": "application/json"})
        BotocoreSigV4(creds, "lambda", self.region).add_auth(aws_req)
        headers = dict(aws_req.headers)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
                resp = await client.post(url, content=body_bytes, headers=headers)
                raw = resp.text
        except Exception as e:
            return {"decision": "ERROR", "error": str(e)}

        # SSE 스트림 파싱 — data: {...} 형식
        text_parts = []
        tool_use_blocks = []
        remaining_quota = {}
        estimated_cost = 0
        stop_reason = ""
        current_tool = {}
        for line in raw.split('\n'):
            line = line.strip()
            if not line.startswith('data: '):
                continue
            try:
                evt = json.loads(line[6:])
                evt_type = evt.get("type", "")
                if evt_type == "content_block_delta":
                    delta = evt.get("delta", {})
                    if "text" in delta:
                        text_parts.append(delta["text"])
                    elif "toolUse" in delta:
                        # toolUse 델타 (input JSON 조각)
                        if current_tool:
                            current_tool["_input_json"] = current_tool.get("_input_json", "") + delta.get("toolUse", {}).get("input", "")
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
                            "toolUse": {
                                "toolUseId": current_tool["toolUseId"],
                                "name": current_tool["name"],
                                "input": inp,
                            }
                        })
                        current_tool = {}
                elif evt_type in ("message_delta", "message_stop"):
                    stop_reason = evt.get("delta", {}).get("stopReason", "") or evt.get("stop_reason", "") or evt.get("stopReason", "")
                elif evt_type == "settlement":
                    remaining_quota = {"cost_krw": evt.get("remaining_quota_krw", 0)}
                    estimated_cost = evt.get("estimated_cost_krw", 0)
                elif evt_type == "error":
                    return {"decision": "ERROR", "error": evt.get("message", str(evt))}
            except json.JSONDecodeError:
                continue

        # content 블록 조합
        content_blocks = []
        if text_parts:
            content_blocks.append({"text": "".join(text_parts)})
        content_blocks.extend(tool_use_blocks)

        if content_blocks:
            return {
                "decision": "ALLOW",
                "output": {"message": {"content": content_blocks}},
                "stopReason": stop_reason,
                "remaining_quota": remaining_quota,
                "estimated_cost_krw": estimated_cost,
            }

        # SSE 파싱 실패 시 원본 텍스트로 fallback
        try:
            data = json.loads(raw)
            if "error" in data:
                return {"decision": "ERROR", "error": data["error"]}
            return {
                "decision": "ALLOW",
                "output": data.get("output", {"message": {"content": [{"text": raw}]}}),
                "remaining_quota": data.get("remaining_quota", {}),
                "estimated_cost_krw": data.get("estimated_cost_krw", 0),
            }
        except json.JSONDecodeError:
            if raw.strip():
                return {"decision": "ALLOW", "output": {"message": {"content": [{"text": raw}]}}}
            return {"decision": "ERROR", "error": "빈 응답"}

    async def _cancel_job(self, job_id):
        """Gateway job cancel — reservation 해제."""
        try:
            url = f"{self.gateway_url}/converse"
            cancel_body = json.dumps({"action": "cancel", "job_id": job_id}).encode()
            headers = self._sign("POST", url, cancel_body)
            import urllib.request
            def _do():
                req = urllib.request.Request(url, data=cancel_body, method="POST")
                for k, v in headers.items():
                    req.add_header(k, v)
                try:
                    urllib.request.urlopen(req, timeout=10)
                except Exception:
                    pass
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _do)
        except Exception:
            pass

    async def stream_converse(self, model_id, messages, system_prompt="") -> AsyncIterator[str]:
        result = await self.converse(model_id, messages, system_prompt)
        decision = result.get("decision", "")
        if decision == "DENY":
            raise RuntimeError(f"DENY: {result.get('denial_reason', '')} (model: {model_id})")
        if decision == "ERROR":
            raise RuntimeError(result.get("error", "Gateway error"))
        if decision == "ACCEPTED":
            job_id = result.get("job_id", "")
            if job_id:
                text = await self._poll_job_result(job_id, max_wait=300)
                if text:
                    yield text
                    return
            yield "[작업 대기 시간 초과]"
            return
        # ALLOW
        output = result.get("output", {}).get("message", {}).get("content", [])
        for c in output:
            if "text" in c:
                yield c["text"]

    async def _poll_job_result(self, job_id, max_wait=300):
        creds = self._get_creds()
        s3 = boto3.client("s3", aws_access_key_id=creds.access_key, aws_secret_access_key=creds.secret_key, aws_session_token=creds.token, region_name=self.region)
        try:
            account = boto3.Session(profile_name=self.aws_profile).client("sts").get_caller_identity()["Account"]
        except Exception:
            account = "107650139384"
        bucket = f"bedrock-gw-dev-payload-{account}"
        key = f"results/{job_id}.json"
        for i in range(max_wait):  # 1초 간격으로 폴링
            await asyncio.sleep(1)
            try:
                obj = s3.get_object(Bucket=bucket, Key=key)
                data = json.loads(obj["Body"].read().decode())
                content = data.get("output", {}).get("message", {}).get("content", [])
                texts = [c.get("text", "") for c in content if "text" in c]
                return "\n".join(texts) if texts else json.dumps(data, ensure_ascii=False)[:500]
            except Exception:
                continue
        return ""

    async def close(self):
        self._creds = None
