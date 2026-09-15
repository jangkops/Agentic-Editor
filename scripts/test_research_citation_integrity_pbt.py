# Feature: deep-research-engine, Property 6: 인용 참조 무결성
"""Property 6 — 인용 참조 무결성(citation reference integrity) property 테스트.

임의의 인용 source_id 목록 ``cited`` 와 근거 source_id 목록 ``evidence`` 에 대해
``deep_research._verify_citations(cited, evidence)`` 가 반환하는
``{"verified": [...], "unverified": [...]}`` 분류가 다음 4개 불변식을 항상
만족하는지 검증한다(단일 속성, 4 facet):

    1. 참조 무결성 : ``set(verified) ⊆ set(evidence)``.
                     검증된(verified) 인용은 모두 근거 집합의 source_id 로
                     뒷받침된다(dangling verified 없음 — P6 방향).
    2. 총괄 분류   : ``set(verified) ∪ set(unverified) == set(cited)`` 이고
                     ``set(verified) ∩ set(unverified) == ∅`` (분류 누락·중복 없음).
                     리스트 수준에서도 ``len(verified)+len(unverified)==len(cited)``
                     (어떤 인용도 드롭되지 않음).
    3. dangling→미검증 : ``set(cited) - set(evidence) ⊆ set(unverified)``.
                     근거에 없는 인용(dangling)은 반드시 미검증으로 분류되며
                     차단·삭제되지 않는다(요구사항 8.3 비차단).
    4. 결정성     : 동일 입력 → 동일 결과(``_verify_citations`` 를 두 번 호출해
                     완전히 같은 dict 를 반환).

위 1~3 을 하나로 요약하는 강한 형태 ``verified == cited∩evidence`` 및
``unverified == cited−evidence`` (집합 수준)도 함께 확정한다.

또한 두 실행 경로가 **어느 쪽이 실행되든** 동일한 속성을 만족함을 확인한다:
  - 정식 경로: ``rag/citation.verify_citations`` 재사용(``_verify_citations_via_rag``,
    ``_encode_source_id`` 로 슬래시를 제거해 정확 멤버십으로 환원 — 기본 경로).
  - 폴백 경로: 순수 분류기 ``_classify_citations`` (rag 자산 부재 시).
두 경로는 동일한 판정 규약(인용 source_id ∈ 근거 source_id 집합 = verified)을
따르므로 결과가 일치해야 한다(경로 무관 속성 보존).

대상은 재구현 금지 자산(``rag/citation.verify_citations``)을 재사용하는
``ai_engine/research/deep_research.py`` 의 ``_verify_citations`` (Task 13.1)이다.

생성기는 ``web:<url>`` / ``doi:<doi>`` 스킴 토큰과 빈 문자열·특수문자·유니코드·
비스킴 garbage 토큰을 섞고, 근거와 겹치는(overlap)·서로소(disjoint)·빈(empty)·
중복(duplicate)·dangling 인용을 모두 발생시킨다. dangling 을 보장하기 위해
근거는 공용 풀에서만 표집하고 인용은 공용 풀 + dangling 전용 풀에서 표집한다.

Stack: Python 3.11+, hypothesis 라이브러리(기존 ``scripts/test_*_pbt.py`` 관례).

**Validates: Requirements 8.2, 8.5**
"""
from __future__ import annotations

import sys
from pathlib import Path

# 스크립트를 직접 실행할 때 ai_engine 패키지를 import 가능하게 한다.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ai_engine.research.deep_research import (  # noqa: E402
    _classify_citations,
    _verify_citations,
    _verify_citations_via_rag,
)


# ---------------------------------------------------------------------------
# 생성기 — dangling 을 보장하기 위해 두 풀을 서로소로 구성한다.
#   * _COMMON_POOL : 근거·인용 양쪽에 등장 가능(overlap/verified 발생원).
#   * _DANGLING_POOL: 인용에만 등장(근거는 여기서 표집하지 않음 → 항상 dangling).
# 근거(evidence)는 _COMMON_POOL 에서만 표집하고, 인용(cited)은 두 풀 모두에서
# 표집한다. 따라서 인용 중 _DANGLING_POOL 원소는 근거에 존재할 수 없어 반드시
# dangling(미검증)이 된다. _COMMON_POOL 원소도 특정 표본에서 근거에 없으면
# dangling 이 되므로, dangling 판정은 항상 ``cited - evidence`` 전체로 검증한다.
# ---------------------------------------------------------------------------

# 근거·인용 양쪽에 나타날 수 있는 공용 source_id 토큰(정확 멤버십으로 겹침 유발).
_COMMON_POOL = [
    "web:https://example.com/a",
    "web:https://example.com/b",
    "web:https://example.com/page?x=1&y=2",   # 특수문자(quote 로 인코딩됨)
    "web:https://example.com/문서",             # 유니코드(quote 로 인코딩됨)
    "doi:10.1000/xyz",
    "doi:10.1234/abcd",
    "",                                         # 빈 문자열(경계값)
]

# 인용에만 등장하는 dangling 전용 토큰(근거는 여기서 표집하지 않음).
_DANGLING_POOL = [
    "web:https://dangling.example/only-cited",
    "doi:10.9999/dangling",
    "garbage-no-scheme-prefix",                 # 스킴 없는 비정상 토큰
    "web:https://example.com/a/deeper/path",    # 접미가 공용 토큰과 겹쳐 보이는 함정
]

# 근거: 공용 풀에서만 표집(빈 목록 허용 → 전부 미검증 케이스 발생).
_evidence_ids = st.lists(st.sampled_from(_COMMON_POOL), max_size=8)

# 인용: 공용 + dangling 풀에서 표집(빈 목록/중복/dangling 모두 발생).
_cited_ids = st.lists(st.sampled_from(_COMMON_POOL + _DANGLING_POOL), max_size=10)


# ---------------------------------------------------------------------------
# 공통 검증 헬퍼 — 하나의 (cited, evidence, result) 삼중항에 4개 facet 을 적용.
# 실행 경로(정식/폴백)에 무관하게 동일 속성을 강제한다.
# ---------------------------------------------------------------------------

def _assert_integrity(cited: list, evidence: list, result: dict, *, path: str) -> None:
    assert isinstance(result, dict), f"[{path}] result must be a dict, got {type(result)}"
    verified = result.get("verified")
    unverified = result.get("unverified")
    assert isinstance(verified, list) and isinstance(unverified, list), (
        f"[{path}] verified/unverified must be lists"
    )

    V, U = set(verified), set(unverified)
    C, E = set(cited), set(evidence)

    # facet 1 — 참조 무결성: 검증된 인용은 모두 근거 집합에 존재(P6 방향).
    assert V <= E, (
        f"[{path}] verified ⊄ evidence (참조 무결성 위반): "
        f"extra={sorted(V - E)!r}"
    )

    # facet 2 — 총괄 분류: 합집합 == 전체 인용, 상호 배타(누락·중복 분류 없음).
    assert V | U == C, (
        f"[{path}] verified ∪ unverified != cited (분류 누락): "
        f"missing={sorted(C - (V | U))!r}, extra={sorted((V | U) - C)!r}"
    )
    assert V & U == set(), (
        f"[{path}] verified ∩ unverified != ∅ (분류 중복): {sorted(V & U)!r}"
    )
    # 리스트 수준 총량 보존: 어떤 인용도 드롭되지 않는다(멀티셋 파티션).
    assert len(verified) + len(unverified) == len(cited), (
        f"[{path}] citation dropped/created: "
        f"len(v)+len(u)={len(verified) + len(unverified)} != len(cited)={len(cited)}"
    )

    # facet 3 — dangling(근거에 없는 인용)은 반드시 미검증으로 분류(비차단, 삭제 없음).
    dangling = C - E
    assert dangling <= U, (
        f"[{path}] dangling citation not in unverified: "
        f"missing={sorted(dangling - U)!r}"
    )

    # 강한 요약 형태(1+2+3 등가): verified == cited∩evidence, unverified == cited−evidence.
    assert V == (C & E), (
        f"[{path}] verified != cited∩evidence: got={sorted(V)!r}, "
        f"expected={sorted(C & E)!r}"
    )
    assert U == (C - E), (
        f"[{path}] unverified != cited−evidence: got={sorted(U)!r}, "
        f"expected={sorted(C - E)!r}"
    )


# ---------------------------------------------------------------------------
# Property 6 — 참조 무결성·총괄 분류·dangling→미검증·결정성 (단일 속성, 다 facet)
# ---------------------------------------------------------------------------

@settings(max_examples=300)
@given(cited=_cited_ids, evidence=_evidence_ids)
def test_citation_reference_integrity(cited: list, evidence: list) -> None:
    # (A) 공개 진입점(정식 경로: rag/citation.verify_citations 재사용) 검증.
    result = _verify_citations(cited, evidence)
    _assert_integrity(cited, evidence, result, path="_verify_citations")

    # (B) 결정성: 동일 입력 → 완전히 동일한 결과.
    result_again = _verify_citations(cited, evidence)
    assert result == result_again, (
        f"non-deterministic result: {result!r} != {result_again!r}"
    )

    # (C) 폴백 경로(순수 분류기)도 동일 속성을 만족한다(경로 무관 속성 보존).
    fallback = _classify_citations(cited, set(evidence))
    _assert_integrity(cited, evidence, fallback, path="_classify_citations")

    # (D) 정식 rag 경로 직접 호출도 동일 속성을 만족한다(rag 자산 가용 시 dict 반환).
    via_rag = _verify_citations_via_rag(cited, evidence)
    if via_rag is not None:  # rag 자산 부재 시 None → 폴백(위 C 에서 검증됨)
        _assert_integrity(cited, evidence, via_rag, path="_verify_citations_via_rag")
        # 두 경로는 동일 판정 규약이므로 집합 수준에서 일치해야 한다(어느 쪽이 실행되든 동일).
        assert set(via_rag["verified"]) == set(fallback["verified"]), (
            f"rag/fallback verified mismatch: {via_rag['verified']!r} vs "
            f"{fallback['verified']!r}"
        )
        assert set(via_rag["unverified"]) == set(fallback["unverified"]), (
            f"rag/fallback unverified mismatch: {via_rag['unverified']!r} vs "
            f"{fallback['unverified']!r}"
        )


# ---------------------------------------------------------------------------
# 스모크 — 생성기가 실제로 verified/unverified/dangling/중복을 만들며 속성이
# 성립함을 구체 예시로 고정(속성 테스트가 공허하게 통과하지 않도록 방어).
# ---------------------------------------------------------------------------

def test_smoke_overlap_dangling_and_duplicates() -> None:
    cited = [
        "web:https://example.com/a",   # 근거에 존재 → verified
        "doi:10.1000/xyz",             # 근거에 존재 → verified
        "web:https://dangling/x",      # 근거에 없음 → unverified(dangling)
        "web:https://example.com/a",   # 중복 → verified(중복 보존)
    ]
    evidence = [
        "web:https://example.com/a",
        "web:https://example.com/b",   # 인용되지 않은 근거(문제 없음)
        "doi:10.1000/xyz",
    ]
    out = _verify_citations(cited, evidence)
    # verified 는 근거 집합의 부분집합(참조 무결성).
    assert set(out["verified"]) <= set(evidence)
    # 집합 수준 정확 분류.
    assert set(out["verified"]) == {"web:https://example.com/a", "doi:10.1000/xyz"}
    assert set(out["unverified"]) == {"web:https://dangling/x"}
    # dangling 은 미검증에 존재(차단·삭제 없음).
    assert "web:https://dangling/x" in out["unverified"]
    # 어떤 인용도 드롭되지 않음(중복 포함 총량 보존).
    assert len(out["verified"]) + len(out["unverified"]) == len(cited) == 4


def test_smoke_empty_cited_and_empty_evidence() -> None:
    # 빈 인용 → 빈 분류(누락 없음).
    assert _verify_citations([], ["web:https://example.com/a"]) == {
        "verified": [],
        "unverified": [],
    }
    # 빈 근거 → 모든 인용이 dangling(미검증), 하나도 verified 되지 않음.
    cited = ["web:https://example.com/a", "doi:10.1000/xyz"]
    out = _verify_citations(cited, [])
    assert out["verified"] == []
    assert set(out["unverified"]) == set(cited)
    assert len(out["unverified"]) == len(cited)


def test_smoke_rag_and_fallback_agree() -> None:
    cited = ["web:https://example.com/a", "doi:10.1000/xyz", "garbage", "web:https://example.com/a"]
    evidence = ["web:https://example.com/a", "doi:10.1000/xyz"]
    fallback = _classify_citations(cited, set(evidence))
    via_rag = _verify_citations_via_rag(cited, evidence)
    assert via_rag is not None, "rag citation asset should be importable in this env"
    # 정식 rag 경로와 순수 폴백이 동일 판정(경로 무관 속성 보존)을 구체 예시로 고정.
    assert via_rag == fallback == _verify_citations(cited, evidence)


# ---------------------------------------------------------------------------
# Driver — 직접 실행(`python scripts/test_research_citation_integrity_pbt.py`) 지원.
# pytest 로도 test_* 함수가 그대로 수집된다.
# ---------------------------------------------------------------------------

def main() -> int:
    print("Property test: deep-research-engine P6 — 인용 참조 무결성")
    print()

    checks = [
        ("smoke: overlap/dangling/중복 분류", test_smoke_overlap_dangling_and_duplicates),
        ("smoke: 빈 인용/빈 근거", test_smoke_empty_cited_and_empty_evidence),
        ("smoke: rag/폴백 경로 일치", test_smoke_rag_and_fallback_agree),
        ("prop: 참조 무결성·총괄 분류·dangling·결정성", test_citation_reference_integrity),
    ]
    failures = []
    for label, fn in checks:
        print(f"[run] {label} ...", end=" ", flush=True)
        try:
            fn()
            print("OK")
        except Exception as e:  # noqa: BLE001
            print("FAIL")
            failures.append((label, e))

    print()
    if failures:
        print(f"FAILED: {len(failures)} of {len(checks)} checks")
        for label, e in failures:
            print(f"  - {label}: {e}")
        return 1
    print(f"PASSED: all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
