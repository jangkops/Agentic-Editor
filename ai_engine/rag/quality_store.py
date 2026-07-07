"""응답 품질 저장소 — deferred(비동기 지연) 검증 결과 영속화.

라이브 실측 결과, 이 게이트웨이의 모델 호출은 110~285초+로 느려 인라인 검증이 부적합하다.
따라서 검증을 응답 경로에서 분리(deferred)하고, 결과를 세션별 JSON에 message_id 키로 저장한다.
프론트는 준비되면 이를 조회해 품질 배지를 표시한다.

저장 위치(우선순위): AE_QUALITY_ROOT env > ~/.cache/ae_answer_quality > /tmp/ae_answer_quality.
자격증명/토큰은 저장하지 않는다(품질 메타데이터만).

Requirements: 3.3, 3.4, 10.1 (비차단·무회귀), 8.x (관측)
"""
import json
import os
import tempfile
import time
from typing import Optional


def _writable(d: str) -> Optional[str]:
    try:
        os.makedirs(d, exist_ok=True)
        t = os.path.join(d, ".w")
        with open(t, "w") as f:
            f.write("ok")
        os.remove(t)
        return d
    except (OSError, PermissionError):
        return None


def quality_dir(env=None) -> str:
    """쓰기 가능한 품질 저장 디렉터리 해석(견고한 폴백)."""
    env = env if env is not None else os.environ
    for cand in (
        env.get("AE_QUALITY_ROOT"),
        os.path.join(os.path.expanduser("~"), ".cache", "ae_answer_quality"),
        os.path.join(tempfile.gettempdir(), "ae_answer_quality"),
    ):
        if not cand:
            continue
        d = _writable(cand)
        if d:
            return d
    # 최후: 현재 작업 디렉터리 하위
    d = _writable(os.path.join(os.getcwd(), ".ae_answer_quality"))
    return d or tempfile.gettempdir()


def _safe_session(session_id: str) -> str:
    """세션 id를 파일명으로 안전화(경로 이탈 방지)."""
    s = (session_id or "default").strip() or "default"
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in s)[:120]


def _path(session_id: str, env=None) -> str:
    return os.path.join(quality_dir(env), f"{_safe_session(session_id)}.json")


def save_quality(session_id: str, message_id: str, metadata: dict, env=None) -> bool:
    """세션 파일에 message_id 키로 품질 메타데이터를 저장(머지). 실패는 False(비차단)."""
    if not message_id:
        return False
    p = _path(session_id, env)
    try:
        data = {}
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f) or {}
            except (json.JSONDecodeError, OSError):
                data = {}
        entry = dict(metadata or {})
        entry.setdefault("updatedAt", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        data[str(message_id)] = entry
        # 세션당 최근 200개만 유지(무한 성장 방지) — updatedAt 기준 정렬 후 절단
        if len(data) > 200:
            items = sorted(data.items(), key=lambda kv: kv[1].get("updatedAt", ""), reverse=True)[:200]
            data = dict(items)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
        return True
    except Exception as e:
        print(f"[QualityStore] save 실패(비차단): {e}")
        return False


def load_quality(session_id: str, env=None) -> dict:
    """세션의 전체 품질 맵 {message_id: metadata} 반환. 없으면 {}."""
    p = _path(session_id, env)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_quality(session_id: str, message_id: str, env=None) -> Optional[dict]:
    """단일 메시지의 품질 메타데이터. 없으면 None."""
    return load_quality(session_id, env).get(str(message_id))
