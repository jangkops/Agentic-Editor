# Feature: deep-research-engine, Property 15: 옵트인 게이트 무회귀
"""Property-based test: 옵트인/동의 게이트 무회귀 (`web_research_enabled`).

Feature: deep-research-engine, Property 15: 옵트인 게이트 무회귀
**Validates: Requirements 10.3, 10.5, 14.2**

For any 환경변수 매핑 `env`에 대해, 외부 리서치 egress의 활성 여부를 결정하는
게이트 함수 `web_research_enabled(env)`는 다음 불변식을 만족한다:

    web_research_enabled(env) is True
        ⇔ AE_ENABLE_WEB_RESEARCH 가 truthy   (옵트인)
          AND AE_RESEARCH_CONSENT 가 truthy   (프라이버시 동의)

즉 **두 플래그가 모두 참일 때만** True이고, 둘 중 하나라도 off이거나 미설정이면
False다(무회귀 — P15 / 요구사항 10.3·10.5·14.2). 이 게이트가 False면 backend의
egress 함수들은 네트워크를 호출하지 않고 즉시 로컬/빈 결과로 폴백하므로, 이 함수의
진리표가 곧 "외부 리서치 미도입 상태와의 동등성(무회귀)"을 결정한다.

truthy/falsy 규약(요구사항 지정 · 기존 rag 관례 계승):
    - truthy: "1" / "true" / "yes" / "on"  (앞뒤 공백 무시, 대소문자 무관)
    - falsy : "0" / "no" / "" / 그 외 임의 문자열 / 미설정(키 부재)

검증 대상은 순수·결정적 함수 `ai_engine.research.backend.web_research_enabled`
하나다(부작용은 주입된 env 읽기뿐 — 속성 테스트에 이상적). 테스트는 실제
os.environ을 건드리지 않고 항상 명시적 env dict를 주입해 밀폐(hermetic)하게
수행한다.

전략(생성기)은 다음을 지능적으로 겨냥한다(본 태스크 요구):
    - 각 플래그를 truthy / falsy / 미설정(absent) 세 상태로 다양하게 생성
    - truthy 값: 4개 토큰에 대소문자 혼합 + 앞뒤 ASCII 공백 부착
    - falsy 값: 명시 falsy 토큰 + 임의 텍스트(정규화 후 truthy 집합 밖으로 배제)
    - 노이즈 env 키: 두 플래그와 다른 임의 키(대소문자 근접 변형 포함)를 주입해
      게이트 결과에 영향이 없음을 확인(대소문자 구분 키 조회)

진리표의 truthy/falsy 판정은 **테스트 내부에 독립적으로 재기술한 리터럴 집합**
`_TRUTHY_CANON`을 오라클로 사용한다(코드의 내부 `_truthy`를 import하지 않음 →
동어반복 회피).

Stack: Python 3.11+, hypothesis library. 기존 scripts/test_*_pbt.py 관례를 따른다
(sys.path 삽입 후 ai_engine import, __main__ 에서 pytest 실행).
"""
from __future__ import annotations

import sys
from pathlib import Path

# ai_engine 패키지를 직접 실행 시에도 import 할 수 있도록 저장소 루트를 경로에 추가.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402
from hypothesis import assume, example, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ai_engine.research.backend import web_research_enabled  # noqa: E402

# 최소 반복 200회(task 9.2 지정 — @settings(max_examples=200)).
_MAX_EXAMPLES = 200

# 게이트가 읽는 두 플래그의 정확한 env 키(대소문자 구분).
_FLAG_ENABLE = "AE_ENABLE_WEB_RESEARCH"
_FLAG_CONSENT = "AE_RESEARCH_CONSENT"

# 독립 오라클: truthy 판정 정규형(요구사항 지정). 코드의 _truthy를 import하지 않고
# 스펙을 테스트 내부에 재기술한다(동어반복 회피). 값은 str.strip().lower() 적용 후 비교.
_TRUTHY_CANON = ("1", "true", "yes", "on")

# 부재(키 미설정)를 나타내는 센티널.
_ABSENT = object()


# --------------------------------------------------------------------------- #
# 생성기(strategies)
# --------------------------------------------------------------------------- #

# str.strip() 이 제거하는 앞뒤 ASCII 공백류(정규화 후 값 불변).
_WS = st.sampled_from(["", " ", "  ", "\t", "\n", "\r", "\f", "\v", " \t ", "\n "])


@st.composite
def _truthy_value(draw) -> str:
    """정규화하면 반드시 truthy 집합에 드는 값(대소문자 혼합 + 앞뒤 공백)."""
    token = draw(st.sampled_from(_TRUTHY_CANON))
    cased = "".join(draw(st.sampled_from([ch.lower(), ch.upper()])) for ch in token)
    return draw(_WS) + cased + draw(_WS)


# 명시 falsy 토큰(임의 텍스트만으로는 잘 안 나오는 "그럴듯한" 반례를 결정적으로 포함).
_FALSY_LITERALS = st.sampled_from(
    [
        "0", "no", "NO", "No", "", " ", "\t", "false", "FALSE", "off", "OFF",
        "n", "y", "t", "2", "-1", "true!", "yess", "onoff", "yes no",
        "enable", "enabled", "disable", "disabled", "nope", "null", "none",
        "0000", "o n", "tru e", "ye s",
    ]
)


@st.composite
def _falsy_value(draw):
    """정규화 후 truthy 집합 밖에 있는 값(→ falsy 보장). None(값)도 포함."""
    v = draw(
        st.one_of(
            _FALSY_LITERALS,
            st.none(),  # 키는 존재하나 값이 None → _truthy 는 "" 로 해석(falsy)
            st.text(max_size=16),
            st.text(
                alphabet=st.characters(min_codepoint=0, max_codepoint=0x2FFF),
                max_size=12,
            ),
        )
    )
    # 독립 오라클로 falsy 라벨을 보장(코드와 동일 정규화식이지만 리터럴 집합은 테스트 소유).
    assume(str(v or "").strip().lower() not in _TRUTHY_CANON)
    return v


@st.composite
def _flag_state(draw):
    """한 플래그의 상태를 (env_value_or_absent, intended_truthy)로 생성."""
    kind = draw(st.sampled_from(["truthy", "falsy", "absent"]))
    if kind == "truthy":
        return (draw(_truthy_value()), True)
    if kind == "falsy":
        return (draw(_falsy_value()), False)
    return (_ABSENT, False)


# 노이즈 env 키: 두 플래그와 다른 임의 키(대소문자 근접 변형 유도). 값은 임의 텍스트.
_NOISE_KEY = (
    st.text(
        alphabet=st.characters(
            whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_"
        ),
        min_size=1,
        max_size=24,
    )
    .filter(lambda k: k not in (_FLAG_ENABLE, _FLAG_CONSENT))
)
_NOISE_ENV = st.dictionaries(_NOISE_KEY, st.text(max_size=12), max_size=6)


def _build_env(en_state, co_state, noise) -> dict:
    """노이즈 먼저 깐 뒤 두 플래그를 덮어써 env dict를 조립(플래그가 항상 우선)."""
    env = dict(noise)
    en_val, _ = en_state
    co_val, _ = co_state
    if en_val is not _ABSENT:
        env[_FLAG_ENABLE] = en_val
    if co_val is not _ABSENT:
        env[_FLAG_CONSENT] = co_val
    return env


# --------------------------------------------------------------------------- #
# Property 15 — 진리표: 두 플래그가 모두 truthy 일 때만 True (그 외 전부 False)
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES)
@given(en=_flag_state(), co=_flag_state(), noise=_NOISE_ENV)
# 결정적으로 2-플래그 진리표 9칸을 매 실행 포함(truthy/falsy/absent 대표값).
@example(en=("1", True), co=("true", True), noise={})            # (T, T) → True
@example(en=("on", True), co=("no", False), noise={})            # (T, F) → False
@example(en=("YES", True), co=(_ABSENT, False), noise={})        # (T, absent) → False
@example(en=("0", False), co=("yes", True), noise={})            # (F, T) → False
@example(en=("false", False), co=("off", False), noise={})       # (F, F) → False
@example(en=("nope", False), co=(_ABSENT, False), noise={})      # (F, absent) → False
@example(en=(_ABSENT, False), co=("on", True), noise={})         # (absent, T) → False
@example(en=(_ABSENT, False), co=("0", False), noise={})         # (absent, F) → False
@example(en=(_ABSENT, False), co=(_ABSENT, False), noise={})     # (absent, absent) → False
# 노이즈·근접 키(대소문자/접미 변형)는 결과에 영향 없음.
@example(
    en=(" \tTrUe\n", True),
    co=("oN ", True),
    noise={
        "ae_enable_web_research": "0",       # 소문자 근접 키(코드는 대소문자 구분)
        "AE_ENABLE_WEB_RESEARCH_X": "false",  # 접미 변형
        "XAE_RESEARCH_CONSENT": "no",         # 접두 변형
        "PATH": "/usr/bin",
    },
)  # (T, T) + 노이즈 → True
def test_optin_gate_truth_table(en, co, noise) -> None:
    """게이트는 두 플래그가 모두 truthy 일 때만 True, 그 외에는 항상 False (P15)."""
    env = _build_env(en, co, noise)
    expected = en[1] and co[1]  # 독립 오라클(생성 시 라벨링한 intended_truthy)
    result = web_research_enabled(env)

    assert isinstance(result, bool)
    assert result is expected, (
        "옵트인 게이트 진리표 위반(P15): 두 플래그가 모두 truthy 일 때만 True 여야 함.\n"
        f"  AE_ENABLE_WEB_RESEARCH = {env.get(_FLAG_ENABLE, '<absent>')!r} "
        f"(intended_truthy={en[1]})\n"
        f"  AE_RESEARCH_CONSENT    = {env.get(_FLAG_CONSENT, '<absent>')!r} "
        f"(intended_truthy={co[1]})\n"
        f"  expected={expected}  result={result}"
    )


# --------------------------------------------------------------------------- #
# 무회귀 핵심: 어느 한 플래그라도 off/미설정이면 반드시 False
# (플래그 하나만 켜는 것으로는 외부 egress가 절대 활성화되지 않는다 — 요구사항 10.3/14.2)
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES)
@given(present=_truthy_value(), other=_flag_state(), noise=_NOISE_ENV)
def test_enable_only_never_activates(present, other, noise) -> None:
    """AE_ENABLE_WEB_RESEARCH 만 truthy 이고 동의가 truthy 가 아니면 False."""
    assume(not other[1])  # consent 는 falsy/absent 로 고정
    env = _build_env((present, True), other, noise)
    assert web_research_enabled(env) is False


@settings(max_examples=_MAX_EXAMPLES)
@given(present=_truthy_value(), other=_flag_state(), noise=_NOISE_ENV)
def test_consent_only_never_activates(present, other, noise) -> None:
    """AE_RESEARCH_CONSENT 만 truthy 이고 옵트인이 truthy 가 아니면 False."""
    assume(not other[1])  # enable 은 falsy/absent 로 고정
    env = _build_env(other, (present, True), noise)
    assert web_research_enabled(env) is False


# --------------------------------------------------------------------------- #
# 노이즈 env 키는 게이트 결과를 바꾸지 않는다(대소문자 구분 키 조회)
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES)
@given(en=_flag_state(), co=_flag_state(), noise=_NOISE_ENV)
def test_noise_keys_do_not_affect_gate(en, co, noise) -> None:
    """두 플래그만이 결과를 결정한다 — 임의 노이즈 키는 무관(무회귀)."""
    base = _build_env(en, co, {})
    with_noise = _build_env(en, co, noise)
    assert web_research_enabled(with_noise) is web_research_enabled(base)


# --------------------------------------------------------------------------- #
# 결정성: 동일 env → 동일 결과 (외부 상태 비의존)
# --------------------------------------------------------------------------- #
@settings(max_examples=_MAX_EXAMPLES)
@given(en=_flag_state(), co=_flag_state(), noise=_NOISE_ENV)
def test_gate_is_deterministic(en, co, noise) -> None:
    """게이트는 순수·결정적이다: 같은 입력에 항상 같은 bool 을 반환한다."""
    env = _build_env(en, co, noise)
    r1 = web_research_enabled(env)
    r2 = web_research_enabled(env)
    assert r1 is r2
    assert isinstance(r1, bool)


# --------------------------------------------------------------------------- #
# 스모크: 문서화된 진리표를 구체 값으로 전 조합 검증(속성 테스트 보완)
# --------------------------------------------------------------------------- #
_TRUTHY_SAMPLES = [
    "1", "true", "TRUE", "True", "tRuE", "yes", "YES", "Yes", "on", "ON", "On",
    " on ", "\tTrue\n", "  1", "yes\t",
]
_FALSY_SAMPLES = [
    "0", "no", "NO", "", " ", "\t\n", "false", "FALSE", "off", "OFF", "n", "y",
    "t", "2", "true!", "yess", "enable", "enabled", "nope", "null", "none",
    "yes no", "o n", None,
]


def test_optin_gate_truth_table_smoke() -> None:
    """truthy×falsy×absent 전 조합을 구체 값으로 확인(P15 구체 오라클)."""
    # (truthy, truthy) → True
    for e in _TRUTHY_SAMPLES:
        for c in _TRUTHY_SAMPLES:
            assert web_research_enabled({_FLAG_ENABLE: e, _FLAG_CONSENT: c}) is True, (
                f"둘 다 truthy 인데 False: enable={e!r}, consent={c!r}"
            )

    # (truthy, falsy) 와 그 대칭 (falsy, truthy) → False
    for t in _TRUTHY_SAMPLES:
        for f in _FALSY_SAMPLES:
            assert web_research_enabled({_FLAG_ENABLE: t, _FLAG_CONSENT: f}) is False, (
                f"동의 falsy 인데 True: enable={t!r}, consent={f!r}"
            )
            assert web_research_enabled({_FLAG_ENABLE: f, _FLAG_CONSENT: t}) is False, (
                f"옵트인 falsy 인데 True: enable={f!r}, consent={t!r}"
            )

    # (falsy, falsy) → False
    for e in _FALSY_SAMPLES:
        for c in _FALSY_SAMPLES:
            assert web_research_enabled({_FLAG_ENABLE: e, _FLAG_CONSENT: c}) is False

    # 미설정(absent) 조합 → 항상 False
    for v in _TRUTHY_SAMPLES + _FALSY_SAMPLES:
        assert web_research_enabled({_FLAG_ENABLE: v}) is False   # 동의 키 부재
        assert web_research_enabled({_FLAG_CONSENT: v}) is False  # 옵트인 키 부재
    assert web_research_enabled({}) is False                      # 둘 다 부재

    # 대소문자 구분 키: 소문자 플래그 키는 인식되지 않아 미설정과 동등(무회귀)
    assert web_research_enabled(
        {"ae_enable_web_research": "true", "ae_research_consent": "true"}
    ) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
