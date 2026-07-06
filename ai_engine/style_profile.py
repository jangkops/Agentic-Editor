"""Style_Profile 데이터 모델 및 결정론적 Serializer.

이 모듈은 템플릿에서 파생된 스타일 토큰 집합(Style_Profile)을 표현한다.
설계 §구성요소 3 참조.

- 색상은 대문자 6자리 16진수 RGB 문자열(`#RRGGBB`)로 표현한다 (요구사항 3.2, 4.7).
- 직렬화 키 순서는 `STYLE_PROFILE_KEY_ORDER`로 고정되어 결정론적 직렬화를 보장한다.
- deserialize()는 고정된 검증 순서(invalid-json → invalid-style-profile →
  invalid-color)를 따르며, 선택 색상 필드 누락 시 SLIDE_DESIGN 기본값으로 채운다
  (요구사항 4.3, 4.5, 4.6, 4.7).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
import re

# 직렬화 키 순서를 명시적으로 고정 (요구사항 4.2) — dict insertion order에 의존하지 않음
STYLE_PROFILE_KEY_ORDER = (
    "primaryColor",
    "secondaryColor",
    "accentColor",
    "textColor",
    "backgroundColor",
    "headingFont",
    "bodyFont",
)

# 역직렬화 시 누락을 허용하지 않는 필수 필드 (요구사항 4.6)
REQUIRED_FIELDS = ("primaryColor", "textColor", "headingFont", "bodyFont")

# `#RRGGBB` 정규화 대상이 되는 색상 필드 (요구사항 4.7)
COLOR_FIELDS = (
    "primaryColor",
    "secondaryColor",
    "accentColor",
    "textColor",
    "backgroundColor",
)

# REQUIRED_FIELDS에 없는 선택 색상 필드. 역직렬화 시 누락되면 SLIDE_DESIGN의
# 대응 기본값으로 채운다 (요구사항 4.3, 4.6).
_OPTIONAL_FIELDS = ("secondaryColor", "accentColor", "backgroundColor")

# 선택 필드 → SLIDE_DESIGN 키 매핑. SLIDE_DESIGN을 직접 import 할 수 없는 환경에서는
# 아래 _LOCAL_DEFAULTS의 하드코딩 값으로 폴백한다 (slide_templates.SLIDE_DESIGN과 동일).
_OPTIONAL_DEFAULT_KEYS = {
    "secondaryColor": "secondary",
    "accentColor": "accent",
    "backgroundColor": "bg_light",
}

# slide_templates.SLIDE_DESIGN의 대응 값과 일치하는 로컬 폴백 상수. import 실패 시에만
# 사용되며, SLIDE_DESIGN에 대한 하드 의존/순환 import를 피하기 위한 것이다.
_LOCAL_DEFAULTS = {
    "secondaryColor": "#00C896",   # SLIDE_DESIGN['secondary']
    "accentColor": "#FF6B35",      # SLIDE_DESIGN['accent']
    "backgroundColor": "#FAFAFA",  # SLIDE_DESIGN['bg_light']
}

# 선택적 '#' + 정확히 6자리 16진수(대소문자 무관). 3자리 축약(#FFF) 등은 불허.
_HEX6_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


class StyleProfileError(ValueError):
    """Style_Profile 역직렬화 실패를 나타내는 구조화된 예외.

    `ValueError`의 서브타입이므로 일반 `except ValueError`로도 잡힌다. 엔드포인트
    레이어는 이 예외의 속성을 `{error, field?, missing?}` JSON으로 변환한다
    (설계 §구성요소 3, 9).

    Attributes:
        code: 에러 코드 문자열. 'invalid-json' | 'invalid-style-profile' |
            'invalid-color' 중 하나 (요구사항 4.5, 4.6, 4.7).
        missing: 'invalid-style-profile'일 때 누락된 모든 필수 필드명 리스트,
            그 외에는 None (요구사항 4.6).
        field: 'invalid-color'일 때 형식 위반 색상 필드명, 그 외에는 None
            (요구사항 4.7).
    """

    def __init__(
        self,
        code: str,
        *,
        missing: list[str] | None = None,
        field: str | None = None,
    ) -> None:
        self.code = code
        self.missing = missing
        self.field = field
        super().__init__(code)


@dataclass(frozen=True)
class StyleProfile:
    """템플릿에서 파생된 스타일 토큰 집합 (요구사항 3.1).

    7개 필드 모두 비어 있지 않은 문자열이어야 하며, 색상 필드는 `#RRGGBB`
    대문자 형식, 폰트 필드는 1–64자 문자열이다.
    """

    primaryColor: str
    secondaryColor: str
    accentColor: str
    textColor: str
    backgroundColor: str
    headingFont: str
    bodyFont: str


def normalize_color(value: str) -> str | None:
    """색상 값을 대문자 `#RRGGBB`로 정규화한다.

    허용: 선택적 '#' + 정확히 6자리 16진수(대소문자 무관). 예) '#1e1e1e',
    '1E1E1E' → '#1E1E1E'. 정확히 6자리가 아니면(예: '#FFF' 3자리 축약) None을
    반환한다 (요구사항 3.2, 4.7).

    Args:
        value: 정규화할 색상 문자열.

    Returns:
        정규화된 '#RRGGBB' 문자열, 형식 불일치 시 None.
    """
    if not isinstance(value, str):
        return None
    if not _HEX6_RE.match(value):
        return None
    digits = value[1:] if value.startswith("#") else value
    return "#" + digits.upper()


def serialize(profile: StyleProfile) -> str:
    """Style_Profile 객체를 결정론적 UTF-8 JSON 문자열로 직렬화한다.

    키는 `STYLE_PROFILE_KEY_ORDER`에 따라 명시적으로 `OrderedDict`에 삽입하므로
    dataclass의 필드 선언 순서에 우연히 의존하지 않는다. `json.dumps`는
    `ensure_ascii=False`(한글/CJK 폰트명을 이스케이프하지 않음),
    `separators=(',', ':')`(공백 없는 고정 구분자), `sort_keys=False`(삽입 순서
    유지)로 호출되어, 동일한 StyleProfile 객체에 대해 매 호출 바이트 단위로
    동일한 문자열을 출력한다 (요구사항 4.1, 4.2).

    Args:
        profile: 직렬화할 StyleProfile 객체.

    Returns:
        키 순서가 고정되고 공백이 없는 결정론적 UTF-8 JSON 문자열.
    """
    ordered = OrderedDict(
        (key, getattr(profile, key)) for key in STYLE_PROFILE_KEY_ORDER
    )
    return json.dumps(
        ordered,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
    )


def _optional_default(field: str) -> str:
    """선택 색상 필드의 기본값을 SLIDE_DESIGN에서 dual-path로 조회한다.

    repo 루트에서 실행 시 `ai_engine.slide_templates`, ai_engine/ 내부에서 실행 시
    `slide_templates`로 import 된다(template_manager.py와 동일 관례). 둘 다 실패하면
    SLIDE_DESIGN에 대한 하드 의존/순환 import를 피하기 위해 `_LOCAL_DEFAULTS`의
    하드코딩 값으로 폴백한다. 반환값은 항상 정규화된 `#RRGGBB` 문자열이다.

    Args:
        field: 선택 색상 필드명(secondaryColor/accentColor/backgroundColor 중 하나).

    Returns:
        해당 필드의 기본 색상('#RRGGBB' 대문자).
    """
    design_key = _OPTIONAL_DEFAULT_KEYS[field]
    slide_design = None
    try:
        from ai_engine.slide_templates import SLIDE_DESIGN as slide_design  # type: ignore
    except ImportError:  # pragma: no cover - exercised when run from ai_engine/
        try:
            from slide_templates import SLIDE_DESIGN as slide_design  # type: ignore
        except ImportError:
            slide_design = None

    raw = None
    if isinstance(slide_design, dict):
        raw = slide_design.get(design_key)

    normalized = normalize_color(raw) if isinstance(raw, str) else None
    if normalized is not None:
        return normalized
    # SLIDE_DESIGN 부재/무효 → 로컬 하드코딩 폴백(이미 정규화된 형식).
    return _LOCAL_DEFAULTS[field]


def deserialize(text: str) -> StyleProfile:
    """JSON 문자열을 StyleProfile 객체로 역직렬화한다.

    검증은 다음의 고정된 순서를 따른다.
      1. `json.loads` 실패 → StyleProfileError('invalid-json') (요구사항 4.5).
      2. REQUIRED_FIELDS 누락 → StyleProfileError('invalid-style-profile',
         missing=[...]) — 누락된 모든 필수 필드를 수집하며 부분 객체를 만들지
         않는다 (요구사항 4.6).
      3. 존재하는 색상 필드를 normalize_color로 검증 — 첫 실패 시
         StyleProfileError('invalid-color', field=...)로 즉시 중단한다
         (요구사항 4.7). 유효한 색상은 대문자 `#RRGGBB`로 정규화한다.
    선택 색상 필드(secondaryColor/accentColor/backgroundColor)가 누락되면
    SLIDE_DESIGN 대응 기본값으로 채운다 (요구사항 4.3). 정상 시 7개 필드가 모두
    채워지고 색상이 정규화된 StyleProfile을 반환하므로, deserialize → serialize는
    안정적으로 합성되어 왕복 보존 불변식(요구사항 4.4)을 만족한다.

    Args:
        text: 역직렬화할 JSON 문자열.

    Returns:
        7개 필드가 모두 채워진 StyleProfile 객체.

    Raises:
        StyleProfileError: 위 검증 단계에서 실패한 경우. `.code`로 원인을 구분한다.
    """
    # 1. JSON 파싱 (요구사항 4.5)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise StyleProfileError("invalid-json")
    if not isinstance(data, dict):
        # 최상위가 객체가 아니면 필수 필드가 존재할 수 없다 — invalid-json으로 취급.
        raise StyleProfileError("invalid-json")

    # 2. 필수 필드 누락 검사 — 누락된 모든 필드를 수집(요구사항 4.6).
    #    부분 객체를 만들지 않도록, 색상 정규화 이전에 먼저 검사한다.
    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise StyleProfileError("invalid-style-profile", missing=missing)

    # 3. 존재하는 색상 필드 정규화 — 첫 실패에서 즉시 중단(요구사항 4.7).
    #    선언된 키 순서(COLOR_FIELDS)대로 검사해 결정론적인 에러 보고를 보장한다.
    values: dict[str, str] = {}
    for field in COLOR_FIELDS:
        if field in data:
            normalized = normalize_color(data[field])
            if normalized is None:
                raise StyleProfileError("invalid-color", field=field)
            values[field] = normalized

    # 선택 색상 필드 누락 시 SLIDE_DESIGN 기본값으로 채움(요구사항 4.3).
    for field in _OPTIONAL_FIELDS:
        if field not in values:
            values[field] = _optional_default(field)

    # 폰트 필드(필수). 위 누락 검사를 통과했으므로 키는 반드시 존재한다.
    values["headingFont"] = data["headingFont"]
    values["bodyFont"] = data["bodyFont"]

    return StyleProfile(
        primaryColor=values["primaryColor"],
        secondaryColor=values["secondaryColor"],
        accentColor=values["accentColor"],
        textColor=values["textColor"],
        backgroundColor=values["backgroundColor"],
        headingFont=values["headingFont"],
        bodyFont=values["bodyFont"],
    )
