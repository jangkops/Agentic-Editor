"""Property 1: LLM 호출은 항상 Gateway 경유 (직접 SDK import 부재).

Validates: Requirements 2.2, 8.4

검증 속성
---------
`ai_engine/agent_system/` 하위 모든 모듈(특히 chat_model_adapter.py, nodes/*,
subgraphs/*, supervisor.py)이 직접 LLM SDK 를 import 하지 않는다. 즉 아래 루트
모듈에 대한 절대 import 가 하나도 없어야 한다.

    boto3, botocore, anthropic, openai

이 모듈들은 Bedrock 을 직접 호출하는 경로(예: ``botocore.client`` 로 bedrock
client 생성, ``anthropic.Anthropic()``, ``openai.OpenAI()``)를 열어주므로, 존재
자체가 "Gateway 우회"의 정적 증거가 된다. 모든 LLM 호출은 ``GatewayClient``
(SigV4 / assume-role) 를 경유해야 한다(design.md Property 1).

접근
----
- 실제 네트워크 호출 없음. `ast` 모듈로 각 소스의 import 문만 정적 분석한다.
- hypothesis 로 "대상 모듈 경로 집합"의 부분집합을 생성해, 어떤 부분집합에 대해서도
  금지 SDK import 가 0 건이라는 불변식이 유지됨을 검증한다(유한 시간, max_examples 상한).
- 상대 import(`from . import x`, level>0)는 내부 모듈이므로 검사 대상에서 제외.

실행:
    ai_engine/.venv/bin/python -m pytest scripts/test_langgraph_gateway_only_imports_pbt.py -q
"""
import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from hypothesis import given, settings, strategies as st

# ---------------------------------------------------------------------------
# 대상 및 정책 정의
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_AGENT_SYSTEM_DIR = os.path.join(_REPO_ROOT, "ai_engine", "agent_system")

# 직접 import 가 금지된 LLM/SDK 루트 모듈. 첫 컴포넌트 기준으로 비교하므로
# botocore.client / anthropic.types / openai.resources 등 하위 모듈도 모두 차단된다.
FORBIDDEN_SDK_ROOTS = frozenset({"boto3", "botocore", "anthropic", "openai"})


def _iter_target_modules():
    """`ai_engine/agent_system/` 하위 모든 .py 파일 경로를 반환(__pycache__ 제외)."""
    out = []
    for root, dirs, files in os.walk(_AGENT_SYSTEM_DIR):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if fn.endswith(".py"):
                out.append(os.path.join(root, fn))
    return sorted(out)


def _import_roots(py_path: str) -> set:
    """소스 파일에서 절대 import 의 루트 모듈 이름 집합을 추출.

    - `import a.b.c`          -> {"a"}
    - `from a.b import c`     -> {"a"}
    - `from . import x` (상대) -> 제외 (level > 0)
    """
    with open(py_path, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source, filename=py_path)
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # 상대 import 는 내부 모듈
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def _forbidden_sdk_imports(py_path: str) -> set:
    """해당 파일이 import 하는 금지 SDK 루트 모듈 집합(없으면 빈 집합)."""
    return _import_roots(py_path) & FORBIDDEN_SDK_ROOTS


_TARGET_MODULES = _iter_target_modules()


# ---------------------------------------------------------------------------
# 사전 조건: 대상 집합이 실제 핵심 파일을 포함하는지 (테스트가 헛돌지 않음을 보장)
# ---------------------------------------------------------------------------

def test_target_set_covers_key_modules():
    rels = {os.path.relpath(p, _AGENT_SYSTEM_DIR) for p in _TARGET_MODULES}
    for expected in (
        "chat_model_adapter.py",
        "supervisor.py",
        os.path.join("nodes", "tool_node.py"),
        os.path.join("subgraphs", "coding.py"),
    ):
        assert expected in rels, f"대상 집합에 {expected} 누락 — 테스트 범위 오류"
    assert len(_TARGET_MODULES) >= 5


# ---------------------------------------------------------------------------
# 체커 자체의 정합성(네거티브 컨트롤): 금지 import 를 실제로 탐지하는가
# ---------------------------------------------------------------------------

def test_checker_detects_direct_sdk_imports(tmp_path):
    bad = tmp_path / "bad_module.py"
    bad.write_text(
        "import os\n"
        "import boto3\n"
        "from botocore.client import BaseClient\n"
        "import anthropic\n"
        "from openai import OpenAI\n",
        encoding="utf-8",
    )
    found = _forbidden_sdk_imports(str(bad))
    assert found == {"boto3", "botocore", "anthropic", "openai"}


def test_checker_ignores_strings_and_comments(tmp_path):
    """문자열/주석 안의 'import boto3' 는 탐지하지 않아야 한다(오탐 방지)."""
    ok = tmp_path / "ok_module.py"
    ok.write_text(
        '# import boto3 (주석일 뿐)\n'
        'DOC = "we never import anthropic here"\n'
        "from . import sibling  # 상대 import 는 무시\n"
        "import os\n",
        encoding="utf-8",
    )
    assert _forbidden_sdk_imports(str(ok)) == set()


# ---------------------------------------------------------------------------
# 핵심 불변식: 모든 대상 모듈에 금지 SDK import 가 0 건
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("py_path", _TARGET_MODULES,
                         ids=[os.path.relpath(p, _AGENT_SYSTEM_DIR) for p in _TARGET_MODULES])
def test_no_direct_sdk_import_per_module(py_path):
    found = _forbidden_sdk_imports(py_path)
    assert found == set(), (
        f"{os.path.relpath(py_path, _AGENT_SYSTEM_DIR)} 가 직접 SDK 를 import 함: "
        f"{sorted(found)} — 모든 LLM 호출은 GatewayClient 경유여야 함"
    )


# ---------------------------------------------------------------------------
# Property (hypothesis): 대상 모듈 경로 집합의 어떤 부분집합에 대해서도 불변식 유지
# ---------------------------------------------------------------------------

@settings(max_examples=100, deadline=None)
@given(data=st.data())
def test_property_gateway_only_over_subsets(data):
    """Property 1: 대상 모듈의 임의 부분집합을 골라도 금지 SDK import 는 항상 0 건.

    hypothesis 가 대상 경로 집합에서 부분집합을 샘플링해 불변식을 반복 검증한다.
    (파일시스템 읽기만; 네트워크/게이트웨이 호출 없음)
    """
    if not _TARGET_MODULES:
        return
    subset = data.draw(
        st.lists(st.sampled_from(_TARGET_MODULES), min_size=1, max_size=len(_TARGET_MODULES),
                 unique=True)
    )
    for py_path in subset:
        assert _forbidden_sdk_imports(py_path) == set()


# ---------------------------------------------------------------------------
# 양성 조건: LLM 호출 모듈은 gateway 경유를 실제로 참조한다
# ---------------------------------------------------------------------------

def test_chat_model_adapter_routes_through_gateway():
    """chat_model_adapter 는 self.gateway (GatewayClient) 를 경유해 LLM 을 호출한다."""
    path = os.path.join(_AGENT_SYSTEM_DIR, "chat_model_adapter.py")
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()
    assert "self.gateway" in src, "LLM 호출이 gateway 경유임을 확인할 수 없음"
    # 그리고 직접 SDK import 는 없어야 함
    assert _forbidden_sdk_imports(path) == set()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
