"""최신 모델 후보를 스트리밍으로 빠르게 검증 — 살아있는 모델 ID 찾기."""
import os, asyncio, sys
from ai_engine.gateway_module import GatewayClient

CANDIDATES = [
    "us.anthropic.claude-sonnet-4-6-20250929-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-opus-4-7-20251015-v1:0",
    "us.anthropic.claude-opus-4-1-20250805-v1:0",
    "us.anthropic.claude-opus-4-20250514-v1:0",
    "us.anthropic.claude-3-5-haiku-20241022-v1:0",
]

async def probe(gw, m):
    msgs = [{"role": "user", "content": [{"text": "OK"}]}]
    got = []
    async for evt in gw.stream_sse_realtime(model_id=m, messages=msgs, system_prompt=""):
        ty = evt.get("type") if isinstance(evt, dict) else ""
        if ty == "error":
            return f"ERROR: {str(evt.get('message',''))[:120]}"
        if ty == "content_block_delta":
            d = evt.get("delta", {})
            if isinstance(d, dict) and "text" in d:
                got.append(d["text"])
        if ty == "message_stop":
            break
    return f"OK text={''.join(got)[:40]!r}" if got else "NO_TEXT"

async def main():
    gw = GatewayClient(aws_profile=os.environ.get("AWS_PROFILE") or "bedrock-gw",
                       bedrock_user=os.environ.get("BEDROCK_USER","") or "cgjang")
    alive = []
    for m in CANDIDATES:
        try:
            r = await asyncio.wait_for(probe(gw, m), timeout=45)
        except asyncio.TimeoutError:
            r = "TIMEOUT"
        except Exception as e:
            r = f"EXC: {repr(e)[:120]}"
        print(f"{m:52s} → {r}")
        if r.startswith("OK"):
            alive.append(m)
    print("\nALIVE:", alive)
    return 0 if alive else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
