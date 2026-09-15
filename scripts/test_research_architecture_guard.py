"""정적 아키텍처 가드 — deep-research-engine (``ai_engine/research/`` 패키지).

작업 18.2 (spec: deep-research-engine). AST import 스캔으로 세 가지 아키텍처 불변식을
강제한다:

  1. **Gateway-only** (요구사항 10.1 / 10.5): ``research/`` 하위 어떤 모듈도
     ``boto3`` / ``anthropic`` / ``openai`` (및 ``botocore``) 등 **직접 모델 SDK** 를
     import 하지 않는다. 모든 LLM·추론 호출은 Bedrock Gateway
     (``agent_system.chat_model_adapter``)를 지연 import 로 경유한다.
  2. **단일 egress** (요구사항 10.4): 외부 HTTP 클라이언트(``httpx``)는 ``research/``
     에서 오직 ``backend.py`` 만 import 한다(단일 네트워크 egress 지점). ``urllib``
     (stdlib)은 허용하되, 아웃바운드 HTTP 클라이언트 소유는 backend.py 에 한정한다.
  3. **의존성 제약** (요구사항 15.3): 외부 벡터DB(faiss/chromadb/pinecone/weaviate/
     qdrant/milvus 등) 미도입, 그리고 ``research/`` 가 **새로 들이는** 서드파티 의존성은
     ``httpx`` / ``lxml`` 뿐이다(기존에 재사용하는 orchestration 스택 langchain/langgraph
     제외 — 재구현 금지 원칙에 따른 재사용).

스캔 방식(``test_native_layout_render_units.py`` 의 forbidden-import 패턴을 계승·강화):
파일 텍스트 regex 는 문자열/주석 오탐을 낸다(예: 모델 ID 문자열
``"us.anthropic.claude-…"`` 나 "boto3 를 직접 호출하지 않는다" 같은 주석). 이를 피하기
위해 ``ast`` 로 파싱해 ``Import`` / ``ImportFrom`` 노드의 **모듈명만** 검사한다.
``ast.walk`` 는 함수 내부·``try`` 블록의 지연(lazy) import 까지 트리 전체를 순회하므로
nested/lazy import 도 포착한다(딥리서치는 게이트웨이 어댑터·langchain·langgraph·lxml 을
함수 내부에서 지연 import 한다).

전부 헤르메틱 — 순수 Python(``ast``), 네트워크 0, research 모듈 import 불필요(소스
텍스트만 파싱하므로 langchain/langgraph 미설치 환경에서도 동작).

Run (hermetic):
  ai_engine/.venv/bin/python -m pytest scripts/test_research_architecture_guard.py -q

Requirements: 10.1, 10.4, 10.5, 15.3
"""
from __future__ import annotations

import ast
import functools
import sys
from collections import namedtuple
from pathlib import Path

# ── 스캔 대상 경로 ──────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
_RESEARCH_DIR = _REPO_ROOT / "ai_engine" / "research"

# ── 금지/허용 규칙 ──────────────────────────────────────────────────────────
# 직접 모델 SDK — Gateway-only 위반(요구사항 10.1/10.5). botocore 는 boto3 의 코어이자
# 직접 AWS SDK 경로이므로 함께 금지한다(SigV4 서명은 gateway_module 만 수행, research 불가).
_FORBIDDEN_SDK_ROOTS = frozenset({"boto3", "botocore", "anthropic", "openai"})

# 외부 HTTP egress 클라이언트 — research/ 에서 backend.py 만 소유(단일 egress, 요구사항 10.4).
_HTTP_EGRESS_CLIENT = "httpx"
_EGRESS_OWNER = "backend.py"

# 외부 벡터DB — 도입 금지(요구사항 15.3). import 되는 실제 패키지 루트명 기준.
_FORBIDDEN_VECTOR_DB_ROOTS = frozenset({
    "faiss", "chromadb", "pinecone", "weaviate",
    "qdrant", "qdrant_client", "milvus", "pymilvus", "lancedb",
})

# research/ 가 새로 들이는 것이 허용되는 유일한 서드파티 의존성(요구사항 15.3).
_ALLOWED_NEW_DEPS = frozenset({"httpx", "lxml"})
# 기존에 이미 쓰이는 orchestration/LLM 스택(재사용 — 신규 의존성 아님). research 는 이들을
# 지연 import 로 재사용한다(재구현 금지). "신규 의존성" 판정에서 제외한다.
_PREEXISTING_REUSED_DEPS = frozenset({"langchain", "langchain_core", "langgraph"})

# 내부 패키지 루트(재사용 자산). 서드파티가 아니다.
_INTERNAL_ROOT = "ai_engine"

# 러닝 인터프리터의 표준 라이브러리 최상위 모듈명 집합(3.10+). stdlib 분류에 사용.
_STDLIB_ROOTS = frozenset(sys.stdlib_module_names)


# ── AST 스캔 ────────────────────────────────────────────────────────────────
ImportRecord = namedtuple("ImportRecord", ["file", "root", "module", "lineno", "relative"])


def _research_py_files():
    """research/ 하위 ``.py`` 소스 파일 목록(정렬, ``__pycache__`` 제외)."""
    return sorted(_RESEARCH_DIR.glob("*.py"))


def _scan_file(path: Path):
    """단일 파일을 ``ast`` 로 파싱해 ImportRecord 목록을 만든다(nested/lazy 포함).

    ``ast.walk`` 로 트리 전체를 순회하므로 최상위 import 뿐 아니라 함수/``try`` 내부의
    지연 import 도 모두 수집한다. 상대 import(``from . import x``, level>0)는
    ``relative=True`` 로 표기하고 root 를 비워 외부 의존성 분류에서 제외한다.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    records = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                full = alias.name or ""
                root = full.split(".", 1)[0]
                records.append(ImportRecord(path.name, root, full, node.lineno, False))
        elif isinstance(node, ast.ImportFrom):
            relative = bool(node.level and node.level > 0)
            full = node.module or ""
            root = "" if relative else full.split(".", 1)[0]
            records.append(ImportRecord(path.name, root, full, node.lineno, relative))
    return records


@functools.lru_cache(maxsize=1)
def _all_records():
    """research/ 전체 import 레코드(파일별 누적). ``lru_cache`` 로 1회만 스캔."""
    recs = []
    for p in _research_py_files():
        recs.extend(_scan_file(p))
    return tuple(recs)


def _absolute_records():
    """절대 import 레코드만(상대 import 제외, root 비어있지 않음)."""
    return [r for r in _all_records() if not r.relative and r.root]


# ===========================================================================
# 0) 스캔 대상 sanity — 패키지 존재 + 핵심 파일 파싱 확인
# ===========================================================================
def test_research_package_present_and_core_files_scanned():
    assert _RESEARCH_DIR.is_dir(), f"research 패키지를 찾을 수 없음: {_RESEARCH_DIR}"
    files = {p.name for p in _research_py_files()}
    # 축 핵심 파일이 스캔 대상에 포함되어야 한다(회귀 방지 — egress 모듈/파이프라인 진입점).
    for required in ("__init__.py", "backend.py", "deep_research.py"):
        assert required in files, f"필수 research 모듈 누락: {required}"
    # 최소 파일 수 — 스캔이 비어있지 않음을 보장(현재 12개).
    assert len(files) >= 10, f"스캔된 research 파일이 예상보다 적음: {sorted(files)}"
    # 모든 파일이 구문 오류 없이 파싱되고 import 레코드가 수집됨.
    assert len(_all_records()) > 0, "research/ 에서 import 레코드를 하나도 수집하지 못함"


# ===========================================================================
# 1) Gateway-only — 직접 모델 SDK import 부재 (요구사항 10.1 / 10.5)
# ===========================================================================
def test_no_direct_model_sdk_imports():
    """research/ 어디에도 boto3/botocore/anthropic/openai 직접 import 가 없어야 한다.

    모델 ID 문자열(``"us.anthropic.claude-…"``)이나 주석의 'boto3'/'anthropic' 언급은
    AST Import/ImportFrom 노드가 아니므로 잡히지 않는다(정확히 이 오탐 회피가 AST 스캔의
    목적). 실제 ``import X`` / ``from X import ...`` 만 검사한다.
    """
    violations = [
        f"{r.file}:{r.lineno} -> {'from ' + r.module if r.module else 'import ' + r.root} (root={r.root})"
        for r in _absolute_records()
        if r.root in _FORBIDDEN_SDK_ROOTS
    ]
    assert not violations, (
        "research/ 에 직접 모델 SDK import 발견(Gateway-only 위반, 요구사항 10.1/10.5). "
        "모든 LLM 호출은 Bedrock Gateway(agent_system.chat_model_adapter) 경유여야 함:\n  "
        + "\n  ".join(violations)
    )


# ===========================================================================
# 2) 단일 egress — httpx 는 backend.py 만 import (요구사항 10.4)
# ===========================================================================
def test_httpx_http_egress_only_in_backend():
    """외부 HTTP egress 클라이언트(httpx)의 소유를 backend.py 단일 모듈로 강제한다."""
    importers = sorted({
        r.file for r in _absolute_records() if r.root == _HTTP_EGRESS_CLIENT
    })
    # (a) egress 누수 금지 — backend.py 이외 파일이 httpx 를 import 하면 위반(요구사항 10.4).
    leaked = [f for f in importers if f != _EGRESS_OWNER]
    assert not leaked, (
        f"외부 HTTP egress('{_HTTP_EGRESS_CLIENT}')가 {_EGRESS_OWNER} 밖으로 누수됨"
        f"(단일 egress 위반, 요구사항 10.4). httpx 를 import 한 다른 파일: {leaked}"
    )
    # (b) 단일 egress 소유 확인 — backend.py 가 httpx egress 클라이언트를 소유해야 함(무회귀).
    assert _EGRESS_OWNER in importers, (
        f"단일 egress 모듈 {_EGRESS_OWNER} 가 '{_HTTP_EGRESS_CLIENT}' 를 import 하지 않음 "
        "— 외부 HTTP egress 소유가 이동/제거되었는지 확인 필요(요구사항 10.4)."
    )


# ===========================================================================
# 3a) 의존성 제약 — 외부 벡터DB 미도입 (요구사항 15.3)
# ===========================================================================
def test_no_external_vector_db_imports():
    violations = [
        f"{r.file}:{r.lineno} -> {r.module or r.root}"
        for r in _absolute_records()
        if r.root in _FORBIDDEN_VECTOR_DB_ROOTS
    ]
    assert not violations, (
        "research/ 에 외부 벡터DB import 발견(요구사항 15.3 위반 — 외부 벡터DB 미도입). "
        "본문 추출은 기존 의존성 lxml 을 재사용해야 함:\n  "
        + "\n  ".join(violations)
    )


# ===========================================================================
# 3b) 의존성 제약 — 신규 서드파티 의존성은 httpx/lxml 뿐 (요구사항 15.3)
# ===========================================================================
def test_new_third_party_deps_are_httpx_lxml_only():
    """research/ 가 새로 들이는 서드파티 의존성이 httpx/lxml 로 한정되는지 검증한다.

    external = (절대 import 루트) − stdlib − 내부(ai_engine). 여기서 기존 재사용
    orchestration/LLM 스택(langchain/langchain_core/langgraph)을 제외한 나머지가
    {httpx, lxml} 의 부분집합이어야 한다. numpy/faiss/trafilatura 같은 신규 무거운
    의존성이 들어오면 실패한다.
    """
    external_roots = {
        r.root for r in _absolute_records()
        if r.root not in _STDLIB_ROOTS and r.root != _INTERNAL_ROOT
    }
    new_deps = external_roots - _PREEXISTING_REUSED_DEPS
    unexpected = new_deps - _ALLOWED_NEW_DEPS
    assert not unexpected, (
        "research/ 가 허용되지 않은 신규 서드파티 의존성을 도입함"
        "(요구사항 15.3 — 신규 의존성은 httpx/lxml 만 허용). "
        f"예상 밖 의존성: {sorted(unexpected)}. "
        f"(전체 외부 루트={sorted(external_roots)}, "
        f"기존 재사용 스택={sorted(_PREEXISTING_REUSED_DEPS)})"
    )
