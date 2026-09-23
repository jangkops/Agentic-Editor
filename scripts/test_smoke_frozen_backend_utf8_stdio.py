"""smoke_frozen_backend.force_utf8_stdio — 콘솔 인코딩에 좌우되지 않는 스모크 판정.

2026-09-22 릴리스 CI 첫 실행(Windows 러너): 동결 백엔드 기동·프로브는 전부 성공했는데 한국어 리포트를
cp1252 콘솔에 print 하다 ``UnicodeEncodeError`` 로 죽어 exit 1 → 패키징이 건너뛰어졌다. 판정 결과가
출력 인코딩에 좌우되면 안 되므로 스크립트가 import 시점에 stdout/stderr 를 UTF-8 로 재설정한다.
여기서는 그 함수의 계약과, cp1252 스트림에 실제 리포트를 찍는 회귀 시나리오를 고정한다.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import smoke_frozen_backend as smoke  # noqa: E402


class _FakeStream:
    def __init__(self, encoding: str, *, raises: Exception | None = None):
        self.encoding = encoding
        self.calls: list[dict] = []
        self._raises = raises

    def reconfigure(self, **kwargs):
        if self._raises is not None:
            raise self._raises
        self.calls.append(kwargs)


class _NoReconfigure:
    encoding = "cp1252"


def _sample_results() -> list[dict]:
    # 실제 리포트에 나오는 한국어 경로명과 em dash 를 포함한다(Windows 실측 위치 149-150 문자).
    return [
        smoke._result("부팅/모듈", ok=True),
        smoke._result("LLM 채팅", ok=False, error="AWS 자격증명 부재로 skip", skipped=True),
        smoke._result("PPTX", ok=False, error="샘플 .pptx 없음 — skip", skipped=True),
        smoke._result("이미지 생성", ok=True),
        smoke._result("하이브리드 렌더", ok=True),
    ]


def test_reconfigures_non_utf8_stream_with_utf8_replace_and_reports_name():
    s = _FakeStream("cp1252")
    assert smoke.force_utf8_stdio([("stdout", s)]) == ["stdout"]
    assert s.calls == [{"encoding": "utf-8", "errors": "replace"}]


@pytest.mark.parametrize("enc", ["utf-8", "UTF-8", "utf8", "Utf_8".replace("_", "-")])
def test_leaves_stream_alone_when_already_utf8(enc):
    s = _FakeStream(enc)
    assert smoke.force_utf8_stdio([("stdout", s)]) == []
    assert s.calls == []


def test_skips_stream_without_reconfigure():
    assert smoke.force_utf8_stdio([("stdout", _NoReconfigure())]) == []


def test_swallows_reconfigure_failure_instead_of_crashing_the_smoke():
    s = _FakeStream("cp1252", raises=ValueError("stream already read"))
    assert smoke.force_utf8_stdio([("stderr", s)]) == []


def test_default_streams_are_safe_under_pytest_capture():
    # 인자 없이 호출해도(모듈 import 시점과 동일) 예외 없이 끝나고 stdout 은 계속 쓸 수 있어야 한다.
    names = smoke.force_utf8_stdio()
    assert isinstance(names, list)
    print("still writable")


def test_regression_korean_report_on_cp1252_console():
    results = _sample_results()
    report = smoke.format_report(smoke.evaluate_smoke(results), results)
    assert "부팅/모듈" in report and "—" in report

    # 수정 전 상황 재현: cp1252 스트림에 그대로 쓰면 죽는다.
    raw_before = io.BytesIO()
    console_before = io.TextIOWrapper(raw_before, encoding="cp1252", write_through=True)
    with pytest.raises(UnicodeEncodeError):
        console_before.write(report)

    # 수정 후: 같은 종류의 스트림을 force_utf8_stdio 로 재설정하면 리포트가 UTF-8 로 온전히 찍힌다.
    raw_after = io.BytesIO()
    console_after = io.TextIOWrapper(raw_after, encoding="cp1252", write_through=True)
    assert smoke.force_utf8_stdio([("stdout", console_after)]) == ["stdout"]
    print(report, file=console_after)
    console_after.flush()
    printed = raw_after.getvalue().decode("utf-8")
    assert "[PASS] 부팅/모듈" in printed
    assert "샘플 .pptx 없음 — skip" in printed
    assert "PASSED: True" in printed
