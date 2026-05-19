#!/usr/bin/env bash
# Test live image generation through the local engine.
# Verifies the _resolve_callable_model_id patch works for image models.
set -e
cd "$(dirname "$0")/.."

PROMPT="${1:-a small cute robot, isometric, vector art}"
SIZE="${2:-1024x1024}"

# Direct tool execution path: simulate what the chat agent would do
# by calling the streamprocess endpoint with a generate_image tool prompt.
REQUEST_ID="$(uuidgen)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > /tmp/agen-req.json <<JSON
{
  "requestid": "${REQUEST_ID}",
  "requestdatetime": "${NOW}",
  "appid": "ai-editor",
  "userid": "cgjang",
  "costcenter": "MOGAM",
  "provider": "amazon-bedrock",
  "model": "anthropic.claude-haiku-4-5-20251001-v1:0",
  "awsProfile": "bedrock-gw",
  "bedrockUser": "cgjang",
  "projectPath": "$(pwd)",
  "prompt": "이미지를 하나 생성해줘. generate_image 도구를 사용해서 prompt='${PROMPT}', size='${SIZE}'로 호출하고, 결과 경로만 한 줄로 알려줘.",
  "systemPrompt": "당신은 도구를 적극 사용하는 어시스턴트입니다. 사용자가 이미지를 요청하면 즉시 generate_image 도구를 호출하세요."
}
JSON

echo "=== POST /api/agents/run-agent (request id ${REQUEST_ID}) ==="
curl -sN -m 90 -X POST http://127.0.0.1:8765/api/agents/run-agent \
  -H "Content-Type: application/json" \
  -d @/tmp/agen-req.json | tee /tmp/agen-stream.txt | head -120

echo
echo "=== Recently created files in .generated/ ==="
ls -lt .generated/ 2>/dev/null | head -5 || echo "(no .generated/ dir)"
