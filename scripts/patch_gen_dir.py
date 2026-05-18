#!/usr/bin/env python3
"""Replace gen_dir construction in-place, preserving each line's indent."""
import os
import re

PATH = os.path.join(os.path.dirname(__file__), '..', 'ai_engine', 'server.py')
with open(PATH, 'r') as f:
    code = f.read()

# Match: <indent>gen_dir = os.path.join(project_path, ".generated") if project_path else os.path.join(os.getcwd(), ".generated")
pattern = re.compile(
    r'^(?P<indent>[ \t]*)gen_dir = os\.path\.join\(project_path, "\.generated"\) if project_path else os\.path\.join\(os\.getcwd\(\), "\.generated"\)$',
    re.MULTILINE,
)

def repl(m):
    ind = m.group('indent')
    # All replacement lines use the same indent
    return (
        f'{ind}# Always use a locally-existing directory for generated media.\n'
        f'{ind}# Remote project_path may not exist locally; fall back to cwd.\n'
        f'{ind}_local_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()\n'
        f'{ind}gen_dir = os.path.join(_local_root, ".generated")'
    )

new_code, count = pattern.subn(repl, code)
print(f"Replaced {count} occurrences")
with open(PATH, 'w') as f:
    f.write(new_code)
