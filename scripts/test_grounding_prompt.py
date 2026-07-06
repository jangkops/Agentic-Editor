"""근거 계약 프롬프트 포함/보존 검증 (Req 1.1, 1.4).

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_grounding_prompt.py -p no:cacheprovider -q
"""
from ai_engine.rag.context_builder import build_system_prompt


def test_grounding_contract_present_and_no_blanket_refusal_ban():
    # project_path 없이 호출 → RAG 컨텍스트는 비지만 지침 문구는 포함되어야 함
    sp = build_system_prompt(project_path="", query="test")
    # 근거 계약 문구 포함
    assert "근거 규칙" in sp
    assert "확인할 수 없습니다" in sp
    # 블랭킷 거부 금지 지시는 제거됨
    assert "거부 표현 절대 금지" not in sp
    # 도구/미디어 생성 지침은 보존
    assert "generate_pdf" in sp
    assert "generate_pptx" in sp
    assert "read_file" in sp
