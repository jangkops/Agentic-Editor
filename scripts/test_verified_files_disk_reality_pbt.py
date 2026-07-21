"""Property test — Property 3: verified_files는 반드시 디스크에 실재.

Validates: Requirements 3.7, 3.8

대상 코드:
    ai_engine/agent_system/nodes/tool_node.py
    - GatewayToolNode._verify_files : 도구 산출물 경로를 절대경로로 해석 후
      디스크 실측(os.path.isfile AND os.path.getsize > 0)을 통과한 항목만
      VerifiedFile 로 반환.
    - _extract_rel_paths : 도구 결과(JSON)에서 산출물 상대경로 후보 추출.

검증 속성 (Property 3):
    도구 실행 결과로 verified_files 에 추가되는 경로는 반드시 디스크에 실제로
    존재(isfile)하고 size > 0 이어야 한다. 존재하지 않거나 빈(0바이트) 파일 경로는
    verified_files 에 포함되지 않는다.

접근:
    - fake fs(tmp_path) 에 hypothesis 로 생성한 후보 파일 집합을 만든다. 각 후보는
      다음 3종 중 하나다: real_nonempty(실재 + size>0), real_empty(실재 + 0바이트),
      missing(디스크에 없음).
    - 후보들을 generate_image 산출물 형식({"images":[{"path": ...}, ...]})의 raw 로
      묶어 실제 필터링 로직(_verify_files)에 통과시킨다.
    - 결과가 (a) 실재+비어있지않은 파일만 포함하고 (b) 각 항목이 실측(isfile & size>0)을
      만족하는지 검증한다.

네트워크 없음. Gateway/도구 실행은 호출하지 않고 순수 필터링 로직만 검증한다.
파일시스템은 tmp_path 로 격리, hypothesis max_examples 로 유한 시간 종료.

실행:
    ai_engine/.venv/bin/python -m pytest scripts/test_verified_files_disk_reality_pbt.py -q
"""
import os
import sys
import json
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hypothesis import given, settings, strategies as st, HealthCheck

from ai_engine.agent_system.nodes import tool_node as TN


# ── 파일시스템 안전 식별자 (경로 구분자/상위참조/널 배제) ──
_SAFE_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"

_safe_name = st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=16)

# 후보 종류: real_nonempty / real_empty / missing
_kind = st.sampled_from(["real_nonempty", "real_empty", "missing"])

# 파일 내용 크기(real_nonempty 전용): 1~64 바이트
_content_size = st.integers(min_value=1, max_value=64)


# 하나의 후보 = (파일명, 종류, 내용크기)
_candidate = st.tuples(_safe_name, _kind, _content_size)

# 후보 목록 — 파일명 중복은 아래에서 dedup 하므로 1~8개 생성
_candidates = st.lists(_candidate, min_size=0, max_size=8)


def _materialize(tmp_root: str, candidates):
    """후보 목록을 fake fs 에 실체화하고, (상대경로, 종류) 리스트를 반환.

    - 각 예제는 유일한 하위 디렉토리(uuid)를 써서 다른 후보 루트(~/.agentic-editor,
      tempdir, cwd)의 동명 파일과 우연히 충돌하지 않도록 격리한다.
    - 파일명 중복은 첫 항목만 채택(dedup)해 상대경로 유일성을 보장한다.
    """
    unique_dir = "verif_" + uuid.uuid4().hex[:12]
    base_abs = os.path.join(tmp_root, unique_dir)
    os.makedirs(base_abs, exist_ok=True)

    seen_names = set()
    rels = []  # [(rel_path, kind)]
    for name, kind, size in candidates:
        if name in seen_names:
            continue
        seen_names.add(name)
        rel = os.path.join(unique_dir, name + ".bin")
        abs_path = os.path.join(tmp_root, rel)
        if kind == "real_nonempty":
            with open(abs_path, "wb") as f:
                f.write(b"x" * size)
        elif kind == "real_empty":
            with open(abs_path, "wb") as f:
                f.write(b"")  # 0바이트
        # missing: 파일을 만들지 않는다
        rels.append((rel, kind))
    return rels


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(candidates=_candidates)
def test_verified_files_are_always_real_on_disk(tmp_path, candidates):
    """Property 3: verified_files 는 반드시 디스크에 실재(isfile & size>0)한다."""
    tmp_root = str(tmp_path)
    rels = _materialize(tmp_root, candidates)

    node = TN.GatewayToolNode(tools=[], deps=None)
    state = {"project_path": tmp_root}

    # 후보 전체를 generate_image 산출물 형식(images 리스트)으로 묶는다.
    raw = json.dumps({"images": [{"path": rel} for rel, _ in rels]})

    out = node._verify_files("generate_image", {}, raw, state)

    # (a) 반환된 모든 항목은 실측(isfile & size>0)을 만족해야 한다 — 핵심 불변식.
    for vf in out:
        abs_path = vf["absPath"]
        assert os.path.isfile(abs_path), f"verified 항목이 파일이 아님: {abs_path}"
        assert os.path.getsize(abs_path) > 0, f"verified 항목 size==0: {abs_path}"

    # (b) 존재하지 않거나 빈 파일의 상대경로는 결과에 포함되지 않아야 한다.
    returned_rels = {vf["path"] for vf in out}
    expected_rels = {rel for rel, kind in rels if kind == "real_nonempty"}
    empty_or_missing = {rel for rel, kind in rels if kind != "real_nonempty"}

    assert returned_rels.isdisjoint(empty_or_missing), (
        f"빈/미존재 파일이 verified 에 포함됨: {returned_rels & empty_or_missing}"
    )

    # (c) 실재+비어있지않은 파일은 모두 verified 에 포함(완전성). dedup 이후 집합 일치.
    assert returned_rels == expected_rels, (
        f"기대={expected_rels} 실제={returned_rels}"
    )


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    name=_safe_name,
    err=st.text(min_size=1, max_size=20),
)
def test_error_result_never_verified(tmp_path, name, err):
    """error 를 포함한 도구 결과는 파일이 실재하더라도 verified 에 포함되지 않는다.

    (요구사항 3.7 필터링 규약: 실패 산출물은 검증 대상에서 제외 — _extract_rel_paths.)
    """
    tmp_root = str(tmp_path)
    unique_dir = "err_" + uuid.uuid4().hex[:12]
    os.makedirs(os.path.join(tmp_root, unique_dir), exist_ok=True)
    rel = os.path.join(unique_dir, name + ".bin")
    with open(os.path.join(tmp_root, rel), "wb") as f:
        f.write(b"real-bytes")  # 실재 파일

    node = TN.GatewayToolNode(tools=[], deps=None)
    state = {"project_path": tmp_root}
    # error 필드가 있으면 _extract_rel_paths 가 경로를 추출하지 않아야 한다.
    raw = json.dumps({"error": err, "path": rel})

    out = node._verify_files("generate_pptx", {}, raw, state)
    assert out == [], f"error 산출물이 verified 됨: {out}"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
