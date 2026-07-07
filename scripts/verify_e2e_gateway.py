"""게이트웨이 경유 E2E 통합 검증 — 단일 / 병렬 / 합의 각 1케이스 + 품질 메타 전체.

실제 Bedrock 게이트웨이를 경유해 세 경로를 한 번씩 실행하고, 각 경로의 결과와
품질 메타데이터(citation·faithfulness·grounding / self-consistency / cross-verify)가
제대로 산출되는지 확인한다. 실패는 정직하게 사유 출력.

실행:
  AWS_PROFILE=bedrock-gw PYTHONPATH=. ai_engine/.venv/bin/python scripts/verify_e2e_gateway.py
선택 env: BEDROCK_USER, AE_GEN_MODEL, AE_VERIFY_MODEL, AE_PARALLEL_MODELS(콤마구분)
"""
import os
import sys
import json
import asyncio

os.environ.setdefault("AE_ANSWER_QUALITY", "1")
os.environ.setdefault("AE_VERIFY", "1")
os.environ.setdefault("AE_VERIFY_TIMEOUT_MS", "120000")

from ai_engine.gateway_module import GatewayClient
from ai_engine.rag.gw_text import converse_text
from ai_engine.rag.context_builder import get_searcher
from ai_engine.rag.answer_quality import enhance_answer, _get_local_embedder
from ai_engine.rag.consensus_select import rank_by_self_consistency
from ai_engine.rag.cross_verify import cross_verify_consensus

PROJECT = os.environ.get("AE_E2E_PROJECT", os.path.join("ai_engine", "rag"))
QUERY = os.environ.get("AE_E2E_QUERY",
                       "하이브리드 검색에서 RRF(reciprocal rank fusion) 융합은 어떻게 동작하나?")
GEN_MODEL = os.environ.get("AE_GEN_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
VERIFY_MODEL = os.environ.get("AE_VERIFY_MODEL", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
PARALLEL_MODELS = [m.strip() for m in os.environ.get(
    "AE_PARALLEL_MODELS",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0,us.anthropic.claude-sonnet-4-20250514-v1:0"
).split(",") if m.strip()]

GEN_TIMEOUT = float(os.environ.get("AE_GEN_TIMEOUT_S", "45"))
# AE_E2E_ONLY=single|parallel|consensus 로 한 경로만 실행(빠른 단일 케이스 확인).
ONLY = (os.environ.get("AE_E2E_ONLY") or "all").strip().lower()

# 파이프(| tail)로 실행해도 진행이 실시간으로 보이도록 라인 버퍼링 강제.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


def _mk_gw():
    return GatewayClient(aws_profile=os.environ.get("AWS_PROFILE") or "bedrock-gw",
                         bedrock_user=os.environ.get("BEDROCK_USER", "") or "cgjang")


def _build_context(chunks) -> str:
    parts = []
    for c, _score in chunks:
        parts.append(f"{c.file_path}:{c.start_line}-{c.end_line}\n{c.content[:1200]}")
    return "\n\n".join(parts)


async def _gen(gw, query, context):
    sys_p = ("당신은 코드베이스 전문가입니다. 아래 [근거]만 사용해 한국어로 정확히 "
             "답하고, 각 주장 끝에 (파일:라인) 형식으로 출처를 표기하세요. 근거에 없으면 "
             "모른다고 하세요.")
    user = f"[질문]\n{query}\n\n[근거]\n{context[:8000]}"
    msgs = [{"role": "user", "content": [{"text": user}]}]
    return await asyncio.wait_for(
        converse_text(gw, GEN_MODEL, msgs, system_prompt=sys_p, timeout=GEN_TIMEOUT),
        timeout=GEN_TIMEOUT + 10)


async def run_single(gw, chunks, context):
    print("\n===== [1] 단일 모델 호출 (게이트웨이 경유) =====")
    ans = await _gen(gw, QUERY, context)
    print(f"[답변 {len(ans)}자]\n{ans[:600]}\n...")
    res = await enhance_answer(ans, context_text=context, retrieved_chunks=chunks,
                               gw=gw, env=os.environ)
    meta = res.get("metadata") or {}
    print("[품질 메타]", json.dumps(meta, ensure_ascii=False, indent=2))
    return ans, meta


async def run_parallel(gw, context):
    print("\n===== [2] 병렬 모델 호출 (게이트웨이 경유) =====")
    async def one(model):
        try:
            t = await _gen(gw, QUERY, context)
            return {"model": model, "status": "done", "content": t}
        except Exception as e:
            return {"model": model, "status": "error", "content": "", "error": str(e)[:200]}
    # 서로 다른 모델로 병렬
    async def one_m(model):
        sys_p = "당신은 코드베이스 전문가입니다. 아래 근거로 한국어로 간결히 답하세요."
        user = f"[질문]\n{QUERY}\n\n[근거]\n{context[:8000]}"
        msgs = [{"role": "user", "content": [{"text": user}]}]
        try:
            t = await asyncio.wait_for(
                converse_text(gw, model, msgs, system_prompt=sys_p, timeout=GEN_TIMEOUT),
                timeout=GEN_TIMEOUT + 10)
            return {"model": model, "status": "done", "content": t}
        except Exception as e:
            return {"model": model, "status": "error", "content": "", "error": str(e)[:200]}

    results = await asyncio.gather(*(one_m(m) for m in PARALLEL_MODELS))
    for r in results:
        st = r["status"]
        prev = (r.get("content") or r.get("error") or "")[:200]
        print(f"  - {r['model']} [{st}] {prev}")
    done = [r for r in results if r["status"] == "done" and r["content"]]
    ranking = None
    if len(done) >= 2:
        ranking = rank_by_self_consistency([r["content"] for r in done], _get_local_embedder())
        print("[self-consistency 랭킹]", json.dumps(ranking, ensure_ascii=False))
    else:
        print(f"[self-consistency] 후보 부족(done={len(done)}) — 랭킹 생략")
    return results, ranking


async def run_consensus(gw, results):
    print("\n===== [3] 합의 도출 — 교차 검증 (게이트웨이 경유) =====")
    agents = [{"role": f"agent{i}", "title": r["model"], "summary": r["content"]}
              for i, r in enumerate(results) if r["status"] == "done" and r["content"]]
    if len(agents) < 2:
        print(f"[cross-verify] 후보 부족(done={len(agents)}) — 생략")
        return None
    rep = await cross_verify_consensus(gw, VERIFY_MODEL, QUERY, agents, timeout=GEN_TIMEOUT)
    print("[cross-verify 결과]", json.dumps(rep.as_dict(), ensure_ascii=False, indent=2))
    return rep


async def main():
    print(f"프로젝트={PROJECT}  질의={QUERY!r}")
    print(f"생성모델={GEN_MODEL}  검증모델={VERIFY_MODEL}  병렬={PARALLEL_MODELS}")
    gw = _mk_gw()

    # RAG 검색 (로컬 neural, 게이트웨이 불필요) — 근거 확보
    searcher = get_searcher(PROJECT, gateway_client=gw)
    chunks = searcher.search(QUERY, top_k=6, score_threshold=0.0)
    context = _build_context(chunks)
    print(f"\n[RAG] 근거 {len(chunks)}개 청크 검색 — 파일: {sorted(set(c.file_path for c,_ in chunks))}")

    ok = {}
    if ONLY in ("all", "single"):
        ok["single"] = False
        try:
            _ans, meta = await run_single(gw, chunks, context)
            ok["single"] = bool(_ans)
        except Exception as e:
            print(f"❌ 단일 호출 실패: {str(e)[:300]}", flush=True)

    results = None
    if ONLY in ("all", "parallel", "consensus"):
        ok["parallel"] = False
        try:
            results, ranking = await run_parallel(gw, context)
            ok["parallel"] = any(r["status"] == "done" for r in (results or []))
        except Exception as e:
            print(f"❌ 병렬 호출 실패: {str(e)[:300]}", flush=True)

    if ONLY in ("all", "consensus"):
        ok["consensus"] = False
        try:
            if results:
                rep = await run_consensus(gw, results)
                ok["consensus"] = rep is not None and not rep.degraded
        except Exception as e:
            print(f"❌ 합의 도출 실패: {str(e)[:300]}", flush=True)

    print("\n===== 전체 확인 요약 =====")
    print(json.dumps(ok, ensure_ascii=False))
    return 0 if all(ok.values()) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
