"""Gateway client — httpx + botocore SigV4 + BedrockUser assume role."""
import os
import json
import time
import asyncio
from typing import AsyncIterator, Optional

import httpx
import boto3
from botocore.auth import SigV4Auth as BotocoreSigV4
from botocore.credentials import Credentials
from botocore.awsrequest import AWSRequest


# ─────────────────────────────────────────────────────────────────
# OpenAI Responses 라우트 통합 — 예외 타입 (순수 add)
# 기존 Bedrock 경로에는 영향 없음. OpenAI 경로 전용.
# ─────────────────────────────────────────────────────────────────
#: converse 1건의 **연결 시도** 예산 기본값(초).
#: 과거 converse 는 3회 재시도 × (urlopen 300s + 잡 폴링 300s) = 최악 1800초(30분)였다.
#: 재시도 사이에 누적 상한이 없어 예산이 매 시도마다 새로 시작한 탓이다.
#: ⚠️ 이 예산은 **HTTP 연결 시도(urlopen)와 그 재시도에만** 적용된다 —
#:    비동기 잡 대기는 포함하지 않는다. 잡은 서버측에서 돌고 결과가 S3 에 떨어지므로
#:    1시간 이상 걸릴 수 있고, 그 대기는 `_job_max_wait()`(AE_JOB_MAX_WAIT)가 관장한다.
#:    env 이름 `AE_CONVERSE_TOTAL_BUDGET` 은 하위 호환으로 유지한다.
_CONVERSE_TOTAL_BUDGET_DEFAULT = 600
#: 단일 HTTP 시도의 read 타임아웃 상한(기존 값) — 이제 남은 예산으로 clamp 된다.
_CONVERSE_ATTEMPT_TIMEOUT_CAP = 300
#: clamp 하한 — 예산이 거의 없어도 최소 이만큼은 준다(0초 타임아웃 즉시 실패 방지).
_CONVERSE_ATTEMPT_TIMEOUT_FLOOR = 5

#: 비동기 잡 대기 상한 기본값(초) — 2시간.
#: 1시간 이상의 긴 출력은 단일 HTTP 연결로 불가능하고 비동기 잡 폴링만이 경로다.
#: `AE_JOB_MAX_WAIT` 로 조정한다.
_JOB_MAX_WAIT_DEFAULT = 7200
#: 적응형 폴링 간격 — 경과 시간 단계 경계(초)와 각 단계의 간격(초).
#: 고정 1초 간격은 2시간이면 S3 GET 7200회다. 단계적으로 늘려 호출 수를 줄인다.
_JOB_POLL_FAST_UNTIL = 30       # 처음 30초
_JOB_POLL_MEDIUM_UNTIL = 300    # ~5분
_JOB_POLL_SLOW_UNTIL = 1200     # ~20분
_JOB_POLL_INTERVAL_FAST = 1
_JOB_POLL_INTERVAL_MEDIUM = 2
_JOB_POLL_INTERVAL_SLOW = 5
_JOB_POLL_INTERVAL_SLOWEST = 10

#: converse 비스트리밍 경로의 max_tokens 하향(step-down) 재시도 상한.
#: SSE(`stream_sse_realtime`)의 `max_retries = 2` 와 동일하며, 만료·prefix 재시도
#: 예산(3회)과는 **별도 카운터**로 관리한다.
_MAX_TOKENS_STEPDOWN_RETRIES = 2
#: step-down 하한 — 이보다 작게는 줄이지 않는다(SSE 와 동일).
_MAX_TOKENS_STEPDOWN_FLOOR = 1024

#: 실시간 SSE 타임아웃 — httpx.Timeout 호출과 에러 문구가 **같은 상수**를 참조한다.
#: (과거 문구는 "120초 무응답"으로 실제 read 타임아웃 300초와 어긋나 조사 오판을 유발했다.)
#: total 은 `AE_SSE_TOTAL_TIMEOUT` 로 조정 가능(`_sse_total_timeout()`), read/connect 는 고정.
_SSE_TOTAL_TIMEOUT = 3600.0
_SSE_CONNECT_TIMEOUT = 30.0
_SSE_READ_TIMEOUT = 300.0

#: 스트리밍 단발(`_converse_stream_live_once`) 타임아웃.
#: 과거 total 300초가 prefer_streaming 경로(supervisor/deep_research/depth_router)의
#: 5분 벽이었다 — SSE total 과 같은 값으로 올린다.
#: `AE_STREAM_LIVE_TOTAL_TIMEOUT` 우선, 없으면 `AE_SSE_TOTAL_TIMEOUT`/기본값.
_STREAM_LIVE_TOTAL_TIMEOUT = _SSE_TOTAL_TIMEOUT
_STREAM_LIVE_CONNECT_TIMEOUT = _SSE_CONNECT_TIMEOUT


def _converse_total_budget() -> float:
    """converse 1건의 연결 시도 예산(초) — 잡 대기는 포함하지 않는다.

    `AE_CONVERSE_TOTAL_BUDGET` 환경변수로 조정하며, 미설정·비정상값·0 이하면
    `_CONVERSE_TOTAL_BUDGET_DEFAULT`(600초)를 쓴다.
    """
    raw = os.environ.get("AE_CONVERSE_TOTAL_BUDGET", "")
    if not raw:
        return float(_CONVERSE_TOTAL_BUDGET_DEFAULT)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return float(_CONVERSE_TOTAL_BUDGET_DEFAULT)
    return val if val > 0 else float(_CONVERSE_TOTAL_BUDGET_DEFAULT)


def _env_positive_float(name: str, default: float) -> float:
    """`name` 환경변수를 양수 float 으로 읽고, 미설정·비정상값·0 이하면 `default`.

    `_converse_total_budget` 과 동일한 폴백 규칙을 공유한다.
    """
    raw = os.environ.get(name, "")
    if not raw:
        return float(default)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return float(default)
    return val if val > 0 else float(default)


def _job_max_wait() -> int:
    """비동기 잡 대기 상한(초).

    `AE_JOB_MAX_WAIT` 로 조정하며, 미설정·비정상값·0 이하면
    `_JOB_MAX_WAIT_DEFAULT`(7200초 = 2시간)를 쓴다.

    이 값은 `_converse_total_budget()`(연결 시도 예산)과 **무관**하다 —
    잡은 서버측에서 돌고 결과가 S3 에 떨어지므로 1시간 이상 기다려야 한다.
    """
    return int(_env_positive_float("AE_JOB_MAX_WAIT", _JOB_MAX_WAIT_DEFAULT))


def _job_poll_interval(elapsed: float) -> int:
    """경과 시간에 따른 적응형 잡 폴링 간격(초).

    처음 30초는 1초, 그 다음 2초, 5분 이후 5초, 20분 이후 10초.
    짧은 대기(수 초)에서는 기존 1초 간격 동작이 그대로 유지된다.
    """
    if elapsed < _JOB_POLL_FAST_UNTIL:
        return _JOB_POLL_INTERVAL_FAST
    if elapsed < _JOB_POLL_MEDIUM_UNTIL:
        return _JOB_POLL_INTERVAL_MEDIUM
    if elapsed < _JOB_POLL_SLOW_UNTIL:
        return _JOB_POLL_INTERVAL_SLOW
    return _JOB_POLL_INTERVAL_SLOWEST


def _sse_total_timeout() -> float:
    """실시간 SSE 스트림의 total 타임아웃(초) — `AE_SSE_TOTAL_TIMEOUT` 로 조정."""
    return _env_positive_float("AE_SSE_TOTAL_TIMEOUT", _SSE_TOTAL_TIMEOUT)


def _stream_live_total_timeout() -> float:
    """스트리밍 단발 호출의 total 타임아웃(초).

    `AE_STREAM_LIVE_TOTAL_TIMEOUT` 이 있으면 그 값, 없으면 SSE total 과 같은 값.
    """
    raw = os.environ.get("AE_STREAM_LIVE_TOTAL_TIMEOUT", "")
    if raw:
        return _env_positive_float("AE_STREAM_LIVE_TOTAL_TIMEOUT", _sse_total_timeout())
    return _sse_total_timeout()


def _payload_max_tokens(payload):
    """payload 의 `inferenceConfig.maxTokens` 를 읽는다(없으면 None).

    step-down 재시도가 시작값을 **중복 계산하지 않고** payload 에서 읽도록 하는 seam.
    """
    if not isinstance(payload, dict):
        return None
    inf = payload.get("inferenceConfig")
    if not isinstance(inf, dict):
        return None
    val = inf.get("maxTokens")
    return val if isinstance(val, int) else None


def _set_payload_max_tokens(payload, value) -> bool:
    """payload 의 `inferenceConfig.maxTokens` 만 덮어쓴다(다른 body 필드 불변)."""
    if not isinstance(payload, dict):
        return False
    inf = payload.get("inferenceConfig")
    if not isinstance(inf, dict):
        return False
    inf["maxTokens"] = value
    return True


class QuotaExceededError(Exception):
    """403 권한·쿼터 거부 — 기존 403 처리 흐름(ApprovalRequestDialog)과 연결."""
    pass


class OpenAISurfaceError(Exception):
    """422 등 사용자에게 표시 가능한 OpenAI 라우트 오류 (원인 ≤200자)."""
    pass


class SyncTimeout(Exception):
    """동기 OpenAI_Responses_Route 호출 타임아웃 — 라우터가 jobs 경로로 폴백."""
    pass


class JobTimeout(Exception):
    """비동기 잡 폴링이 최대 대기 시간을 초과."""
    pass


class JobFailed(Exception):
    """비동기 잡이 failed/cancelled/error 상태로 종결."""
    pass


class OpenAIModelUnsupported(Exception):
    """게이트웨이가 해당 OpenAI 모델 식별자를 미지원으로 거부."""
    pass


def mask_token(token) -> str:
    """API 토큰 로그 마스킹 — 앞 4자만 남기고 나머지를 가린다.

    - 4자 초과: token[:4] + "****"
    - 4자 이하(빈값/None 포함): 원문을 노출하지 않도록 "****" 반환
    """
    if not token or not isinstance(token, str):
        return "****"
    if len(token) > 4:
        return token[:4] + "****"
    return "****"


# 참고: OpenAI input 정규화는 GatewayClient._to_openai_input 메서드가 단일 진입점이다.
# (설계 5절 — _build_openai_payload가 self._to_openai_input을 사용)


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

# Conservative floor — kept for malformed input (empty model id) and for callers
# that still reference the Bedrock minimum guarantee.
_DEFAULT_MAX_TOKENS = 4096

#: 맵에 매칭되지 않는 **미지 모델**의 낙관적 상한.
#: `AE_MAX_TOKENS` 기본값(64000)과 같은 값이며, 사실상 "env cap 을 그대로 쓴다"는 뜻이다.
#: 근거 없는 모델별 추측값을 `_MODEL_MAX_TOKENS_MAP` 에 추가하는 대신 이 상한을 쓴다
#: (예: anthropic.claude-opus-5 / claude-sonnet-5 / gpt 계열은 어느 키에도 걸리지 않는다).
#: ⚠️ 낙관적 상한이므로 실제 한계를 넘길 수 있다 — `converse`(비스트리밍)와
#:    `stream_sse_realtime`(스트리밍) 양쪽의 max_tokens step-down 재시도가 안전망이다.
_UNKNOWN_MODEL_MAX_TOKENS = 64000


def _resolve_model_max_tokens(model_id: str) -> int:
    """Return the safe max_tokens limit for a model id.

    Matches by case-insensitive substring against `_MODEL_MAX_TOKENS_MAP`.
    When no pattern matches, returns `_UNKNOWN_MODEL_MAX_TOKENS` (64000) —
    an optimistic ceiling so that new/unknown models are not silently capped
    at the old 4096 floor. Over-shooting is corrected at runtime by the
    max_tokens step-down retry in `converse` / `stream_sse_realtime`.
    An empty model id still falls back to `_DEFAULT_MAX_TOKENS` (malformed call).

    Examples:
        us.deepseek.r1-v1:0             → 32767
        us.anthropic.claude-opus-4-7    → 64000
        amazon.nova-pro-v1:0            → 5120
        anthropic.claude-opus-5         → 64000  (미지 모델 → 낙관적 상한)
        unknown-model-id                → 64000  (미지 모델 → 낙관적 상한)
        ""                              → 4096
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
    # 매칭 실패 = 미지 모델. 4096 으로 누르지 않고 낙관적 상한을 쓴다.
    return _UNKNOWN_MODEL_MAX_TOKENS


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


def _is_prefix_form_error(msg: str) -> bool:
    """모델 ID prefix 형태(bare vs us./eu./global.) 불일치로 인한 거부인지 감지.

    게이트웨이 /converse 실측(2026-07)에서 확인된 형태:
    - CRIS 필수 모델에 bare ID 사용 → "not in allowed"
    - ON_DEMAND 모델에 us./global. prefix 사용 → "model identifier is invalid" (ValidationException)
    게이트웨이 ConverseStream 실측(2026-08)에서 확인된 형태:
    - INFERENCE_PROFILE 전용 모델에 bare ID 사용 → ValidationException.
      "Invocation of model ID ... with on-demand throughput isn't supported.
       Retry your request with the ID or ARN of an inference profile ..."
      control-plane이 `inferenceTypesSupported=["INFERENCE_PROFILE"]`로 보고하는
      모델이며, 이 신호는 정확히 "bare ID → CRIS(us./global.) 필요"를 뜻한다.
      벤더 메시지 전문 대신 안정적인 부분 문자열만 본다.
    양방향 prefix 폴백의 트리거로 사용한다.
    """
    if not msg:
        return False
    low = str(msg).lower()
    if "not in allowed" in low:
        return True
    if "model identifier is invalid" in low:
        return True
    if "invalid model identifier" in low:
        return True
    if "unknown model" in low:
        return True
    if "model not found" in low:
        return True
    if "resourcenotfound" in low and "model" in low:
        return True
    if "on-demand throughput" in low:
        return True
    if "inference profile" in low:
        return True
    return False


#: SSE 이벤트 중 **모델 출력을 담지 않는** 프레임 타입(실측 관측 기준).
#: 이 프레임만 방출된 상태는 "출력 미방출"로 보고 prefix 폴백 1회를 허용한다.
#: 출력이 이미 소비자에게 전달된 뒤에는 어떤 경우에도 재시도하지 않는다.
_SSE_NON_OUTPUT_EVENT_TYPES = ("stream_start", "error")


def _strip_region_prefix(model_id: str) -> str:
    """us./eu./global. prefix를 제거한 bare 모델 ID 반환. prefix 없으면 원본."""
    for p in ("us.", "eu.", "global."):
        if model_id.startswith(p):
            return model_id[len(p):]
    return model_id


def _has_region_prefix(model_id: str) -> bool:
    """us./eu./global. prefix가 붙어 있으면 True."""
    return model_id.startswith("us.") or model_id.startswith("eu.") or model_id.startswith("global.")


#: S3 오류 코드 — "결과가 아직 안 올라옴"(폴링을 계속하면 해소될 수 있음).
_S3_TRANSIENT_MISS_CODES = frozenset({"NoSuchKey", "NotFound", "404"})
#: S3 오류 코드 — 기다려도 해소되지 않음(권한·자격증명·버킷 문제 → 즉시 종료).
_S3_FATAL_CODES = frozenset({
    "AccessDenied",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "ExpiredToken",
    "ExpiredTokenException",
    "NoSuchBucket",
    "AllAccessDisabled",
})
#: HTTP 상태 기반 판정 — 403은 즉시 종료, 404는 정상적인 miss.
_S3_FATAL_HTTP_STATUS = frozenset({403})
_S3_TRANSIENT_HTTP_STATUS = frozenset({404})


def _is_transient_s3_miss(exc) -> bool:
    """잡 폴링 중 발생한 S3 예외가 "아직 결과 없음"인지 판정한다.

    ``True``  → 정상적인 miss(또는 판정 불가) → 폴링을 계속한다.
    ``False`` → 권한·자격증명·버킷 오류처럼 기다려도 해소되지 않음 → 즉시 종료한다.

    botocore ``ClientError`` 의 ``response["Error"]["Code"]`` 와
    ``response["ResponseMetadata"]["HTTPStatusCode"]`` 를 본다.
    판정 불가(네트워크 순간 오류, ``response`` 없는 예외 등)는 **``True``** 로 본다 —
    기존 ``except Exception: continue`` 동작을 그대로 보존해 무회귀를 보장한다.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return True
    error = response.get("Error")
    code = str((error or {}).get("Code") or "") if isinstance(error, dict) else ""
    meta = response.get("ResponseMetadata")
    raw_status = meta.get("HTTPStatusCode") if isinstance(meta, dict) else None
    try:
        status = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        status = None
    # 종료 사유를 먼저 본다 — 403 AccessDenied 가 404 로 위장하는 경우까지 잡는다.
    if code in _S3_FATAL_CODES or status in _S3_FATAL_HTTP_STATUS:
        return False
    if code in _S3_TRANSIENT_MISS_CODES or status in _S3_TRANSIENT_HTTP_STATUS:
        return True
    # 그 밖은 판정 불가 → 기존 동작(폴링 계속) 유지.
    return True


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
        print("[GW] 자격증명 주입 완료")

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
                    # 느린 모델(Upscale 등)은 동기 요청이어도 게이트웨이가 async 로 자동
                    # 핸드오프해 200/202 + {decision:ACCEPTED, job_id, async, status_url} 을
                    # 즉시 반환한다. 이 경우 job 을 폴링해 최종 이미지를 받는다(29초 하드리밋 회피).
                    if self._is_async_accepted(data):
                        return await self._poll_invoke_job(data)
                    return self._extract_invoke_result(data)

                # 202 ACCEPTED — async 잡 핸들 반환. 폴링으로 완료 대기.
                if resp.status_code == 202:
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}
                    if self._is_async_accepted(data) or data.get("job_id"):
                        return await self._poll_invoke_job(data)
                    return {"error": f"HTTP 202 이나 job_id 없음: {resp.text[:160]}"}

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

    # ─────────────────────────────────────────────────────────────────
    # /invoke async 핸드오프 지원 — 느린 이미지 모델(Upscale 등)
    # 게이트웨이가 29초 하드리밋을 넘는 연산을 async 잡으로 자동 전환(202 또는
    # 200+decision:ACCEPTED)하므로, 여기서 job 을 폴링해 최종 이미지를 받는다.
    # 호출자(_tool_edit_image 등)는 변경 불필요 — invoke_model 이 결과를 그대로 반환.
    # ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _is_async_accepted(data) -> bool:
        """게이트웨이 응답이 async 잡 수락(ACCEPTED)인지 판별."""
        if not isinstance(data, dict):
            return False
        if data.get("async") is True:
            return True
        dec = str(data.get("decision", "")).upper()
        if dec == "ACCEPTED":
            return True
        # async 표식 없이 job_id + status_url 만 오는 경우도 async 로 간주.
        return bool(data.get("job_id") and data.get("status_url"))

    @staticmethod
    def _extract_invoke_result(data) -> dict:
        """/invoke 성공 응답에서 이미지 데이터를 추출한다(동기·잡 결과 공용).

        지원 형태:
          - {"images": [...]}
          - {"decision":"ALLOW","output":{"images":[...] 또는 기타}}
          - {"body": "<json>" | {...}} 안에 images
          - 그 외에는 원본 dict 를 그대로 반환(상위에서 해석).
        """
        if not isinstance(data, dict):
            return {"error": f"예상치 못한 응답 형식: {str(data)[:160]}"}
        if "images" in data:
            return {"images": data["images"]}
        out = data.get("output")
        if isinstance(out, dict):
            if "images" in out:
                return {"images": out["images"]}
            return out
        resp_body = data.get("body", data)
        if isinstance(resp_body, str):
            try:
                resp_body = json.loads(resp_body)
            except (ValueError, TypeError):
                resp_body = {}
        if isinstance(resp_body, dict) and "images" in resp_body:
            return {"images": resp_body["images"]}
        return data

    async def _poll_invoke_job(self, submit_data: dict, max_wait: int = 600,
                               interval: int = 3) -> dict:
        """async /invoke 잡을 완료까지 폴링해 최종 이미지를 반환한다.

        Args:
            submit_data: 제출 응답 dict(job_id, status_url 포함).
            max_wait: 최대 대기(초). Upscale 은 통상 40~120초.
            interval: 폴링 간격(초).

        Returns:
            {"images":[...]} 성공, {"error":...} 실패/타임아웃.
        """
        job_id = submit_data.get("job_id") or self._extract_job_id(submit_data)
        if not job_id:
            return {"error": f"async 잡 id 추출 실패: {str(submit_data)[:160]}"}

        # 결과 문서에서 이미지/상태를 추출하는 공용 로직.
        def _extract(doc):
            if not isinstance(doc, dict):
                return None, None
            status = str(doc.get("status", "")).upper()
            result = self._extract_invoke_result(doc)
            if isinstance(result, dict) and result.get("images"):
                return result, status
            for key in ("result", "output"):
                sub = doc.get(key)
                if isinstance(sub, dict):
                    r2 = self._extract_invoke_result(sub)
                    if isinstance(r2, dict) and r2.get("images"):
                        return r2, status
            return None, status

        # ── 주 경로: HTTP 상태 엔드포인트 폴링(GET /invoke-jobs/{id}) ──
        # IAM 그랜트(execute-api:Invoke on GET /invoke-jobs/*) 적용 후 이 경로가 200 으로 동작한다.
        # 잡 라이프사이클(ACCEPTED→QUEUED→RUNNING→SUCCEEDED/FAILED)을 실시간 확인하므로,
        # 실패 사유(error_message)를 즉시 표면화할 수 있다(S3 결과 미기록 실패를 예산 소진 없이 감지).
        # 권한 거부(403)로 회귀하면 아래 S3 폴백으로 전환한다(무권한 배포/역할 무손상).
        status_path = submit_data.get("status_url") or f"/invoke-jobs/{job_id}"
        url = (
            f"{self.gateway_url}{status_path}"
            if status_path.startswith("/")
            else status_path
        )
        elapsed = 0
        _http_denied = False
        while elapsed <= max_wait:
            await asyncio.sleep(interval)
            elapsed += interval
            headers = self._sign("GET", url, b"")
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(30.0, connect=10.0)
                ) as client:
                    r = await client.get(url, headers=headers)
            except Exception:
                continue
            if r.status_code != 200:
                low = (r.text or "").lower()
                if r.status_code == 403 or "not authorized to perform" in low:
                    _http_denied = True  # 권한 없음 → S3 폴백으로
                    break
                if self._is_expired_error(r.text):
                    self.force_refresh_creds()
                continue
            try:
                d = r.json()
            except Exception:
                continue
            status = str(d.get("status", "")).upper()
            if status in ("SUCCEEDED", "COMPLETED", "SUCCESS"):
                result, _st = _extract(d)
                if result:
                    return result
                # 상태 응답에 인라인 이미지가 없으면 S3 결과 문서에서 회수.
                s3doc = await self._poll_job_data(job_id, max_wait=30)
                result, _st = _extract(s3doc)
                if result:
                    return result
                return {"error": "잡 성공이나 이미지 미검출"}
            if status in ("FAILED", "ERROR", "CANCELED", "CANCELLED"):
                detail = str(d.get("error_message") or d.get("error") or "")[:300]
                return {"error": f"async 잡 {status}: {detail}"}
            # QUEUED / RUNNING / ACCEPTED — 계속 폴링

        # ── 폴백: HTTP 권한 거부(403) 시 S3 결과 문서 폴링 ──
        if _http_denied:
            s3doc = await self._poll_job_data(job_id, max_wait=max_wait)
            if isinstance(s3doc, dict):
                status = str(s3doc.get("status", "")).upper()
                if status in ("FAILED", "ERROR", "CANCELED", "CANCELLED"):
                    detail = str(s3doc.get("error_message") or s3doc.get("error") or "")[:300]
                    return {"error": f"async 잡 {status}: {detail}"}
                result, _st = _extract(s3doc)
                if result:
                    return result

        return {"error": f"async 잡 시간 초과/폴링 불가 ({max_wait}초, job={job_id[:12]}...)"}

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

        # 양방향 prefix 폴백은 정확히 1회만 수행 (무한루프 방지)
        _prefix_fallback_used = False

        # max_tokens 하향(step-down) 재시도 — **별도 카운터**로 만료·prefix 재시도
        # 예산(3회)을 잠식하지 않는다. 시작값은 payload 가 이미 계산한 값을 읽는다
        # (중복 계산 금지 — _build_payload 의 min(env_cap, model_limit) 결과).
        _stepdown_used = 0
        current_max = _payload_max_tokens(payload)

        # 연결 시도 예산 — urlopen 시도와 그 재시도에만 적용된다(잡 대기는 제외).
        # 이 deadline 이 없던 과거엔 시도마다 예산이 새로 시작해 최악 1800초(30분)였다.
        deadline = time.monotonic() + _converse_total_budget()

        attempt = 0
        while attempt < 3:
            # 예산이 이미 끝났으면 새 시도를 시작하지 않는다(첫 시도는 항상 보장).
            if (attempt > 0 or _stepdown_used) and time.monotonic() >= deadline:
                print(f"[GW] 연결 시도 예산 소진 — 재시도 중단 (시도 {attempt+1}/3 미시작)")
                break
            headers = self._sign("POST", url, body_bytes)
            # 남은 예산으로 clamp — 300은 기존 값이며 이제 상한으로만 남는다.
            attempt_timeout = max(
                _CONVERSE_ATTEMPT_TIMEOUT_FLOOR,
                min(_CONVERSE_ATTEMPT_TIMEOUT_CAP, int(deadline - time.monotonic())),
            )
            def _call(h=headers, b=body_bytes, t=attempt_timeout):
                req = urllib.request.Request(url, data=b, method="POST")
                for k, v in h.items():
                    req.add_header(k, v)
                try:
                    resp = urllib.request.urlopen(req, timeout=t)
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
                    # 이미 하향한 max_tokens 는 재구성 후에도 유지한다(다시 초과 실패 방지).
                    if current_max is not None:
                        _set_payload_max_tokens(payload, current_max)
                    body_bytes = json.dumps(payload).encode()
                    await asyncio.sleep(0.5)
                    attempt += 1
                    continue

            # prefix 형태 불일치로 거부 → 반대 형태로 1회 폴백 (양방향)
            err_or_deny = result.get("decision") in ("ERROR", "DENY")
            deny_reason = result.get("denial_reason", "") + result.get("error", "")
            if err_or_deny and not _prefix_fallback_used and _is_prefix_form_error(deny_reason):
                cur_id = payload.get("modelId", model_id)
                if _has_region_prefix(cur_id):
                    # 이미 prefix 있음 → bare로 재시도 (3P 신규 모델의 invalid identifier 복구)
                    new_id = _strip_region_prefix(cur_id)
                else:
                    # bare → us. prefix로 재시도 (CRIS 필수 모델)
                    new_id = f"us.{cur_id}"
                if new_id != cur_id:
                    print(f"[GW] prefix 형태 거부 '{cur_id}' → '{new_id}' 로 재시도")
                    payload["modelId"] = new_id
                    body_bytes = json.dumps(payload).encode()
                    _prefix_fallback_used = True  # 정확히 1회만
                    attempt += 1
                    continue

            # max_tokens 초과 → 하향 후 재시도 (SSE 와 동일한 판정·계산 재사용).
            # 미지 모델에 낙관적 상한을 싣는 정책의 안전망이다 — 하드 실패시키지 않는다.
            if err_or_deny and current_max is not None \
                    and _stepdown_used < _MAX_TOKENS_STEPDOWN_RETRIES \
                    and _is_max_tokens_error(deny_reason):
                extracted = _extract_model_token_limit(deny_reason)
                if extracted and extracted - 1 < current_max:
                    # 에러는 "lower than X" 라고 함 → X-1 사용
                    new_max = max(_MAX_TOKENS_STEPDOWN_FLOOR, extracted - 1)
                else:
                    new_max = max(_MAX_TOKENS_STEPDOWN_FLOOR, int(current_max * 0.5))
                if new_max < current_max and _set_payload_max_tokens(payload, new_max):
                    print(f"[GW] max_tokens 한계 초과 — {current_max} → {new_max}로 재시도 "
                          f"(step-down {_stepdown_used+1}/{_MAX_TOKENS_STEPDOWN_RETRIES})")
                    current_max = new_max
                    body_bytes = json.dumps(payload).encode()
                    _stepdown_used += 1  # 만료·prefix 예산(attempt)은 소모하지 않는다
                    continue

            if result.get("decision") == "ACCEPTED":
                # 비동기 모델 — S3 폴링으로 결과 대기.
                # ⚠️ 구조화 dict 를 그대로 받아 toolUse 블록을 보존한다(도구 호출 필수).
                #    과거엔 text 만 뽑아 반환해 toolUse 가 유실 → planner/evaluator 등
                #    toolChoice 강제 호출이 tool_calls 를 못 받아 폴백되던 결함이 있었다.
                job_id = result.get("job_id", "")
                if job_id:
                    # 잡 대기는 연결 시도 예산과 무관하다 — 서버측에서 도는 잡은
                    # 1시간 이상 걸릴 수 있으므로 _job_max_wait()(기본 7200초)를 쓴다.
                    data = await self._poll_job_data(job_id, max_wait=_job_max_wait())
                    if isinstance(data, dict):
                        out_msg = (data.get("output") or {}).get("message")
                        if isinstance(out_msg, dict) and out_msg.get("content"):
                            # 구조화 응답(toolUse/text 블록 포함) 손실 없이 전달.
                            merged = {
                                "decision": "ALLOW",
                                "output": {"message": out_msg},
                                "remaining_quota": result.get("remaining_quota", {}),
                                "estimated_cost_krw": result.get("estimated_cost_krw", 0),
                            }
                            # 사용량/종료사유 등 부가 필드가 있으면 함께 전달(있을 때만).
                            for k in ("usage", "stopReason", "metrics"):
                                if k in data:
                                    merged[k] = data[k]
                            return merged
                        # content 가 없으면 텍스트로 폴백(하위 호환).
                        text = self._job_data_to_text(data)
                        if text:
                            return {"decision": "ALLOW",
                                    "output": {"message": {"content": [{"text": text}]}},
                                    "remaining_quota": result.get("remaining_quota", {}),
                                    "estimated_cost_krw": result.get("estimated_cost_krw", 0)}
                    return {"decision": "ERROR", "error": f"비동기 작업 시간 초과 (job: {job_id[:12]}...)"}
                if attempt < 2:
                    await asyncio.sleep(2)
                    attempt += 1
                    continue
            return result
        return result

    async def converse_stream_live(self, model_id, messages, system_prompt="", tool_config=None):
        """Lambda Function URL — 토큰 만료 시 자동 갱신 + 재시도 (최대 3회)."""
        result = None
        _prefix_fallback_used = False  # 양방향 prefix 폴백은 정확히 1회
        for _retry in range(3):
            result = await self._converse_stream_live_once(model_id, messages, system_prompt, tool_config)
            err = result.get("error", "")
            if self._is_expired_error(err):
                print(f"[Stream] 토큰 만료 감지 (시도 {_retry+1}/3) — 자격증명 갱신 후 재시도")
                self.force_refresh_creds()
                await asyncio.sleep(0.5)
                continue
            # prefix 형태 불일치로 거부 → 반대 형태로 1회 폴백 (양방향)
            if not _prefix_fallback_used and _is_prefix_form_error(err):
                if _has_region_prefix(model_id):
                    new_id = _strip_region_prefix(model_id)
                else:
                    new_id = f"us.{model_id}"
                if new_id != model_id:
                    print(f"[Stream] prefix 형태 거부 '{model_id}' → '{new_id}' 로 재시도")
                    model_id = new_id
                    _prefix_fallback_used = True
                    continue
            return result
        return result

    async def stream_sse_realtime(self, model_id, messages, system_prompt="", tool_config=None):
        """Lambda Function URL — 실시간 SSE 이터레이터 (httpx 비동기, 스레드 고갈 없음).

        max_tokens 검증 실패 시 자동으로 한계의 50%로 줄여 재시도. 최대 2회 재시도.
        """
        max_retries = 2
        attempt = 0
        _prefix_fallback_used = False  # 양방향 prefix 폴백은 정확히 1회
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
            _output_seen = False   # 모델 출력 프레임을 방출했는지
            _prefix_retry = False  # in-band prefix 폴백 재시도가 예약됐는지
            try:
                async with httpx.AsyncClient(
                    # SSE 스트림 — Lambda 응답 시간 제한 없음 (1시간), connect 30초, read 5분
                    # 모델이 read 타임아웃 이상 토큰 생성 안 하면 끊김으로 판단.
                    # 아래 ReadTimeout 에러 문구가 같은 상수를 참조한다(값 어긋남 방지).
                    timeout=httpx.Timeout(_sse_total_timeout(),
                                          connect=_SSE_CONNECT_TIMEOUT,
                                          read=_SSE_READ_TIMEOUT)
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
                                        # prefix 형태 거부가 in-band error 이벤트(HTTP 200)로
                                        # 오는 실측 형태 — 아래 비-200 폴백 블록은 이 경우를
                                        # 볼 수 없으므로 여기서 반대 형태로 1회만 재시도한다.
                                        # 한도는 동일 route 1회(_prefix_fallback_used) 그대로.
                                        if (not _output_seen) and (not _prefix_fallback_used) \
                                                and _is_prefix_form_error(msg):
                                            if _has_region_prefix(model_id):
                                                _flip_id = _strip_region_prefix(model_id)
                                            else:
                                                _flip_id = f"us.{model_id}"
                                            if _flip_id != model_id:
                                                print(f"[GW SSE] prefix 형태 거부(in-band) '{model_id}' → '{_flip_id}' 로 재시도")
                                                model_id = _flip_id
                                                _prefix_fallback_used = True
                                                _prefix_retry = True
                                                # 오류를 방출하지 않고 while 루프 재시작
                                                break
                                    if evt.get("type") not in _SSE_NON_OUTPUT_EVENT_TYPES:
                                        _output_seen = True
                                    had_data = True
                                    yield evt
                                else:
                                    # inner while 정상 종료 (break 안 일어남) — 다음 chunk 받기
                                    continue
                                # break 발생 — chunk 루프 끝
                                break
            except httpx.ReadTimeout:
                error_event = {"type": "error",
                               "message": f"Lambda 응답 타임아웃 ({int(_SSE_READ_TIMEOUT)}초 무응답)"}
            except httpx.ConnectTimeout:
                error_event = {"type": "error", "message": "Lambda 연결 타임아웃"}
            except httpx.RemoteProtocolError as e:
                error_event = {"type": "error", "message": f"Lambda 연결 끊김: {e}"}
            except Exception as e:
                error_event = {"type": "error", "error": str(e)}

            # in-band prefix 폴백 예약 → 교정된 model_id로 같은 while 루프에서 재시도
            if _prefix_retry:
                continue
            # prefix 형태 불일치로 거부 → 반대 형태로 1회 폴백 (양방향, 데이터 미방출 시에만)
            if error_event is not None and not had_data and not _prefix_fallback_used:
                _emsg = str(error_event.get("message") or error_event.get("error") or "")
                if _is_prefix_form_error(_emsg):
                    if _has_region_prefix(model_id):
                        _new_id = _strip_region_prefix(model_id)
                    else:
                        _new_id = f"us.{model_id}"
                    if _new_id != model_id:
                        print(f"[GW SSE] prefix 형태 거부 '{model_id}' → '{_new_id}' 로 재시도")
                        model_id = _new_id
                        _prefix_fallback_used = True
                        error_event = None
                        continue
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
            # total 300초는 prefer_streaming 경로의 5분 벽이었다 — SSE total 과 같은 값으로.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_stream_live_total_timeout(),
                                      connect=_STREAM_LIVE_CONNECT_TIMEOUT)
            ) as client:
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

    async def _poll_job_data(self, job_id, max_wait=300):
        """비동기 잡 결과(S3)를 **구조화 dict 그대로** 폴링해 반환한다.

        ``max_wait`` 는 **총 대기 초**다(기본값 300 유지 — 호출자가 긴 값을 넘긴다.
        converse 는 `_job_max_wait()`, 기본 7200초를 넘긴다).
        폴링 간격은 `_job_poll_interval` 의 적응형 단계를 따른다 —
        처음 30초 1초, 그 다음 2초, 5분 이후 5초, 20분 이후 10초.
        짧은 ``max_wait``(예: 4)에서는 1초 간격 4회로 기존 동작과 동일하다.

        반환: 파싱된 전체 응답 엔벨로프 dict(예: ``{"output": {"message": {...}}}``) 또는
              시간 초과 시 None.

        ⚠️ 이 메서드는 toolUse 블록을 포함한 전체 content 를 손실 없이 보존한다.
           텍스트만 필요하면 _poll_job_result 를 쓴다(내부적으로 이 메서드를 재사용).
        """
        creds = self._get_creds()
        s3 = boto3.client("s3", aws_access_key_id=creds.access_key, aws_secret_access_key=creds.secret_key, aws_session_token=creds.token, region_name=self.region)
        try:
            account = boto3.Session(profile_name=self.aws_profile).client("sts").get_caller_identity()["Account"]
        except Exception:
            account = "107650139384"
        bucket = f"bedrock-gw-dev-payload-{account}"
        key = f"results/{job_id}.json"
        elapsed = 0.0
        while elapsed < max_wait:
            # 적응형 간격 — 남은 대기 시간을 넘기지 않는다(max_wait = 총 대기 초).
            interval = min(_job_poll_interval(elapsed), max_wait - elapsed)
            await asyncio.sleep(interval)
            elapsed += interval
            try:
                obj = s3.get_object(Bucket=bucket, Key=key)
                return json.loads(obj["Body"].read().decode())
            except Exception as exc:
                # 권한·자격증명·버킷 오류는 기다려도 해소되지 않는다 — 5분을 다 태우지 말고
                # 실제 원인을 남기고 즉시 종료한다("아직 결과 없음"과 구분).
                if not _is_transient_s3_miss(exc):
                    print(f"[GW] 잡 폴링 중단 — S3 오류가 재시도로 해소되지 않음: "
                          f"{type(exc).__name__}: {str(exc)[:200]}")
                    return None
                continue
        return None

    @staticmethod
    def _job_data_to_text(data) -> str:
        """잡 결과 dict → text 블록만 이어붙인 문자열(toolUse 등 비텍스트는 무시).

        텍스트 블록이 없으면 원본 dict 를 JSON 으로 직렬화(앞 500자)해 진단용으로 반환한다.
        """
        if not isinstance(data, dict):
            return ""
        content = data.get("output", {}).get("message", {}).get("content", []) or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and "text" in c]
        return "\n".join(texts) if texts else json.dumps(data, ensure_ascii=False)[:500]

    async def _poll_job_result(self, job_id, max_wait=300):
        """비동기 잡 결과를 폴링해 **텍스트**만 반환(하위 호환 — 스트리밍/미디어 경로용).

        toolUse 를 보존해야 하는 converse 는 _poll_job_data 를 직접 사용한다.
        """
        data = await self._poll_job_data(job_id, max_wait=max_wait)
        if data is None:
            return ""
        return self._job_data_to_text(data)

    # ─────────────────────────────────────────────────────────────────
    # OpenAI Responses 라우트 통합 — 신규 메서드 (순수 add)
    # 기존 converse/invoke/스트리밍 메서드의 시그니처·동작은 불변.
    # 기존 _sign / _get_creds / force_refresh_creds / _is_expired_error 재사용.
    # ─────────────────────────────────────────────────────────────────
    def _build_openai_payload(self, model_id, messages, system_prompt=""):
        """OpenAI Responses 표준 요청 본문 구성.

        - {"model": model_id}
        - messages가 str이면 그대로 "input"에, 아니면 _to_openai_input로 정규화
        - system_prompt가 있으면 "instructions"로 부착

        주의(라이브 확인 2026-06): 동기 라우트(/openai/responses)는 본문을 그대로
        OpenAI/mantle 백엔드로 전달하므로 'modelId' 같은 게이트웨이 전용 필드를
        넣으면 'Unknown parameter: modelId'로 거부(502)된다. 따라서 여기서는
        OpenAI 표준 필드(model/input/instructions)만 구성한다. 비동기 잡 라우트가
        요구하는 'modelId'는 openai_responses_job_submit에서만 별도로 부착한다.
        """
        body = {"model": model_id}
        if isinstance(messages, str):
            body["input"] = messages
        else:
            body["input"] = self._to_openai_input(messages)
        if system_prompt:
            body["instructions"] = system_prompt
        return body

    def _to_openai_input(self, messages):
        """Bedrock 스타일 messages를 OpenAI Responses ``input`` 형태로 정규화.

        - messages가 str이면 그대로 반환(단순 텍스트 입력).
        - list이면 각 메시지를
          ``{"role": <role>, "content": [{"type": <input_text|output_text>, "text": ...}]}``
          형태로 변환한다. assistant 역할은 ``output_text``, 그 외는 ``input_text``.
        - 텍스트가 비는 메시지는 건너뛴다. 방어적으로 비정형 입력도 흡수한다.

        반환: list[dict] | str
        """
        if messages is None:
            return []
        if isinstance(messages, str):
            return messages
        if not isinstance(messages, (list, tuple)):
            return []

        normalized = []
        for msg in messages:
            # 비정형 항목(문자열 등)은 user 메시지로 흡수
            if not isinstance(msg, dict):
                if isinstance(msg, str) and msg:
                    normalized.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": msg}],
                    })
                continue
            role = msg.get("role") or "user"
            content = msg.get("content")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, (list, tuple)):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        t = block.get("text")
                        if isinstance(t, str):
                            parts.append(t)
                    elif isinstance(block, str):
                        parts.append(block)
                text = "".join(parts)
            elif content is not None:
                text = str(content)
            if not text:
                continue
            block_type = "output_text" if role == "assistant" else "input_text"
            normalized.append({
                "role": role,
                "content": [{"type": block_type, "text": text}],
            })
        return normalized

    def _openai_request_blocking(self, method, url, body_bytes, timeout):
        """urllib 동기 호출 — 상태코드/본문을 그대로 반환(에러 매핑은 호출자가 수행).

        반환: {"status": int, "body": str, "json": dict|None} 또는
              {"status": -1, "error": str}  (네트워크/타임아웃 등)
        """
        import urllib.request, urllib.error
        import socket
        headers = self._sign(method, url, body_bytes)
        req = urllib.request.Request(url, data=body_bytes, method=method)
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read().decode()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
            return {"status": getattr(resp, "status", 200) or 200, "body": raw, "json": parsed}
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode()
            except Exception:
                err_body = ""
            return {"status": e.code, "body": err_body, "json": None}
        except (TimeoutError, socket.timeout) as e:
            return {"status": -1, "error": f"timeout: {e}", "timeout": True}
        except Exception as e:
            return {"status": -1, "error": str(e)}

    def _looks_unsupported_model(self, status, body_text):
        """게이트웨이 응답이 '미지원 모델' 거부인지 방어적으로 판정."""
        low = (body_text or "").lower()
        return (
            "unsupported" in low and "model" in low
        ) or "model not found" in low or "unknown model" in low or "not in allowed" in low

    async def _openai_post_with_retry(self, url, body_bytes, timeout, label="openai"):
        """OpenAI 라우트 POST 공통 처리.

        에러 규칙 (요구사항 7 / gateway.md):
        - 403 → QuotaExceededError
        - 422 → OpenAISurfaceError(원인 ≤200자)
        - 500 → 1s/2s/4s 지수 백오프 최대 3회
        - 토큰 만료(_is_expired_error) → force_refresh_creds 후 최대 3회 재시도
        - 타임아웃 → SyncTimeout
        - 미지원 모델 응답 → OpenAIModelUnsupported (호출자가 model_id 부착)
        반환: 게이트웨이 원본 응답 dict
        """
        loop = asyncio.get_event_loop()
        backoff = [1, 2, 4]
        last_err = ""
        for attempt in range(3):
            result = await loop.run_in_executor(
                None, self._openai_request_blocking, "POST", url, body_bytes, timeout
            )
            status = result.get("status")

            # 네트워크 계층 (타임아웃 등)
            if status == -1:
                if result.get("timeout"):
                    raise SyncTimeout(f"{label} sync timeout: {result.get('error','')[:200]}")
                err_str = result.get("error", "")
                last_err = err_str
                if self._is_expired_error(err_str) and attempt < 2:
                    print(f"[GW {label}] 토큰 만료 감지 (시도 {attempt+1}/3) — 자격증명 갱신 후 재시도")
                    self.force_refresh_creds()
                    await asyncio.sleep(0.5)
                    continue
                # 일반 네트워크 오류는 백오프 재시도
                if attempt < 2:
                    await asyncio.sleep(backoff[attempt])
                    continue
                raise OpenAISurfaceError(f"{label} 호출 실패: {err_str[:200]}")

            body_text = result.get("body", "") or ""

            if status in (200, 202):
                # 200: 동기 완료 응답. 202: 비동기 잡 ACCEPTED(job_id 포함).
                if self._looks_unsupported_model(status, body_text):
                    raise OpenAIModelUnsupported(body_text[:200])
                return result.get("json") if result.get("json") is not None else {"body": body_text}

            # 토큰 만료
            if self._is_expired_error(body_text):
                if attempt < 2:
                    print(f"[GW {label}] 토큰 만료 감지 (시도 {attempt+1}/3) — 자격증명 갱신 후 재시도")
                    self.force_refresh_creds()
                    await asyncio.sleep(0.5)
                    continue

            if status == 403:
                raise QuotaExceededError(f"{label} 403: {body_text[:200]}")
            if status == 422:
                if self._looks_unsupported_model(status, body_text):
                    raise OpenAIModelUnsupported(body_text[:200])
                raise OpenAISurfaceError(body_text[:200])
            if status == 404 and self._looks_unsupported_model(status, body_text):
                raise OpenAIModelUnsupported(body_text[:200])
            if status >= 500:
                last_err = f"HTTP {status}: {body_text[:200]}"
                if attempt < 2:
                    print(f"[GW {label}] HTTP {status} — {backoff[attempt]}s 후 재시도 (시도 {attempt+1}/3)")
                    await asyncio.sleep(backoff[attempt])
                    continue
                raise OpenAISurfaceError(last_err)
            # 기타 4xx
            raise OpenAISurfaceError(f"HTTP {status}: {body_text[:200]}")

        raise OpenAISurfaceError(f"{label} 최대 재시도 횟수 초과: {last_err[:200]}")

    async def openai_responses_sync(self, model_id, messages, system_prompt="", timeout=120):
        """POST {gateway_url}/openai/responses (동기).

        반환: 게이트웨이 원본 응답 dict (어댑터가 후처리).
        에러 매핑은 _openai_post_with_retry 참조. 미지원 모델 → OpenAIModelUnsupported(model_id).
        Runtime_Credentials만 사용하며 자격증명을 저장하지 않는다.
        """
        url = f"{self.gateway_url}/openai/responses"
        body = self._build_openai_payload(model_id, messages, system_prompt)
        body_bytes = json.dumps(body).encode()
        try:
            return await self._openai_post_with_retry(url, body_bytes, timeout, label="responses")
        except OpenAIModelUnsupported:
            raise OpenAIModelUnsupported(model_id)

    async def openai_responses_call(self, body: dict, timeout=120):
        """임의 본문으로 POST /openai/responses (도구 실행 루프 전용).

        body는 호출자가 구성한다({"model","input","tools","tool_choice","instructions"} 등).
        에러 매핑/재시도/토큰갱신은 _openai_post_with_retry를 그대로 재사용한다.
        Runtime_Credentials만 사용하며 자격증명을 저장하지 않는다.
        """
        url = f"{self.gateway_url}/openai/responses"
        body_bytes = json.dumps(body).encode()
        return await self._openai_post_with_retry(url, body_bytes, timeout, label="responses-tools")

    def _apply_jobs_model_id(self, body, model_id):
        """비동기 잡 라우트의 model ID 부착 seam(오버라이드 지점).

        비동기 잡 라우트는 게이트웨이 레벨에서 'modelId'를 요구한다(라이브 확인:
        누락 시 400 'modelId is required'). 동기 라우트와 달리 게이트웨이가 이
        필드를 소비/제거 후 백엔드를 호출하므로 잡 경로에서만 부착한다.

        기본 구현은 기존 인라인 동작과 **바이트 동일**하다 — 입력 body를 변형하지
        않고 top-level 'modelId'를 부착한 새 dict를 반환한다(키 순서도 동일).
        하위 클래스는 verified Route_Contract에 따라 부착 위치·필요 여부를
        오버라이드할 수 있다.
        """
        return {**body, "modelId": model_id}

    async def openai_responses_job_submit(self, model_id, messages, system_prompt="", timeout=30):
        """POST {gateway_url}/openai/responses-jobs 제출 → job_id 반환.

        제출 응답에서 job_id를 방어적으로 추출(후보 키 job_id/jobId/id/job/task_id).
        동일한 403/422/500/토큰만료 처리 규칙 적용.
        model ID 부착은 `_apply_jobs_model_id` seam에 위임한다.
        """
        url = f"{self.gateway_url}/openai/responses-jobs"
        body = self._build_openai_payload(model_id, messages, system_prompt)
        body = self._apply_jobs_model_id(body, model_id)
        body_bytes = json.dumps(body).encode()
        try:
            raw = await self._openai_post_with_retry(url, body_bytes, timeout, label="responses-jobs")
        except OpenAIModelUnsupported:
            raise OpenAIModelUnsupported(model_id)

        job_id = self._extract_job_id(raw)
        if not job_id:
            raise OpenAISurfaceError(f"job_id 추출 실패: {str(raw)[:200]}")
        return job_id

    @staticmethod
    def _extract_job_id(raw):
        """잡 제출 응답에서 job_id 방어적 추출."""
        if not isinstance(raw, dict):
            return ""
        for key in ("job_id", "jobId", "id", "job", "task_id"):
            val = raw.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict):
                # 중첩된 경우 한 단계 더 탐색
                for k2 in ("job_id", "jobId", "id"):
                    v2 = val.get(k2)
                    if isinstance(v2, str) and v2:
                        return v2
        return ""

    @staticmethod
    def _extract_job_status(raw):
        """잡 상태 응답에서 status 방어적 추출(소문자)."""
        if not isinstance(raw, dict):
            return ""
        for key in ("status", "state", "job_status"):
            val = raw.get(key)
            if isinstance(val, str) and val:
                return val.lower()
        return ""

    async def _openai_poll_job(self, job_id, poll_interval=5, max_wait=300):
        """잡 상태 폴링.

        - status ∈ {completed, succeeded} → 결과 dict 반환
        - status ∈ {failed, cancelled, canceled, error} → JobFailed(status)
        - 그 외(queued/in_progress/running 등) → poll_interval 만큼 sleep 후 재조회
        - 누적 대기 > max_wait → JobTimeout
        """
        url = f"{self.gateway_url}/openai/responses-jobs/{job_id}"
        loop = asyncio.get_event_loop()
        elapsed = 0
        while True:
            # GET 상태 조회 (서명은 빈 본문)
            result = await loop.run_in_executor(
                None, self._openai_request_blocking, "GET", url, b"", 30
            )
            status_code = result.get("status")
            if status_code == 200:
                raw = result.get("json")
                if not isinstance(raw, dict):
                    raw = {"body": result.get("body", "")}
                job_status = self._extract_job_status(raw)
                if job_status in ("completed", "succeeded"):
                    return raw
                if job_status in ("failed", "cancelled", "canceled", "error"):
                    raise JobFailed(job_status)
                # 진행중 — 계속 폴링
            elif status_code == 403:
                raise QuotaExceededError(f"job poll 403: {result.get('body','')[:200]}")
            elif status_code and status_code >= 400 and status_code != -1:
                body_text = result.get("body", "") or ""
                if self._is_expired_error(body_text):
                    self.force_refresh_creds()
                # 일시 오류로 보고 계속 폴링
            # -1(네트워크 오류)도 폴링 지속

            if elapsed >= max_wait:
                raise JobTimeout(f"job {job_id[:12]}... 폴링 {max_wait}s 초과")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

    async def openai_responses_job_submit_and_poll(self, model_id, messages, system_prompt="",
                                                   poll_interval=5, max_wait=300):
        """비동기 잡 제출 + 폴링 결합. 완료 결과 dict 반환."""
        job_id = await self.openai_responses_job_submit(model_id, messages, system_prompt)
        return await self._openai_poll_job(job_id, poll_interval, max_wait)

    async def close(self):
        self._creds = None
