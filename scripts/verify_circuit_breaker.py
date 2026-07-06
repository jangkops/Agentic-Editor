"""Verify the 5 changes to ai_engine/server.py.

Run from /Users/jcg/agentic-editor:
    .venv/bin/python scripts/verify_circuit_breaker.py
"""
import sys, os, json, asyncio, time

sys.path.insert(0, "/Users/jcg/agentic-editor")
os.environ.setdefault("AE_GENERATED_ROOT", "/tmp/ae-verify-gen")
os.makedirs(os.environ["AE_GENERATED_ROOT"], exist_ok=True)
# clean prior
gen_dir = "/tmp/ae-verify-gen/.generated"
if os.path.isdir(gen_dir):
    for f in os.listdir(gen_dir):
        try:
            os.remove(os.path.join(gen_dir, f))
        except Exception:
            pass

from ai_engine import server

def title(msg):
    print()
    print("=" * 60)
    print(msg)
    print("=" * 60)

title("TEST 1: circuit breaker trip + TTL expiry reset")
assert server._image_gen_is_circuit_broken() is False
print("  initial state: OK (not broken)")

detail = "stability.sd3-5-large-v1:0: HTTP 403 access denied execute-api:Invoke"
assert server._image_gen_error_is_access_denied(detail) is True
server._image_gen_trip_circuit(detail)
assert server._image_gen_is_circuit_broken() is True
print("  after trip: BROKEN (correct)")

server._IMAGE_GEN_CIRCUIT["disabled_at"] = time.time() - 301
assert server._image_gen_is_circuit_broken() is False
assert server._IMAGE_GEN_CIRCUIT["disabled_at"] == 0
print("  after TTL expiry: RESET (correct)")

server._image_gen_trip_circuit("HTTP 403 access denied")
assert server._image_gen_is_circuit_broken() is True

title("TEST 2: _tool_generate_image short-circuits when broken")
result = asyncio.run(server._tool_generate_image(
    {"prompt": "a sunset over mountains"},
    project_path="/tmp/ae-verify-gen",
))
parsed = json.loads(result)
assert parsed.get("error") == "circuit-breaker", f"expected circuit-breaker, got {parsed}"
print(f"  short-circuit response: {parsed}")
print("  Bedrock NOT called - instant response")

server._IMAGE_GEN_CIRCUIT["disabled_at"] = 0  # reset

title("TEST 3: _tool_generate_native_diagram (tree)")
tree_content = """src/
    components/
        button.js
        modal.js
    utils/
        helpers.js
tests/
    button.test.js
README.md"""
res = asyncio.run(server._tool_generate_native_diagram(
    "tree", "Project Structure", tree_content,
    project_path="/tmp/ae-verify-gen",
))
parsed = json.loads(res)
print(f"  result: {parsed}")
assert "path" in parsed, f"expected path, got {parsed}"
abs_path = os.path.join("/tmp/ae-verify-gen", parsed["path"])
assert os.path.isfile(abs_path)
assert os.path.getsize(abs_path) > 1000
assert parsed["model"] == "matplotlib (native)"
print(f"  tree PNG ok: {os.path.basename(abs_path)} ({os.path.getsize(abs_path)} bytes)")

title("TEST 4: _tool_generate_native_diagram (flow)")
res = asyncio.run(server._tool_generate_native_diagram(
    "flow", "Pipeline", "Ingest -> Validate -> Transform -> Output",
    project_path="/tmp/ae-verify-gen",
))
parsed = json.loads(res)
print(f"  result: {parsed}")
assert "path" in parsed
abs_path = os.path.join("/tmp/ae-verify-gen", parsed["path"])
assert os.path.isfile(abs_path)
assert "native-flow-" in parsed["path"]
print(f"  flow PNG ok")

title("TEST 5: _tool_generate_native_diagram (block)")
res = asyncio.run(server._tool_generate_native_diagram(
    "block", "Steps", "First step\nSecond step\nThird step",
    project_path="/tmp/ae-verify-gen",
))
parsed = json.loads(res)
assert "path" in parsed
print(f"  block PNG ok: {parsed['path']}")

title("TEST 6: _tool_generate_pdf with imageFile (Bedrock NOT called)")
native_files = [f for f in os.listdir("/tmp/ae-verify-gen/.generated") if f.startswith("native-tree-")]
assert native_files, "no native-tree png from test 3"
native_rel = ".generated/" + native_files[0]
print(f"  using pre-rendered: {native_rel}")

server._image_gen_trip_circuit("HTTP 403 access denied")

pdf_input = {
    "title": "Test Doc",
    "sections": [
        {"heading": "Section 1", "body": "Body 1", "imageFile": native_rel},
        {"heading": "Section 2", "body": "Body 2", "imageFile": native_rel},
    ],
}
t0 = time.time()
res = asyncio.run(server._tool_generate_pdf(
    pdf_input, project_path="/tmp/ae-verify-gen",
))
elapsed = time.time() - t0
parsed = json.loads(res)
print(f"  result: {parsed} (elapsed {elapsed:.2f}s)")
assert "path" in parsed, f"PDF gen failed: {parsed}"
abs_pdf = os.path.join("/tmp/ae-verify-gen", parsed["path"])
assert os.path.isfile(abs_pdf)
assert os.path.getsize(abs_pdf) > 5000
assert elapsed < 5.0
print(f"  PDF ok ({os.path.getsize(abs_pdf)} bytes, {elapsed:.2f}s)")

title("TEST 7: _tool_generate_pdf with imagePrompt + circuit broken (text-only fail-soft)")
pdf_input = {
    "title": "Visual Test",
    "sections": [
        {"heading": "Cool Picture", "body": "Some body text", "imagePrompt": "a cat"},
    ],
}
t0 = time.time()
res = asyncio.run(server._tool_generate_pdf(
    pdf_input, project_path="/tmp/ae-verify-gen",
))
elapsed = time.time() - t0
parsed = json.loads(res)
assert "path" in parsed, f"PDF gen failed: {parsed}"
print(f"  PDF still generated (text-only): {parsed['path']} ({elapsed:.2f}s)")
assert elapsed < 5.0

title("TEST 8: _looks_structural keyword detection")
assert server._looks_structural(description=u"\uc774 \ud504\ub85c\uc81d\ud2b8\uc758 \ud3f4\ub354 \uad6c\uc870\ub97c \ubd84\uc11d\ud574\uc8fc\uc138\uc694")
assert server._looks_structural(title="Folder Structure")
assert server._looks_structural(description=u"\ud750\ub984\ub3c4\ub97c \uadf8\ub824\uc918")
assert server._looks_structural(description="flowchart of build pipeline")
assert not server._looks_structural(description=u"\uace0\uc591\uc774 \uc0ac\uc9c4\uc744 \uadf8\ub824\uc918")
print("  keyword detector ok")

title("TEST 9: _normalize_doc_input preserves imageFile + aliases")
t, items = server._normalize_doc_input({
    "title": "T",
    "sections": [
        {"heading": "h1", "body": "b1", "imageFile": "foo.png"},
        {"heading": "h2", "body": "b2", "image_file": "bar.png"},
        {"heading": "h3", "body": "b3", "imagePath": "baz.png"},
    ],
}, default_kind="sections")
assert items[0]["imageFile"] == "foo.png"
assert items[1]["imageFile"] == "bar.png"
assert items[2]["imageFile"] == "baz.png"
print("  imageFile + aliases ok")

title("TEST 10: PPTX with imageFile (circuit broken)")
pptx_input = {
    "title": "Deck",
    "slides": [
        {"title": "Slide A", "bullets": ["one", "two"], "imageFile": native_rel},
    ],
}
t0 = time.time()
res = asyncio.run(server._tool_generate_pptx(
    pptx_input, project_path="/tmp/ae-verify-gen",
))
elapsed = time.time() - t0
parsed = json.loads(res)
assert "path" in parsed, f"PPTX failed: {parsed}"
print(f"  PPTX ok: {parsed['path']} ({elapsed:.2f}s)")

title("TEST 11: DOCX with imageFile (circuit broken)")
docx_input = {
    "title": "Word Doc",
    "sections": [
        {"heading": "Sec 1", "body": "Hello", "imageFile": native_rel},
    ],
}
t0 = time.time()
res = asyncio.run(server._tool_generate_docx(
    docx_input, project_path="/tmp/ae-verify-gen",
))
elapsed = time.time() - t0
parsed = json.loads(res)
assert "path" in parsed, f"DOCX failed: {parsed}"
print(f"  DOCX ok: {parsed['path']} ({elapsed:.2f}s)")

title("TEST 12: _force_generate_from_text - circuit broken + structural prompt")
# Reset gen dir for clean count
for f in list(os.listdir("/tmp/ae-verify-gen/.generated")):
    try:
        os.remove(os.path.join("/tmp/ae-verify-gen/.generated", f))
    except Exception:
        pass
server._image_gen_trip_circuit("HTTP 403 access denied")

t0 = time.time()
result = asyncio.run(server._force_generate_from_text(
    primary_tool="generate_pdf",
    target_files=["report.pdf"],
    title=u"\ud504\ub85c\uc81d\ud2b8 \uad6c\uc870 \ubd84\uc11d",  # 프로젝트 구조 분석
    description=u"\ud3f4\ub354 \uad6c\uc870\ub97c \uc2dc\uac01\ud654\ud574\uc8fc\uc138\uc694",  # 폴더 구조를 시각화해주세요
    final_text="""# 프로젝트 개요
이 프로젝트의 폴더 구조는 다음과 같습니다.

## 루트 디렉토리
src/
    components/
    utils/

tests/
    unit/
    integration/

## 빌드 시스템
build/
docs/
""",
    project_path="/tmp/ae-verify-gen",
    aws_profile="",
    bedrock_user="",
))
elapsed = time.time() - t0
print(f"  result count: {len(result)}, elapsed {elapsed:.2f}s")
assert len(result) >= 1, f"expected at least 1 file, got {result}"
assert elapsed < 10.0, f"too slow ({elapsed}s) - Bedrock probably called"
for rel, info in result:
    print(f"    {rel} ({info['size']} bytes, model={info.get('model')})")
# Confirm a native diagram PNG was created and shared
gen_files = os.listdir("/tmp/ae-verify-gen/.generated")
native_pngs = [f for f in gen_files if f.startswith("native-")]
assert native_pngs, f"no native diagram in {gen_files}"
print(f"  native diagram: {native_pngs[0]}")

print()
print("=" * 60)
print("ALL TESTS PASSED")
print("=" * 60)
