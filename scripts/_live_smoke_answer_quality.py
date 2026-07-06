"""라이브 게이트웨이 answer_quality 실측 스모크 — 단발 호출.

프로파일 후보를 순회하며 gw.converse가 되는 첫 프로파일로 enhance_answer(AE_VERIFY on)를
1회 실행해 실제 충실도 점수를 받는다. 실패는 정직하게 사유 출력.
"""
import os, asyncio, sys
os.environ["AE_ANSWER_QUALITY"] = "1"
os.environ["AE_VERIFY"] = "1"

from ai_engine.gateway_module import GatewayClient
from ai_engine.rag.answer_quality import enhance_answer

CTX = ("[근거]\nai_engine/rag/hybrid_search.py:200-220\n"
       "rrf_fuse(rank_lists, k=60): 여러 검색기의 순위 리스트를 Reciprocal Rank Fusion으로 "
       "융합한다. RRF(d)=sum 1/(k+rank). 점수 스케일에 무관.")
ANSWER = ("rrf_fuse 함수는 k=60 기본값으로 여러 순위 리스트를 RRF로 융합하며, "
          "점수 절대값이 아니라 순위 기반이라 스케일 차이에 견고합니다 "
          "(ai_engine/rag/hybrid_search.py:200-220).")
MODEL = "anthropic.claude-3-5-sonnet-20241022-v2:0"


async def try_profile(profile):
    gw = GatewayClient(aws_profile=profile, bedrock_user=os.environ.get("BEDROCK_USER", ""))
    # 최소 converse 1회로 연결 확인
    res = await enhance_answer(ANSWER, context_text=CTX, retrieved_chunks=None,
                              gw=gw, env=os.environ)
    return res


async def main():
    profiles = [os.environ.get("AWS_PROFILE") or "", "bedrock-gw", "mg-infra-admin", "default"]
    tried = []
    for p in [x for x in profiles if x]:
        if p in tried:
            continue
        tried.append(p)
        try:
            res = await asyncio.wait_for(try_profile(p), timeout=60)
            meta = res.get("metadata") or {}
            f = meta.get("faithfulness") or {}
            print(f"[profile={p}] metadata={meta}")
            if f and not f.get("degraded") and f.get("score") is not None:
                print(f"✅ LIVE OK — 충실도 실측 score={f['score']} (profile={p})")
                return 0
            else:
                print(f"⚠️ profile={p}: degraded/무점수 → {f.get('feedback','')[:200]}")
        except Exception as e:
            print(f"❌ profile={p} 실패: {str(e)[:300]}")
    print("=== 라이브 검증 미완료: 어떤 프로파일도 게이트웨이 converse 성공 못함 ===")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
