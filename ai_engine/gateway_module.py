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


class GatewayClient:
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
        self._cred_time = __import__("time").time()
        return self._creds

    def _sign(self, method, url, body_bytes):
        """botocore SigV4로 서명된 헤더 반환."""
        creds = self._get_creds()
        aws_req = AWSRequest(method=method, url=url, data=body_bytes, headers={"Content-Type": "application/json"})
        BotocoreSigV4(creds, "execute-api", self.region).add_auth(aws_req)
        return dict(aws_req.headers)

    def _build_payload(self, model_id, messages, system_prompt=""):
        # Gateway는 us. prefix 모델을 선호 — 이미 prefix가 있으면 그대로
        # 일부 모델은 prefix 없이도 작동하므로 원본도 시도
        if not model_id.startswith("us.") and not model_id.startswith("eu."):
            # 먼저 원본 ID로 시도, 실패하면 us. prefix 추가
            self._try_us_prefix = True
            prefixed = f"us.{model_id}"
        else:
            self._try_us_prefix = False
            prefixed = model_id
        body = {"modelId": prefixed, "messages": messages, "inferenceConfig": {"maxTokens": 2048}}
        if system_prompt:
            body["system"] = [{"text": system_prompt}]
        return body

    async def converse(self, model_id, messages, system_prompt=""):
        url = f"{self.gateway_url}/converse"
        payload = self._build_payload(model_id, messages, system_prompt)
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
                    resp = urllib.request.urlopen(req, timeout=120)
                    return json.loads(resp.read().decode())
                except urllib.error.HTTPError as e:
                    return {"decision": "ERROR", "error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
                except Exception as e:
                    return {"decision": "ERROR", "error": str(e)}
            result = await loop.run_in_executor(None, _call)

            # us. prefix로 실패하면 원본 ID로 재시도
            if result.get("decision") == "ERROR" and self._try_us_prefix and attempt == 0:
                payload["modelId"] = model_id
                body_bytes = json.dumps(payload).encode()
                continue

            if result.get("decision") == "ACCEPTED":
                # 비동기 모델 — S3 폴링으로 결과 대기
                job_id = result.get("job_id", "")
                if job_id:
                    text = await self._poll_job_result(job_id, max_wait=120)
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
                text = await self._poll_job_result(job_id)
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

    async def _poll_job_result(self, job_id, max_wait=120):
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
