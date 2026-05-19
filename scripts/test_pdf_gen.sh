#!/usr/bin/env bash
# Live test: have the agent invoke generate_pdf and confirm a file lands in
# .generated/. Verifies the patched _execute_tool credential threading does
# not regress the existing PDF flow.
set -e
cd "$(dirname "$0")/.."

REQUEST_ID="$(uuidgen)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > /tmp/agen-pdf-req.json <<JSON
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
  "prompt": "PDF를 하나 생성해줘. generate_pdf 도구로 title='live-pdf-test', sections=[{heading:'테스트', body:'정상 동작 확인용 PDF'}]를 호출하고 결과 경로만 알려줘.",
  "systemPrompt": "당신은 도구를 적극 사용하는 어시스턴트입니다. 사용자가 PDF를 요청하면 즉시 generate_pdf 도구를 호출하세요."
}
JSON

echo "=== POST /api/agents/run-agent (rid ${REQUEST_ID}) ==="
curl -sN -m 90 -X POST http://127.0.0.1:8765/api/agents/run-agent \
  -H "Content-Type: application/json" \
  -d @/tmp/agen-pdf-req.json | tee /tmp/agen-pdf-stream.txt | head -80

echo
echo "=== Recently created files in .generated/ ==="
ls -lt .generated/ 2>/dev/null | head -8 || echo "(no .generated/ dir)"
