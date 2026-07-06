"""Pytest 공통 설정 — scripts/ 테스트를 헤르메틱(네트워크 비의존)하게 만든다.

배경: Vertex 이미지 키가 ~/.config 영구 저장소에 시드되면 VertexImageClient가
'자동 활성'되어, _tool_generate_pptx 직접 경로가 슬라이드마다 실제 Vertex(Gemini)
네트워크 호출을 시도한다. 구조/카운트만 검증하는 일반 테스트가 이로 인해 네트워크에
의존해 멈추거나 비용이 발생한다.

해결: 테스트 세션에서는 기본적으로 Vertex를 끈다(AE_PREFER_VERTEX_IMAGE=0). Vertex
동작 자체를 검증하는 테스트(test_pptx_vertex_first.py)는 테스트 내부에서 환경변수를
"1"로 명시 설정하고 클라이언트를 mock하므로 이 기본값에 영향받지 않는다.

setdefault가 아니라 명시 설정으로 두면 외부 환경에서 ON으로 켜둔 경우에도 테스트는
항상 hermetic하게 동작한다. 개별 테스트가 os.environ["AE_PREFER_VERTEX_IMAGE"]="1"로
덮어쓰면 그 테스트에서만 켜진다.
"""
import os

# HTML 디자인 슬라이드도 기본 OFF — 브리지가 없으면 어차피 폴백이지만, 명시적으로
# 꺼서 테스트가 _call_bridge mock 여부에 의존하지 않도록 한다(개별 테스트가 재정의 가능).
os.environ["AE_PREFER_VERTEX_IMAGE"] = "0"
# 편집 가능 다이어그램 LLM 구조화도 테스트에서는 기본 OFF(게이트웨이 네트워크 비의존).
# 구조화를 검증하는 테스트는 내부에서 "1"로 설정하고 _llm_structure_native_diagram을 mock한다.
os.environ["AE_PREFER_EDITABLE_DIAGRAM"] = "0"
# 목차(TOC) 자동 생성은 슬라이드 수를 바꾸므로, 카운트 기반 테스트의 안정성을 위해
# 기본 OFF. TOC 동작을 검증하는 테스트는 내부에서 AE_PPTX_TOC=1로 opt-in한다.
os.environ["AE_PPTX_TOC"] = "0"
