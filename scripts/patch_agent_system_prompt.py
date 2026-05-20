#!/usr/bin/env python3
"""Add agent-mode base instruction to run_agent_with_tools system prompt.
Forces tool execution instead of text-only responses."""
import ast
from pathlib import Path

SERVER = Path("ai_engine/server.py")
text = SERVER.read_text(encoding="utf-8")

MARKER = "[에이전트 모드 지시]"
if MARKER in text:
    print("already patched")
    raise SystemExit(0)

# Find the exact insertion point — after the system_prompt construction block
# in run_agent_with_tools (line ~1541)
TARGET = '    if project_path and _is_code_related(prompt):'
if TARGET not in text:
    raise SystemExit("target not found")

# Insert agent instruction BEFORE the RAG block
INSTRUCTION = '''
    # 에이전트 모드 기본 지시 — 도구 사용 강제 + 작업 완료 후 보고
    _AGENT_BASE = (
        "\\n\\n[에이전트 모드 지시]\\n"
        "- 사용자의 요청을 완수하기 위해 제공된 도구를 반드시 실행하세요.\\n"
        "- 도구를 실행하지 않고 텍스트로만 답변하지 마세요.\\n"
        "- 작업을 모두 완료한 후 결과를 간결하게 보고하세요.\\n"
        "- 이미지/PDF/PPTX 생성 요청 시 generate_image/generate_pdf/generate_pptx 도구를 즉시 호출하세요.\\n"
        "- 파일 읽기/쓰기/검색이 필요하면 read_file/write_file/search_files 도구를 사용하세요.\\n"
    )
    system_prompt = (system_prompt or "") + _AGENT_BASE

'''

text = text.replace(TARGET, INSTRUCTION + TARGET, 1)
ast.parse(text)
SERVER.write_text(text, encoding="utf-8")
print("Agent system prompt instruction added")
