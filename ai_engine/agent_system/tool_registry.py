"""5 built-in tools for the agent system."""
import os
import subprocess
from typing import Any


class ToolRegistry:
    """Registry of tools available to agents."""

    def __init__(self, workspace_dir: str = "."):
        self.workspace_dir = os.path.abspath(workspace_dir)
        self._tools = {
            "read_file": self.read_file,
            "write_file": self.write_file,
            "list_files": self.list_files,
            "run_command": self.run_command,
            "search_web": self.search_web,
        }

    @property
    def tool_names(self) -> list:
        return list(self._tools.keys())

    @property
    def tool_descriptions(self) -> list:
        return [
            {"name": "read_file", "description": "Read contents of a file", "parameters": {"path": "string"}},
            {"name": "write_file", "description": "Write content to a file", "parameters": {"path": "string", "content": "string"}},
            {"name": "list_files", "description": "List files in a directory", "parameters": {"path": "string"}},
            {"name": "run_command", "description": "Run a shell command", "parameters": {"command": "string"}},
            {"name": "search_web", "description": "Search the web for information", "parameters": {"query": "string"}},
        ]

    async def execute(self, tool_name: str, args: dict) -> str:
        fn = self._tools.get(tool_name)
        if not fn:
            return f"Error: Unknown tool '{tool_name}'"
        try:
            return fn(**args)
        except Exception as e:
            return f"Error executing {tool_name}: {e}"

    def read_file(self, path: str) -> str:
        full = os.path.join(self.workspace_dir, path)
        if not os.path.isfile(full):
            return f"Error: File not found: {path}"
        with open(full, "r") as f:
            return f.read()

    def write_file(self, path: str, content: str) -> str:
        full = os.path.join(self.workspace_dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return f"Written {len(content)} chars to {path}"

    def list_files(self, path: str = ".") -> str:
        full = os.path.join(self.workspace_dir, path)
        if not os.path.isdir(full):
            return f"Error: Directory not found: {path}"
        entries = []
        for entry in sorted(os.listdir(full)):
            fp = os.path.join(full, entry)
            prefix = "📁" if os.path.isdir(fp) else "📄"
            entries.append(f"{prefix} {entry}")
        return "\n".join(entries)

    def run_command(self, command: str) -> str:
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=30, cwd=self.workspace_dir,
            )
            output = result.stdout + result.stderr
            return output[:5000] if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Command timed out (30s)"

    def search_web(self, query: str) -> str:
        return f"Web search not implemented. Query: {query}"
