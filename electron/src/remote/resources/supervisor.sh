#!/usr/bin/env bash
set -e
PIDFILE="$HOME/.agentic-editor/server.pid"
LOGFILE="$HOME/.agentic-editor/server.log"
VENV="$HOME/.agentic-editor/venv"
AE="$HOME/.agentic-editor/ai_engine"
cd "$AE"
while true; do
  "$VENV/bin/python" -m uvicorn server:app --host 127.0.0.1 --port 8765 >> "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  wait $!
  sleep 2
done
