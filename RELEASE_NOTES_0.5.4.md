# Mogam Works v0.5.4 — 릴리스 노트

## 개요
RAG 검색 품질의 **폴백 하한을 어휘검색(TF-IDF)에서 의미검색(LSA)으로 상승**시킨
릴리스입니다. 정상 경로(fastembed 다국어 신경망)는 불변이며, 실패 시 품질 저하를 완화합니다.

## RAG 품질 개선

### fastembed 미가용 시 폴백을 TF-IDF → LSA(의미검색)로 개선
- **문제**: 검색 기본 임베딩은 fastembed(다국어 신경망, 의미검색)이지만, 동결(DMG) 환경에서
  ONNX 런타임 로드가 실패하면 **약한 어휘 TF-IDF로 조용히 폴백**해 배포본 RAG 품질이
  키워드 매칭 수준으로 저하됐다(사용자 인지 불가).
- **개선**: fastembed 미가용 시 **LSA(TruncatedSVD 잠재의미 임베딩)** 로 폴백한다. LSA는
  sklearn만 사용(신규 의존성 0), 오프라인·동결안전이며, 동의어/문맥을 포착하는 의미검색을
  제공한다. LSA fit 실패 시에도 hybrid_search 차원 가드가 인덱서 어휘검색을 하한으로
  남겨 무손상을 보장한다.
- **옵트아웃**: `AE_EMBED_FALLBACK=tfidf` 로 기존 어휘 폴백을 명시 선택 가능.
- **효과**: 정상 경로(fastembed)는 동작 불변. fastembed 로드 실패 시에도 의미검색이 유지되어
  배포본 RAG 품질 하한이 "어휘 → 잠재의미"로 상승.

## 스택 성숙도(감사 결과)
- **LangGraph**: DAG planner·evaluator 재계획·다중워커 종합·스트리밍+converse 폴백 완비.
- **RAG**: fastembed(다국어 신경망) 기본 → LSA(의미) 폴백 → 어휘 하한. hybrid search(RRF),
  reranker, query expansion, faithfulness 검증 + 로컬 grounding, eval 지표 완비.
- **VectorDB**: numpy 코사인 스토어 + 캐싱 + 차원 가드.

## 알려진 제한
- Opus 는 게이트웨이 스트리밍 미지원으로 reasoning 메타 노드에 부적합(Sonnet 사용).
- LSA 폴백은 코퍼스 기반 잠재의미라 순수 KR질의↔EN코드 교차언어는 fastembed보다 약함
  (정상 경로 fastembed가 교차언어 담당, LSA는 fastembed 실패 시의 의미검색 하한).

## 보안/정합
- LLM 호출은 Bedrock Gateway 경유만. 자격증명 미저장. 무한 종료 방지.
