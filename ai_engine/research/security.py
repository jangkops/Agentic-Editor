"""deep-research-engine: 검색 제공자 자격증명 보안 — env 로딩 + 로그 마스킹.

Provider_Credential(검색 제공자 API 키)을 다루는 유일한 보안 헬퍼다. 두 함수만
노출한다.

- ``mask_secret(key)`` : 로그·진단 출력용 마스킹. 앞 4자만 남기고 나머지를 ``*``로
  대체해 원문을 노출하지 않는다. None/빈 값/짧은 값을 방어한다.
- ``load_credential(provider, env=None)`` : 제공자 이름 → 환경변수 이름 매핑으로
  env에서만 자격증명을 로딩한다. 파일 저장·기록은 절대 하지 않으며 값은 호출자에게
  반환(메모리 유지)만 한다. 미설정/빈 값이면 ``None``.

보안 불변식 (요구사항 11 / 정확성 속성 P9, steering security):
    - 자격증명은 어떤 파일에도 저장하지 않는다(env/시크릿 런타임 주입만). — 11.1 / 11.2
    - 자격증명 원문을 로그·예외 메시지·리턴 구조(캐시/리포트 등) 어디에도 평문으로
      노출하지 않는다. 로깅이 필요하면 반드시 ``mask_secret``을 거친다. — 11.4 / 11.5
    - 따라서 이 모듈은 자격증명 값을 로깅하지 않고, 값을 담은 예외도 던지지 않는다.

기존 관례 정합: 게이트웨이 API 토큰 마스킹(``gateway_module.mask_token``)과 동일하게
"앞 4자만 노출" 원칙을 따른다. 다만 여기서는 요구사항 11.4("나머지 문자를 마스킹
문자로 대체")에 맞춰 남은 각 문자를 ``*`` 한 자로 치환한다.

스택 제약: Python 3.11 표준 라이브러리(``os``)만 사용한다. 신규 의존성 없음.
순수/최소 부작용(env 읽기뿐).

Requirements: 11.1, 11.2, 11.4
Design: "Components and Interfaces" > "security.py" / "환경변수 / 설정" 표
        (TAVILY_API_KEY / EXA_API_KEY / BRAVE_API_KEY / SEMANTIC_SCHOLAR_API_KEY)
"""

import os
from typing import Optional

# 마스킹 시 노출하는 접두 길이(앞 4자).
# steering security("token.substring(0,4)") / gateway_module.mask_token 과 정합.
_VISIBLE_PREFIX = 4

# 제공자 이름 → 환경변수 이름 매핑 (design.md 환경변수 표).
# 값(자격증명)이 아니라 "변수 이름"만 담는다 — 이 상수에 키 원문은 존재하지 않는다.
PROVIDER_ENV_VARS: dict[str, str] = {
    "tavily": "TAVILY_API_KEY",
    "exa": "EXA_API_KEY",
    "brave": "BRAVE_API_KEY",
    "semantic_scholar": "SEMANTIC_SCHOLAR_API_KEY",
}


def mask_secret(key) -> str:
    """자격증명을 로그용으로 마스킹한다 — 앞 4자만 남기고 나머지는 ``*``.

    원문(전체 키)을 절대 반환하지 않는다(요구사항 11.4, P9). 로깅·진단 출력에는
    이 함수를 거친 값만 사용한다.

    규칙:
        - None / 빈 문자열 / 문자열이 아닌 입력 → ``""`` (노출할 것이 없음).
        - 길이 ≤ 4 (앞 4자 노출이 곧 전체 노출) → 전부 마스킹(``"*" * 길이``).
        - 길이 > 4 → ``key[:4] + "*" * (len(key) - 4)``.

    예: ``mask_secret("abcd1234efgh") == "abcd" + "*" * 8``.

    Args:
        key: 마스킹할 자격증명 문자열(방어적으로 None/비문자열도 허용).

    Returns:
        원문을 노출하지 않는 마스킹 문자열.
    """
    if not key or not isinstance(key, str):
        return ""
    n = len(key)
    if n <= _VISIBLE_PREFIX:
        return "*" * n
    return key[:_VISIBLE_PREFIX] + "*" * (n - _VISIBLE_PREFIX)


def _env_var_for(provider: str) -> str:
    """제공자 이름 → 환경변수 이름. 미등록 제공자는 관례적 이름을 파생한다.

    문서화된 제공자는 ``PROVIDER_ENV_VARS`` 매핑을 사용하고, 그 외에는
    ``{정규화_대문자}_API_KEY`` 규칙으로 파생한다(영숫자 외 문자는 ``_``로 치환).
    이 파생 규칙은 문서화된 4개 제공자에 대해서도 동일한 이름을 산출한다.
    """
    name = provider.strip().lower()
    mapped = PROVIDER_ENV_VARS.get(name)
    if mapped:
        return mapped
    normalized = "".join(ch if ch.isalnum() else "_" for ch in name)
    return f"{normalized.upper()}_API_KEY"


def load_credential(provider: str, env=None) -> Optional[str]:
    """검색 제공자 자격증명을 환경변수에서만 로딩한다(파일 미저장).

    제공자 이름을 환경변수 이름으로 매핑해 env에서 값을 읽어 반환한다. 값은
    호출자에게 반환(메모리 유지)만 하며, 어디에도 저장·기록하지 않는다(요구사항
    11.1 / 11.2). 원문을 로그·예외에 노출하지 않으므로 이 함수는 자격증명 값을
    로깅하거나 값을 담은 예외를 던지지 않는다.

    Args:
        provider: 제공자 이름(예: ``"tavily"``, ``"semantic_scholar"``).
                  대소문자·앞뒤 공백은 무시한다.
        env: 환경변수 매핑. ``None``이면 ``os.environ``을 사용한다(테스트 시
             dict 주입 가능).

    Returns:
        설정된 자격증명 문자열(앞뒤 공백 제거). 제공자명이 유효하지 않거나
        해당 env가 미설정/빈 값이면 ``None``.
    """
    if not provider or not isinstance(provider, str):
        return None
    env = env if env is not None else os.environ
    raw = env.get(_env_var_for(provider))
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    return value or None
