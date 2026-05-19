#!/usr/bin/env bash
# Live test: generate_pptx with imagePrompt — verify slide is kept even if
# image gen fails (Req 5.3).
set -e
cd "$(dirname "$0")/.."

REQUEST_ID="$(uuidgen)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat > /tmp/agen-pptximg-req.json <<JSON
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
  "prompt": "PPTX 생성: title='이미지프롬프트 테스트', 첫 슬라이드 title='소개' bullets=['항목1','항목2'] imagePrompt='blue robot mascot, isometric'. 두 번째 슬라이드 title='요약' bullets=['결론']. generate_pptx 도구 즉시 호출.",
  "systemPrompt": "당신은 도구를 적극 사용하는 어시스턴트입니다. 사용자가 도큐멘트 생성을 요청하면 generate_pptx 도구를 즉시 호출하세요."
}
JSON

echo "=== POST /api/agents/run-agent (rid ${REQUEST_ID}) ==="
curl -sN -m 120 -X POST http://127.0.0.1:8765/api/agents/run-agent \
  -H "Content-Type: application/json" \
  -d @/tmp/agen-pptximg-req.json | tee /tmp/agen-pptximg-stream.txt | head -120

echo
echo "=== Recently created files in .generated/ ==="
ls -lt .generated/ 2>/dev/null | head -5 || echo "(no .generated/ dir)"
