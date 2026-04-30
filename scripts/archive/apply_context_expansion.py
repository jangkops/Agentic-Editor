#!/usr/bin/env python3
"""server.py 에 컨텍스트 확장 패치를 일괄 적용.

개선 항목:
1) _execute_tool의 read_file 30000자 → AE_READ_FILE_MAX(기본 120000)
2) _execute_tool의 run_command 10000자 → AE_RUN_CMD_MAX(기본 40000)
3) agent tool_results tool_output[:15000] → AE_TOOL_RESULT_MAX(기본 80000) (2곳)
4) orchestrator 루프에 max_tokens continue 로직 추가

각 치환은 원본 문자열이 정확히 1회만 나타날 때만 적용한다(안전).
이미 패치된 경우 skip 한다(멱등성).
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "ai_engine" / "server.py"

src = TARGET.read_text(encoding="utf-8")
orig = src

def patch(src: str, old: str, new: str, label: str) -> str:
    if new in src:
        print(f"[skip] {label} (이미 적용됨)")
        return src
    count = src.count(old)
    if count == 0:
        print(f"[warn] {label} — 원본 문자열을 찾지 못함")
        return src
    if count > 1:
        print(f"[error] {label} — 원본 문자열이 {count}회 존재(모호), 건너뜀")
        return src
    print(f"[patch] {label}")
    return src.replace(old, new, 1)

# ── 1) read_file 제한 확대 ──────────────────────────────────────
old_read = """            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            if len(content) > 30000:
                content = content[:30000] + f\"\\n... (총 {len(content)}자, 30000자까지 표시)\"
            return content"""
new_read = """            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            _rf_max = int(os.environ.get(\"AE_READ_FILE_MAX\", \"120000\"))
            if len(content) > _rf_max:
                content = content[:_rf_max] + f\"\\n... (총 {len(content)}자, {_rf_max}자까지 표시)\"
            return content"""
src = patch(src, old_read, new_read, "read_file 제한 확대(30000→AE_READ_FILE_MAX=120000)")

# ── 2) run_command 제한 확대 ────────────────────────────────────
old_cmd = """            output = result.stdout + result.stderr
            if len(output) > 10000:
                output = output[:10000] + \"\\n... (출력 잘림)\"
            return output or \"(출력 없음)\""""
new_cmd = """            output = result.stdout + result.stderr
            _rc_max = int(os.environ.get(\"AE_RUN_CMD_MAX\", \"40000\"))
            if len(output) > _rc_max:
                output = output[:_rc_max] + f\"\\n... (출력 잘림, 총 {len(output)}자 중 {_rc_max}자 표시)\"
            return output or \"(출력 없음)\""""
src = patch(src, old_cmd, new_cmd, "run_command 제한 확대(10000→AE_RUN_CMD_MAX=40000)")

# ── 3) agent tool_results 크기 확대(2곳, line 673 & 973) ───────
# 두 곳은 주변 컨텍스트가 달라 각각 패치
old_tr1 = """                    tool_results.append({\"toolResult\": {\"toolUseId\": tool_id, \"content\": [{\"text\": tool_output[:15000]}]}})"""
new_tr1 = """                    _tr_max = int(os.environ.get(\"AE_TOOL_RESULT_MAX\", \"80000\"))
                    tool_results.append({\"toolResult\": {\"toolUseId\": tool_id, \"content\": [{\"text\": tool_output[:_tr_max]}]}})"""
src = patch(src, old_tr1, new_tr1, "run-agent tool_results 크기(15000→AE_TOOL_RESULT_MAX=80000)")

old_tr2 = """                tool_results.append({\"toolResult\": {\"toolUseId\": tid, \"content\": [{\"text\": tout[:15000]}]}})"""
new_tr2 = """                _tr_max = int(os.environ.get(\"AE_TOOL_RESULT_MAX\", \"80000\"))
                tool_results.append({\"toolResult\": {\"toolUseId\": tid, \"content\": [{\"text\": tout[:_tr_max]}]}})"""
src = patch(src, old_tr2, new_tr2, "orchestrator tool_results 크기(15000→AE_TOOL_RESULT_MAX=80000)")

# ── 4) orchestrator 에 max_tokens continue 로직 추가 ───────────
# 현재: tool_use_blocks 가 없으면 곧바로 break
# 변경: stop_reason == "max_tokens" 이고 텍스트가 있으면 이어 생성
old_orch = """            messages.append({\"role\": \"assistant\", \"content\": content_blocks})
            if not tool_use_blocks:
                break

            tool_results = []"""
new_orch = """            messages.append({\"role\": \"assistant\", \"content\": content_blocks})
            if not tool_use_blocks:
                if stop_reason == \"max_tokens\" and text_parts and turn < max_turns - 1:
                    print(f\"[Orchestrator] max_tokens 도달 — 이어서 생성 (task={task_id}, turn={turn+1})\")
                    messages.append({\"role\": \"user\", \"content\": [{\"text\": \"계속 이어서 작성해주세요.\"}]})
                    continue
                break

            tool_results = []"""
src = patch(src, old_orch, new_orch, "orchestrator max_tokens continue 로직 추가")

if src == orig:
    print("\n변경 없음 — 모든 패치가 이미 적용되었거나 원본을 찾지 못함.")
    sys.exit(0)

TARGET.write_text(src, encoding="utf-8")
print(f"\n✅ {TARGET} 패치 완료 ({len(orig)} → {len(src)}자)")
