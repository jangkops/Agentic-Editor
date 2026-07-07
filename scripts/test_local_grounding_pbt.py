"""로컬 grounding 점수(게이트웨이 무관) 단위/속성 테스트.

실제 임베딩 로드는 느리므로 결정적 Mock 임베더로 순위·방어 성질을 고정한다.
"""
import numpy as np
from ai_engine.rag.verifier import _split_sentences, local_grounding_score


class MockEmb:
    """'login' 토큰 유무로 2D 벡터를 내는 결정적 임베더."""
    is_ready = True

    def embed_batch(self, texts):
        out = []
        for t in texts:
            has = 1.0 if "login" in t.lower() else 0.0
            out.append(np.array([has, 1.0 - has], dtype="float32"))
        return out


def test_split_sentences_basic():
    s = _split_sentences("첫 문장입니다. 두 번째 문장!\n세 번째 줄")
    assert len(s) == 3
    assert all(len(x) >= 5 for x in s)


def test_split_sentences_empty():
    assert _split_sentences("") == []
    assert _split_sentences(None) == []


def test_grounding_none_guards():
    emb = MockEmb()
    assert local_grounding_score("", ["ctx"], emb) is None
    assert local_grounding_score("answer text", [], emb) is None
    assert local_grounding_score("answer text", ["ctx"], None) is None


def test_grounding_grounded_higher_than_hallucinated():
    emb = MockEmb()
    ctx = ["def login(user): return verify(user)"]
    grounded = local_grounding_score("login 함수가 사용자 login 을 처리한다", ctx, emb)
    halluc = local_grounding_score("위성 통신으로 결제를 정산한다", ctx, emb)
    assert grounded is not None and halluc is not None
    assert grounded > halluc
    assert 0.0 <= halluc <= grounded <= 1.0


def test_grounding_score_in_range():
    emb = MockEmb()
    ctx = ["login handler", "database pool"]
    s = local_grounding_score("login 처리 로직입니다. database 연결도 합니다.", ctx, emb)
    assert s is not None
    assert 0.0 <= s <= 1.0
