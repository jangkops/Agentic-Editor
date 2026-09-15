"""Capability 영속 계층 — ``userData`` 하위 한정 경로 해석과 원자적 쓰기.

이 모듈은 `gateway-models-effort-support` 기능의 **유일한 디스크 접근 지점**이다.
Capability_Map·Effort_Settings·Baseline_Record·Verification_Record·catalog
snapshot·Validation_Run 보고서를 design.md "영속 경로 (userData 하위 한정)" 표의
경로로만 읽고 쓰며, 루트 밖 경로는 읽기·쓰기를 모두 거부한다(요구사항 10.15).

경로 규약 (design.md "영속 경로 (userData 하위 한정)")::

    userData/capability/capability_map.json                    Capability_Map
    userData/capability/effort_settings.json                   Effort_Settings
    userData/capability/baseline/{revision}.json               Baseline_Record 집합
    userData/capability/evidence/{evidenceRecordId}.json       정제 Verification_Record
    userData/capability/catalog/{catalogFingerprint}.json      정제 catalog snapshot
    userData/capability/runs/{runId}.json                      Validation_Run 보고서

userData 루트 결정(server.py ``_userdata_settings_path_candidates``·
research/cache.py ``_user_data_root``와 정합):

    1. 명시 인자 ``user_data_root`` (테스트·CLI 주입)
    2. 환경변수 ``AE_USERDATA_PATH`` (Electron이 주입한 userData 절대 경로)
    3. 환경변수 ``AE_GENERATED_ROOT`` — Electron은 ``{userData}/generated``를 주입하므로
       basename이 ``generated``면 그 부모를, 아니면(폴백 레이아웃) 값 자체를 루트로 본다
    4. 안전 기본 ``~/.agentic-editor`` (OS user별 격리·항상 쓰기 가능)

경로 가드(요구사항 10.15): 동적 경로 성분(revision·Evidence_Record_ID·
Catalog_Fingerprint·run ID)은 basename만 취해 안전 문자로 필터링하므로 ``..``·
절대경로·경로 구분자 주입으로 루트를 벗어날 수 없다. 최종 경로는 ``os.path.realpath``
정규화 후 루트 하위인지 재확인하며(symlink 이스케이프 차단) 벗어나면
``PathOutsideRootError``를 던진다. ``evr1:sha256:…`` 같은 ID의 ``:``는 Windows에서
경로 구분 문자이므로 ``_``로 치환한다(동일 ID → 항상 동일 파일명, 조회 왕복 보존).

원자적 쓰기(요구사항 10.15): 같은 디렉터리에 임시 파일을 만들고 ``fsync`` 후
``os.replace``로 교체한다. 따라서 독자는 항상 이전 완전본 또는 새 완전본만 보며
부분 기록 파일이 남지 않는다. 임시 파일은 ``mkstemp`` 기본 권한(0600)을 그대로
승계하고, 실패 시 삭제한다.

정제(요구사항 10.7~10.10, 10.12~10.14 — 저장 직전 강제):

    - credential·authorization·cookie·signature field는 중첩 위치를 포함해 **제거**
    - raw prompt field는 **Probe_ID로 대체** (같은 dict 또는 상위 dict의 ``probeId``)
    - raw request/response body field는 **Sanitized_Schema로 대체**
      (field name·type·cardinality·필요한 status만 — 값 본문은 보존하지 않음)

로깅(요구사항 10.10): ``log_probe``는 Probe_ID와 Sanitized_Schema만 출력하며,
토큰류 마스킹은 기존 ``ai_engine.gateway_module.mask_token``에 위임한다(신규 마스킹
구현을 만들지 않는다 — ``mask_token_for_log``).

제약: Python 3.11+ 표준 라이브러리만 사용하고 신규 의존성을 도입하지 않는다. 같은
패키지의 다른 모듈(``contracts.py`` 등)에 import 시점 결합을 만들지 않는다.

Requirements: 10.7, 10.8, 10.9, 10.10, 10.12, 10.15
Design: "Data Models" → "영속 경로 (userData 하위 한정)" · "보안" 절(정제·로깅·영속 범위)
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path

# ─────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────

#: userData 하위 capability 루트 디렉터리 이름.
CAPABILITY_DIRNAME = "capability"

#: 고정 파일명(design.md 영속 경로 표).
CAPABILITY_MAP_FILENAME = "capability_map.json"
EFFORT_SETTINGS_FILENAME = "effort_settings.json"

#: 하위 디렉터리 이름(design.md 영속 경로 표).
BASELINE_DIRNAME = "baseline"
EVIDENCE_DIRNAME = "evidence"
CATALOG_DIRNAME = "catalog"
RUNS_DIRNAME = "runs"

#: 안전 기본 userData 루트(비-Electron 실행·폴백 레이아웃).
_DEFAULT_USER_DATA_ROOT = "~/.agentic-editor"

#: Electron이 ``{userData}/generated``를 주입할 때의 마지막 경로 성분.
_GENERATED_DIRNAME = "generated"

#: 파일명에 허용하는 문자 집합. 그 외는 ``_``로 치환해 경로 구분자·제어문자·
#: 상위참조 유발 문자를 차단한다(research/cache.py와 동일 규약).
_SAFE_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)

#: 파일명 최대 길이(대부분 파일시스템의 255바이트 한계 대비 보수적 상한).
_MAX_NAME_LEN = 100

#: 정제·schema 파생의 최대 중첩 깊이. 초과분은 Sanitized_Schema로 축약한다.
_MAX_DEPTH = 12

#: Sanitized_Schema가 배열에 보존하는 서로 다른 원소 schema 최대 개수.
_MAX_ITEM_SCHEMAS = 8

#: 로그 한 줄에 허용하는 부가 field(비민감 화이트리스트)와 값 길이 상한.
_LOG_ALLOWED_EXTRA = (
    "modelId", "route", "routeKey", "status", "category", "retryCount",
    "fallback", "candidateLabel", "revision", "runId",
)
_LOG_VALUE_MAX = 200

#: Probe_ID를 찾을 수 없을 때 raw prompt 자리에 남기는 결정적 sentinel.
PROBE_ID_UNKNOWN = "probe:unknown"

# 정제 규칙 — 키 분류 집합 -------------------------------------------------
# DROP: 정규화 키에 **부분 일치**하면 제거한다(헤더 변형·prefix 대응).
#   `maxTokens`·`inputTokens`처럼 정상 field를 지우지 않도록 'token' 단독은
#   트리거로 쓰지 않고 실제 비밀 토큰 조합만 열거한다.
_DROP_KEY_SUBSTRINGS = (
    "authorization", "cookie", "signature", "credential",
    "accesskey", "secretkey", "secretaccess", "privatekey",
    "apitoken", "apikey", "authtoken", "accesstoken", "refreshtoken",
    "idtoken", "bearertoken", "sessiontoken", "securitytoken",
    "password", "passwd", "passphrase", "clientsecret",
)

# DROP: 정규화 키가 **정확히 일치**하면 제거한다.
_DROP_KEY_EXACT = frozenset({
    "auth", "bearer", "secret", "secrets", "token", "tokens",
    "sig", "xamzsignature", "xamzsecuritytoken", "xamzcontentsha256",
    "awsaccesskeyid", "awssecretaccesskey", "awssessiontoken",
})

# PROMPT: 정규화 키가 정확히 일치하면 값을 Probe_ID로 대체한다(요구사항 10.13).
_PROMPT_KEY_EXACT = frozenset({
    "prompt", "prompts", "rawprompt", "prompttext", "systemprompt",
    "messages", "rawmessages", "instructions", "input", "inputtext",
    "userinput", "usermessage", "systemtext",
})

# BODY: 정규화 키가 정확히 일치하면 값을 Sanitized_Schema로 대체한다(요구사항 10.14).
#   부분 일치를 쓰지 않으므로 `minOutputBound` 같은 계약 field는 보존된다.
_BODY_KEY_EXACT = frozenset({
    "body", "rawbody", "requestbody", "responsebody", "sentbody",
    "receivedbody", "rawrequestbody", "rawresponsebody", "rawrequest",
    "rawresponse", "requestjson", "responsejson", "payload", "rawpayload",
    "rawoutput", "outputtext", "responsetext", "rawtext", "completion",
})

# Sanitized_Schema가 값까지 보존하는 status 계열 키(설계: "필요한 status만").
_STATUS_KEY_EXACT = frozenset({
    "status", "statuscode", "httpstatus", "jobstatus", "state", "decision",
    "terminal", "stopreason",
})


# ─────────────────────────────────────────────────────────────────
# 예외
# ─────────────────────────────────────────────────────────────────
class StoreError(Exception):
    """capability 영속 계층의 기반 예외."""


class PathOutsideRootError(StoreError):
    """userData 루트 밖 경로에 대한 읽기·쓰기 시도(요구사항 10.15)."""


# ─────────────────────────────────────────────────────────────────
# userData 루트 해석과 경로 가드
# ─────────────────────────────────────────────────────────────────
def resolve_user_data_root(user_data_root=None, env=None) -> Path:
    """userData 루트 절대 경로를 결정한다(순수 경로 계산 — 디스크 접근 없음).

    Args:
        user_data_root: 명시 루트(테스트·CLI 주입). 지정 시 최우선.
        env: 환경변수 매핑. ``None``이면 ``os.environ``.

    Returns:
        모듈 docstring의 4단계 우선순위로 결정된 절대 경로.
    """
    env = env if env is not None else os.environ

    raw = str(user_data_root).strip() if user_data_root else ""
    if not raw:
        raw = (env.get("AE_USERDATA_PATH") or "").strip()
    if not raw:
        gen_root = (env.get("AE_GENERATED_ROOT") or "").strip()
        if gen_root:
            trimmed = gen_root.rstrip("/\\")
            parent, base = os.path.split(trimmed)
            # Electron: AE_GENERATED_ROOT = {userData}/generated → 부모가 userData.
            raw = parent if (base == _GENERATED_DIRNAME and parent) else trimmed
    if not raw:
        raw = _DEFAULT_USER_DATA_ROOT

    return Path(os.path.abspath(os.path.expanduser(raw)))


def is_within(path, root) -> bool:
    """``path``가 ``root`` 하위(또는 동일)인지 판정한다(symlink 정규화 포함).

    존재하지 않는 경로도 ``realpath``가 존재하는 prefix의 symlink를 해소하므로
    쓰기 전 검사에 사용할 수 있다.
    """
    try:
        root_real = os.path.realpath(os.path.abspath(os.path.expanduser(str(root))))
        path_real = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
    except (OSError, ValueError):
        return False
    # 경로 구분자를 붙여 prefix 매칭이 형제 디렉터리를 오인하지 않게 한다.
    root_norm = root_real.rstrip(os.sep) + os.sep
    return path_real == root_real or path_real.startswith(root_norm)


def safe_component(raw, *, fallback_prefix="id-") -> str:
    """임의 문자열을 루트를 벗어날 수 없는 단일 파일명 성분으로 정규화한다.

    basename만 취해 경로 구분자·상위참조를 제거하고, ``_SAFE_NAME_CHARS`` 밖의
    문자(``:`` 포함)를 ``_``로 치환한다. 결과가 비면(``..``·빈 문자열 등) 원본의
    sha256 축약으로, 지나치게 길면 앞부분 + sha256 축약으로 폴백한다. 동일 입력은
    항상 동일 성분을 만들어 저장·조회가 왕복한다.
    """
    text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    base = os.path.basename(text.replace("\\", "/"))
    filtered = "".join(c if c in _SAFE_NAME_CHARS else "_" for c in base).strip("._")
    if not filtered:
        return fallback_prefix + digest[:32]
    if len(filtered) > _MAX_NAME_LEN:
        return filtered[:_MAX_NAME_LEN - 17] + "-" + digest[:16]
    return filtered


# ─────────────────────────────────────────────────────────────────
# 정제 (저장 직전 강제) — 요구사항 10.7~10.10, 10.12~10.14
# ─────────────────────────────────────────────────────────────────
def _normalize_key(key) -> str:
    """키를 소문자 영숫자만 남긴 형태로 정규화한다(``X-Amz-Security-Token`` → ``xamzsecuritytoken``)."""
    text = key if isinstance(key, str) else str(key)
    return "".join(c for c in text.lower() if c.isalnum())


def classify_key(key) -> str:
    """키 분류를 반환한다: ``"DROP"`` / ``"PROMPT"`` / ``"BODY"`` / ``"KEEP"``.

    비밀정보 제거가 최우선이므로 DROP을 먼저 판정한다.
    """
    norm = _normalize_key(key)
    if not norm:
        return "KEEP"
    if norm in _DROP_KEY_EXACT:
        return "DROP"
    for needle in _DROP_KEY_SUBSTRINGS:
        if needle in norm:
            return "DROP"
    if norm in _PROMPT_KEY_EXACT:
        return "PROMPT"
    if norm in _BODY_KEY_EXACT:
        return "BODY"
    return "KEEP"


def _extract_probe_id(value) -> str:
    """dict에서 Probe_ID를 찾아 반환한다(없으면 빈 문자열)."""
    if not isinstance(value, dict):
        return ""
    for key, val in value.items():
        if _normalize_key(key) == "probeid" and isinstance(val, str) and val:
            return val
    return ""


def sanitized_schema(value, *, _depth=0) -> dict:
    """raw body를 field name·type·cardinality·필요 status만 남긴 schema로 축약한다.

    값 본문(문자열 내용·숫자 값)은 보존하지 않는다. 예외적으로 status 계열 키의
    scalar 값만 ``statusFields``에 남긴다(설계 "field name·type·cardinality와
    필요한 status만 보존"). 비밀 키는 schema 파생 단계에서도 제거된다.
    """
    if _depth > _MAX_DEPTH:
        return {"type": "truncated"}

    if isinstance(value, bool):
        return {"type": "boolean"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, (bytes, bytearray)):
        return {"type": "bytes", "length": len(value)}

    if isinstance(value, dict):
        fields = {}
        status_fields = {}
        for key, val in value.items():
            if classify_key(key) == "DROP":
                continue
            name = key if isinstance(key, str) else str(key)
            fields[name] = sanitized_schema(val, _depth=_depth + 1)
            if _normalize_key(key) in _STATUS_KEY_EXACT and isinstance(
                val, (str, int, float, bool)
            ):
                status_fields[name] = val
        out = {"type": "object", "fieldCount": len(fields), "fields": fields}
        if status_fields:
            out["statusFields"] = status_fields
        return out

    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        distinct = {}
        for item in items:
            schema = sanitized_schema(item, _depth=_depth + 1)
            distinct[json.dumps(schema, sort_keys=True, ensure_ascii=False)] = schema
        keys = sorted(distinct)[:_MAX_ITEM_SCHEMAS]
        return {
            "type": "array",
            "count": len(items),
            "items": [distinct[k] for k in keys],
        }

    # JSON 표현이 없는 객체는 타입 이름만 남긴다(값 노출 없음).
    return {"type": type(value).__name__}


def sanitize_for_persist(value, *, probe_id=None, _depth=0):
    """저장 직전 정제 — credential 제거 · raw prompt → Probe_ID · raw body → Schema.

    Args:
        value: 저장할 임의 구조(dict/list/scalar).
        probe_id: 상위 문맥에서 알려진 Probe_ID. 각 dict의 ``probeId``가 있으면
            그 하위 트리에서 우선한다.
        _depth: 내부 재귀 깊이. 상한 초과분은 Sanitized_Schema로 축약한다.

    Returns:
        비밀정보·raw prompt·raw body가 제거·대체된 새 구조(입력을 변경하지 않음).
    """
    if _depth > _MAX_DEPTH:
        return sanitized_schema(value)

    if isinstance(value, dict):
        current_probe = _extract_probe_id(value) or probe_id
        out = {}
        for key, val in value.items():
            kind = classify_key(key)
            if kind == "DROP":
                continue
            if kind == "PROMPT":
                out[key] = current_probe or PROBE_ID_UNKNOWN
                continue
            if kind == "BODY":
                out[key] = sanitized_schema(val)
                continue
            out[key] = sanitize_for_persist(
                val, probe_id=current_probe, _depth=_depth + 1
            )
        return out

    if isinstance(value, (list, tuple)):
        return [
            sanitize_for_persist(item, probe_id=probe_id, _depth=_depth + 1)
            for item in value
        ]

    if isinstance(value, (set, frozenset)):
        # 집합은 JSON 표현이 없으므로 정렬 가능한 경우 리스트로, 아니면 schema로.
        try:
            items = sorted(value)
        except TypeError:
            return sanitized_schema(value)
        return [
            sanitize_for_persist(item, probe_id=probe_id, _depth=_depth + 1)
            for item in items
        ]

    if isinstance(value, (bytes, bytearray)):
        return sanitized_schema(value)

    return value


# ─────────────────────────────────────────────────────────────────
# 로깅 (요구사항 10.10) — Probe_ID · Sanitized_Schema만
# ─────────────────────────────────────────────────────────────────
def mask_token_for_log(token) -> str:
    """토큰 마스킹을 기존 ``ai_engine.gateway_module.mask_token``에 위임한다.

    신규 마스킹 규칙을 만들지 않는다(지연 import로 무거운 transport 의존을 피한다).
    import가 불가한 환경에서는 원문을 노출하지 않도록 전량 마스킹만 반환한다.
    """
    try:
        from ai_engine.gateway_module import mask_token as _mask
    except Exception:  # pragma: no cover - import 실패 시 전량 마스킹 폴백
        return "****"
    return _mask(token)


def _log_scalar(value) -> str:
    """로그용 scalar 표현 — 200자 절단(기존 원인 문자열 규칙과 동일)."""
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, default=str
    )
    return text[:_LOG_VALUE_MAX]


def probe_log_fields(probe_id, *, body=None, schema=None, extra=None) -> dict:
    """로그에 허용되는 field만 담은 dict를 만든다(Probe_ID · Sanitized_Schema · 화이트리스트).

    Args:
        probe_id: raw prompt 자리를 대신하는 Probe_ID.
        body: raw request/response body. 주면 Sanitized_Schema로 축약해 담는다.
        schema: 이미 파생된 Sanitized_Schema. 주면 ``body``보다 우선한다.
        extra: 비민감 화이트리스트(``_LOG_ALLOWED_EXTRA``) field. 그 외 키는 버린다.
    """
    fields = {"probeId": probe_id if isinstance(probe_id, str) and probe_id else PROBE_ID_UNKNOWN}
    if schema is not None:
        # 이미 schema이므로 값 본문이 없다. credential 키만 방어적으로 한 번 더 제거.
        fields["sanitizedSchema"] = sanitize_for_persist(schema)
    elif body is not None:
        fields["sanitizedSchema"] = sanitized_schema(body)
    if isinstance(extra, dict):
        for key in _LOG_ALLOWED_EXTRA:
            if key in extra and extra[key] is not None:
                fields[key] = _log_scalar(sanitize_for_persist(extra[key]))
    return fields


def log_probe(probe_id, *, body=None, schema=None, extra=None,
              prefix="[Capability]", emit=True) -> str:
    """probe 로그 한 줄을 만들고(기본) 출력한다 — Probe_ID와 Sanitized_Schema만 남긴다.

    Returns:
        출력한 로그 한 줄(테스트·보고서에서 재사용 가능).
    """
    payload = probe_log_fields(probe_id, body=body, schema=schema, extra=extra)
    line = f"{prefix} " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    if emit:
        print(line)
    return line


# ─────────────────────────────────────────────────────────────────
# 원자적 쓰기
# ─────────────────────────────────────────────────────────────────
def atomic_write_bytes(path, data: bytes) -> Path:
    """임시 파일 + ``os.replace``로 원자적으로 바이트를 기록한다.

    같은 디렉터리에 임시 파일을 만들어 rename이 같은 파일시스템에서 원자적으로
    성립하게 하고, ``fsync`` 후 교체한다. 실패 시 임시 파일을 남기지 않는다.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(target))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    # 디렉터리 엔트리 내구성 확보(지원 플랫폼에서만 — 실패는 무해).
    try:
        dir_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, AttributeError):
        pass

    return target


# ─────────────────────────────────────────────────────────────────
# CapabilityStore
# ─────────────────────────────────────────────────────────────────
class CapabilityStore:
    """``userData/capability/`` 하위 경로 빌더 + 정제·원자적 쓰기 게이트.

    모든 경로는 userData 루트 기준으로 해석되며, 루트 밖 경로는
    ``PathOutsideRootError``로 거부한다(요구사항 10.15). ``write_json``은 기본으로
    저장 직전 정제를 강제하므로 credential·raw prompt·raw body가 디스크에 남지
    않는다(요구사항 10.7~10.9, 10.12~10.14).

    Args:
        user_data_root: 명시 userData 루트. ``None``이면 환경변수·기본값으로 해석.
        env: 환경변수 매핑(테스트 주입용). ``None``이면 ``os.environ``.
    """

    def __init__(self, user_data_root=None, env=None):
        self._root = resolve_user_data_root(user_data_root, env)

    # -- 루트 --------------------------------------------------------------
    @property
    def user_data_root(self) -> Path:
        """userData 루트 절대 경로."""
        return self._root

    @property
    def capability_root(self) -> Path:
        """``userData/capability`` 절대 경로."""
        return self._root / CAPABILITY_DIRNAME

    # -- 경로 가드 ---------------------------------------------------------
    def resolve(self, *parts) -> Path:
        """userData 루트 기준으로 경로를 해석하고 루트 밖이면 거부한다.

        Args:
            *parts: 루트 기준 상대 경로 성분 또는 단일 절대 경로.

        Returns:
            루트 하위임이 확인된 절대 경로.

        Raises:
            PathOutsideRootError: 정규화 결과가 userData 루트를 벗어나는 경우
                (``..``·절대경로 주입·symlink 이스케이프 포함).
        """
        joined = os.path.join(str(self._root), *[str(p) for p in parts]) if parts else str(self._root)
        candidate = os.path.abspath(os.path.expanduser(joined))
        if not is_within(candidate, self._root):
            raise PathOutsideRootError(
                f"userData 루트 밖 경로 거부: root={self._root} path={candidate}"
            )
        return Path(candidate)

    def _target(self, path) -> Path:
        """읽기·쓰기 대상 경로를 루트 기준으로 확정한다(CWD에 의존하지 않는다).

        절대 경로는 루트 하위인지 검사하고, 상대 경로는 **항상 루트 기준**으로
        결합한 뒤 검사한다. 어느 쪽이든 루트를 벗어나면 거부한다.
        """
        raw = os.path.expanduser(str(path))
        if os.path.isabs(raw):
            candidate = os.path.abspath(raw)
            if not is_within(candidate, self._root):
                raise PathOutsideRootError(
                    f"userData 루트 밖 경로 거부: root={self._root} path={candidate}"
                )
            return Path(candidate)
        return self.resolve(raw)

    # -- 경로 빌더 (design.md 영속 경로 표) --------------------------------
    def capability_map_path(self) -> Path:
        """``userData/capability/capability_map.json``."""
        return self.resolve(CAPABILITY_DIRNAME, CAPABILITY_MAP_FILENAME)

    def effort_settings_path(self) -> Path:
        """``userData/capability/effort_settings.json``."""
        return self.resolve(CAPABILITY_DIRNAME, EFFORT_SETTINGS_FILENAME)

    def baseline_path(self, revision) -> Path:
        """``userData/capability/baseline/{revision}.json``."""
        name = safe_component(revision, fallback_prefix="revision-")
        return self.resolve(CAPABILITY_DIRNAME, BASELINE_DIRNAME, name + ".json")

    def evidence_path(self, evidence_record_id) -> Path:
        """``userData/capability/evidence/{evidenceRecordId}.json``.

        ``evr1:sha256:…``의 ``:``는 파일명 안전 문자가 아니므로 ``_``로 치환된다.
        """
        name = safe_component(evidence_record_id, fallback_prefix="evidence-")
        return self.resolve(CAPABILITY_DIRNAME, EVIDENCE_DIRNAME, name + ".json")

    def catalog_path(self, catalog_fingerprint) -> Path:
        """``userData/capability/catalog/{catalogFingerprint}.json``."""
        name = safe_component(catalog_fingerprint, fallback_prefix="catalog-")
        return self.resolve(CAPABILITY_DIRNAME, CATALOG_DIRNAME, name + ".json")

    def run_path(self, run_id) -> Path:
        """``userData/capability/runs/{runId}.json``."""
        name = safe_component(run_id, fallback_prefix="run-")
        return self.resolve(CAPABILITY_DIRNAME, RUNS_DIRNAME, name + ".json")

    # -- 읽기 --------------------------------------------------------------
    def read_json(self, path, default=None):
        """루트 하위 JSON 파일을 읽는다. 파일이 없으면 ``default``.

        Raises:
            PathOutsideRootError: 루트 밖 경로.
            StoreError: JSON 손상 또는 I/O 오류(원인 ≤200자).
        """
        target = self._target(path)
        if not target.is_file():
            return default
        try:
            with open(target, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except ValueError as exc:
            raise StoreError(f"JSON 손상: {target} — {str(exc)[:200]}") from exc
        except OSError as exc:
            raise StoreError(f"읽기 실패: {target} — {str(exc)[:200]}") from exc

    def read_json_safe(self, path, default=None):
        """``read_json``의 비차단 버전 — 루트 밖·손상·I/O 오류 모두 ``default``."""
        try:
            return self.read_json(path, default=default)
        except StoreError:
            return default

    # -- 쓰기 --------------------------------------------------------------
    def write_json(self, path, data, *, sanitize=True) -> Path:
        """루트 하위에 JSON을 원자적으로 기록한다(기본: 저장 직전 정제 강제).

        Args:
            path: 루트 기준 상대 경로 또는 루트 하위 절대 경로.
            data: 저장할 구조.
            sanitize: ``True``면 ``sanitize_for_persist``를 적용해 credential 제거·
                raw prompt → Probe_ID·raw body → Sanitized_Schema 변환을 강제한다.
                이미 정제된 구조를 재기록할 때만 ``False``를 사용한다.

        Returns:
            기록된 절대 경로.

        Raises:
            PathOutsideRootError: 루트 밖 경로.
            StoreError: 직렬화 또는 I/O 오류(원인 ≤200자).
        """
        target = self._target(path)
        payload = sanitize_for_persist(data) if sanitize else data
        try:
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        except (TypeError, ValueError) as exc:
            raise StoreError(f"직렬화 실패: {target} — {str(exc)[:200]}") from exc
        try:
            return atomic_write_bytes(target, text.encode("utf-8"))
        except OSError as exc:
            raise StoreError(f"쓰기 실패: {target} — {str(exc)[:200]}") from exc

    # -- 항목별 편의 접근자 -----------------------------------------------
    def read_capability_map(self, default=None):
        """Capability_Map을 읽는다(없으면 ``default``)."""
        return self.read_json(self.capability_map_path(), default=default)

    def write_capability_map(self, map_obj, *, sanitize=True) -> Path:
        """Capability_Map을 원자적으로 기록한다(요구사항 10.8)."""
        return self.write_json(self.capability_map_path(), map_obj, sanitize=sanitize)

    def read_effort_settings(self, default=None):
        """Effort_Settings를 읽는다(없으면 ``default``)."""
        return self.read_json(self.effort_settings_path(), default=default)

    def write_effort_settings(self, settings, *, sanitize=True) -> Path:
        """Effort_Settings를 원자적으로 기록한다."""
        return self.write_json(self.effort_settings_path(), settings, sanitize=sanitize)

    def read_baseline(self, revision, default=None):
        """revision별 Baseline_Record 집합을 읽는다."""
        return self.read_json(self.baseline_path(revision), default=default)

    def write_baseline(self, revision, records, *, sanitize=True) -> Path:
        """revision별 Baseline_Record 집합을 원자적으로 기록한다."""
        return self.write_json(self.baseline_path(revision), records, sanitize=sanitize)

    def read_evidence(self, evidence_record_id, default=None):
        """Evidence_Record_ID로 정제 Verification_Record를 읽는다."""
        return self.read_json(self.evidence_path(evidence_record_id), default=default)

    def write_evidence(self, evidence_record_id, record, *, sanitize=True) -> Path:
        """정제 Verification_Record를 원자적으로 기록한다(요구사항 10.9, 10.12~10.14)."""
        return self.write_json(
            self.evidence_path(evidence_record_id), record, sanitize=sanitize
        )

    def read_catalog_snapshot(self, catalog_fingerprint, default=None):
        """Catalog_Fingerprint로 정제 catalog snapshot을 읽는다."""
        return self.read_json(self.catalog_path(catalog_fingerprint), default=default)

    def write_catalog_snapshot(self, catalog_fingerprint, snapshot, *, sanitize=True) -> Path:
        """정제 catalog snapshot을 원자적으로 기록한다."""
        return self.write_json(
            self.catalog_path(catalog_fingerprint), snapshot, sanitize=sanitize
        )

    def read_run_report(self, run_id, default=None):
        """Validation_Run 보고서를 읽는다."""
        return self.read_json(self.run_path(run_id), default=default)

    def write_run_report(self, run_id, report, *, sanitize=True) -> Path:
        """Validation_Run 보고서를 원자적으로 기록한다."""
        return self.write_json(self.run_path(run_id), report, sanitize=sanitize)

    # -- 조회 --------------------------------------------------------------
    def list_evidence_ids(self) -> list:
        """저장된 evidence 파일명(확장자 제외) 목록을 정렬해 반환한다."""
        return self._list_json_stems(EVIDENCE_DIRNAME)

    def list_run_ids(self) -> list:
        """저장된 run 보고서 파일명(확장자 제외) 목록을 정렬해 반환한다."""
        return self._list_json_stems(RUNS_DIRNAME)

    def _list_json_stems(self, subdir) -> list:
        directory = self.resolve(CAPABILITY_DIRNAME, subdir)
        if not directory.is_dir():
            return []
        try:
            return sorted(p.stem for p in directory.iterdir() if p.suffix == ".json")
        except OSError:
            return []
