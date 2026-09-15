"""회귀 테스트 — ``_execute_tool("search_files")`` 가 모델 입력(query/path/file_pattern)을
셸 인용 없이 grep 문자열에 넣던 결함. shlex.quote 로 인용되고 ``-e``/``--`` 로 옵션 오인도
막는지 확인한다. subprocess.run 은 가로채서 실제 grep 은 실행하지 않는다.
"""
import os
import re
import shlex

import ai_engine.server as s


def test_search_files_quotes_model_supplied_args(monkeypatch, tmp_path):
    captured = {}

    class _Result:
        stdout = "ok"
        stderr = ""
        returncode = 0

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(s.subprocess, "run", fake_run)
    monkeypatch.setattr(s, "_bridge_is_remote", lambda: False, raising=False)

    query = "$(id) `whoami` \"x\" 'y' \\ z -v"
    out = s._execute_tool(
        "search_files",
        {"query": query, "path": "sub", "file_pattern": "*.py"},
        project_path=str(tmp_path),
    )
    cmd = captured["cmd"]
    assert out == "ok"
    assert cmd.startswith("grep -rn ")
    assert f"-e {shlex.quote(query)}" in cmd
    assert f"-- {shlex.quote(os.path.join(str(tmp_path), 'sub'))}" in cmd
    assert f"--include={shlex.quote('*.py')}" in cmd
    # 인용 구간을 걷어낸 나머지에 셸 메타문자가 남아 있으면 안 된다.
    outside = cmd.replace("'\"'\"'", "")
    outside = re.sub(r"'[^']*'", "", outside)
    assert not re.search(r"[$`\"\\]", outside), outside
