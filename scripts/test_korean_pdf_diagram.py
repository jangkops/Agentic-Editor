"""실제 PDF + native diagram 생성 통합 테스트 — 한글이 깨지지 않고 다이어그램이 들어가는지 확인."""
import sys, os, asyncio, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_engine'))

from server import (
    _tool_generate_native_diagram,
    _tool_generate_pdf,
    _tool_generate_pptx,
    _resolve_local_root,
)

PROJECT = os.path.join(tempfile.gettempdir(), 'ae_diagram_test')
os.makedirs(PROJECT + '/.generated', exist_ok=True)


async def main():
    tree_content = """ai_engine/
  agent_system/
    agent_graph.py
    chat_model_adapter.py
  agents/
    coordinator.py
    planner_agent.py
  rag/
    embedder.py
    indexer.py
  server.py
  gateway_module.py
src/
  components/
    file-preview-panel.js
  main.js
  index.html
electron/
  preload.js
  src/
    ipc-fs-handlers.js"""

    res = await _tool_generate_native_diagram(
        diagram_type="tree",
        title="프로젝트 구조 다이어그램",
        content=tree_content,
        project_path=PROJECT,
    )
    print("native diagram result:", res[:300])
    parsed = json.loads(res)
    if "error" in parsed:
        print("FAIL: native diagram failed")
        return
    diagram_path = parsed["path"]
    abs_diagram = os.path.join(_resolve_local_root(PROJECT), diagram_path)
    print(f"diagram saved: {abs_diagram}, size={os.path.getsize(abs_diagram)}")

    pdf_input = {
        "title": "프로젝트 아키텍처 분석 보고서",
        "sections": [
            {
                "heading": "프로젝트 개요",
                "body": "이 프로젝트는 Electron + Python FastAPI 기반의 AI 에디터입니다.\n주요 컴포넌트는 ai_engine/, src/, electron/ 세 갈래로 구성되어 있습니다.",
                "imageFile": diagram_path,
            },
            {
                "heading": "디렉토리 구조",
                "body": "각 폴더는 명확한 역할 분담을 가지고 있으며 한글 주석으로 문서화되어 있습니다.",
            },
        ],
    }
    pdf_res = await _tool_generate_pdf(pdf_input, PROJECT)
    print("\nPDF result:", pdf_res[:400])
    pdf_parsed = json.loads(pdf_res)
    if "error" in pdf_parsed:
        print("FAIL: PDF generation failed")
        return
    abs_pdf = os.path.join(_resolve_local_root(PROJECT), pdf_parsed["path"])
    pdf_size = os.path.getsize(abs_pdf)
    print(f"PDF saved: {abs_pdf}, size={pdf_size} bytes ({pdf_size/1024:.1f} KB)")
    print(f"PDF model field: {pdf_parsed.get('model', 'MISSING')}")
    if pdf_size < 30000:
        print("WARN: PDF size suspiciously small — image may not be embedded")
    else:
        print("OK: PDF size reasonable for image-embedded document")

    pptx_input = {
        "title": "프로젝트 구조 PPTX 슬라이드 (이미지 포함)",
        "slides": [
            {
                "title": "Agentic Editor 아키텍처",
                "bullets": [
                    "Electron + Vanilla JS 프론트엔드",
                    "Python FastAPI + Bedrock Gateway 백엔드",
                    "70+ LLM 모델 단일 인터페이스",
                ],
                "imageFile": diagram_path,
            },
            {
                "title": "프로젝트 개요",
                "bullets": [
                    "병렬 추론 + 합의 도출",
                    "RAG: TF-IDF + BM25 하이브리드",
                    "실시간 협업 fast-path",
                ],
                "imageFile": diagram_path,
            },
        ],
    }
    pptx_res = await _tool_generate_pptx(pptx_input, PROJECT)
    print("\nPPTX result:", pptx_res[:400])
    pptx_parsed = json.loads(pptx_res)
    if "error" in pptx_parsed:
        print("FAIL: PPTX generation failed")
        return
    abs_pptx = os.path.join(_resolve_local_root(PROJECT), pptx_parsed["path"])
    pptx_size = os.path.getsize(abs_pptx)
    print(f"PPTX saved: {abs_pptx}, size={pptx_size} bytes ({pptx_size/1024:.1f} KB)")
    print(f"PPTX model field: {pptx_parsed.get('model', 'MISSING')}")

    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(abs_pdf)
        full_text = ""
        for page in reader.pages:
            full_text += page.extract_text() or ""
        print(f"\nPDF text extracted: {len(full_text)} chars")
        has_proj = '프로젝트' in full_text
        has_dir = '디렉토리' in full_text
        print(f"Korean keyword '프로젝트' in PDF: {has_proj}")
        print(f"Korean keyword '디렉토리' in PDF: {has_dir}")
        if has_proj:
            print("OK: Korean text properly embedded in PDF")
        else:
            print("FAIL: Korean text NOT found in PDF — font issue")
    except ImportError:
        print("PyPDF2 not installed — skipping text extraction check")

asyncio.run(main())
