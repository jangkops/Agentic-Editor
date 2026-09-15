"""테스트 위생 가드 — 테스트가 다른 테스트를 깨뜨리지 못하게 한다.

── 배경(실측 사고) ────────────────────────────────────────────────────────
전체 스위트에서 실패 1건이 원인 불명으로 떠 있었다:

    test_vertex_auto_enable.py::test_get_client_accepts_credentials_param
    → assert 'credentials' in mappingproxy(... <Signature (*a, **k)>)

제품 코드에는 문제가 없었다. ``test_pptx_hybrid_render_content_editable_pbt.py`` 가
import 시점에 모듈 전역을 맨 대입으로 덮고 복원하지 않은 것이 원인이었다:

    import ai_engine.vertex_image_module as _vim
    _vim.get_vertex_image_client = lambda *a, **k: _DisabledVertexClient()

pytest 는 한 프로세스에서 전 파일을 수집·실행하므로 스텁이 세션 끝까지 남았고,
뒤에 도는 파일이 **스텁의 시그니처**를 검사해 거짓 실패했다. 파일 단위로 돌리면
통과하고 전체로 돌리면 실패하는, 추적이 가장 비싼 형태의 실패다.

── 이 가드가 고정하는 성질 ────────────────────────────────────────────────
테스트 파일은 **import 시점에** 다른 모듈의 전역을 맨 대입으로 덮지 않는다.
대체가 필요하면 ``monkeypatch`` 또는 복원을 보장하는 fixture 를 쓴다.

함수/fixture **안의** 대입은 허용한다 — 복원 책임이 명확한 지점이기 때문이다.
검사는 AST 로 하며, 모듈 최상위(및 최상위 try/if/with)의 ``mod.attr = ...`` 만 본다.

이 파일은 네트워크·게이트웨이·LLM 을 쓰지 않는다(소스 파싱만).
"""
import ast
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# 이 가드가 감시하는 디렉터리들. tests/ 는 jest 소관이라 제외한다.
_SCAN_DIRS = [_SCRIPTS]

# 맨 대입이 허용되는 수신자 — 테스트가 자기 자신에게 설정하는 값들.
# hypothesis/pytest 의 관례적 모듈 설정(예: `pytestmark`)은 자기 모듈이라 대상 아님.
_ALLOWED_TARGET_MODULES = frozenset({
    "os",          # os.environ[...] 은 Subscript 라 애초에 대상 아님
})

# 사고 이전부터 존재해 이 가드로 정리 대상이지만 지금 손대면 회귀 위험이 큰 지점.
# **비어 있는 상태를 기본으로 둔다** — 예외 목록이 자라면 가드가 무력화된다.
# 항목을 추가할 때는 왜 지금 고칠 수 없는지 한 줄로 남긴다.
_KNOWN_EXCEPTIONS: dict = {
    # "scripts/example.py": "이유",
}


def _iter_test_files():
    for d in _SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.startswith("test_") and name.endswith(".py"):
                yield os.path.join(d, name)


def _module_level_attribute_assignments(tree):
    """모듈 최상위에서 실행되는 ``something.attr = ...`` 를 모은다.

    최상위 ``try``/``if``/``with``/``for`` 본문도 import 시점에 실행되므로 함께 본다.
    ``def``/``class`` 안은 보지 않는다 — 호출 시점이 통제되는 지점이다.
    """
    found = []

    def walk_body(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue                      # 호출 시점이 통제됨 — 대상 아님
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if not isinstance(tgt, ast.Attribute):
                        continue
                    base = tgt.value
                    if not isinstance(base, ast.Name):
                        continue              # self.x / a.b.c 는 대상 아님
                    if base.id in _ALLOWED_TARGET_MODULES:
                        continue
                    found.append((node.lineno, f"{base.id}.{tgt.attr}"))
            # 최상위에서 즉시 실행되는 블록들 안으로 들어간다
            for attr in ("body", "orelse", "finalbody"):
                inner = getattr(node, attr, None)
                if isinstance(inner, list):
                    walk_body(inner)

    walk_body(tree.body)
    return found


def _imported_module_aliases(tree):
    """``import x as y`` / ``import x`` 로 들어온 모듈 별칭 집합.

    이 별칭에 대한 최상위 속성 대입만 문제로 본다 — 지역 객체 설정과 구분하기 위해서다.
    """
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                aliases.add(a.asname or a.name)
    return aliases


def _violations():
    out = []
    for path in _iter_test_files():
        rel = os.path.relpath(path, _ROOT)
        if rel in _KNOWN_EXCEPTIONS:
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            tree = ast.parse(src)
        except SyntaxError as e:      # 문법 오류는 별도 테스트가 잡는다
            out.append((rel, e.lineno or 0, f"<문법 오류: {e.msg}>"))
            continue
        aliases = _imported_module_aliases(tree)
        for lineno, target in _module_level_attribute_assignments(tree):
            base = target.split(".", 1)[0]
            if base in aliases:
                out.append((rel, lineno, target))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 가드 본체
# ─────────────────────────────────────────────────────────────────────────────

def test_no_module_level_global_clobber_in_tests():
    """테스트 파일이 import 시점에 남의 모듈 전역을 덮지 않는다.

    걸렸다면 해당 대입을 ``monkeypatch`` 나 복원을 보장하는 fixture 로 옮긴다.
    복원 없는 대입은 **파일 단위로는 통과하고 전체 스위트에서만 실패**하는,
    추적 비용이 가장 큰 실패를 만든다.
    """
    v = _violations()
    if v:
        lines = "\n".join(f"  {rel}:{ln}  →  {tgt} = ..." for rel, ln, tgt in v)
        raise AssertionError(
            "테스트가 import 시점에 다른 모듈의 전역을 맨 대입으로 덮습니다.\n"
            "복원되지 않아 뒤에 실행되는 다른 파일의 테스트를 거짓 실패시킬 수 있습니다.\n"
            "monkeypatch 또는 try/finally 복원 fixture 로 옮기세요.\n" + lines
        )


def test_known_exception_list_stays_empty_or_justified():
    """예외 목록이 자라면 가드가 죽는다 — 사유 없는 항목을 금지한다."""
    for path, reason in _KNOWN_EXCEPTIONS.items():
        assert isinstance(reason, str) and reason.strip(), f"{path}: 예외 사유가 비어 있음"
        assert os.path.exists(os.path.join(_ROOT, path)), \
            f"{path}: 존재하지 않는 파일이 예외로 남아 있음 — 목록에서 지우세요"


# ─────────────────────────────────────────────────────────────────────────────
# 가드 자체의 정확성 — 잡아야 할 것을 잡고, 잡지 말아야 할 것은 통과시킨다
# ─────────────────────────────────────────────────────────────────────────────

_BAD_SRC = """
import ai_engine.vertex_image_module as _vim
_vim.get_vertex_image_client = lambda *a, **k: None
"""

_BAD_IN_TRY = """
try:
    import ai_engine.vertex_image_module as _vim
    _vim.get_vertex_image_client = lambda *a, **k: None
except Exception:
    pass
"""

_GOOD_FIXTURE = """
import pytest
import ai_engine.vertex_image_module as _vim

@pytest.fixture(autouse=True, scope="module")
def _stub():
    original = _vim.get_vertex_image_client
    _vim.get_vertex_image_client = lambda *a, **k: None
    try:
        yield
    finally:
        _vim.get_vertex_image_client = original
"""

_GOOD_LOCAL_OBJECT = """
class Cfg:
    pass

cfg = Cfg()
cfg.enabled = False
"""

_GOOD_SELF_IN_METHOD = """
import ai_engine.server as srv

class T:
    def test_x(self, monkeypatch):
        monkeypatch.setattr(srv, "thing", 1)
"""


def _scan_src(src):
    tree = ast.parse(src)
    aliases = _imported_module_aliases(tree)
    return [t for _, t in _module_level_attribute_assignments(tree)
            if t.split(".", 1)[0] in aliases]


def test_guard_catches_bare_module_assignment():
    assert _scan_src(_BAD_SRC) == ["_vim.get_vertex_image_client"]


def test_guard_catches_assignment_inside_toplevel_try():
    """실제 사고 형태 — try 로 감싸도 import 시점에 실행된다."""
    assert _scan_src(_BAD_IN_TRY) == ["_vim.get_vertex_image_client"]


def test_guard_allows_fixture_with_restore():
    """올바른 패턴은 통과해야 한다 — 아니면 개발자가 가드를 끈다."""
    assert _scan_src(_GOOD_FIXTURE) == []


def test_guard_allows_local_object_attribute():
    assert _scan_src(_GOOD_LOCAL_OBJECT) == []


def test_guard_allows_monkeypatch_in_method():
    assert _scan_src(_GOOD_SELF_IN_METHOD) == []


def test_guard_survives_every_test_file_without_raising():
    """가드가 실제 리포 전체를 파싱해도 터지지 않는다(가드의 비차단성)."""
    files = list(_iter_test_files())
    assert len(files) > 20, "스캔 대상이 갑자기 줄었다 — 경로 규칙이 깨진 것 아닌지 확인"
    _violations()   # 예외를 던지면 실패


# ─────────────────────────────────────────────────────────────────────────────
# 사고 지점의 회귀 고정
# ─────────────────────────────────────────────────────────────────────────────

def test_hybrid_render_pbt_uses_restoring_fixture():
    """사고 파일이 fixture 방식을 유지하는지 직접 확인한다."""
    p = os.path.join(_SCRIPTS, "test_pptx_hybrid_render_content_editable_pbt.py")
    with open(p, encoding="utf-8") as f:
        src = f.read()
    assert "_hermetic_vertex" in src, "헤르메틱 fixture 가 사라졌다"
    assert "finally:" in src, "복원 블록이 사라졌다"
    assert _scan_src(src) == [], "맨 대입이 되살아났다"


if __name__ == "__main__":
    import pytest as _pytest

    raise SystemExit(_pytest.main([__file__, "-q", "-p", "no:randomly"]))
