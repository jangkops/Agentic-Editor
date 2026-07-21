"""Eval_Harness — reasoning-perf-reliability Phase 1 순수 함수 계층.

응답 경로(`ai_engine/server.py`)와 완전히 분리된 오프라인 CLI 모듈. ground-truth
Query_Set 을 실행해 지연·근거성·정확성·검색품질 지표를 재현 가능하게 산출하고
Baseline_Record 로 기록·비교하기 위한 순수 함수를 제공한다.

이 파일(태스크 1.1)은 **순수 함수 기반**만 구현한다. MockGateway / run_query /
run_eval / CLI(태스크 2.x)는 별도 태스크에서 추가한다.

불변 제약(요구사항 11):
- boto3 / anthropic / openai 를 import 하지 않는다(Gateway 전용 정책).
- Baseline_Record 는 자격증명(accessKeyId/secretAccessKey/sessionToken)이나 프롬프트
  전문을 어떤 깊이에도 담지 않는다(요구사항 3.3/3.4, 11.2).

검색품질 식별자 규약(요구사항 1.4): `recall_at_k`/`mrr` 의 relevant/retrieved 항목은
`"path:start-end"`(라인 정보 존재 시) 또는 파일 경로(라인 부재 시) 문자열이다. 이는
`ai_engine/rag/retrieval_pipeline._cid` 및 `ai_engine/rag/trace.hits_from_results` 규약과
일치한다.

Requirements: 1.3, 1.4, 1.5, 3.1, 3.2, 3.3, 3.4
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

# 응답 경로와 분리된 오프라인 도구이지만, 지표 계산은 프로덕션과 동일한 순수 함수를
# 재사용한다(재구현 금지 — 요구사항 1.3/1.4, 설계 원칙 2).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_ENGINE_ROOT = os.path.join(_REPO_ROOT, "ai_engine")
# 순수 지표 함수는 `from rag.eval_metrics import ...`(ai_engine 루트 기준)로 재사용하고,
# 오케스트레이션 계층은 `import ai_engine.agent_system...`(패키지 기준)을 쓰므로 두 경로를
# 모두 sys.path 에 둔다. 자격증명·부작용 없는 경로 삽입이며 순수 함수 동작에 영향 없다.
for _p in (_ENGINE_ROOT, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from rag.eval_metrics import (  # noqa: E402  (경로 주입 후 import)
    context_precision,
    groundedness,
    mrr,
    recall_at_k,
    unsupported_claim_rate,
)

__all__ = [
    "load_query_set",
    "chunks_to_refs",
    "aggregate_metrics",
    "build_baseline_record",
    "compare_baselines",
    "MockGateway",
    # 오케스트레이션(태스크 2.2)
    "run_query",
    "run_eval",
    "_select_compiled_graph",
    "resolve_gateway_mode",
    "main",
    # 재사용 지표(참조 편의)
    "recall_at_k",
    "mrr",
    "context_precision",
    "groundedness",
    "unsupported_claim_rate",
]

# Baseline_Record 및 per-query 산출물에 절대 등장해선 안 되는 자격증명 키(요구사항 3.3/11.2).
_CREDENTIAL_KEYS = frozenset({"accessKeyId", "secretAccessKey", "sessionToken"})

# 집계 대상 스칼라 지표 키(compare_baselines delta 성분 — 요구사항 3.2).
_AGGREGATE_METRIC_KEYS = (
    "latency_ms_mean",
    "latency_ms_median",
    "grounding_mean",
    "accuracy_mean",
    "recall_at_k_mean",
    "mrr_mean",
)


# ─────────────────────────────────────────────────────────────────────────
# Query_Set 로드/검증 (요구사항 1.5 입력)
# ─────────────────────────────────────────────────────────────────────────
def load_query_set(path: str) -> List[Dict[str, Any]]:
    """Query_Set JSON 을 로드·검증해 질의 리스트를 반환한다.

    스키마(설계 Data Models):
        {"version": 1, "queries": [{"id","prompt",
            "project_path"(optional),
            "expected_evidence_refs":[...], "expected_answer_refs":[...]}]}

    각 질의는 다음을 만족해야 한다:
        - id: 비어있지 않은 문자열(질의 식별자)
        - prompt: 문자열
        - expected_evidence_refs: 리스트(recall_at_k/mrr 의 relevant 식별자)
        - expected_answer_refs: 리스트(정확성 대조용 정답 참조)

    Raises:
        ValueError: 구조/필드가 규약과 어긋날 때.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Query_Set 최상위는 객체(dict)여야 합니다.")
    queries = data.get("queries")
    if not isinstance(queries, list):
        raise ValueError("Query_Set 'queries' 는 리스트여야 합니다.")

    seen_ids: set[str] = set()
    validated: List[Dict[str, Any]] = []
    for i, item in enumerate(queries):
        if not isinstance(item, dict):
            raise ValueError(f"queries[{i}] 는 객체여야 합니다.")
        qid = item.get("id")
        if not isinstance(qid, str) or not qid.strip():
            raise ValueError(f"queries[{i}].id 는 비어있지 않은 문자열이어야 합니다.")
        if qid in seen_ids:
            raise ValueError(f"중복된 질의 id: {qid!r}")
        seen_ids.add(qid)

        prompt = item.get("prompt")
        if not isinstance(prompt, str):
            raise ValueError(f"queries[{i}].prompt 는 문자열이어야 합니다.")

        ev_refs = item.get("expected_evidence_refs", [])
        if not isinstance(ev_refs, list):
            raise ValueError(
                f"queries[{i}].expected_evidence_refs 는 리스트여야 합니다."
            )
        ans_refs = item.get("expected_answer_refs", [])
        if not isinstance(ans_refs, list):
            raise ValueError(
                f"queries[{i}].expected_answer_refs 는 리스트여야 합니다."
            )

        validated.append(
            {
                "id": qid,
                "prompt": prompt,
                "project_path": item.get("project_path"),
                "expected_evidence_refs": list(ev_refs),
                "expected_answer_refs": list(ans_refs),
            }
        )
    return validated


# ─────────────────────────────────────────────────────────────────────────
# chunks → 식별자 refs (요구사항 1.4 — recall_at_k/mrr 대조용)
# ─────────────────────────────────────────────────────────────────────────
def _chunk_ref(chunk: Any) -> str | None:
    """단일 청크 → `"path:start-end"` 또는 파일경로 식별자(방어적).

    - file_path 가 없으면 None.
    - start_line/end_line 이 정수로 존재하면 `"path:start-end"`,
      아니면 파일경로만 반환(요구사항 1.4 규약: path:start-end 또는 파일경로).
    """
    fp = getattr(chunk, "file_path", None)
    if fp is None and isinstance(chunk, dict):
        fp = chunk.get("file_path")
    if not fp:
        return None
    s = getattr(chunk, "start_line", None)
    e = getattr(chunk, "end_line", None)
    if s is None and isinstance(chunk, dict):
        s = chunk.get("start_line")
    if e is None and isinstance(chunk, dict):
        e = chunk.get("end_line")
    if isinstance(s, int) and isinstance(e, int):
        return f"{fp}:{s}-{e}"
    return str(fp)


def chunks_to_refs(evidence: Dict[str, Any] | None) -> List[str]:
    """evidence(`{"chunks": [(chunk, score), ...]}`) → retrieved 식별자 리스트.

    `recall_at_k`/`mrr` 의 retrieved 시퀀스로 그대로 사용된다. 순위(관련도 내림차순)를
    보존하며, `(chunk, score)` 튜플 또는 청크 단독 형태를 모두 방어적으로 처리한다.
    식별자 규약은 `ai_engine/rag/trace.hits_from_results` 와 동일하다.
    """
    if not evidence:
        return []
    chunks = evidence.get("chunks") if isinstance(evidence, dict) else None
    if not chunks:
        return []
    refs: List[str] = []
    for item in chunks:
        chunk = item[0] if isinstance(item, (tuple, list)) and item else item
        ref = _chunk_ref(chunk)
        if ref is not None:
            refs.append(ref)
    return refs


# ─────────────────────────────────────────────────────────────────────────
# 집계 (요구사항 1.5)
# ─────────────────────────────────────────────────────────────────────────
def _mean(values: Sequence[float]) -> float:
    return float(statistics.fmean(values)) if values else 0.0


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


def aggregate_metrics(per_query: List[Dict[str, Any]]) -> Dict[str, Any]:
    """질의별 지표 리스트 → 집계 요약(순수 함수, 요구사항 1.5).

    - 지연: 성공 질의의 평균·중앙값(밀리초).
    - 근거성/정확성/recall@k/mrr: 성공 질의의 평균.
    - status != "ok" 인 질의는 지표 집계에서 제외하되 n_failed 로 계수한다(요구사항 1.6).
    - k: 성공 질의에 기록된 k(있으면), 집계는 첫 값 사용. 없으면 0.

    입력이 비어있으면 모든 지표 0.0, n_queries=0, n_failed=0.
    """
    n_queries = len(per_query)
    ok = [q for q in per_query if q.get("status") == "ok"]
    n_failed = sum(1 for q in per_query if q.get("status") != "ok")

    latencies = [float(q["latency_ms"]) for q in ok if q.get("latency_ms") is not None]
    groundings = [float(q["grounding"]) for q in ok if q.get("grounding") is not None]
    accuracies = [float(q["accuracy"]) for q in ok if q.get("accuracy") is not None]
    recalls = [float(q["recall_at_k"]) for q in ok if q.get("recall_at_k") is not None]
    mrrs = [float(q["mrr"]) for q in ok if q.get("mrr") is not None]

    k_val = 0
    for q in ok:
        if q.get("k") is not None:
            k_val = int(q["k"])
            break

    return {
        "latency_ms_mean": _mean(latencies),
        "latency_ms_median": _median(latencies),
        "grounding_mean": _mean(groundings),
        "accuracy_mean": _mean(accuracies),
        "recall_at_k_mean": _mean(recalls),
        "mrr_mean": _mean(mrrs),
        "k": k_val,
        "n_queries": n_queries,
        "n_failed": n_failed,
    }


# ─────────────────────────────────────────────────────────────────────────
# Baseline_Record 조립 (요구사항 3.1, 3.3, 3.4)
# ─────────────────────────────────────────────────────────────────────────
def _assert_no_credentials(obj: Any, _path: str = "$") -> None:
    """재귀적으로 자격증명 키 부재를 강제한다(요구사항 3.3/11.2 방어)."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key in _CREDENTIAL_KEYS:
                raise ValueError(
                    f"Baseline_Record 에 자격증명 키가 포함됨: {_path}.{key}"
                )
            _assert_no_credentials(val, f"{_path}.{key}")
    elif isinstance(obj, (list, tuple)):
        for i, val in enumerate(obj):
            _assert_no_credentials(val, f"{_path}[{i}]")


def _sanitize_per_query(per_query: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """per-query 항목에서 질의 id·지표만 남긴다(프롬프트 전문 미포함 — 요구사항 3.4).

    허용 키만 화이트리스트로 통과시켜, 실수로 prompt/대화 원문이 새어나가지 않게 한다.
    """
    allowed = (
        "id",
        "latency_ms",
        "grounding",
        "accuracy",
        "recall_at_k",
        "mrr",
        "k",
        "status",
        "error",
    )
    out: List[Dict[str, Any]] = []
    for q in per_query:
        rec = {key: q[key] for key in allowed if key in q}
        # error 메시지는 진단용으로 유지하되 문자열로 강제(객체·프롬프트 누출 방지).
        if "error" in rec and rec["error"] is not None:
            rec["error"] = str(rec["error"])
        out.append(rec)
    return out


def build_baseline_record(
    active_flags: Dict[str, Any],
    per_query: List[Dict[str, Any]],
    now: str,
    *,
    gateway_mode: str = "mock",
) -> Dict[str, Any]:
    """Baseline_Record dict 를 조립한다(요구사항 3.1/3.3/3.4).

    Args:
        active_flags: 활성 플래그 구성(자격증명 값은 포함 금지).
        per_query: 질의별 지표 리스트(id·지표만 유지, 프롬프트 전문 미포함).
        now: ISO8601 타임스탬프 문자열.
        gateway_mode: "mock" | "live".

    Returns:
        {"timestamp","gateway_mode","active_flags","aggregate","per_query"} 형태.
        자격증명 키가 어떤 깊이에도 없음을 조립 시점에 강제 검증한다.
    """
    sanitized_per_query = _sanitize_per_query(per_query)
    record = {
        "timestamp": now,
        "gateway_mode": gateway_mode,
        "active_flags": dict(active_flags or {}),
        "aggregate": aggregate_metrics(per_query),
        "per_query": sanitized_per_query,
    }
    # 보안 불변식(요구사항 3.3/11.2): 자격증명 키가 어떤 깊이에도 없어야 한다.
    _assert_no_credentials(record)
    return record


# ─────────────────────────────────────────────────────────────────────────
# Baseline 비교 (요구사항 3.2)
# ─────────────────────────────────────────────────────────────────────────
def _extract_aggregate(record: Dict[str, Any]) -> Dict[str, float]:
    """Baseline_Record(또는 aggregate dict 자체)에서 집계 스칼라 지표를 뽑는다."""
    agg = record.get("aggregate", record) if isinstance(record, dict) else {}
    return {
        key: float(agg.get(key, 0.0) or 0.0)
        for key in _AGGREGATE_METRIC_KEYS
    }


def compare_baselines(
    before: Dict[str, Any], after: Dict[str, Any]
) -> Dict[str, float]:
    """두 Baseline_Record 의 성분별 delta(`after[m] - before[m]`)를 산출한다(순수).

    - 공통 집계 지표(지연 평균/중앙값, 근거성/정확성/recall@k/mrr 평균)에 대해 delta.
    - `compare_baselines(x, x)` 는 모든 delta 가 0 이다(요구사항 3.2, Property 13).
    """
    b = _extract_aggregate(before)
    a = _extract_aggregate(after)
    return {key: a[key] - b[key] for key in _AGGREGATE_METRIC_KEYS}


# ═════════════════════════════════════════════════════════════════════════
# MockGateway — 결정론적 Gateway 스텁 (태스크 2.1, 요구사항 2.1/2.4/11.1)
# ═════════════════════════════════════════════════════════════════════════
#
# 실제 Gateway(`ai_engine/gateway_module.py`)의 소비 표면만 흉내내는 오프라인
# 결정론적 스텁이다. Eval_Harness 가 `mock` 모드로 실행될 때 실제 Gateway 호출·
# 네트워크·비용 없이 재현 가능한 지표를 산출하도록 한다.
#
# 불변 제약(요구사항 11.1): boto3 / anthropic / openai 를 절대 import 하지 않는다.
# 이 모듈은 표준 라이브러리(hashlib/json)만 사용해 완전히 self-contained 하다.
#
# 결정론 계약(요구사항 2.4): 응답 텍스트는 프롬프트(+ system_prompt 로 주입되는
# 근거 컨텍스트 + tool_config)의 SHA-256 해시로 시드된 canned 텍스트다. 동일한
# 입력(동일 Query_Set)은 항상 동일한 텍스트·이벤트 시퀀스를 낳으므로 지표가
# 결정론적으로 재현된다.
#
# 흉내내는 표면(실 Gateway 반환/이벤트 형태와 일치):
#   - converse(...)            -> {"decision":"ALLOW","output":{"message":{"content":[{"text":...}]}}, ...}
#   - converse_stream_live(...)-> 위와 동일한 dict(+ "stopReason")
#   - stream_sse_realtime(...) -> SSE 이벤트 dict 를 방출하는 async generator
#                                 (content_block_delta / message_stop / settlement)
#
# 태스크 2.2 연결 지점(seam): run_eval 의 mock 모드는 이 MockGateway 를
# GatewayModel 대체물(deps.gateway 등)로 주입해 동일 메서드 표면으로 사용한다.
# 여기서는 MockGateway 만 구현하며 run_query/run_eval/CLI 는 태스크 2.2 에서 얹는다.

# canned 응답을 구성하는 결정론적 문장 코퍼스. 해시 시드로 선택·조합된다.
_MOCK_SENTENCES = (
    "제공된 근거 범위 안에서 요청을 분석했습니다.",
    "핵심 흐름은 입력 검증, 처리, 결과 반환의 세 단계로 구성됩니다.",
    "관련 모듈은 서로 명확히 분리된 책임을 가집니다.",
    "이 동작은 기존 계약을 변경하지 않고 확장 지점에서만 이뤄집니다.",
    "근거 컨텍스트에 따르면 해당 경로는 유한하게 종료됩니다.",
    "예외는 격리되어 나머지 처리에 영향을 주지 않습니다.",
    "설정 플래그가 off 이면 기본 동작이 그대로 유지됩니다.",
    "지표는 재현 가능하도록 결정론적으로 산출됩니다.",
    "요약하면 요청된 기능은 명세된 제약을 준수합니다.",
    "추가 확인이 필요한 부분은 근거로 뒷받침되어야 합니다.",
)


def _stable_json(obj: Any) -> str:
    """tool_config 등 부가 입력을 결정론적으로 직렬화한다(키 정렬)."""
    try:
        return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(obj)


class MockGateway:
    """실제 Gateway 표면을 흉내내는 결정론적 오프라인 스텁.

    동일한 (messages, system_prompt, tool_config) 입력에 대해 항상 동일한 응답
    텍스트와 SSE 이벤트 시퀀스를 반환한다. 네트워크/자격증명/비용이 전혀 없다.
    """

    def __init__(
        self,
        *,
        min_sentences: int = 3,
        max_sentences: int = 6,
        words_per_chunk: int = 4,
        salt: str = "",
    ) -> None:
        """
        Args:
            min_sentences/max_sentences: 응답에 조합할 문장 수 범위(해시로 결정).
            words_per_chunk: stream_sse_realtime 델타당 단어 수(결정론적 청킹).
            salt: 서로 다른 mock 구성을 재현 가능하게 구분하기 위한 시드 소금.
        """
        if min_sentences < 1:
            min_sentences = 1
        if max_sentences < min_sentences:
            max_sentences = min_sentences
        self._min = min_sentences
        self._max = max_sentences
        self._words_per_chunk = max(1, int(words_per_chunk))
        self._salt = str(salt)

    # ── 입력 정규화 ──────────────────────────────────────────────────────
    @staticmethod
    def _extract_text(messages: Any, system_prompt: str = "") -> str:
        """Bedrock Converse 형식 messages(+ system_prompt)에서 텍스트를 추출한다.

        messages 는 str 또는 `[{"role","content":[{"text":...}]}]` 형태를 모두
        방어적으로 처리한다. system_prompt 로 주입되는 근거 컨텍스트도 시드에
        포함되어 "프롬프트 + 근거 컨텍스트" 해시 계약을 만족한다.
        """
        parts: List[str] = []
        if system_prompt:
            parts.append(str(system_prompt))
        if isinstance(messages, str):
            parts.append(messages)
        elif isinstance(messages, (list, tuple)):
            for msg in messages:
                if isinstance(msg, str):
                    parts.append(msg)
                    continue
                if not isinstance(msg, dict):
                    continue
                role = str(msg.get("role", ""))
                content = msg.get("content")
                if isinstance(content, str):
                    parts.append(f"{role}:{content}")
                elif isinstance(content, (list, tuple)):
                    for block in content:
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            parts.append(f"{role}:{block['text']}")
                        elif isinstance(block, str):
                            parts.append(f"{role}:{block}")
        return "\n".join(parts)

    # ── 결정론 시드/텍스트 생성 ─────────────────────────────────────────
    def _digest(
        self, messages: Any, system_prompt: str = "", tool_config: Any = None
    ) -> bytes:
        """입력의 SHA-256 다이제스트(결정론 시드)를 계산한다."""
        h = hashlib.sha256()
        h.update(self._salt.encode("utf-8"))
        h.update(b"\x00")
        h.update(self._extract_text(messages, system_prompt).encode("utf-8"))
        if tool_config:
            h.update(b"\x00tool\x00")
            h.update(_stable_json(tool_config).encode("utf-8"))
        return h.digest()

    def _canned_text(
        self, messages: Any, system_prompt: str = "", tool_config: Any = None
    ) -> str:
        """해시 시드로 canned 응답 텍스트를 결정론적으로 생성한다."""
        digest = self._digest(messages, system_prompt, tool_config)
        span = self._max - self._min + 1
        n = self._min + (digest[0] % span)
        n_corpus = len(_MOCK_SENTENCES)
        chosen = [
            _MOCK_SENTENCES[digest[(i + 1) % len(digest)] % n_corpus]
            for i in range(n)
        ]
        return " ".join(chosen)

    def _chunk_text(self, text: str) -> List[str]:
        """스트리밍 델타용으로 텍스트를 결정론적으로 단어 단위 청킹한다."""
        words = text.split(" ")
        step = self._words_per_chunk
        chunks: List[str] = []
        for i in range(0, len(words), step):
            piece = " ".join(words[i : i + step])
            # 마지막을 제외한 청크에는 공백을 붙여 재조립 시 원문이 보존되게 한다.
            if i + step < len(words):
                piece += " "
            if piece:
                chunks.append(piece)
        return chunks

    @staticmethod
    def _build_result(text: str, *, stop_reason: str | None = None) -> Dict[str, Any]:
        """실 Gateway 의 비스트리밍 성공 반환 형태를 재현한다."""
        result: Dict[str, Any] = {
            "decision": "ALLOW",
            "output": {"message": {"content": [{"text": text}]}},
            "remaining_quota": {},
            "estimated_cost_krw": 0,
        }
        if stop_reason is not None:
            result["stopReason"] = stop_reason
        return result

    # ── Gateway 표면(비동기) ────────────────────────────────────────────
    async def converse(
        self, model_id, messages, system_prompt="", tool_config=None
    ) -> Dict[str, Any]:
        """비스트리밍 Converse 를 결정론적으로 흉내낸다(요구사항 2.1)."""
        text = self._canned_text(messages, system_prompt, tool_config)
        return self._build_result(text)

    async def converse_stream_live(
        self, model_id, messages, system_prompt="", tool_config=None
    ) -> Dict[str, Any]:
        """스트리밍 단발(집계 반환) 경로를 결정론적으로 흉내낸다.

        실 Gateway 의 `converse_stream_live` 와 동일하게, 스트림을 모두 소비한 뒤의
        집계 dict 를 반환한다(+ stopReason).
        """
        text = self._canned_text(messages, system_prompt, tool_config)
        return self._build_result(text, stop_reason="end_turn")

    async def stream_sse_realtime(
        self, model_id, messages, system_prompt="", tool_config=None
    ) -> AsyncIterator[Dict[str, Any]]:
        """실시간 SSE 이터레이터를 결정론적으로 흉내낸다.

        실 Gateway 가 방출하는 이벤트 형태(`content_block_delta` 의 `delta.text`,
        이어서 `message_stop`, `settlement`)와 일치하는 dict 를 순서대로 yield 한다.
        델타를 재조립하면 `converse` 의 응답 텍스트와 정확히 동일하다.
        """
        text = self._canned_text(messages, system_prompt, tool_config)
        for chunk in self._chunk_text(text):
            yield {"type": "content_block_delta", "delta": {"text": chunk}}
        yield {"type": "message_stop", "stopReason": "end_turn"}
        yield {"type": "settlement", "remaining_quota_krw": 0, "estimated_cost_krw": 0}


# ═════════════════════════════════════════════════════════════════════════
# 오케스트레이션 계층 — run_query / run_eval / Gateway_Mode 선택 + CLI
# (태스크 2.2, 요구사항 1.1, 1.2, 1.6, 2.2, 2.3, 11.1)
# ═════════════════════════════════════════════════════════════════════════
#
# 불변 제약(요구사항 11.1): 이 계층은 boto3 / anthropic / openai 를 직접 import 하지
# 않는다. live 모드의 실제 LLM 호출은 오직 Bedrock Gateway(`GatewayClient` →
# `GatewayChatModel`) 경유로만 이뤄지며, 해당 import 는 live 분기에서만 지연 로드한다.
# 자격증명은 상태·config·Baseline_Record 어디에도 저장하지 않는다(요구사항 11.2).

# Baseline_Record 에 기록할 플래그(자격증명·프롬프트 전문 미포함 — 요구사항 3.3/3.4).
_TRUTHY_OFF = ("0", "false", "off", "no", "")


def _env_bool(name: str, default: bool = False) -> bool:
    """환경변수 불리언 판독(미설정 시 default)."""
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in _TRUTHY_OFF


def _collect_active_flags() -> Dict[str, Any]:
    """현재 활성 플래그 구성 스냅샷(Baseline_Record active_flags — 요구사항 3.1).

    설정 값(불리언/수치)만 담으며 자격증명은 어떤 것도 포함하지 않는다(요구사항 11.2).
    """
    try:
        threshold = float(os.environ.get("AE_VERIFY_THRESHOLD", "0.7"))
    except (TypeError, ValueError):
        threshold = 0.7
    try:
        max_refine = int(os.environ.get("AE_MAX_REFINE", "1"))
    except (TypeError, ValueError):
        max_refine = 1
    return {
        "AE_ENABLE_ADAPTIVE_DEPTH": _env_bool("AE_ENABLE_ADAPTIVE_DEPTH", False),
        "AE_ENABLE_GROUNDING_GATE": _env_bool("AE_ENABLE_GROUNDING_GATE", False),
        "AE_LANGGRAPH_PARALLEL": _env_bool("AE_LANGGRAPH_PARALLEL", True),
        "AE_MAX_REFINE": max_refine,
        "AE_VERIFY_THRESHOLD": threshold,
    }


def resolve_gateway_mode(cli_value: Optional[str] = None) -> str:
    """Gateway_Mode 선택(요구사항 2.3).

    우선순위: CLI 인자 > 환경변수 `AE_EVAL_GATEWAY_MODE` > 기본값 `mock`.
    `{mock, live}` 이외의 값은 안전하게 `mock` 으로 폴백한다.
    """
    mode = (cli_value or os.environ.get("AE_EVAL_GATEWAY_MODE") or "mock").strip().lower()
    return mode if mode in ("mock", "live") else "mock"


def _extract_grounding(answer_quality: Optional[Dict[str, Any]]) -> Optional[float]:
    """`state["answer_quality"]` → 근거성 스칼라(요구사항 1.3).

    - faithfulness.score 가 있고 not degraded 이면 그 값(LLM 채점 우선).
    - 아니면 grounding.score(로컬 임베딩 코사인)가 있으면 그 값.
    - 둘 다 부재/degraded 이면 None(집계에서 중립 제외 — aggregate_metrics 계약).
    """
    aq = answer_quality or {}
    faith = aq.get("faithfulness") or {}
    score = faith.get("score")
    if score is not None and not faith.get("degraded"):
        try:
            return float(score)
        except (TypeError, ValueError):
            pass
    grounding = aq.get("grounding") or {}
    gscore = grounding.get("score")
    if gscore is not None:
        try:
            return float(gscore)
        except (TypeError, ValueError):
            pass
    return None


def _compute_accuracy(
    final_text: str,
    expected_answer_refs: Sequence[str],
    citations: Optional[Dict[str, Any]],
) -> Optional[float]:
    """정확성 지표(결정론적, 요구사항 1.1).

    - expected_answer_refs 가 주어지면: 정답 참조 중 최종 응답 본문에 등장한 비율.
    - 없으면: citation 메타데이터 기반 groundedness(지원 주장 / 전체 주장)로 폴백.
      (eval_metrics.groundedness 재사용 — 재구현 금지.)
    - 둘 다 근거가 없으면 None(집계에서 제외).
    """
    refs = [r for r in (expected_answer_refs or []) if isinstance(r, str) and r]
    text = final_text or ""
    if refs:
        found = sum(1 for r in refs if r in text)
        return found / len(refs)
    cits = citations or {}
    verified = cits.get("verified") or []
    unverified = cits.get("unverified") or []
    n_supported = len(verified)
    n_claims = n_supported + len(unverified)
    if n_claims <= 0:
        return None
    return groundedness(n_supported, n_claims)


def _build_initial_state(query: Dict[str, Any]) -> Dict[str, Any]:
    """Query → GraphState 초기값(server.py graph-stream 배선과 정합, 자격증명 미포함).

    aws_profile/bedrock_user 는 빈 문자열로 둔다(오프라인 평가는 주입된 gateway 경유).
    """
    from langchain_core.messages import HumanMessage  # 지연 import(패키지 의존 최소화)

    prompt = query.get("prompt", "")
    return {
        "prompt": prompt,
        "session_id": str(query.get("id", "eval")),
        "project_path": query.get("project_path") or "",
        "open_file": "",
        "open_file_content": "",
        "aws_profile": "",
        "bedrock_user": "",
        "template_id": "",
        "system_prompt": "",
        "messages": [HumanMessage(content=prompt)],
        "visited_routes": [],
    }


async def run_query(
    compiled_graph: Any,
    query: Dict[str, Any],
    config: Dict[str, Any],
    *,
    k: int = 5,
) -> Dict[str, Any]:
    """단일 질의를 컴파일된 그래프로 실행해 지표를 산출한다(요구사항 1.1/1.2/1.6).

    - 지연: `time.perf_counter()` 로 제출 직전~최종 응답(final_text) 완료까지 밀리초 측정.
    - 근거성: `state["answer_quality"]`(faithfulness.score / grounding.score) 집계.
    - 검색품질: `chunks_to_refs(state["evidence"])` 와 `expected_evidence_refs` 로
      recall@k/mrr 산출(eval_metrics 재사용).
    - 정확성: expected_answer_refs 등장 비율 또는 citation groundedness 폴백.

    실패 격리(요구사항 1.6): 어떤 예외든 잡아 `{id, status:"failed", error}` 로 기록하고
    절대 전파하지 않는다(나머지 질의 실행 계속 보장).
    """
    qid = query.get("id")
    try:
        initial_state = _build_initial_state(query)
        # 지연 측정: 개별 ainvoke await 하나만 계측(스트림 루프 아님 — 요구사항 11.3).
        t0 = time.perf_counter()
        state = await compiled_graph.ainvoke(initial_state, config)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        state = state or {}
        grounding = _extract_grounding(state.get("answer_quality"))
        retrieved = chunks_to_refs(state.get("evidence"))
        relevant = query.get("expected_evidence_refs") or []
        recall = recall_at_k(relevant, retrieved, k)
        mrr_val = mrr(relevant, retrieved)
        accuracy = _compute_accuracy(
            state.get("final_text", ""),
            query.get("expected_answer_refs") or [],
            state.get("citations"),
        )
        return {
            "id": qid,
            "latency_ms": latency_ms,
            "grounding": grounding,
            "accuracy": accuracy,
            "recall_at_k": recall,
            "mrr": mrr_val,
            "k": k,
            "status": "ok",
        }
    except Exception as e:  # noqa: BLE001 — 실패 격리(요구사항 1.6): 전파 금지.
        return {"id": qid, "status": "failed", "error": str(e)}


def _build_live_gateway(aws_profile: str, bedrock_user: str) -> Any:
    """live 모드 Bedrock Gateway 클라이언트(지연 import — Gateway 전용, 요구사항 11.1)."""
    from ai_engine.gateway_module import GatewayClient  # 지연 import(mock 모드는 미로드)

    return GatewayClient(
        aws_profile=aws_profile or "default",
        bedrock_user=bedrock_user or "",
    )


def _build_deps(gateway_mode: str, aws_profile: str, bedrock_user: str) -> Any:
    """Gateway_Mode 에 맞는 GraphDeps 조립.

    - mock: 결정론적 MockGateway 주입(네트워크·비용·자격증명 없음, 요구사항 2.1/2.4).
    - live: 실제 Bedrock Gateway 주입(모든 LLM 호출 Gateway 경유, 요구사항 2.2/11.1).
    자격증명은 deps 에 저장하지 않는다(GatewayClient 가 런타임 주입/assume-role).
    """
    from ai_engine.agent_system.deps import GraphDeps

    if gateway_mode == "live":
        gw = _build_live_gateway(aws_profile, bedrock_user)
    else:
        gw = MockGateway()
    return GraphDeps(gateway=gw)


def _build_compiled_graph(deps: Any) -> Any:
    """Full_Graph 컴파일(플래그 off 기준 baseline — server.py 선택 로직과 정합).

    `AE_LANGGRAPH_PARALLEL`(기본 on)이면 병렬 fan-out, off 면 순차 멀티홉.
    적응형 깊이 라우팅 플래그는 이 오프라인 baseline 경로에 개입하지 않는다.
    """
    from ai_engine.agent_system.supervisor import (
        build_parallel_top_graph,
        build_top_graph,
    )

    parallel_on = _env_bool("AE_LANGGRAPH_PARALLEL", True)
    if parallel_on:
        return build_parallel_top_graph(deps)
    return build_top_graph(deps)


async def _select_compiled_graph(
    deps: Any,
    query: Dict[str, Any],
    full_graph: Any,
    fast_cache: Dict[str, Any],
) -> Any:
    """질의별 적응형 그래프 선택(server.py graph-stream 분기 미러 — 요구사항 12.2/5.x/6.x).

    프로덕션 응답 경로(`ai_engine/server.py`)가 `AE_ENABLE_ADAPTIVE_DEPTH` on 일 때 하는 것과
    동일하게 질의마다 라우팅한다:
      - 플래그 off(기본) → 항상 `full_graph`(플래그 off 무회귀: 기존 동작과 바이트 동등).
      - 플래그 on → `classify_complexity(prompt, deps, use_llm=depth_router_llm_enabled())` 로
        분류. 'simple' 이면 `build_fast_path_graph(deps, pick_fast_domain(prompt, deps))`(단일
        도메인, planner/aggregate/evaluator 없음), 그 외는 `full_graph`.
      - 분류/조립 중 어떤 예외든 → `full_graph` 폴백(fail-safe, 요구사항 6.3).

    `classify_complexity` 는 개별 ainvoke 하나만 wait_for 로 감싼 단일 코루틴이므로 직접 await
    한다(스트림 async-for 아님 — 요구사항 11.3). Fast_Path 컴파일 그래프는 도메인별로 캐시해
    질의마다 재컴파일하지 않는다(`fast_cache` 는 domain → CompiledStateGraph).
    """
    # 지연 import — 플래그 off 경로에서는 depth_router 를 건드리지 않아 기존 동작과 바이트 동등.
    from ai_engine.agent_system.depth_router import (
        adaptive_depth_enabled,
        build_fast_path_graph,
        classify_complexity,
        depth_router_llm_enabled,
        pick_fast_domain,
    )

    if not adaptive_depth_enabled():
        return full_graph

    prompt = query.get("prompt", "")
    try:
        depth = await classify_complexity(
            prompt, deps, use_llm=depth_router_llm_enabled()
        )
        if depth == "simple":
            domain = pick_fast_domain(prompt, deps)
            cached = fast_cache.get(domain)
            if cached is None:
                cached = build_fast_path_graph(deps, domain)
                fast_cache[domain] = cached
            return cached
        return full_graph
    except Exception:  # noqa: BLE001 — 분류/조립 실패 → Full_Graph 폴백(요구사항 6.3 fail-safe).
        return full_graph


async def run_eval(
    query_set: List[Dict[str, Any]],
    gateway_mode: str = "mock",
    k: int = 5,
    *,
    aws_profile: str = "",
    bedrock_user: str = "",
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """전체 Query_Set 실행 → Baseline_Record(요구사항 1.1/1.5/2.2/3.1).

    mock 모드는 재현성을 위해 `compiled_graph.ainvoke`(비스트리밍)로 실행하며(요구사항 2.4,
    Property 10), live 모드도 동일 인터페이스로 실측한다(스트리밍 오버헤드 배제).

    적응형 깊이 라우팅(요구사항 12.2): `AE_ENABLE_ADAPTIVE_DEPTH` on 이면 프로덕션(server.py)과
    동일하게 **질의마다** Fast_Path(단순) vs Full_Graph(복잡)를 선택한다. Full_Graph 는 한 번만
    컴파일해 재사용하고, Fast_Path 는 도메인별로 캐시해 재컴파일을 피한다. 플래그 off(기본)이면
    모든 질의가 Full_Graph 를 쓰므로 기존 동작과 바이트 동등하다.

    개별 질의 실패는 run_query 내부에서 격리되어 `status="failed"` 로 기록되고 나머지
    질의는 계속 실행된다(요구사항 1.6).
    """
    gateway_mode = resolve_gateway_mode(gateway_mode)
    deps = _build_deps(gateway_mode, aws_profile, bedrock_user)
    # Full_Graph 는 한 번만 컴파일(플래그 off 경로 및 복잡 질의용). Fast_Path 는 도메인별 캐시.
    full_graph = _build_compiled_graph(deps)
    fast_cache: Dict[str, Any] = {}

    recursion_limit = int(os.environ.get("AE_GRAPH_RECURSION", "50"))
    per_query: List[Dict[str, Any]] = []
    for query in query_set:
        qid = str(query.get("id", "q"))
        config = {
            "configurable": {"thread_id": f"eval-{qid}:{uuid.uuid4().hex[:8]}"},
            "recursion_limit": recursion_limit,
        }
        # 질의별 적응형 그래프 선택(server.py graph-stream 분기 미러). 플래그 off → full_graph.
        compiled = await _select_compiled_graph(deps, query, full_graph, fast_cache)
        per_query.append(await run_query(compiled, query, config, k=k))

    timestamp = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return build_baseline_record(
        _collect_active_flags(),
        per_query,
        timestamp,
        gateway_mode=gateway_mode,
    )


# ─────────────────────────────────────────────────────────────────────────
# CLI (요구사항 2.3, 3.1)
# ─────────────────────────────────────────────────────────────────────────
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="eval_reasoning_perf",
        description=(
            "Eval_Harness — Query_Set 을 실행해 지연·근거성·정확성·검색품질 "
            "Baseline_Record 를 산출한다(reasoning-perf-reliability Phase 1)."
        ),
    )
    # --query-set(평가 실행)와 --compare(두 Baseline_Record 비교)는 상호 배타적이며
    # 정확히 하나가 필요하다(요구사항 3.1 실행 vs 3.2 비교).
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--query-set",
        default=None,
        help="Query_Set JSON 경로(id/prompt/expected_evidence_refs/expected_answer_refs).",
    )
    mode_group.add_argument(
        "--compare",
        nargs=2,
        metavar=("BEFORE", "AFTER"),
        default=None,
        help="두 Baseline_Record JSON 경로를 로드해 성분별 delta(compare_baselines)를 출력.",
    )
    parser.add_argument(
        "--gateway-mode",
        choices=["mock", "live"],
        default=None,
        help="LLM 백엔드 선택. 미지정 시 AE_EVAL_GATEWAY_MODE 또는 기본값 mock.",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=5,
        help="recall@k / mrr 산출에 사용할 상위 k(기본 5).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Baseline_Record JSON 출력 경로. 미지정 시 stdout 으로 출력.",
    )
    return parser.parse_args(argv)


def _run_compare(before_path: str, after_path: str) -> int:
    """두 Baseline_Record 를 로드해 성분별 delta 를 출력한다(요구사항 3.2)."""
    with open(before_path, "r", encoding="utf-8") as f:
        before = json.load(f)
    with open(after_path, "r", encoding="utf-8") as f:
        after = json.load(f)
    delta = compare_baselines(before, after)
    print(json.dumps(delta, ensure_ascii=False, indent=2))
    return 0


def _run_eval_cli(args: argparse.Namespace) -> int:
    """Query_Set 실행 후 Baseline_Record 를 기록/출력한다(요구사항 3.1)."""
    gateway_mode = resolve_gateway_mode(args.gateway_mode)
    query_set = load_query_set(args.query_set)
    record = asyncio.run(run_eval(query_set, gateway_mode=gateway_mode, k=args.k))

    payload = json.dumps(record, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"[eval] Baseline_Record 기록: {args.out} "
              f"(queries={record['aggregate']['n_queries']}, "
              f"failed={record['aggregate']['n_failed']}, mode={gateway_mode})")
    else:
        print(payload)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 엔트리 — 평가 실행(--query-set) 또는 baseline 비교(--compare).

    반환값은 프로세스 종료 코드(0=성공)로, `sys.exit(main())` 규약과 정합한다.
    """
    args = _parse_args(argv)
    if args.compare:
        before_path, after_path = args.compare
        return _run_compare(before_path, after_path)
    return _run_eval_cli(args)


if __name__ == "__main__":
    sys.exit(main())
