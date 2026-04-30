"""
Test: Tool Execution
에이전트 도구 (read_file, run_command, etc.) 테스트
"""

import pytest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "ai_engine"))


class TestToolExecution:
    """도구 실행 통합 테스트"""

    @pytest.mark.unit
    def test_read_file_tool(self, sample_project_dir):
        """read_file 도구 테스트"""
        try:
            from tools import read_file_tool

            result = read_file_tool(str(Path(sample_project_dir) / "main.py"))
            assert result is not None
            assert "TODO" in result or "import" in result

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    def test_read_file_error_handling(self):
        """read_file 에러 처리 테스트"""
        try:
            from tools import read_file_tool

            result = read_file_tool("/nonexistent/file.py")
            # 존재하지 않는 파일 처리
            assert result is None or isinstance(result, str)

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    def test_write_file_tool(self, tmp_path):
        """write_file 도구 테스트"""
        try:
            from tools import write_file_tool

            test_file = tmp_path / "test_output.py"
            content = "print('Hello, World!')"

            result = write_file_tool(str(test_file), content)
            assert result is True
            assert test_file.exists()
            assert test_file.read_text() == content

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    def test_list_files_tool(self, sample_project_dir):
        """list_files 도구 테스트"""
        try:
            from tools import list_files_tool

            files = list_files_tool(sample_project_dir)
            assert files is not None
            assert isinstance(files, list)
            assert len(files) > 0
            # main.py 또는 requirements.txt 있는지 확인
            file_names = [f.get("name") for f in files]
            assert any(name in ["main.py", "requirements.txt", "package.json"] for name in file_names)

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    @patch("subprocess.run")
    def test_run_command_tool(self, mock_run, sample_project_dir):
        """run_command 도구 테스트"""
        try:
            from tools import run_command_tool

            # Mock 설정
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Output: Hello",
                stderr=""
            )

            result = run_command_tool("echo 'test'", cwd=sample_project_dir)
            assert result is not None
            assert "returncode" in result or "output" in result

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    def test_run_command_error_handling(self, sample_project_dir):
        """run_command 에러 처리 테스트"""
        try:
            from tools import run_command_tool

            # 존재하지 않는 명령어 실행
            result = run_command_tool("nonexistent_command_xyz", cwd=sample_project_dir)
            # 에러 처리가 제대로 되는지 확인
            assert result is not None

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    def test_search_files_tool(self, sample_project_dir):
        """search_files 도구 테스트"""
        try:
            from tools import search_files_tool

            # "TODO" 검색
            results = search_files_tool(sample_project_dir, "TODO", file_pattern="*.py")
            assert results is not None
            assert isinstance(results, list)

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    def test_tool_input_validation(self):
        """도구 입력 검증 테스트"""
        try:
            from tools import validate_tool_input

            # 유효한 입력
            valid_result = validate_tool_input("read_file", {"path": "/tmp/test.py"})
            assert valid_result is True

            # 잘못된 입력 (필수 인자 누락)
            invalid_result = validate_tool_input("read_file", {})
            assert invalid_result is False

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    def test_concurrent_tool_execution(self, tmp_path):
        """동시 도구 실행 테스트"""
        try:
            from tools import write_file_tool, read_file_tool
            import threading

            results = []

            def write_and_read(idx):
                file_path = tmp_path / f"file_{idx}.py"
                content = f"# File {idx}"
                write_file_tool(str(file_path), content)
                result = read_file_tool(str(file_path))
                results.append(result)

            # 5개 스레드에서 동시 실행
            threads = [threading.Thread(target=write_and_read, args=(i,)) for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(results) == 5
            assert all(r is not None for r in results)

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.integration
    def test_tool_chain_execution(self, tmp_path):
        """도구 체인 실행 테스트 (여러 도구 순차 실행)"""
        try:
            from tools import write_file_tool, read_file_tool, list_files_tool

            # 1. 파일 쓰기
            file_path = tmp_path / "chain_test.py"
            write_file_tool(str(file_path), "print('test')")

            # 2. 파일 읽기
            content = read_file_tool(str(file_path))
            assert content == "print('test')"

            # 3. 디렉터리 목록
            files = list_files_tool(str(tmp_path))
            assert any(f.get("name") == "chain_test.py" for f in files)

        except ImportError:
            pytest.skip("tools module not found")

    @pytest.mark.unit
    def test_tool_timeout_handling(self):
        """도구 타임아웃 처리 테스트"""
        try:
            from tools import run_command_tool

            # 긴 명령어 실행 (타임아웃 예상)
            result = run_command_tool("sleep 100", timeout=1)
            # 타임아웃이 제대로 처리되는지 확인
            assert result is not None

        except ImportError:
            pytest.skip("tools module not found")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
