"""
메모리 유지 테스트 — 단일 ↔ 병렬 자유 전환 시 대화 맥락이 유지되는지 직접 검증.

시나리오:
  Turn 1 (단일): "내 이름은 테스트유저, 좋아하는 숫자는 777. 기억해둬"
  Turn 2 (병렬): "방금 내가 알려준 이름과 숫자 뭐였지?" — 2개 모델 동시 호출
  Turn 3 (단일): "그럼 그 숫자에 3을 곱하면?" — 병렬 합의 맥락을 받아서 답해야 함
  Turn 4 (병렬): "내 이름과 최종 숫자를 조합한 문장 만들어줘"
"""
import asyncio
import json
import sys
import httpx

BASE = "http://localhost:8765"
SESSION_ID = "test-memory-flow-001"
MODEL_A = "anthropic.claude-sonnet-4-5-20250929-v1:0"
MODEL_B = "anthropic.claude-haiku-4-5-20251001-v1:0"


async def call_single(client, prompt, chat_history, model=MODEL_A):
    """단일 호출 (run-stream SSE)."""
    body = {
        "prompt": prompt,
        "model": model,
        "chatHistory": chat_history,
        "sessionId": SESSION_ID,
        "systemPrompt": "간결하게 답변하세요. 1-2문장.",
    }
    full_text = ""
    async with client.stream("POST", f"{BASE}/api/agents/run-stream", json=body, timeout=120) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
                if "text" in evt:
                    full_text += evt["text"]
                if "error" in evt:
                    print(f"  [ERR] {evt['error']}")
            except json.JSONDecodeError:
                pass
    return full_text


async def call_parallel(client, prompt, chat_history, models=None):
    """병렬 호출 (run-parallel SSE)."""
    if models is None:
        models = [
            {"slotId": "A", "modelId": MODEL_A, "systemPrompt": "간결하게 1-2문장."},
            {"slotId": "B", "modelId": MODEL_B, "systemPrompt": "간결하게 1-2문장."},
        ]
    body = {
        "prompt": prompt,
        "models": models,
        "chatHistory": chat_history,
        "sessionId": SESSION_ID,
    }
    results = {}
    async with client.stream("POST", f"{BASE}/api/agents/run-parallel", json=body, timeout=300) as r:
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if data == "[DONE]":
                break
            try:
                evt = json.loads(data)
                slot = evt.get("slotId", "?")
                results[slot] = evt
            except json.JSONDecodeError:
                pass
    return results


def build_consensus_and_combined(parallel_results):
    """병렬 결과를 chatHistory에 넣을 합본(hidden) + 합의 답변으로 변환."""
    combined_parts = []
    for slot, r in parallel_results.items():
        content = r.get("content", "")
        combined_parts.append(f"[Model {slot} ({r.get('modelId','')})]\n{content}")
    combined = "\n\n---\n\n".join(combined_parts)
    # 간단 합의: 첫 모델 답변을 합의로 가정 (실제 앱에서는 별도 LLM 호출로 합의)
    consensus = list(parallel_results.values())[0].get("content", "") if parallel_results else ""
    return combined, consensus


async def main():
    chat_history = []

    async with httpx.AsyncClient() as client:
        # ---------- Turn 1: 단일 ----------
        print("=" * 70)
        print("TURN 1 [단일] → '내 이름은 테스트유저, 좋아하는 숫자 777. 기억해둬'")
        print("=" * 70)
        prompt1 = "내 이름은 테스트유저야. 그리고 내가 가장 좋아하는 숫자는 777이야. 꼭 기억해둬."
        resp1 = await call_single(client, prompt1, chat_history)
        print(f"[assistant]\n{resp1}\n")
        chat_history.append({"role": "user", "content": prompt1})
        chat_history.append({"role": "assistant", "content": resp1})

        # ---------- Turn 2: 병렬 ----------
        print("=" * 70)
        print("TURN 2 [병렬 2모델] → '내 이름과 숫자 뭐였지?'")
        print("=" * 70)
        prompt2 = "방금 내가 알려준 내 이름과 좋아하는 숫자가 뭐였지? 정확히 말해줘."
        parallel_res = await call_parallel(client, prompt2, chat_history)
        for slot, r in parallel_res.items():
            print(f"[slot {slot} / {r.get('modelId','')}] status={r.get('status')}")
            print(f"  → {r.get('content','')[:300]}")
        combined2, consensus2 = build_consensus_and_combined(parallel_res)
        chat_history.append({"role": "user", "content": prompt2})
        # 프론트엔드 동작 모사: 합본(hidden) + 합의 둘 다 push (role, content만)
        chat_history.append({"role": "assistant", "content": combined2[:2000]})  # hidden 합본
        chat_history.append({"role": "assistant", "content": consensus2})       # consensus
        print()

        # ---------- Turn 3: 다시 단일 ----------
        print("=" * 70)
        print("TURN 3 [단일] → '그 숫자에 3을 곱하면?' (병렬 맥락 참조)")
        print("=" * 70)
        prompt3 = "방금 네가 말한 그 숫자에 3을 곱하면 얼마야? 계산 과정도 보여줘."
        resp3 = await call_single(client, prompt3, chat_history)
        print(f"[assistant]\n{resp3}\n")
        chat_history.append({"role": "user", "content": prompt3})
        chat_history.append({"role": "assistant", "content": resp3})

        # ---------- Turn 4: 병렬 ----------
        print("=" * 70)
        print("TURN 4 [병렬 2모델] → '내 이름과 최종 숫자 조합 문장'")
        print("=" * 70)
        prompt4 = "내 이름과, 방금 계산한 최종 숫자를 모두 포함해서 짧은 한 문장을 만들어줘."
        parallel_res4 = await call_parallel(client, prompt4, chat_history)
        for slot, r in parallel_res4.items():
            print(f"[slot {slot} / {r.get('modelId','')}] status={r.get('status')}")
            print(f"  → {r.get('content','')[:300]}")
        print()

        # ---------- 검증 ----------
        print("=" * 70)
        print("VERIFICATION — 메모리 유지 평가")
        print("=" * 70)
        checks = []
        # Turn 2에서 "테스트유저" + "777" 둘 다 언급했는가
        t2_text = " ".join(r.get("content", "") for r in parallel_res.values())
        checks.append(("T2 [병렬] 이름 기억", "테스트유저" in t2_text))
        checks.append(("T2 [병렬] 숫자 기억", "777" in t2_text))
        # Turn 3에서 2331 (777*3)
        checks.append(("T3 [단일→병렬 맥락] 2331 계산", "2331" in resp3 or "2,331" in resp3))
        # Turn 4에서 이름과 2331 모두
        t4_text = " ".join(r.get("content", "") for r in parallel_res4.values())
        checks.append(("T4 [병렬] 이름 포함", "테스트유저" in t4_text))
        checks.append(("T4 [병렬] 2331 포함", "2331" in t4_text or "2,331" in t4_text))

        passed = 0
        for name, ok in checks:
            mark = "✅" if ok else "❌"
            print(f"  {mark} {name}")
            if ok:
                passed += 1
        print(f"\n최종: {passed}/{len(checks)} 통과")
        return passed == len(checks)


if __name__ == "__main__":
    ok = asyncio.run(main())
    sys.exit(0 if ok else 1)
