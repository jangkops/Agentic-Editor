"""게이트웨이 경유 end-to-end 검증 — 단일/병렬/합의 전체를 한 번에 실측.

게이트웨이가 정상 응답하는 환경(사내망/앱 서버)에서 실행하면, 세 경로 각각을 실제
게이트웨이로 1회씩 돌려 결과와 모든 품질 메타데이터를 출력한다. 개발 샌드박스처럼
게이트웨이 모델 응답이 지연되면 각 단계가 timeout으로 정직하게 사유를 남긴다.

실행:
  AWS_PROFILE=bedrock-gw BEDROCK_USER=<name> \
  PYTHONPATH=. ai_engine/.venv/bin/python scripts/verify_e2e_gateway.py

옵션 env:
  AE_E2E_QUERY        검증 질의(기본: RRF 관련 질의)
  AE_E2E_TIMEOUT_MS   단계별 게이트웨이 타임아웃(기본 300000=5분)
  AE_GEN_MODEL        생성 모델(기본 sonnet)
  AE_PARALLEL_MODELS  병렬 모델 CSV(기본 sonnet,opus)
"""
import os
import sys
import json
import asyncio

os.environ.setdefault("AE_ANSWER_QUALITY", "1")
os.environ.setdefault("AE_VERIFY", "1")

from ai_engine.gateway_module import GatewayClient
from ai_engine.rag.gw_text import converse_text
from ai_engine.rag.answer_quality import enhance_answer, _get_local_embedder
from ai_engine.rag.consensus_select import rank_by_self_consistency
from ai_engine.rag.cross_verify import cross_verify_consensus

QUERY = os.environ.get("AE_E2E_QUERY") or "rrf_fuse 함수는 무엇을 하고 기본 k 값은 얼마인가?"
CTX = ("[근거] ai_engine/rag/hybrid_search.py\n"
       "rrf_fuse(rank_lists, k=60): 여러 검색기의 순위 리스트를 Reciprocal Rank Fusion으로 "
       "융합한다. RRF(d)=sum 1/(k+rank). 점수 스케일에 무관하며 k 기본값은 60이다.")
GEN_MODEL = os.environ.get("AE_GEN_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0")
PARALLEL_MODELS = [m.strip() for m in (os.environ.get("AE_PARALLEL_MODELS") or
    "anthropic.claude-3-5-sonnet-20241022-v2:0,anthropic.claude-3-opus-20240229-v1:0").split(",") if m.strip()]
TIMEOUT = float(os.environ.get("AE_E2E_TIMEOUT_MS", "300000")) / 1000.0


def _gw():
    return GatewayClient(aws_profile=os.environ.get("AWS_PROFILE", ""),
                         bedrock_user=os.environ.get("BEDROCK_USER", ""))


async def _generate(gw, model, query, context):
    """근거를 시스템 컨텍스트로 준 단일 답변 생성."""
    msgs = [{"role": "user", "content": [{"text":
        f"다음 근거만 사용해 질문에 답하세요.\n\n{context}\n\n질문: {query}"}]}]
    return await converse_text(gw, model, msgs, timeout=TIMEOUT)


async def verify_single(gw) -> dict:
    """단일 호출: 답변 생성 + 근거/충실도/grounding 전체 메타."""
    out = {"path": "single", "model": GEN_MODEL}
    try:
        answer = await asyncio.wait_for(_generate(gw, GEN_MODEL, QUERY, CTX), timeout=TIMEOUT + 20)
        out["answer"] = answer[:500]
        res = await enhance_answer(answer, context_text=CTX, retrieved_chunks=None,
                                   gw=gw, env=os.environ)
        out["metadata"] = res.get("metadata")
        out["ok"] = bool(answer.strip())
    except asyncio.TimeoutError:
        out["ok"] = False
        out["error"] = f"gateway timeout after {TIMEOUT:.0f}s"
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:300]
    return out


async def verify_parallel(gw) -> dict:
    """병렬 호출: 여러 모델 답변 + self-consistency 대표 선별."""
    out = {"path": "parallel", "models": PARALLEL_MODELS}
    answers, per = [], []
    for m in PARALLEL_MODELS:
        try:
            a = await asyncio.wait_for(_generate(gw, m, QUERY, CTX), timeout=TIMEOUT + 20)
            answers.append(a)
            per.append({"model": m, "ok": bool(a.strip()), "answer": a[:300]})
        except Exception as e:
            answers.append("")
            per.append({"model": m, "ok": False, "error": str(e)[:200]})
    out["candidates"] = per
    ranking = rank_by_self_consistency(answers, _get_local_embedder())
    out["selfConsistency"] = ranking
    out["ok"] = ranking is not None and any(p["ok"] for p in per)
    return out


async def verify_consensus(gw) -> dict:
    """합의: 병렬 후보들을 검증자 모델로 교차 채점(충실도/충돌)."""
    out = {"path": "consensus"}
    agents = []
    for i, m in enumerate(PARALLEL_MODELS):
        try:
            a = await asyncio.wait_for(_generate(gw, m, QUERY, CTX), timeout=TIMEOUT + 20)
            agents.append({"role": f"agent{i}", "title": m, "summary": a})
        except Exception as e:
            agents.append({"role": f"agent{i}", "title": m, "summary": f"(실패: {str(e)[:100]})"})
    rep = await cross_verify_consensus(gw, GEN_MODEL, QUERY, agents, timeout=TIMEOUT)
    out["crossVerify"] = rep.as_dict()
    out["ok"] = not rep.degraded
    return out


async def main():
    print(f"[E2E] profile={os.environ.get('AWS_PROFILE')} bedrock_user={os.environ.get('BEDROCK_USER')}")
    print(f"[E2E] query={QUERY!r} timeout={TIMEOUT:.0f}s")
    gw = _gw()
    results = {}
    for name, fn in (("single", verify_single), ("parallel", verify_parallel),
                     ("consensus", verify_consensus)):
        print(f"\n=== {name.upper()} 검증 중 (게이트웨이 경유) ===")
        results[name] = await fn(gw)
        print(json.dumps(results[name], ensure_ascii=False, indent=2))
    ok_all = all(v.get("ok") for v in results.values())
    print("\n=== 종합 ===")
    print(json.dumps({k: {"ok": v.get("ok"), "error": v.get("error")} for k, v in results.items()},
                     ensure_ascii=False, indent=2))
    print("✅ 전체 게이트웨이 경유 검증 성공" if ok_all else
          "⚠️ 일부 경로 미완료 — 위 error 사유 확인(게이트웨이 응답 지연 시 timeout)")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
