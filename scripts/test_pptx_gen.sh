#!/usr/bin/env bash
# Live test: PPTX generation through agent loop.
set -e
cd "$(dirname "$0")/.."

REQUEST_ID="$(uuidgen)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > /tmp/agen-pptx-req.json <<JSON
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
  "prompt": "PPTX를 만들어줘. generate_pptx 도구로 title='라이브 검증', slides=[{title:'슬라이드1',bullets:['포인트A','포인트B']},{title:'슬라이드2',bullets:['포인트C']}]를 호출하고 결과 경로만 알려줘. imagePrompt는 빼고.",
  "systemPrompt": "당신은 도구를 적극 사용하는 어시스턴트입니다."
}
JSON

echo "=== POST /api/agents/run-agent (rid ${REQUEST_ID}) ==="
curl -sN -m 90 -X POST http://127.0.0.1:8765/api/agents/run-agent \
  -H "Content-Type: application/json" \
  -d @/tmp/agen-pptx-req.json | tee /tmp/agen-pptx-stream.txt | head -80

echo
echo "=== Recently created files in .generated/ ==="
ls -lt .generated/ 2>/dev/null | head -8 || echo "(no .generated/ dir)"
