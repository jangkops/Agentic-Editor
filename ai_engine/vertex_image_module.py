"""Vertex AI Image Generation client (Nano Banana / Imagen).

자동 활성화 — 키만 발견되면 자동 ON. 다른 사용자가 별도 환경변수 설정 없이도
SSO 자격증명만으로 사용 가능하도록 키를 AWS Secrets Manager 에서 조회.

게이트웨이 규칙 예외:
  `.kiro/steering/gateway.md` 는 모든 LLM 호출을 Bedrock Gateway 경유로 강제하지만,
  이미지 생성에 한해 Bedrock에 동급 모델이 없는 현실을 반영해 Vertex AI 호출 예외를
  허용한다 (사용자 결정). LLM 텍스트/추론/operation JSON 생성은 그대로 Bedrock Gateway
  유지 — 이 모듈은 *이미지 생성 한정* 으로만 호출되어야 한다.

키 발견 순서 (첫 번째 성공 사용):
  1. AWS Secrets Manager   — secret name: `agentic-editor/gcp-vertex-key`
                              (사용자 SSO 자격증명으로 조회, 30명 멀티유저 대응)
  2. 환경변수              — GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json (개발자용)
  3. ~/.config/agentic-editor/gcp-vertex-key.json (로컬 캐시)
  4. <userData>/gcp-vertex-key.json (Electron app bundle)

조회된 키는 다음 호출까지 캐싱 (TTL 24h, AE_VERTEX_KEY_TTL_SEC 환경변수 조정).

지원 모델:
  - "gemini-3-pro-image-preview"     — Nano Banana Pro (최고 품질, 4K, 멀티턴)
  - "gemini-2.5-flash-image-preview" — Nano Banana (빠르고 저렴, 멀티턴 OK)
  - "imagen-4.0-ultra-generate-preview-06-06" — Imagen 4 Ultra (사진 최고급)
  - "imagen-4.0-generate-001"        — Imagen 4 (사진 빠름)
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# google-auth 는 옵션 의존성. 미설치 시 1회 자동 설치를 시도해 사용자가 별도
# 설정 없이 바로 쓰도록 한다(자동 self-heal). 자동 설치까지 실패하면 import 는
# 성공하되 enabled=False 로 안전하게 폴백한다. AE_NO_AUTO_PIP=1 로 비활성 가능.
def _ensure_google_auth():
    try:
        from google.oauth2 import service_account as _sa  # type: ignore
        from google.auth.transport.requests import Request as _rq  # type: ignore
        return _sa, _rq
    except ImportError:
        pass
    if os.environ.get("AE_NO_AUTO_PIP", "").strip() == "1":
        return None, None
    try:
        import subprocess
        print("[VertexImage] google-auth 미설치 — 자동 설치 시도(pip install google-auth requests)")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "google-auth>=2.30.0", "requests>=2.31.0"],
            check=True, timeout=180,
        )
        from google.oauth2 import service_account as _sa  # type: ignore
        from google.auth.transport.requests import Request as _rq  # type: ignore
        print("[VertexImage] google-auth 자동 설치 완료")
        return _sa, _rq
    except Exception as _e:
        print(f"[VertexImage] google-auth 자동 설치 실패 — 비활성 폴백: {str(_e)[:160]}")
        return None, None


service_account, _GAuthRequest = _ensure_google_auth()
_GOOGLE_AUTH_AVAILABLE = service_account is not None and _GAuthRequest is not None

import httpx

# boto3 는 이미 ai_engine 의존성 — Secrets Manager 호출에 사용
try:
    import boto3  # type: ignore
    from botocore.exceptions import ClientError, BotoCoreError  # type: ignore
    _BOTO3_AVAILABLE = True
except ImportError:
    _BOTO3_AVAILABLE = False
    boto3 = None  # type: ignore


# ===== Capability registry — 모델명 하드코딩 금지 (capability 기준 라우팅) =====

VERTEX_MODEL_REGISTRY = {
    "image_generation_high_quality": "gemini-3-pro-image-preview",
    "image_generation_creative":     "gemini-3-pro-image-preview",
    "image_generation_fast":         "gemini-2.5-flash-image-preview",
    "image_generation_photo_high":   "imagen-4.0-ultra-generate-preview-06-06",
    "image_generation_photo_fast":   "imagen-4.0-generate-001",
    # 멀티턴 편집/리믹스용 (텍스트가 정확히 들어가야 하는 슬라이드 본문)
    "image_edit_text_aware":         "gemini-3-pro-image-preview",
}

# Default region — `global` works for all Gemini 3 endpoints
VERTEX_DEFAULT_REGION = os.environ.get("AE_VERTEX_REGION", "global")
VERTEX_API_BASE = "https://aiplatform.googleapis.com/v1"

# AWS Secrets Manager — single secret stores the GCP service account JSON
SECRETS_MANAGER_NAME = os.environ.get(
    "AE_VERTEX_SECRET_NAME", "agentic-editor/gcp-vertex-key"
)
SECRETS_MANAGER_REGION = os.environ.get(
    "AE_VERTEX_SECRET_REGION", "us-west-2"
)
SECRETS_MANAGER_CACHE_TTL = int(
    os.environ.get("AE_VERTEX_KEY_TTL_SEC", str(24 * 3600))
)

# Local cache file — populated after first successful Secrets Manager fetch (TTL 적용)
_LOCAL_CACHE_PATH = Path.home() / ".config" / "agentic-editor" / "gcp-vertex-key.json"
# Permanent key store — TTL 면제. 한 번 시드되면 사용자 설정 없이 영구 자동 활성화.
# (Secrets Manager/env로 키가 해석되면 자동으로 이 파일에도 영구 보존된다.)
_PERMANENT_KEY_PATH = Path.home() / ".config" / "agentic-editor" / "gcp-vertex-key.permanent.json"


def _try_secrets_manager(aws_profile: str = "", credentials: Optional[dict] = None) -> Optional[str]:
    """Fetch GCP service account JSON from AWS Secrets Manager.

    Uses the user's SSO credentials (same profile used for Bedrock Gateway),
    so any of the 30 users with `secretsmanager:GetSecretValue` permission
    on `agentic-editor/gcp-vertex-key` can transparently use Vertex AI.

    credentials(선택): {accessKeyId, secretAccessKey, sessionToken} 형태의 주입
    자격증명. 앱이 SSO 캐시를 우회해 직접 주입한 자격증명을 그대로 사용해야
    프로파일에 유효한 creds가 없는 환경(원격/주입 모드)에서도 키를 조회할 수 있다.

    Returns the JSON string on success, None on any error.
    """
    if not _BOTO3_AVAILABLE:
        return None
    profile = aws_profile or os.environ.get("AWS_PROFILE", "")
    try:
        if credentials and credentials.get("accessKeyId"):
            # 주입 자격증명 우선 — SSO 캐시 우회(앱 로그인 흐름과 동일).
            session = boto3.Session(
                aws_access_key_id=credentials.get("accessKeyId"),
                aws_secret_access_key=credentials.get("secretAccessKey"),
                aws_session_token=credentials.get("sessionToken") or None,
                region_name=credentials.get("region") or SECRETS_MANAGER_REGION,
            )
        elif profile:
            session = boto3.Session(profile_name=profile)
        else:
            session = boto3.Session()
        client = session.client("secretsmanager", region_name=SECRETS_MANAGER_REGION)
        resp = client.get_secret_value(SecretId=SECRETS_MANAGER_NAME)
        secret = resp.get("SecretString") or ""
        if not secret:
            return None
        # Validate it's parseable JSON before returning
        json.loads(secret)
        return secret
    except (ClientError, BotoCoreError, json.JSONDecodeError) as e:
        print(f"[VertexImage] Secrets Manager lookup failed: {type(e).__name__}: {str(e)[:200]}")
        return None
    except Exception as e:
        print(f"[VertexImage] Secrets Manager unexpected: {type(e).__name__}: {str(e)[:200]}")
        return None


def _try_env_var() -> Optional[str]:
    """Read GCP key path from GOOGLE_APPLICATION_CREDENTIALS env var."""
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            data = f.read()
        json.loads(data)  # validate
        return data
    except Exception as e:
        print(f"[VertexImage] env var key read failed: {e}")
        return None


def _try_local_cache() -> Optional[str]:
    """Read previously-cached key from ~/.config/agentic-editor/gcp-vertex-key.json."""
    if not _LOCAL_CACHE_PATH.is_file():
        return None
    try:
        # Honor TTL — refuse cache older than configured TTL
        st = _LOCAL_CACHE_PATH.stat()
        if SECRETS_MANAGER_CACHE_TTL > 0 and (time.time() - st.st_mtime) > SECRETS_MANAGER_CACHE_TTL:
            return None
        with open(_LOCAL_CACHE_PATH, "r") as f:
            data = f.read()
        json.loads(data)  # validate
        return data
    except Exception as e:
        print(f"[VertexImage] local cache read failed: {e}")
        return None


def _try_permanent_store() -> Optional[str]:
    """Read TTL-exempt permanent key store. 사용자 설정 없이 영구 자동 활성화의 핵심.

    로컬 캐시(_LOCAL_CACHE_PATH)와 달리 만료(TTL)가 없다. 키가 한 번이라도
    영구 저장소에 시드되면 이후 모든 기동에서 env 변수 없이 자동 활성화된다.
    """
    if not _PERMANENT_KEY_PATH.is_file():
        return None
    try:
        with open(_PERMANENT_KEY_PATH, "r") as f:
            data = f.read()
        json.loads(data)  # validate
        return data
    except Exception as e:
        print(f"[VertexImage] permanent store read failed: {e}")
        return None


def _try_userdata_bundle() -> Optional[str]:
    """Electron app bundle path — checked when AE_USERDATA_PATH is set."""
    userdata = os.environ.get("AE_USERDATA_PATH", "").strip()
    if not userdata:
        return None
    candidate = Path(userdata) / "gcp-vertex-key.json"
    if not candidate.is_file():
        return None
    try:
        with open(candidate, "r") as f:
            data = f.read()
        json.loads(data)
        return data
    except Exception:
        return None


def _persist_to_local_cache(secret_json: str) -> None:
    """Save successfully-fetched key to local cache for offline / quick re-auth."""
    _write_key_file(_LOCAL_CACHE_PATH, secret_json, "local cache")


def _persist_to_permanent_store(secret_json: str) -> None:
    """Save key to TTL-exempt permanent store so future startups need zero setup."""
    _write_key_file(_PERMANENT_KEY_PATH, secret_json, "permanent store")


def _write_key_file(path: Path, secret_json: str, label: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 0600 permissions — only the owner can read
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(secret_json)
        except Exception:
            pass
    except Exception as e:
        print(f"[VertexImage] {label} write skipped: {e}")


def _resolve_gcp_key(aws_profile: str = "", credentials: Optional[dict] = None) -> Optional[dict]:
    """Try every key source in order. Returns parsed dict or None.

    Order: Secrets Manager → env var → permanent store → local cache → Electron userData bundle.
    The first successful source short-circuits. Secrets Manager / env-var success is
    persisted to BOTH the permanent store (TTL-exempt) and local cache so subsequent
    startups activate with zero user setup.
    """
    sources = [
        ("AWS Secrets Manager", lambda: _try_secrets_manager(aws_profile, credentials)),
        ("GOOGLE_APPLICATION_CREDENTIALS env", _try_env_var),
        ("permanent store (~/.config)",        _try_permanent_store),
        ("local cache (~/.config)",            _try_local_cache),
        ("Electron userData bundle",           _try_userdata_bundle),
    ]
    for source_name, fn in sources:
        try:
            data = fn()
        except Exception as e:
            print(f"[VertexImage] source '{source_name}' raised: {e}")
            data = None
        if not data:
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not parsed.get("project_id") or not parsed.get("private_key"):
            continue
        # Persist freshly-fetched keys so future startups need no env/setup.
        # (Already-stored sources skip re-writing themselves.)
        if source_name in ("AWS Secrets Manager", "GOOGLE_APPLICATION_CREDENTIALS env"):
            _persist_to_permanent_store(data)
            _persist_to_local_cache(data)
        print(f"[VertexImage] key resolved via: {source_name} (project={parsed['project_id']})")
        return parsed
    return None


class VertexImageClient:
    """Vertex AI image generation HTTP client.

    Auto-enabled when a key can be resolved from any source. No env opt-in
    required — the presence of a valid key in Secrets Manager / env / cache
    is sufficient to activate.

    Why no SDK: keeps dependency surface small (only google-auth for token
    signing) and makes the request flow explicit. Match Bedrock Gateway client
    style.
    """

    def __init__(self, aws_profile: str = "", credentials: Optional[dict] = None):
        self._aws_profile = aws_profile
        self._enabled = False
        self._project_id: Optional[str] = None
        self._credentials = None
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0
        self._key_data: Optional[dict] = None
        # Disable explicitly via env (user opt-out path)
        if os.environ.get("AE_DISABLE_VERTEX_IMAGE", "").strip() == "1":
            print("[VertexImage] disabled by AE_DISABLE_VERTEX_IMAGE=1")
            return
        if not _GOOGLE_AUTH_AVAILABLE:
            print("[VertexImage] google-auth not installed — disabled. Run: pip install google-auth")
            return
        try:
            self._key_data = _resolve_gcp_key(self._aws_profile, credentials)
        except Exception as e:
            print(f"[VertexImage] key resolution outer error: {e}")
            self._key_data = None
        if not self._key_data:
            print("[VertexImage] no key found in Secrets Manager / env / cache → disabled")
            return
        try:
            self._load_credentials_from_dict(self._key_data)
            self._enabled = True
        except Exception as e:
            print(f"[VertexImage] credential load failed → disabling: {e}")
            self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def project_id(self) -> Optional[str]:
        return self._project_id

    def _load_credentials_from_dict(self, key_data: dict):
        self._project_id = key_data.get("project_id")
        if not self._project_id:
            raise ValueError("service account JSON missing project_id field")
        self._credentials = service_account.Credentials.from_service_account_info(
            key_data,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        print(f"[VertexImage] enabled — project={self._project_id}, region={VERTEX_DEFAULT_REGION}")

    def _ensure_token(self):
        """Refresh the access token if missing or near expiry. Tokens last 1h."""
        now = time.time()
        if self._token and now < self._token_expiry - 60:
            return
        if not self._credentials:
            raise RuntimeError("VertexImage: no credentials loaded")
        request = _GAuthRequest()
        self._credentials.refresh(request)
        self._token = self._credentials.token
        try:
            self._token_expiry = self._credentials.expiry.timestamp() if self._credentials.expiry else now + 3500
        except Exception:
            self._token_expiry = now + 3500

    def resolve_model_id(self, model_class: str) -> Optional[str]:
        """Capability → concrete model id. Returns None if unknown class."""
        return VERTEX_MODEL_REGISTRY.get(model_class)

    async def generate(
        self,
        prompt: str,
        model_class: str = "image_generation_high_quality",
        aspect_ratio: str = "16:9",
        negative_prompt: str = "",
        num_images: int = 1,
        timeout: int = 60,
    ) -> dict:
        """Generate an image via Vertex AI Gemini Image / Imagen.

        Returns:
            {"images": [base64 png, ...], "model": "<resolved-model-id>"} on success
            {"error": "...", "detail": "..."} on failure
        """
        if not self._enabled:
            return {"error": "vertex-disabled", "detail": "no key resolved or auth unavailable"}
        if not prompt or not prompt.strip():
            return {"error": "invalid-parameter", "detail": "prompt required"}
        model_id = self.resolve_model_id(model_class)
        if not model_id:
            return {"error": "unknown-model-class", "detail": f"no Vertex model for {model_class!r}"}
        try:
            self._ensure_token()
        except Exception as e:
            return {"error": "auth-failed", "detail": str(e)[:200]}

        # Branch by model family — Gemini Image vs Imagen (different API shapes)
        if model_id.startswith("gemini-"):
            return await self._generate_gemini_image(
                model_id, prompt, aspect_ratio, negative_prompt, num_images, timeout
            )
        elif model_id.startswith("imagen-"):
            return await self._generate_imagen(
                model_id, prompt, aspect_ratio, negative_prompt, num_images, timeout
            )
        return {"error": "unsupported-model", "detail": f"unknown model id family: {model_id}"}

    async def _generate_gemini_image(
        self,
        model_id: str,
        prompt: str,
        aspect_ratio: str,
        negative_prompt: str,
        num_images: int,
        timeout: int,
    ) -> dict:
        """Gemini 3 Pro Image / 2.5 Flash Image via generateContent endpoint."""
        full_prompt = prompt
        if negative_prompt:
            full_prompt = f"{prompt}\n\nAVOID: {negative_prompt}"
        url = (
            f"{VERTEX_API_BASE}/projects/{self._project_id}/locations/{VERTEX_DEFAULT_REGION}"
            f"/publishers/google/models/{model_id}:generateContent"
        )
        body = {
            "contents": [
                {"role": "user", "parts": [{"text": full_prompt}]}
            ],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {
                    "aspectRatio": aspect_ratio,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        try:
            import asyncio as _aio
            resp = None
            for _attempt in range(4):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 200:
                    break
                # 429(rate limit)/503(overloaded)는 동시 호출 시 흔하다. 지수
                # 백오프 후 재시도 → 마지막 시도까지 실패해야 에러(네이티브 폴백).
                if resp.status_code in (429, 503) and _attempt < 3:
                    await _aio.sleep((2 ** _attempt) + 0.5)
                    continue
                break
            if resp is None or resp.status_code != 200:
                return {
                    "error": f"http-{resp.status_code if resp is not None else 'none'}",
                    "detail": (resp.text[:300] if resp is not None else "no response"),
                    "model": model_id,
                }
            data = resp.json()
            images_b64 = self._extract_gemini_images(data)
            if not images_b64:
                return {
                    "error": "no-image-in-response",
                    "detail": str(data)[:300],
                    "model": model_id,
                }
            return {"images": images_b64, "model": model_id}
        except httpx.TimeoutException:
            return {"error": "timeout", "detail": f"after {timeout}s", "model": model_id}
        except Exception as e:
            return {"error": "request-failed", "detail": str(e)[:300], "model": model_id}

    async def _generate_imagen(
        self,
        model_id: str,
        prompt: str,
        aspect_ratio: str,
        negative_prompt: str,
        num_images: int,
        timeout: int,
    ) -> dict:
        """Imagen 4 / Imagen 4 Ultra via predict endpoint."""
        url = (
            f"{VERTEX_API_BASE}/projects/{self._project_id}/locations/{VERTEX_DEFAULT_REGION}"
            f"/publishers/google/models/{model_id}:predict"
        )
        body = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "sampleCount": max(1, min(4, num_images)),
                "aspectRatio": aspect_ratio,
            },
        }
        if negative_prompt:
            body["parameters"]["negativePrompt"] = negative_prompt
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        try:
            import asyncio as _aio
            resp = None
            for _attempt in range(4):
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 200:
                    break
                # 429/503 → 지수 백오프 재시도 후 폴백 (gemini 경로와 동일 정책).
                if resp.status_code in (429, 503) and _attempt < 3:
                    await _aio.sleep((2 ** _attempt) + 0.5)
                    continue
                break
            if resp is None or resp.status_code != 200:
                return {
                    "error": f"http-{resp.status_code if resp is not None else 'none'}",
                    "detail": (resp.text[:300] if resp is not None else "no response"),
                    "model": model_id,
                }
            data = resp.json()
            preds = data.get("predictions", []) or []
            images_b64 = []
            for p in preds:
                b64 = p.get("bytesBase64Encoded") or ""
                if b64:
                    images_b64.append(b64)
            if not images_b64:
                return {
                    "error": "no-image-in-response",
                    "detail": str(data)[:300],
                    "model": model_id,
                }
            return {"images": images_b64, "model": model_id}
        except httpx.TimeoutException:
            return {"error": "timeout", "detail": f"after {timeout}s", "model": model_id}
        except Exception as e:
            return {"error": "request-failed", "detail": str(e)[:300], "model": model_id}

    @staticmethod
    def _extract_gemini_images(data: dict) -> list:
        """Walk the Gemini generateContent response and extract base64 image bytes."""
        out = []
        for cand in data.get("candidates", []) or []:
            content = cand.get("content", {}) or {}
            for part in content.get("parts", []) or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if not inline:
                    continue
                mime = inline.get("mimeType") or inline.get("mime_type") or ""
                b64 = inline.get("data", "")
                if b64 and mime.startswith("image/"):
                    out.append(b64)
        return out


# ===== Module-level singleton =====
_VERTEX_CLIENT: Optional[VertexImageClient] = None


def get_vertex_image_client(aws_profile: str = "", credentials: Optional[dict] = None) -> VertexImageClient:
    """Lazy-init module-level singleton. Re-evaluates env+secrets on first call only.
    Pass the user's AWS profile so Secrets Manager auth uses the same SSO.

    credentials(선택): 앱이 SSO 캐시를 우회해 주입한 자격증명. 제공되고 아직
    enable되지 않았다면, 그 자격증명으로 키 해석을 한 번 더 시도한다(로그인 직후
    자동 활성화 경로). 이미 enable된 싱글톤은 그대로 재사용한다.
    """
    global _VERTEX_CLIENT
    if _VERTEX_CLIENT is None:
        _VERTEX_CLIENT = VertexImageClient(aws_profile=aws_profile, credentials=credentials)
    elif credentials and not getattr(_VERTEX_CLIENT, "enabled", False):
        # 주입 자격증명으로 재시도 — 프로파일에 유효 creds가 없던 첫 init이 실패했을 때
        # 로그인 시 주입된 SSO 자격증명으로 Secrets Manager에서 키를 다시 해석한다.
        _VERTEX_CLIENT = VertexImageClient(aws_profile=aws_profile, credentials=credentials)
    return _VERTEX_CLIENT


def reset_vertex_image_client():
    """Force re-init (useful when env vars change at runtime or AWS SSO refreshes)."""
    global _VERTEX_CLIENT
    _VERTEX_CLIENT = None
