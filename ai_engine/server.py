"""FastAPI server — AI Editor backend."""
import os
import json
import uuid
import asyncio
import subprocess
import re
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

__version__ = "0.3.0"

app = FastAPI(title="AI Editor Engine", version=__version__)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ===== Agent Tool Definitions =====
AGENT_TOOLS = {
    "tools": [
        {
            "toolSpec": {
                "name": "read_file",
                "description": "파일 내용을 읽습니다. 프로젝트 내 모든 파일을 읽을 수 있습니다.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "읽을 파일의 절대 경로 또는 프로젝트 상대 경로"}
                        },
                        "required": ["path"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "write_file",
                "description": "파일에 내용을 씁니다. 새 파일 생성 또는 기존 파일 덮어쓰기.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "쓸 파일의 절대 경로"},
                            "content": {"type": "string", "description": "파일에 쓸 내용"}
                        },
                        "required": ["path", "content"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "list_directory",
                "description": "디렉토리의 파일/폴더 목록을 반환합니다.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "탐색할 디렉토리 경로"}
                        },
                        "required": ["path"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "run_command",
                "description": "터미널 명령어를 실행하고 결과를 반환합니다. git, npm, pip 등 모든 CLI 도구 사용 가능.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "실행할 셸 명령어"},
                            "cwd": {"type": "string", "description": "작업 디렉토리 (선택)"}
                        },
                        "required": ["command"]
                    }
                }
            }
        },
        {
            "toolSpec": {
                "name": "search_files",
                "description": "프로젝트 내 파일에서 텍스트를 검색합니다 (grep).",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "검색할 텍스트 또는 정규식"},
                            "path": {"type": "string", "description": "검색할 디렉토리 경로"},
                            "file_pattern": {"type": "string", "description": "파일 패턴 (예: *.py, *.js)"}
                        },
                        "required": ["query", "path"]
                    }
                }
            }
        }
                ,
            {
                "toolSpec": {
                    "name": "generate_image",
                    "description": "Generate an image from a text prompt using Bedrock image models. Saves PNG to .generated/. Supports models: Stability SD3.5, Stable Image Core, Amazon Titan Image v2.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "prompt": {"type": "string", "description": "Image description (max 2000 chars)"},
                                "size": {"type": "string", "description": "Image size like 1024x1024, 1024x768, 768x1024", "default": "1024x1024"},
                                "style": {"type": "string", "description": "Optional Stability style preset (photographic, cinematic, anime, etc.)"}
                            },
                            "required": ["prompt"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "generate_pdf",
                    "description": "Generate a multi-page PDF document from structured sections. Saves to .generated/.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "Document title (cover page)"},
                                "sections": {
                                    "type": "array",
                                    "description": "List of sections, each with heading and body text",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "heading": {"type": "string"},
                                            "body": {"type": "string"}
                                        }
                                    }
                                }
                            },
                            "required": ["title", "sections"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "generate_pptx",
                    "description": "Generate a PowerPoint presentation from structured slides. Each slide can have a title, bullets, and an optional imagePrompt to auto-generate an image. Saves to .generated/.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "Cover slide title"},
                                "slides": {
                                    "type": "array",
                                    "description": "List of slides",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "title": {"type": "string"},
                                            "bullets": {"type": "array", "items": {"type": "string"}},
                                            "imagePrompt": {"type": "string", "description": "Optional: auto-generate an image for the slide"},
                                            "layout": {"type": "string", "description": "title | content | two-column", "default": "content"}
                                        }
                                    }
                                }
                            },
                            "required": ["title", "slides"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "edit_image",
                    "description": "Edit an existing PNG/JPEG image using inpaint (mask-based replace) or outpaint (extend canvas). Uses Titan Image v2 with Nova Canvas fallback.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "mode": {"type": "string", "description": "inpaint or outpaint"},
                                "image_path": {"type": "string", "description": "Path to the source image"},
                                "prompt": {"type": "string", "description": "Edit instruction (1-512 chars)"},
                                "mask_path": {"type": "string", "description": "Mask image path (inpaint only — white=replace, black=keep)"},
                                "direction": {
                                    "type": "array",
                                    "description": "Outpaint directions: any of left, right, top, bottom",
                                    "items": {"type": "string"}
                                },
                                "extend_pixels": {"type": "integer", "description": "Outpaint extend amount per direction (1-1024)"}
                            },
                            "required": ["mode", "image_path", "prompt"]
                        }
                    }
                }
            }]
}


# ===== Remote Bridge Tool Routing =====
# When AE_BRIDGE_URL is set (by Electron), AI agent tools route through
# the bridge HTTP server which forwards to the remote SSH session.
import httpx as _httpx

_BRIDGE_URL = os.environ.get("AE_BRIDGE_URL", "")
_BRIDGE_TOKEN = os.environ.get("AE_BRIDGE_TOKEN", "")

# Dev mode fallback: read discovery file written by Electron main.js
if not _BRIDGE_URL:
    import tempfile as _tempfile
    try:
        _disc_path = os.path.join(_tempfile.gettempdir(), "ae-bridge.json")
        with open(_disc_path, "r") as _f:
            _disc = json.load(_f)
        _BRIDGE_URL = (_disc.get("url") or "").strip()
        _BRIDGE_TOKEN = (_disc.get("token") or "").strip()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
_BRIDGE_TOOLS = {"read_file", "write_file", "list_directory", "search_files", "run_command"}

def _refresh_bridge_discovery():
    """Re-read bridge URL/token from env or discovery file (lazy)."""
    global _BRIDGE_URL, _BRIDGE_TOKEN
    # Priority 1: env vars (set by Electron ProcessManager)
    url = os.environ.get("AE_BRIDGE_URL", "").strip()
    token = os.environ.get("AE_BRIDGE_TOKEN", "").strip()
    if url and token:
        _BRIDGE_URL, _BRIDGE_TOKEN = url, token
        return
    # Priority 2: discovery file (written by bridge-server.js)
    import tempfile as _tf
    try:
        with open(os.path.join(_tf.gettempdir(), "ae-bridge.json"), "r") as f:
            d = json.load(f)
        url = (d.get("url") or "").strip()
        token = (d.get("token") or "").strip()
        if url and token:
            _BRIDGE_URL, _BRIDGE_TOKEN = url, token
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass


def _call_bridge(endpoint: str, payload: dict):
    """Call the Electron bridge server. Returns dict or None on failure."""
    global _BRIDGE_URL, _BRIDGE_TOKEN
    # Lazy discovery: if URL not set, try to find it now
    if not _BRIDGE_URL or not _BRIDGE_TOKEN:
        _refresh_bridge_discovery()
    if not _BRIDGE_URL or not _BRIDGE_TOKEN:
        return None
    try:
        r = _httpx.post(
            f"{_BRIDGE_URL}/bridge/{endpoint}",
            headers={"X-AE-Bridge-Token": _BRIDGE_TOKEN},
            json=payload,
            timeout=30.0,
        )
        if r.status_code == 200:
            return r.json()
        # Bridge server died or restarted — clear cached URL so next call retries
        if r.status_code in (401, 404, 502, 503):
            _BRIDGE_URL, _BRIDGE_TOKEN = "", ""
    except Exception:
        # Connection refused = bridge not running, clear for retry
        _BRIDGE_URL, _BRIDGE_TOKEN = "", ""
    return None

def _bridge_is_remote() -> bool:
    """Check if a remote SSH session is currently active."""
    res = _call_bridge("status", {})
    return bool(res and res.get("remote"))


def _format_bridge_result(tool_name, br):
    """Format bridge JSON response into a string for the agent."""
    if not isinstance(br, dict):
        return str(br)
    if not br.get('ok', True):
        return f'[Bridge Error] {br.get("error", "unknown")}'
    if tool_name == 'read_file':
        c = br.get('content', '')
        _max = int(os.environ.get('AE_READ_FILE_MAX', '120000'))
        if len(c) > _max:
            c = c[:_max] + '\n... (truncated)'
        return c
    elif tool_name == 'write_file':
        return 'File saved [remote]'
    elif tool_name == 'list_directory':
        entries = br.get('entries', [])
        lines = []
        for e in sorted(entries, key=lambda x: (not x.get('isDirectory'), x.get('name', ''))):
            nm = e.get('name', '')
            if nm.startswith('.') and nm not in ('.env', '.gitignore'):
                continue
            kind = 'DIR' if e.get('isDirectory') else 'FILE'
            lines.append(f'  {kind}  {nm}')
        return f'({len(lines)} items) [remote]\n' + '\n'.join(lines[:200])
    elif tool_name == 'run_command':
        output = (br.get('stdout', '') + br.get('stderr', ''))
        _max = int(os.environ.get('AE_RUN_CMD_MAX', '40000'))
        if len(output) > _max:
            output = output[:_max] + '\n... (truncated)'
        return output or '(no output)'
    elif tool_name == 'search_files':
        return br.get('output', 'no results')
    return str(br)



# ===== Media Generation Tools =====

# Image generation model fallback chain
IMAGE_MODELS = [
    "stability.stable-image-ultra-v1:1",
    "stability.sd3-5-large-v1:0",
    "stability.stable-image-core-v1:1",
    "amazon.nova-canvas-v1:0",
    "amazon.titan-image-generator-v2:0",
]
IMAGE_EDIT_MODELS = [
    "amazon.titan-image-generator-v2:0",
    "amazon.nova-canvas-v1:0",
]


async def _tool_generate_image(tool_input: dict, project_path: str, aws_profile: str = '', bedrock_user: str = '') -> str:  # [patched-credentials]
    """Generate an image via Bedrock image models with fallback chain.

    Returns JSON string: {path, model, width, height} on success,
    {error, detail?} on failure.
    """
    import time as _t, hashlib, base64
    prompt = (tool_input.get("prompt") or "").strip()
    size = tool_input.get("size", "1024x1024")
    style = tool_input.get("style", "")

    if not prompt:
        return json.dumps({"error": "invalid-parameter", "detail": "prompt is required"})
    if len(prompt) > 2000:
        return json.dumps({"error": "invalid-parameter", "detail": "prompt exceeds 2000 chars"})

    # Parse size
    try:
        w, h = (int(x) for x in size.lower().split("x"))
    except Exception:
        w, h = 1024, 1024

    # Output path
    # Always use a locally-existing directory for generated media.
    # Remote project_path may not exist locally; fall back to cwd.
    _local_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
    gen_dir = os.path.join(_local_root, ".generated")
    os.makedirs(gen_dir, exist_ok=True)
    ts = str(int(_t.time() * 1000))
    short_hash = hashlib.md5(prompt.encode()).hexdigest()[:4]
    filename = f"image-{ts}-{short_hash}.png"
    output_path = os.path.join(gen_dir, filename)
    relative_path = f".generated/{filename}"

    # [patched-credentials] honor explicit kw-args, fall back to env
    aws_profile = aws_profile or os.environ.get("AWS_PROFILE", "bedrock-gw")
    bedrock_user = bedrock_user or os.environ.get("BEDROCK_USER", "")
    gw = _get_gw(aws_profile, bedrock_user)

    last_error = ""
    for model_id in IMAGE_MODELS:
        try:
            if model_id.startswith("stability."):
                body = {
                    "prompt": prompt,
                    "mode": "text-to-image",
                    "output_format": "png",
                    "aspect_ratio": "1:1" if w == h else (f"{w}:{h}"),
                }
                if style:
                    body["style_preset"] = style
            elif model_id.startswith("amazon.titan"):
                body = {
                    "textToImageParams": {"text": prompt},
                    "imageGenerationConfig": {
                        "numberOfImages": 1,
                        "width": w,
                        "height": h,
                        "quality": "standard",
                    },
                }
            else:
                body = {"prompt": prompt, "width": w, "height": h}

            callable_id = _resolve_callable_model_id(model_id, aws_profile, bedrock_user)
            result = await gw.invoke_model(callable_id, body, timeout=60)

            if "error" in result:
                last_error = f"{model_id}: {result['error'][:200]}"
                continue

            # Extract image bytes
            images = result.get("images", [])
            if not images and isinstance(result, dict):
                # Stability returns "images" with base64 strings
                # Titan returns {"images": ["base64..."]}
                # Some return artifacts
                images = result.get("artifacts", [])
                if images and isinstance(images[0], dict):
                    images = [a.get("base64", "") for a in images]

            if not images:
                last_error = f"{model_id}: no images returned"
                continue

            img_b64 = images[0] if isinstance(images[0], str) else (images[0].get("base64", "") if isinstance(images[0], dict) else "")
            if not img_b64:
                last_error = f"{model_id}: empty image data"
                continue

            img_bytes = base64.b64decode(img_b64)
            with open(output_path, "wb") as f:
                f.write(img_bytes)

            # Get actual dimensions
            try:
                from PIL import Image as _PIL
                with _PIL.open(output_path) as im:
                    aw, ah = im.size
            except Exception:
                aw, ah = w, h

            return json.dumps({
                "path": relative_path,
                "model": model_id,
                "size": f"{w}x{h}",
                "width": aw,
                "height": ah,
                "sizeBytes": len(img_bytes),
            })

        except Exception as e:
            last_error = f"{model_id}: {str(e)[:200]}"
            continue

    # Req 1.2: cap final error detail at 200 chars total
    detail = (last_error or "all image models failed")[:200]
    return json.dumps({"error": "model-unavailable", "detail": detail})


async def _tool_generate_pdf(tool_input: dict, project_path: str) -> str:
    """Generate a PDF document using reportlab."""
    title = (tool_input.get("title") or "").strip()
    sections = tool_input.get("sections")

    if not title:
        return json.dumps({"error": "invalid-parameter", "detail": "title is required"})
    if not sections or not isinstance(sections, list):
        return json.dumps({"error": "invalid-parameter", "detail": "sections is required (non-empty array)"})

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.units import cm
    except ImportError:
        return json.dumps({"error": "missing-dep", "lib": "reportlab", "hint": "pip install reportlab"})

    import time as _t, re as _re
    # Always use a locally-existing directory for generated media.
    # Remote project_path may not exist locally; fall back to cwd.
    _local_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
    gen_dir = os.path.join(_local_root, ".generated")
    os.makedirs(gen_dir, exist_ok=True)
    slug = _re.sub(r"[^a-z0-9]+", "-", title.lower())[:30].strip("-") or "doc"
    ts = str(int(_t.time() * 1000))
    filename = f"{slug}-{ts}.pdf"
    output_path = os.path.join(gen_dir, filename)
    relative_path = f".generated/{filename}"

    try:
        doc = SimpleDocTemplate(output_path, pagesize=A4)
        styles = getSampleStyleSheet()
        # Req 4.2: enforce Heading2 = 14pt bold, Normal = 10pt explicitly so
        # the contract holds regardless of reportlab version defaults.
        styles["Heading2"].fontName = "Helvetica-Bold"
        styles["Heading2"].fontSize = 14
        styles["Normal"].fontSize = 10
        story = [Paragraph(title, styles["Title"]), Spacer(1, 1 * cm)]

        for sec in sections:
            heading = sec.get("heading", "") if isinstance(sec, dict) else ""
            body = sec.get("body", "") if isinstance(sec, dict) else str(sec)
            if heading:
                story.append(Paragraph(heading, styles["Heading2"]))
                story.append(Spacer(1, 0.3 * cm))
            if body:
                for para in body.split("\n"):
                    if para.strip():
                        story.append(Paragraph(para, styles["Normal"]))
                        story.append(Spacer(1, 0.2 * cm))

        doc.build(story)
        size_bytes = os.path.getsize(output_path)
        # Req 4.3: prefer reportlab's actual page counter; fall back to
        # section count if the attribute is unavailable.
        page_count = getattr(doc, "page", 0) or len(sections)
        return json.dumps({
            "path": relative_path,
            "pageCount": page_count,
            "sizeBytes": size_bytes,
            "fileSize": size_bytes,
        })
    except Exception as e:
        return json.dumps({"error": "pdf-generation-failed", "detail": str(e)[:200]})


async def _tool_generate_pptx(tool_input: dict, project_path: str, aws_profile: str = '', bedrock_user: str = '') -> str:  # [patched-credentials]
    """Generate a PowerPoint presentation using python-pptx."""
    title = (tool_input.get("title") or "").strip()
    slides_data = tool_input.get("slides")

    if not title:
        return json.dumps({"error": "invalid-parameter", "detail": "title is required"})
    if not slides_data or not isinstance(slides_data, list):
        return json.dumps({"error": "invalid-parameter", "detail": "slides is required (non-empty array)"})

    try:
        from pptx import Presentation
        from pptx.util import Inches
    except ImportError:
        return json.dumps({"error": "missing-dep", "lib": "python-pptx", "hint": "pip install python-pptx"})

    import time as _t, re as _re
    # Always use a locally-existing directory for generated media.
    # Remote project_path may not exist locally; fall back to cwd.
    _local_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
    gen_dir = os.path.join(_local_root, ".generated")
    os.makedirs(gen_dir, exist_ok=True)
    slug = _re.sub(r"[^a-z0-9]+", "-", title.lower())[:30].strip("-") or "deck"
    ts = str(int(_t.time() * 1000))
    filename = f"{slug}-{ts}.pptx"
    output_path = os.path.join(gen_dir, filename)
    relative_path = f".generated/{filename}"

    LAYOUT_MAP = {"title": 0, "content": 1, "two-column": 3}

    try:
        prs = Presentation()
        # Cover slide
        cover_layout = prs.slide_layouts[0]
        cover = prs.slides.add_slide(cover_layout)
        cover.shapes.title.text = title
        if len(cover.placeholders) > 1:
            from datetime import datetime as _dt
            cover.placeholders[1].text = _dt.now().strftime("%Y-%m-%d")

        for i, sd in enumerate(slides_data):
            if not isinstance(sd, dict):
                sd = {"title": str(sd)}
            layout_name = sd.get("layout", "content")
            layout_idx = LAYOUT_MAP.get(layout_name, 1)
            try:
                layout = prs.slide_layouts[layout_idx]
            except IndexError:
                layout = prs.slide_layouts[1]
            s = prs.slides.add_slide(layout)
            s.shapes.title.text = sd.get("title", f"Slide {i + 2}")

            bullets = sd.get("bullets", [])
            body_shape = s.placeholders[1] if len(s.placeholders) > 1 else None
            if body_shape and bullets:
                tf = body_shape.text_frame
                tf.clear()
                for j, bullet in enumerate(bullets):
                    if j == 0:
                        tf.text = str(bullet)
                    else:
                        p = tf.add_paragraph()
                        p.text = str(bullet)

            # Auto-generate image if imagePrompt is set
            img_prompt = sd.get("imagePrompt", "")
            if img_prompt:
                try:
                    img_result_str = await _tool_generate_image({"prompt": img_prompt, "size": "1024x1024"},
                        project_path, aws_profile=aws_profile, bedrock_user=bedrock_user)  # [patched-credentials]
                    img_result = json.loads(img_result_str)
                    if "path" in img_result:
                        img_path = os.path.join(project_path, img_result["path"]) if project_path else img_result["path"]
                        if os.path.isfile(img_path):
                            s.shapes.add_picture(img_path, Inches(5), Inches(1.5), width=Inches(4))
                except Exception as e:
                    print(f"[generate_pptx] image gen failed slide {i + 2}: {e}")

        try:
            prs.save(output_path)
        except Exception as save_err:
            return json.dumps({"error": "pptx-generation-failed", "detail": str(save_err)[:200]})

        size_bytes = os.path.getsize(output_path)
        return json.dumps({
            "path": relative_path,
            "slideCount": len(slides_data) + 1,  # +1 for cover
            "sizeBytes": size_bytes,
        })
    except Exception as e:
        return json.dumps({"error": "pptx-generation-failed", "detail": str(e)[:200]})


async def _tool_edit_image(tool_input: dict, project_path: str, aws_profile: str = '', bedrock_user: str = '') -> str:  # [patched-credentials]
    """Edit an image using inpaint or outpaint."""
    import time as _t, base64
    mode = tool_input.get("mode", "")
    image_path = tool_input.get("image_path", "")
    prompt = (tool_input.get("prompt") or "").strip()

    if mode not in ("inpaint", "outpaint"):
        return json.dumps({"error": "invalid-mode", "detail": "mode must be inpaint or outpaint"})
    if not image_path:
        return json.dumps({"error": "invalid-parameter", "detail": "image_path is required"})
    if not prompt:
        return json.dumps({"error": "invalid-parameter", "detail": "prompt is required"})
    if len(prompt) > 512:
        return json.dumps({"error": "invalid-parameter", "detail": "prompt exceeds 512 chars"})

    # Resolve path (relative to project_path)
    if not os.path.isabs(image_path) and project_path:
        full_path = os.path.join(project_path, image_path)
    else:
        full_path = image_path
    if not os.path.isfile(full_path):
        return json.dumps({"error": "file-not-found", "detail": f"image not found: {image_path}"})

    # Validate format and size
    # Req 3.5: outpaint surfaces "invalid-input"; inpaint (Req 2.9) keeps "invalid-image"
    _fmt_err = "invalid-input" if mode == "outpaint" else "invalid-image"
    try:
        with open(full_path, "rb") as f:
            magic = f.read(8)
        if magic[:8] == b"\x89PNG\r\n\x1a\n":
            fmt = "png"
        elif magic[:3] == b"\xff\xd8\xff":
            fmt = "jpeg"
        elif magic[:4] == b"RIFF":
            fmt = "webp"
        else:
            return json.dumps({"error": _fmt_err, "detail": "unsupported format (PNG/JPEG/WEBP only)"})
    except Exception as e:
        return json.dumps({"error": _fmt_err, "detail": str(e)[:200]})

    file_size = os.path.getsize(full_path)
    if file_size > 5 * 1024 * 1024:
        return json.dumps({"error": "invalid-image", "detail": "image exceeds 5MB"})

    # Req 2.9: inpaint allows only PNG/JPEG (outpaint additionally accepts WEBP per Req 3.5)
    if mode == "inpaint" and fmt == "webp":
        return json.dumps({"error": "invalid-image", "detail": "inpaint requires PNG or JPEG"})

    # Encode image
    with open(full_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode("ascii")

    # Build request body per mode
    # [patched-credentials] honor explicit kw-args, fall back to env
    aws_profile = aws_profile or os.environ.get("AWS_PROFILE", "bedrock-gw")
    bedrock_user = bedrock_user or os.environ.get("BEDROCK_USER", "")
    gw = _get_gw(aws_profile, bedrock_user)

    last_error = ""

    if mode == "inpaint":
        mask_path = tool_input.get("mask_path", "")
        if not mask_path:
            return json.dumps({"error": "invalid-parameter", "detail": "mask_path required for inpaint"})
        if not os.path.isabs(mask_path) and project_path:
            mask_full = os.path.join(project_path, mask_path)
        else:
            mask_full = mask_path
        if not os.path.isfile(mask_full):
            return json.dumps({"error": "mask-not-found", "detail": f"mask not found: {mask_path}"})

        # Req 2.8: mask dimensions must match the original image
        try:
            from PIL import Image as _PIL_dim
            with _PIL_dim.open(full_path) as _img_orig:
                _orig_size = _img_orig.size
            with _PIL_dim.open(mask_full) as _img_mask:
                _mask_size = _img_mask.size
        except Exception as _e:
            return json.dumps({"error": "invalid-image", "detail": f"failed to read image dimensions: {str(_e)[:160]}"})
        if _orig_size != _mask_size:
            return json.dumps({
                "error": "mask-dimension-mismatch",
                "detail": f"mask {_mask_size[0]}x{_mask_size[1]} does not match image {_orig_size[0]}x{_orig_size[1]}"
            })

        with open(mask_full, "rb") as f:
            mask_b64 = base64.b64encode(f.read()).decode("ascii")

        for model_id in IMAGE_EDIT_MODELS:
            try:
                if model_id.startswith("amazon.titan"):
                    body = {
                        "taskType": "INPAINTING",
                        "inPaintingParams": {
                            "image": img_b64,
                            "maskImage": mask_b64,
                            "text": prompt,
                        },
                        "imageGenerationConfig": {"numberOfImages": 1, "quality": "standard"},
                    }
                else:  # nova-canvas
                    body = {
                        "taskType": "INPAINTING",
                        "inPaintingParams": {
                            "image": img_b64,
                            "maskImage": mask_b64,
                            "text": prompt,
                        },
                        "imageGenerationConfig": {"numberOfImages": 1},
                    }
                callable_id = _resolve_callable_model_id(model_id, aws_profile, bedrock_user)
                result = await gw.invoke_model(callable_id, body, timeout=60)
                if "error" in result:
                    last_error = f"{model_id}: {result['error'][:200]}"
                    continue
                images = result.get("images", [])
                if not images:
                    last_error = f"{model_id}: no images returned"
                    continue
                img_out = images[0] if isinstance(images[0], str) else (images[0].get("base64", "") if isinstance(images[0], dict) else "")
                if not img_out:
                    last_error = f"{model_id}: empty image data"
                    continue
                # Save
                # Always use a locally-existing directory for generated media.
                # Remote project_path may not exist locally; fall back to cwd.
                _local_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
                gen_dir = os.path.join(_local_root, ".generated")
                os.makedirs(gen_dir, exist_ok=True)
                ts = str(int(_t.time() * 1000))
                filename = f"inpaint-{ts}.png"
                output_path = os.path.join(gen_dir, filename)
                with open(output_path, "wb") as f:
                    f.write(base64.b64decode(img_out))
                try:
                    from PIL import Image as _PIL
                    with _PIL.open(output_path) as im:
                        aw, ah = im.size
                except Exception:
                    aw, ah = 0, 0
                return json.dumps({
                    "path": f".generated/{filename}",
                    "model": model_id,
                    "width": aw,
                    "height": ah,
                    "mode": "inpaint",
                })
            except Exception as e:
                last_error = f"{model_id}: {str(e)[:200]}"
                continue

        return json.dumps({"error": "model-unavailable", "detail": last_error or "all inpaint models failed"})

    # outpaint mode
    # Req 3.5: enforce 4096px max on the longer original edge
    try:
        from PIL import Image as _PIL_dim_op
        with _PIL_dim_op.open(full_path) as _src_img:
            _src_w, _src_h = _src_img.size
    except Exception as _e:
        return json.dumps({"error": "invalid-input", "detail": f"failed to read image dimensions: {str(_e)[:160]}"})
    if max(_src_w, _src_h) > 4096:
        return json.dumps({
            "error": "invalid-input",
            "detail": f"image dimension exceeds 4096px (got {_src_w}x{_src_h})",
        })

    # Req 3.7: direction is required, must be a list of 1..4 entries from the allowed set.
    # AGENT_TOOLS schema exposes "up"/"down" while spec uses "top"/"bottom"; accept both
    # and normalize to {left, right, top, bottom}.
    raw_direction = tool_input.get("direction", ["right"])
    if not isinstance(raw_direction, list) or not (1 <= len(raw_direction) <= 4):
        return json.dumps({
            "error": "invalid-parameter",
            "detail": "direction must be a list of 1-4 values (left/right/top/bottom)",
        })
    _dir_alias = {"up": "top", "down": "bottom", "top": "top", "bottom": "bottom",
                  "left": "left", "right": "right"}
    normalized = []
    seen = set()
    for d in raw_direction:
        if not isinstance(d, str) or d not in _dir_alias:
            return json.dumps({
                "error": "invalid-parameter",
                "detail": f"invalid direction value: {d!r} (allowed: left/right/top/bottom)",
            })
        nd = _dir_alias[d]
        if nd not in seen:
            seen.add(nd)
            normalized.append(nd)
    direction = normalized

    # Req 3.7: extend_pixels must be an integer in [1, 1024]
    raw_extend = tool_input.get("extend_pixels", 256)
    if isinstance(raw_extend, bool) or not isinstance(raw_extend, int):
        return json.dumps({
            "error": "invalid-parameter",
            "detail": "extend_pixels must be an integer in 1-1024",
        })
    extend_pixels = raw_extend
    if not (1 <= extend_pixels <= 1024):
        return json.dumps({"error": "invalid-parameter", "detail": "extend_pixels must be 1-1024"})

    for model_id in IMAGE_EDIT_MODELS:
        try:
            if model_id.startswith("amazon.titan"):
                body = {
                    "taskType": "OUTPAINTING",
                    "outPaintingParams": {
                        "image": img_b64,
                        "text": prompt,
                        "outPaintingMode": "DEFAULT",
                    },
                    "imageGenerationConfig": {"numberOfImages": 1, "quality": "standard"},
                }
            else:
                body = {
                    "taskType": "OUTPAINTING",
                    "outPaintingParams": {
                        "image": img_b64,
                        "text": prompt,
                    },
                    "imageGenerationConfig": {"numberOfImages": 1},
                }
            callable_id = _resolve_callable_model_id(model_id, aws_profile, bedrock_user)
            result = await gw.invoke_model(callable_id, body, timeout=60)
            if "error" in result:
                last_error = f"{model_id}: {result['error'][:200]}"
                continue
            images = result.get("images", [])
            if not images:
                last_error = f"{model_id}: no images"
                continue
            img_out = images[0] if isinstance(images[0], str) else (images[0].get("base64", "") if isinstance(images[0], dict) else "")
            if not img_out:
                continue
            # Always use a locally-existing directory for generated media.
            # Remote project_path may not exist locally; fall back to cwd.
            _local_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
            gen_dir = os.path.join(_local_root, ".generated")
            os.makedirs(gen_dir, exist_ok=True)
            ts = str(int(_t.time() * 1000))
            filename = f"outpaint-{ts}.png"
            output_path = os.path.join(gen_dir, filename)
            with open(output_path, "wb") as f:
                f.write(base64.b64decode(img_out))
            try:
                from PIL import Image as _PIL
                with _PIL.open(output_path) as im:
                    aw, ah = im.size
            except Exception:
                aw, ah = 0, 0
            return json.dumps({
                "path": f".generated/{filename}",
                "model": model_id,
                "width": aw,
                "height": ah,
                "mode": "outpaint",
                "direction": direction,
            })
        except Exception as e:
            last_error = f"{model_id}: {str(e)[:200]}"
            continue

    return json.dumps({"error": "model-unavailable", "detail": last_error or "all outpaint models failed"})



def _execute_tool(tool_name: str, tool_input: dict, project_path: str = "", aws_profile: str = "", bedrock_user: str = "") -> str:
    """도구를 실행하고 결과를 문자열로 반환."""
    # === Remote Bridge Routing ===
    _REMOTE_TOOLS = {"read_file", "write_file", "list_directory", "search_files", "run_command"}
    if not _BRIDGE_URL:
        _refresh_bridge_discovery()
    if _BRIDGE_URL and tool_name in _REMOTE_TOOLS and _bridge_is_remote():
        _br = _call_bridge(tool_name, tool_input)
        if _br is not None:
            return _format_bridge_result(tool_name, _br)
        # bridge returned None = unavailable, fall through to local

    # Async media generation tools
    if tool_name in ("generate_image", "generate_pdf", "generate_pptx", "edit_image"):
        try:
            import asyncio as _asyncio
            if tool_name == "generate_image":
                return _asyncio.run(_tool_generate_image(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))
            if tool_name == "generate_pdf":
                return _asyncio.run(_tool_generate_pdf(tool_input, project_path))
            if tool_name == "generate_pptx":
                return _asyncio.run(_tool_generate_pptx(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))
            if tool_name == "edit_image":
                return _asyncio.run(_tool_edit_image(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))
        except Exception as e:
            return json.dumps({"error": "tool-execution-failed", "detail": str(e)[:300]})


    try:
        if tool_name == "read_file":
            path = tool_input["path"]
            if not os.path.isabs(path) and project_path:
                path = os.path.join(project_path, path)
            if not os.path.exists(path):
                return f"파일 없음: {path}"
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            _rf_max = int(os.environ.get("AE_READ_FILE_MAX", "120000"))
            if len(content) > _rf_max:
                content = content[:_rf_max] + f"\n... (총 {len(content)}자, {_rf_max}자까지 표시)"
            return content

        elif tool_name == "write_file":
            path = tool_input["path"]
            if not os.path.isabs(path) and project_path:
                path = os.path.join(project_path, path)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(tool_input["content"])
            return f"파일 저장 완료: {path} ({len(tool_input['content'])}자)"

        elif tool_name == "list_directory":
            path = tool_input["path"]
            if not os.path.isabs(path) and project_path:
                path = os.path.join(project_path, path)
            if not os.path.isdir(path):
                return f"디렉토리 없음: {path}"
            entries = os.listdir(path)
            result = []
            for e in sorted(entries):
                if e.startswith('.') and e not in ('.env', '.gitignore'):
                    continue
                fp = os.path.join(path, e)
                kind = "DIR" if os.path.isdir(fp) else "FILE"
                result.append(f"  {kind}  {e}")
            return f"{path}/ ({len(result)}개)\n" + "\n".join(result[:100])

        elif tool_name == "run_command":
            cmd = tool_input["command"]
            cwd = tool_input.get("cwd", project_path or os.getcwd())
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=30, cwd=cwd,
                env={**os.environ, "PATH": os.environ.get("PATH", "")},
            )
            output = result.stdout + result.stderr
            _rc_max = int(os.environ.get("AE_RUN_CMD_MAX", "40000"))
            if len(output) > _rc_max:
                output = output[:_rc_max] + f"\n... (출력 잘림, 총 {len(output)}자 중 {_rc_max}자 표시)"
            return output or "(출력 없음)"

        elif tool_name == "search_files":
            query = tool_input["query"]
            path = tool_input["path"]
            if not os.path.isabs(path) and project_path:
                path = os.path.join(project_path, path)
            pattern = tool_input.get("file_pattern", "")
            include = f"--include='{pattern}'" if pattern else ""
            cmd = f"grep -rn {include} --color=never '{query}' '{path}' 2>/dev/null | head -50"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            return result.stdout or "검색 결과 없음"

        else:
            return f"알 수 없는 도구: {tool_name}"
    except subprocess.TimeoutExpired:
        return "명령 실행 시간 초과 (30초)"
    except Exception as e:
        return f"도구 실행 오류: {str(e)}"


# GatewayClient 캐시 — 동일 profile+user 조합은 재사용
_gw_cache = {}


def _is_code_related(prompt: str) -> bool:
    """프롬프트가 코드/프로젝트 관련인지 판단."""
    p = prompt.lower().strip()
    # 코드 관련 키워드
    code_keywords = [
        'code', 'function', 'class', 'import', 'error', 'bug', 'fix',
        'implement', 'refactor', 'test', 'deploy', 'build', 'compile',
        '코드', '함수', '클래스', '에러', '버그', '수정', '구현', '리팩토링',
        '파일', '모듈', '컴포넌트', '테스트', '배포', '빌드', '변수',
        'api', 'endpoint', 'database', 'query', 'schema', 'migration',
        'this file', 'this project', '이 파일', '이 프로젝트', '현재',
        '경로', '폴더', '디렉토리', '열린', '오픈', 'path', 'directory',
        '에디터', 'editor', 'project', '프로젝트',
        '.js', '.py', '.ts', '.css', '.html', '.json',
    ]
    for kw in code_keywords:
        if kw in p:
            return True
    # 200자 이상이면 코드 관련일 가능성 높음
    if len(p) > 200:
        return True
    return False


def _build_messages(chat_history: list, current_prompt: str, session_id: str = "") -> list:
    """ConversationMemory를 통해 messages 구성."""
    from ai_engine.rag.conversation_memory import get_memory
    mem = get_memory()
    messages, _ = mem.build_messages(session_id or "default", chat_history, current_prompt)
    return messages

async def _maybe_summarize(session_id: str, chat_history: list, gw):
    """대화가 길어지면 비동기로 요약 체크포인트 생성."""
    try:
        from ai_engine.rag.conversation_memory import get_memory
        mem = get_memory()
        _, needs = mem.build_messages(session_id, chat_history, "")
        if needs:
            await mem.summarize_and_checkpoint(session_id, chat_history, gw)
    except Exception as e:
        print(f"[Memory] 요약 트리거 실패: {e}")


def _get_gw(aws_profile, bedrock_user):
    key = f"{aws_profile}:{bedrock_user}"
    if key not in _gw_cache:
        from ai_engine.gateway_module import GatewayClient
        _gw_cache[key] = GatewayClient(
            gateway_url=os.environ.get("GATEWAY_URL", "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1"),
            aws_profile=aws_profile,
            region=os.environ.get("AWS_REGION", "us-west-2"),
            bedrock_user=bedrock_user,
        )
    gw = _gw_cache[key]
    # 주입된 자격증명이 있으면 캐시 만료하지 않음
    if not hasattr(gw, '_injected_creds') or not gw._injected_creds:
        gw._cred_time = 0
    return gw


# ─── Callable Model ID Resolution ──────────────────────────────────────
# Bedrock 모델 ID는 3가지 형태가 있음:
#  1) ON_DEMAND: 'nvidia.nemotron-nano-12b-v2' (prefix 없이 직접 호출)
#  2) INFERENCE_PROFILE (CRIS): 'us.anthropic.claude-opus-4-7' (us./eu./global. prefix 필수)
#  3) 둘 다 지원: 이 경우 CRIS 우선 (성능·리전 분산)
#
# foundation model 목록의 inferenceTypesSupported를 보고 올바른 형태를 선택한다.
# 캐시는 profile 부팅 후 list_foundation_models로 한 번만 조회 (프로세스 생애).

_model_inference_type_cache = {}  # key: "{profile}:{user}", val: {modelId: [types]}


def _load_inference_types(aws_profile, bedrock_user):
    key = f"{aws_profile}:{bedrock_user}"
    if key in _model_inference_type_cache:
        return _model_inference_type_cache[key]
    try:
        import boto3
        session = boto3.Session(profile_name=aws_profile, region_name=os.environ.get("AWS_REGION", "us-west-2"))
        client = session.client("bedrock")
        resp = client.list_foundation_models()
        cache = {}
        for m in resp.get("modelSummaries", []):
            mid = m.get("modelId", "")
            cache[mid] = m.get("inferenceTypesSupported", []) or []
        _model_inference_type_cache[key] = cache
        return cache
    except Exception as e:
        print(f"[ModelResolver] list_foundation_models 실패: {e}")
        _model_inference_type_cache[key] = {}
        return {}


def _resolve_callable_model_id(model_id, aws_profile, bedrock_user):
    """모델 ID를 실제 Bedrock 호출 가능한 형태로 변환.
    - ON_DEMAND only → prefix 제거 (prefix가 붙어있으면 떼어냄)
    - INFERENCE_PROFILE only → us. prefix 강제 (없으면 붙임)
    - 둘 다 / 알 수 없음 → prefix 있으면 유지, 없으면 us. 붙임 (기본값, 대부분 CRIS 커버)
    """
    if not model_id:
        return model_id
    # 이미 prefix 붙어있으면 원본 ID 추출
    raw_id = model_id
    for prefix in ("us.", "eu.", "global."):
        if model_id.startswith(prefix):
            raw_id = model_id[len(prefix):]
            break

    cache = _load_inference_types(aws_profile, bedrock_user)
    types = cache.get(raw_id, [])

    has_on_demand = "ON_DEMAND" in types
    has_inference_profile = "INFERENCE_PROFILE" in types

    if has_on_demand and not has_inference_profile:
        # ON_DEMAND 전용 — prefix 제거
        return raw_id
    if has_inference_profile and not has_on_demand:
        # INFERENCE_PROFILE 전용 — us. prefix 강제
        if not any(model_id.startswith(p) for p in ("us.", "eu.", "global.")):
            return f"us.{raw_id}"
        return model_id
    # 둘 다 또는 알 수 없음 → 기본 CRIS (기존 동작 유지)
    if not any(model_id.startswith(p) for p in ("us.", "eu.", "global.")):
        return f"us.{raw_id}"
    return model_id


def _is_expired_error(result):
    """응답이 토큰 만료 에러인지 판단."""
    err = ""
    if isinstance(result, dict):
        err = result.get("error", "")
    elif isinstance(result, str):
        err = result
    low = err.lower()
    return "expired" in low or "security token" in low


async def _refresh_and_retry_gw(gw, aws_profile, bedrock_user):
    """토큰 만료 시 자격증명을 다시 assume role하여 주입."""
    try:
        import boto3 as b3
        from botocore.credentials import Credentials as BotoCreds
        # boto3 세션 캐시 초기화
        b3.DEFAULT_SESSION = None
        gw.force_refresh_creds()
        session = b3.Session(profile_name=aws_profile)
        sts = session.client("sts")
        account = sts.get_caller_identity()["Account"]
        if bedrock_user:
            assumed = sts.assume_role(
                RoleArn=f"arn:aws:iam::{account}:role/BedrockUser-{bedrock_user}",
                RoleSessionName="ai-editor-refresh",
            )
            c = assumed["Credentials"]
            gw.inject_credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
            print(f"[AutoRefresh] BedrockUser-{bedrock_user} 자격증명 재주입 성공")
            return True
        else:
            fc = session.get_credentials().get_frozen_credentials()
            gw.inject_credentials(fc.access_key, fc.secret_key, fc.token)
            print(f"[AutoRefresh] 프로파일 {aws_profile} 자격증명 재주입 성공")
            return True
    except Exception as e:
        print(f"[AutoRefresh] 자격증명 재주입 실패: {e}")
        return False


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {
        "status": "ok",
        "service": "ai-editor-engine",
        "timestamp": datetime.utcnow().isoformat(),
        "version": __version__,
    }



@app.get("/api/debug/cwd")
async def debug_cwd():
    """Return server cwd so renderer knows where .generated/ files land."""
    return {"cwd": os.getcwd()}

@app.get("/api/debug/bridge")
async def debug_bridge():
    """Debug: show bridge state."""
    _refresh_bridge_discovery()
    return {
        "bridge_url": _BRIDGE_URL,
        "bridge_token_set": bool(_BRIDGE_TOKEN),
        "is_remote": _bridge_is_remote() if _BRIDGE_URL else False,
    }


@app.post("/api/reset-cache")
async def reset_cache(request: Request):
    """Gateway 클라이언트 캐시 초기화 + 선택적 자격증명 주입."""
    _gw_cache.clear()
    _quota_cache["used_krw"] = 0
    _quota_cache["remaining_krw"] = 0
    _quota_cache["limit_krw"] = 0
    _quota_cache["last_updated"] = ""
    _quota_cache["user"] = ""
    try:
        import boto3
        boto3.DEFAULT_SESSION = None
    except Exception:
        pass
    # 자격증명 직접 주입 (Electron에서 전달)
    try:
        body = await request.json()
        creds = body.get("credentials")
        if creds and creds.get("AWS_ACCESS_KEY_ID"):
            profile = body.get("profile", "bedrock-gw")
            user = body.get("bedrockUser", "")
            # 새 GatewayClient 생성 후 자격증명 주입
            from ai_engine.gateway_module import GatewayClient
            gw = GatewayClient(
                gateway_url=os.environ.get("GATEWAY_URL", "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1"),
                aws_profile=profile,
                region=os.environ.get("AWS_REGION", "us-west-2"),
                bedrock_user=user,
            )
            # SSO 기본 자격증명으로 BedrockUser assume role 시도
            try:
                import boto3 as b3
                from botocore.credentials import Credentials as BotoCreds
                # 전달받은 SSO 자격증명으로 임시 세션 생성
                tmp_session = b3.Session()
                sts = tmp_session.client(
                    "sts",
                    aws_access_key_id=creds["AWS_ACCESS_KEY_ID"],
                    aws_secret_access_key=creds["AWS_SECRET_ACCESS_KEY"],
                    aws_session_token=creds.get("AWS_SESSION_TOKEN", ""),
                    region_name=creds.get("AWS_DEFAULT_REGION", "us-west-2"),
                )
                account = sts.get_caller_identity()["Account"]
                if user:
                    assumed = sts.assume_role(
                        RoleArn=f"arn:aws:iam::{account}:role/BedrockUser-{user}",
                        RoleSessionName="ai-editor",
                    )
                    c = assumed["Credentials"]
                    gw.inject_credentials(c["AccessKeyId"], c["SecretAccessKey"], c["SessionToken"])
                    print(f"[Cache] BedrockUser-{user} assume role 성공")
                else:
                    gw.inject_credentials(
                        creds["AWS_ACCESS_KEY_ID"],
                        creds["AWS_SECRET_ACCESS_KEY"],
                        creds.get("AWS_SESSION_TOKEN", ""),
                    )
                key = f"{profile}:{user}"
                _gw_cache[key] = gw
            except Exception as e:
                print(f"[Cache] assume role 실패: {e}")
    except Exception:
        pass
    return {"status": "ok", "message": "cache cleared"}


@app.post("/api/rag/index")
async def rag_index(request: Request):
    """프로젝트 인덱싱 수동 트리거."""
    body = await request.json()
    project_path = body.get("projectPath", "")
    if not project_path or not os.path.isdir(project_path):
        return JSONResponse(content={"error": "Invalid project path"}, status_code=400)
    from ai_engine.rag.context_builder import get_indexer
    idx = get_indexer(project_path)
    count = idx.index_project(project_path)
    return {"status": "ok", "chunks": count, "files": len(set(c.file_path for c in idx.chunks))}


@app.get("/api/rag/status")
async def rag_status(request: Request):
    """RAG 인덱싱 상태 조회."""
    project_path = request.query_params.get("projectPath", "")
    if not project_path:
        return {"indexed": False, "chunks": 0}
    from ai_engine.rag.context_builder import _indexer_cache
    if project_path in _indexer_cache:
        idx = _indexer_cache[project_path]
        return {"indexed": True, "chunks": len(idx.chunks), "files": len(set(c.file_path for c in idx.chunks))}
    return {"indexed": False, "chunks": 0}


@app.get("/api/models")
@app.post("/api/models")
async def list_models(request: Request):
    """Return available models. POST로 자격증명을 직접 전달 가능."""
    profile = request.query_params.get("profile", os.environ.get("AWS_PROFILE", "default"))
    
    # POST body에서 자격증명 직접 받기
    creds_override = None
    if request.method == "POST":
        try:
            body = await request.json()
            if body.get("accessKeyId"):
                creds_override = body
                profile = body.get("profile", profile)
        except Exception:
            pass
    
    try:
        import boto3

        if creds_override:
            # 전달받은 자격증명으로 직접 클라이언트 생성
            client = boto3.client(
                "bedrock",
                aws_access_key_id=creds_override["accessKeyId"],
                aws_secret_access_key=creds_override["secretAccessKey"],
                aws_session_token=creds_override.get("sessionToken", ""),
                region_name=creds_override.get("region", os.environ.get("AWS_REGION", "us-west-2")),
            )
        else:
            session = boto3.Session(
                profile_name=profile,
                region_name=os.environ.get("AWS_REGION", "us-west-2"),
            )
            client = session.client("bedrock")
        
        # list_foundation_models는 등록된 전체 카탈로그를 반환하지만 모든 모델이
        # converse API로 호출 가능한 건 아님. 실제 호출 가능한 모델만 걸러내려면
        # list_inference_profiles (CRIS) 결과와 교차 검증 필요.
        # - Cross-region Inference Profile이 있는 모델만 converse 호출 가능
        # - 없는 모델은 ValidationException 발생
        try:
            profiles_resp = client.list_inference_profiles()
            callable_profile_ids = set()
            profile_id_to_name = {}
            for p in profiles_resp.get("inferenceProfileSummaries", []):
                if p.get("status", "").upper() != "ACTIVE":
                    continue
                pid = p.get("inferenceProfileId", "")
                pname = p.get("inferenceProfileName", pid)
                if pid:
                    callable_profile_ids.add(pid)
                    profile_id_to_name[pid] = pname
        except Exception as _e:
            # 프로파일 조회 실패 → 폴백으로 foundation model 전체 사용 (기존 동작)
            print(f"[Models] list_inference_profiles 실패, 폴백: {_e}")
            callable_profile_ids = None
            profile_id_to_name = {}

        resp = client.list_foundation_models()
        catalog = {}
        seen_model_keys = set()  # 리전/컨텍스트 변형 중복 제거

        skip_output = ["VIDEO", "EMBEDDING"]  # IMAGE 별도 카탈로그
        image_catalog = {}
        seen_image_keys = set()
        for m in resp.get("modelSummaries", []):
            modes = m.get("outputModalities", [])
            if m.get("modelLifecycle", {}).get("status") in ["EOL"]:
                continue
            input_modes = m.get("inputModalities", [])

            # === Image generation/edit models ===
            if "IMAGE" in modes:
                _mid = m["modelId"]
                _no_region = _mid
                for _pfx in ("us.", "eu.", "global."):
                    if _no_region.startswith(_pfx):
                        _no_region = _no_region[len(_pfx):]
                        break
                _parts = _no_region.split(":")
                _img_base = _parts[0] + ":" + _parts[1] if len(_parts) >= 2 else _no_region
                if _img_base in seen_image_keys:
                    continue
                seen_image_keys.add(_img_base)
                inference_types = m.get("inferenceTypesSupported", [])
                callable_id = _mid
                if callable_profile_ids is not None and "ON_DEMAND" not in inference_types:
                    for pid in callable_profile_ids:
                        if pid.endswith(_mid) or pid.endswith(f"{_mid}:0") or _mid in pid:
                            callable_id = pid
                            break
                _prov = m.get("providerName", "Unknown")
                if _prov not in image_catalog:
                    image_catalog[_prov] = []
                image_catalog[_prov].append({
                    "id": callable_id,
                    "name": m.get("modelName", _mid),
                })
                continue

            # === Text models ===
            if any(s in str(modes) for s in skip_output):
                continue
            if "TEXT" not in input_modes:
                continue
            inference_types = m.get("inferenceTypesSupported", [])
            if inference_types and "ON_DEMAND" not in inference_types and "INFERENCE_PROFILE" not in inference_types:
                continue
            if m.get("responseStreamingSupported") is False:
                continue

            # 실제 호출 가능 여부: CRIS profile 존재 여부로 판단
            model_id = m["modelId"]
            # 중복 제거: 리전 prefix(us./eu./global.) + context window 변형(:8k,:20k,:1000k,:mm) 정규화
            _no_region = model_id
            for _pfx in ("us.", "eu.", "global."):
                if _no_region.startswith(_pfx):
                    _no_region = _no_region[len(_pfx):]
                    break
            _parts = _no_region.split(":")
            if len(_parts) >= 2:
                _base_key = _parts[0] + ":" + _parts[1]
            else:
                _base_key = _no_region
            if _base_key in seen_model_keys:
                continue
            seen_model_keys.add(_base_key)
            if len(_parts) > 2:
                model_id = _parts[0] + ":" + _parts[1]  # 기본 ID(:0) 우선

            if callable_profile_ids is not None and "INFERENCE_PROFILE" in inference_types and "ON_DEMAND" not in inference_types:
                # 이 모델은 CRIS profile 필수 — profile 존재 여부 확인
                has_profile = any(
                    pid.endswith(model_id) or pid.endswith(f"{model_id}:0") or model_id in pid
                    for pid in callable_profile_ids
                )
                if not has_profile:
                    continue  # CRIS profile 없으면 호출 불가 → 스킵

            provider = m.get("providerName", "Unknown")
            if provider not in catalog:
                catalog[provider] = []
            catalog[provider].append({
                "id": model_id,
                "name": m.get("modelName", model_id),
            })
        return JSONResponse(content={
            "models": catalog,
            "image_models": image_catalog,
            "count": sum(len(v) for v in catalog.values()),
            "image_count": sum(len(v) for v in image_catalog.values()),
        })
    except Exception as e:
        return JSONResponse(content={"models": {}, "error": str(e)})


@app.post("/api/agents/run-stream")
async def run_agent_stream(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-sonnet-4-6")
    system_prompt = body.get("systemPrompt", "")
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    project_path = body.get("projectPath", "")
    open_file = body.get("openFile", "")
    open_file_content = body.get("openFileContent", "")

    gw = _get_gw(aws_profile, bedrock_user)

    if project_path and not system_prompt:
        system_prompt = f"사용자의 프로젝트 경로: {project_path}"
        if open_file:
            system_prompt += f"\n현재 열린 파일: {open_file}"

    if project_path and _is_code_related(prompt):
        try:
            from ai_engine.rag.context_builder import build_system_prompt
            system_prompt = build_system_prompt(
                project_path=project_path, query=prompt,
                open_file=open_file, open_file_content=open_file_content,
                base_system_prompt=system_prompt,
                aws_profile=aws_profile, bedrock_user=bedrock_user, gateway_client=gw,
            )
        except Exception as e:
            print(f"[RAG] 컨텍스트 빌드 실패 (무시): {e}")

    messages = _build_messages(body.get("chatHistory", []), prompt, body.get("sessionId", "default"))
    stream_model = _resolve_callable_model_id(model, aws_profile, bedrock_user)

    async def realtime_stream():
        """Lambda SSE를 실시간으로 프론트엔드에 중계 — ChatGPT처럼 글자가 써지는 효과.
        max_tokens로 끊기면 자동으로 이어서 생성 (최대 5회)."""
        nonlocal messages
        max_continues = int(os.environ.get("AE_MAX_CONTINUES", "50"))
        try:
            for cont in range(max_continues + 1):
                text_parts = []
                stop_reason = ""
                async for evt in gw.stream_sse_realtime(model_id=stream_model, messages=messages, system_prompt=system_prompt):
                    evt_type = evt.get("type", "")
                    if evt_type == "content_block_delta":
                        delta = evt.get("delta", {})
                        if "text" in delta:
                            text_parts.append(delta["text"])
                            yield f"data: {json.dumps({'text': delta['text']}, ensure_ascii=False)}\n\n"
                    elif evt_type in ("message_delta", "message_stop"):
                        stop_reason = evt.get("delta", {}).get("stopReason", "") or evt.get("stop_reason", "") or evt.get("stopReason", "") or stop_reason
                    elif evt_type == "settlement":
                        rq = {"cost_krw": evt.get("remaining_quota_krw", 0)}
                        _extract_quota({"remaining_quota": rq, "estimated_cost_krw": evt.get("estimated_cost_krw", 0)}, _quota_cache.get("user", ""))
                    elif evt_type == "error":
                        yield f"data: {json.dumps({'error': evt.get('message', str(evt))}, ensure_ascii=False)}\n\n"

                # max_tokens로 끊김 → 이어서 생성
                if stop_reason == "max_tokens" and cont < max_continues and text_parts:
                    print(f"[Stream] max_tokens 도달 — 이어서 생성 ({cont+1}/{max_continues})")
                    messages.append({"role": "assistant", "content": [{"text": "".join(text_parts)}]})
                    messages.append({"role": "user", "content": [{"text": "계속 이어서 작성해주세요."}]})
                    continue
                break
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        asyncio.create_task(_maybe_summarize(body.get("sessionId", "default"), body.get("chatHistory", []), gw))

    return StreamingResponse(
        realtime_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/agents/run-agent")
async def run_agent_with_tools(request: Request):
    """에이전트 모드 — 도구 실행 루프 포함. 모델이 tool_use로 응답하면 실행 후 재호출."""
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-sonnet-4-6")
    system_prompt = body.get("systemPrompt", "")
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    project_path = body.get("projectPath", "")
    open_file = body.get("openFile", "")
    open_file_content = body.get("openFileContent", "")

    gw = _get_gw(aws_profile, bedrock_user)
    stream_model = _resolve_callable_model_id(model, aws_profile, bedrock_user)

    # 시스템 프롬프트 구성
    if project_path and not system_prompt:
        system_prompt = f"사용자의 프로젝트 경로: {project_path}"
        if open_file:
            system_prompt += f"\n현재 열린 파일: {open_file}"
    if project_path and _is_code_related(prompt):
        try:
            from ai_engine.rag.context_builder import build_system_prompt
            system_prompt = build_system_prompt(
                project_path=project_path, query=prompt,
                open_file=open_file, open_file_content=open_file_content,
                base_system_prompt=system_prompt,
                aws_profile=aws_profile, bedrock_user=bedrock_user, gateway_client=gw,
            )
        except Exception as e:
            print(f"[Agent] RAG 실패 (무시): {e}")

    async def agent_stream():
        """에이전트 루프 — 최상위 try/finally 로 어떤 예외에도 [DONE] 송출 보장.
        ERR_INCOMPLETE_CHUNKED_ENCODING 방지 핵심."""
        done_sent = False
        try:
            # ── 메시지 구성 (실패해도 스트림은 이미 시작된 상태) ──
            try:
                messages = _build_messages(body.get("chatHistory", []), prompt, body.get("sessionId", "default"))
            except Exception as e:
                print(f"[Agent] _build_messages 실패: {e}")
                yield f"data: {json.dumps({'error': f'message build failed: {e}'}, ensure_ascii=False)}\n\n"
                return

            max_turns = int(os.environ.get("AE_MAX_AGENT_TURNS", "50"))
            refreshed_once = False  # 자격증명 만료 자동복구 1회만
            tool_unsupported_fallback_tried = False  # tool-use 미지원 모델 fallback 1회만

            for turn in range(max_turns):
                use_tool_config = not tool_unsupported_fallback_tried
                print(f"[Agent] turn={turn}, realtime stream, toolConfig={use_tool_config}")
                text_parts = []
                tool_use_blocks = []
                current_tool = {}
                stop_reason = ""
                turn_error = None

                try:
                    async for evt in gw.stream_sse_realtime(
                        model_id=stream_model, messages=messages,
                        system_prompt=system_prompt, tool_config=(AGENT_TOOLS if use_tool_config else None),
                    ):
                        evt_type = evt.get("type", "")
                        if evt_type == "content_block_delta":
                            delta = evt.get("delta", {})
                            if "text" in delta:
                                text_parts.append(delta["text"])
                                yield f"data: {json.dumps({'text': delta['text']}, ensure_ascii=False)}\n\n"
                            elif "toolUse" in delta:
                                if current_tool:
                                    current_tool["_input_json"] = current_tool.get("_input_json", "") + delta["toolUse"].get("input", "")
                        elif evt_type == "content_block_start":
                            cb = evt.get("content_block") or evt.get("contentBlock") or {}
                            if "toolUse" in cb:
                                tu = cb["toolUse"]
                                current_tool = {"toolUseId": tu.get("toolUseId", ""), "name": tu.get("name", ""), "_input_json": ""}
                        elif evt_type == "content_block_stop":
                            if current_tool and current_tool.get("name"):
                                try:
                                    inp = json.loads(current_tool.get("_input_json", "{}"))
                                except json.JSONDecodeError:
                                    inp = {}
                                tool_use_blocks.append({
                                    "toolUse": {"toolUseId": current_tool["toolUseId"], "name": current_tool["name"], "input": inp}
                                })
                                current_tool = {}
                        elif evt_type in ("message_delta", "message_stop"):
                            stop_reason = evt.get("delta", {}).get("stopReason", "") or evt.get("stop_reason", "") or evt.get("stopReason", "")
                        elif evt_type == "settlement":
                            _extract_quota({"remaining_quota": {"cost_krw": evt.get("remaining_quota_krw", 0)}, "estimated_cost_krw": evt.get("estimated_cost_krw", 0)}, _quota_cache.get("user", ""))
                        elif evt_type == "error":
                            msg = evt.get("message", str(evt))
                            turn_error = msg
                            # 자격증명 만료 자동 복구 후 현재 turn 재시도
                            if (not refreshed_once) and _is_expired_error(msg):
                                refreshed_once = True
                                print("[Agent] 자격증명 만료 감지 — 자동 갱신 시도")
                                ok = await _refresh_and_retry_gw(gw, aws_profile, bedrock_user)
                                if ok:
                                    yield f"data: {json.dumps({'info': 'credentials refreshed, retrying'}, ensure_ascii=False)}\n\n"
                                    break  # async for 를 벗어나 현재 turn 재시도
                            yield f"data: {json.dumps({'error': msg}, ensure_ascii=False)}\n\n"
                except asyncio.CancelledError:
                    # 클라이언트가 중단한 경우 — 조용히 종료
                    print("[Agent] 클라이언트 중단")
                    return
                except Exception as e:
                    import traceback
                    print(f"[Agent] stream 내부 예외: {e}\n{traceback.format_exc()}")
                    yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
                    break

                # 자격증명 갱신 후 재시도 케이스
                if turn_error and _is_expired_error(turn_error) and refreshed_once and not text_parts and not tool_use_blocks:
                    # 같은 turn 인덱스를 다시 쓰기 위해 range 를 못 돌리므로, messages 는 그대로 두고 continue 로 다음 turn 에서 재호출
                    continue


                # tool-use 미지원 모델 fallback — InternalServerException + 아직 콘텐츠 없음 + 첫 시도
                if turn_error and not tool_unsupported_fallback_tried and not text_parts and not tool_use_blocks:
                    err_lower = str(turn_error).lower()
                    if "internalserverexception" in err_lower or "internal server" in err_lower or "tool" in err_lower:
                        tool_unsupported_fallback_tried = True
                        print(f"[Agent] tool-use 미지원 모델 감지 — toolConfig 없이 재시도")
                        yield f"data: {json.dumps({"info": "tool-unsupported, retrying without tools"}, ensure_ascii=False)}\n\n"
                        continue

                # content_blocks 조합
                content_blocks = []
                if text_parts:
                    content_blocks.append({"text": "".join(text_parts)})
                content_blocks.extend(tool_use_blocks)

                print(f"[Agent] turn={turn}, stopReason={stop_reason}, text={len(text_parts)}parts, tools={len(tool_use_blocks)}")

                if not content_blocks:
                    # Surface error or warn about empty response instead of silent break
                    if turn_error:
                        error_msg = f"모델 응답 오류: {str(turn_error)[:300]}"
                        print(f"[Agent] turn_error surfaced: {turn_error}")
                        yield f"data: " + json.dumps({"text": error_msg}, ensure_ascii=False) + "\n\n"
                    else:
                        print(f"[Agent] WARNING: empty response from model={stream_model}, sys_prompt_len={len(system_prompt)}")
                        yield f"data: " + json.dumps({"text": "⚠️ 모델이 빈 응답을 반환했습니다. 다시 시도해 주세요."}, ensure_ascii=False) + "\n\n"
                    break
                messages.append({"role": "assistant", "content": content_blocks})

                if not tool_use_blocks:
                    if stop_reason == "max_tokens" and turn < max_turns - 1:
                        print(f"[Agent] max_tokens 도달 — 이어서 생성 (turn {turn+1})")
                        messages.append({"role": "user", "content": [{"text": "계속 이어서 작성해주세요."}]})
                        continue
                    break

                # 도구 실행
                tool_results = []
                for block in tool_use_blocks:
                    tu = block["toolUse"]
                    tool_name = tu.get("name", "")
                    tool_id = tu.get("toolUseId", "")
                    tool_input = tu.get("input", {})
                    yield f"data: {json.dumps({'tool': tool_name, 'input': tool_input, 'status': 'running'}, ensure_ascii=False)}\n\n"
                    import time as _time
                    _tool_start = _time.time()
                    try:
                        tool_output = await asyncio.to_thread(_execute_tool, tool_name, tool_input, project_path, aws_profile, bedrock_user)  # [patched-credentials]
                    except Exception as e:
                        tool_output = f"도구 실행 예외: {e}"
                    _tool_duration_ms = int((_time.time() - _tool_start) * 1000)
                    print(f"[Agent] 도구 실행: {tool_name} → {len(tool_output)}자 ({_tool_duration_ms}ms)")
                    yield f"data: {json.dumps({'tool': tool_name, 'output': tool_output[:500], 'status': 'done', 'durationMs': _tool_duration_ms}, ensure_ascii=False)}\n\n"
                    _tr_max = int(os.environ.get("AE_TOOL_RESULT_MAX", "80000"))
                    tool_results.append({"toolResult": {"toolUseId": tool_id, "content": [{"text": tool_output[:_tr_max]}]}})

                messages.append({"role": "user", "content": tool_results})
        except asyncio.CancelledError:
            print("[Agent] stream cancelled")
            return
        except Exception as e:
            import traceback
            print(f"[Agent] 최상위 예외: {e}\n{traceback.format_exc()}")
            try:
                yield f"data: {json.dumps({'error': f'fatal: {e}'}, ensure_ascii=False)}\n\n"
            except Exception:
                pass
        finally:
            # 어떤 경로로 끝나든 [DONE] 을 반드시 송출 → ERR_INCOMPLETE_CHUNKED_ENCODING 방지
            if not done_sent:
                done_sent = True
                try:
                    yield "data: [DONE]\n\n"
                except Exception:
                    pass

    return StreamingResponse(
        agent_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/agents/run-parallel")
async def run_agent_parallel(request: Request):
    """병렬 모델 호출 — 서버에서 동시 실행, SSE로 각 모델 결과 전달."""
    body = await request.json()
    prompt = body.get("prompt", "")
    models = body.get("models", [])
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    project_path = body.get("projectPath", "")
    open_file = body.get("openFile", "")
    open_file_content = body.get("openFileContent", "")

    gw = _get_gw(aws_profile, bedrock_user)

    # RAG 컨텍스트 — 코드/프로젝트 관련 질문에만
    rag_context = ""
    if project_path and _is_code_related(prompt):
        try:
            from ai_engine.rag.context_builder import build_system_prompt
            rag_context = build_system_prompt(
                project_path=project_path,
                query=prompt,
                open_file=open_file,
                open_file_content=open_file_content,
                aws_profile=aws_profile,
                bedrock_user=bedrock_user,
                gateway_client=gw,
            )
        except Exception as e:
            print(f"[RAG] 컨텍스트 빌드 실패 (무시): {e}")

    # 이전 대화 맥락(chatHistory) + 세션 메모리를 반영
    chat_history = body.get("chatHistory", [])
    session_id = body.get("sessionId", "default")
    messages = _build_messages(chat_history, prompt, session_id)

    async def parallel_stream():
        async def call_model(slot):
            model_id = slot.get("modelId", "")
            slot_id = slot.get("slotId", "")
            sp = slot.get("systemPrompt", "")
            # CRIS profile 존재 여부에 따라 us. prefix 적용 (ON_DEMAND 모델은 prefix 없이)
            sid = _resolve_callable_model_id(model_id, aws_profile, bedrock_user)
            # RAG 컨텍스트를 시스템 프롬프트에 추가
            if rag_context:
                sp = (sp + "\n\n" + rag_context) if sp else rag_context

            # 자동 재시도 (최대 3회, 지수 백오프)
            for attempt in range(3):
                try:
                    result = await asyncio.wait_for(
                        gw.converse_stream_live(model_id=sid, messages=messages, system_prompt=sp),
                        timeout=600
                    )
                    if result.get("decision") == "ERROR":
                        err = result.get("error", "")
                        # throttling/rate limit → 재시도
                        if attempt < 2 and ("throttl" in err.lower() or "rate" in err.lower() or "timed out" in err.lower()):
                            await asyncio.sleep(2 ** attempt * 2)
                            continue
                        result = await asyncio.wait_for(
                            gw.converse(model_id=sid, messages=messages, system_prompt=sp),
                            timeout=600
                        )
                    decision = result.get("decision", "")
                    if decision == "ALLOW":
                        output = result.get("output", {}).get("message", {}).get("content", [])
                        text = "\n".join(c.get("text", "") for c in output if "text" in c)
                        return {"slotId": slot_id, "modelId": model_id, "status": "done", "content": text}
                    elif decision == "ACCEPTED":
                        job_id = result.get("job_id", "")
                        if job_id:
                            text = await gw._poll_job_result(job_id, max_wait=600)
                            if text:
                                return {"slotId": slot_id, "modelId": model_id, "status": "done", "content": text}
                        return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": "ACCEPTED — 결과 대기 시간 초과"}
                    elif decision == "DENY":
                        return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": result.get("denial_reason", "DENIED")}
                    else:
                        err_msg = result.get("error", f"Unknown: {decision}")
                        if attempt < 2 and ("throttl" in err_msg.lower() or "timed out" in err_msg.lower()):
                            await asyncio.sleep(2 ** attempt * 2)
                            continue
                        return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": err_msg}
                except asyncio.TimeoutError:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt * 2)
                        continue
                    return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": "600초 타임아웃"}
                except Exception as e:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": str(e)}
            return {"slotId": slot_id, "modelId": model_id, "status": "error", "content": "재시도 3회 실패"}

        # 배치 실행: 10개씩 동시 호출 (rate limit 방지) + 배치 간 heartbeat
        batch_size = 10
        for i in range(0, len(models), batch_size):
            batch = models[i:i+batch_size]
            tasks = [call_model(slot) for slot in batch]
            for coro in asyncio.as_completed(tasks):
                result = await coro
                yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
            # 배치 간 heartbeat — 클라이언트 idle timeout 방지
            if i + batch_size < len(models):
                yield f"data: {json.dumps({'heartbeat': True, 'progress': min(i+batch_size, len(models)), 'total': len(models)})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(parallel_stream(), media_type="text/event-stream")


# ─────────────────────────────────────────────────────────────────
# Multi-Agent Orchestrator  (Role Split + Parallel Tool-Using Agents)
# ─────────────────────────────────────────────────────────────────

ORCHESTRATOR_PLANNER_PROMPT = """당신은 멀티-에이전트 시스템의 플래너입니다.
사용자의 요청을 서로 독립적으로 병렬 실행 가능한 N개의 하위 작업(subtask)으로 분해하세요.

규칙:
1. 각 subtask는 서로 파일/코드 영역이 겹치지 않아야 합니다 (충돌 방지).
2. 각 subtask는 하나의 에이전트가 도구(read_file, write_file, list_directory, run_command, search_files)를 사용해 독립적으로 완료할 수 있어야 합니다.
3. subtask 개수는 요청 내용에 맞게 결정하되, 최대 {max_agents}개를 넘지 마세요.
4. 사용자가 "수정 1~4" 처럼 명시한 번호/단계가 있으면 그대로 따르세요.

반드시 아래 JSON 형식으로만 응답하세요 (마크다운 코드블록 없이):
{{
  "subtasks": [
    {{
      "id": "A",
      "role": "역할명 (예: Tagger, Builder, Connector, Anchor)",
      "title": "간결한 제목",
      "description": "이 에이전트가 수행해야 할 작업의 상세 지시",
      "target_files": ["상대경로 or 절대경로", ...]
    }},
    ...
  ]
}}
"""

ORCHESTRATOR_AGENT_PROMPT = """당신은 멀티-에이전트 시스템의 전문 작업자입니다.

[할당된 역할] {role}
[작업 ID] {task_id}
[작업 제목] {title}
[대상 파일] {target_files}

[지시사항]
{description}

[규칙]
- 반드시 제공된 도구(read_file, write_file, list_directory, run_command, search_files)를 사용하여 실제로 작업을 수행하세요.
- 대상 파일 외의 파일은 수정하지 마세요.
- 작업이 끝나면 "[완료] <한 줄 요약>" 형태로 마무리하세요.
- 다른 에이전트의 작업 영역을 침범하지 마세요.
"""

ORCHESTRATOR_MERGER_PROMPT = """당신은 멀티-에이전트 결과를 통합하는 리뷰어입니다.
아래 각 에이전트의 작업 결과를 검토하고:
1. 성공/실패 여부를 판정
2. 충돌이나 누락이 있으면 지적
3. 사용자에게 전달할 최종 요약 보고서를 마크다운으로 작성

보고서 형식:
## ✅ 최종 통합 결과
| 에이전트 | 역할 | 상태 | 요약 |
|---------|------|------|------|
...

### 세부 사항
- (각 에이전트별 핵심 변경점)

### ⚠️ 주의/후속 작업
- (있다면)
"""


async def _orchestrator_plan(gw, stream_model, user_prompt: str, system_prompt: str, max_agents: int) -> dict:
    """Planner 호출 — subtask 분해."""
    plan_sys = ORCHESTRATOR_PLANNER_PROMPT.format(max_agents=max_agents)
    if system_prompt:
        plan_sys = system_prompt + "\n\n" + plan_sys
    messages = [{"role": "user", "content": [{"text": user_prompt}]}]
    result = await asyncio.wait_for(
        gw.converse(model_id=stream_model, messages=messages, system_prompt=plan_sys),
        timeout=120,
    )
    if result.get("decision") != "ALLOW":
        raise RuntimeError(f"Planner 실패: {result.get('error') or result.get('decision')}")
    output = result.get("output", {}).get("message", {}).get("content", [])
    text = "\n".join(c.get("text", "") for c in output if "text" in c).strip()
    # JSON 추출
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise RuntimeError(f"Planner 응답에서 JSON을 찾을 수 없음: {text[:300]}")
    plan = json.loads(m.group(0))
    if not isinstance(plan.get("subtasks"), list) or not plan["subtasks"]:
        raise RuntimeError("Planner가 subtasks를 생성하지 않았습니다.")
    return plan


async def _orchestrator_run_agent(
    gw, stream_model: str, subtask: dict, project_path: str,
    base_system_prompt: str, emit_queue: asyncio.Queue,
):
    """하나의 하위 에이전트 실행 — 도구 루프 포함."""
    task_id = subtask.get("id", "?")
    role = subtask.get("role", "Worker")
    title = subtask.get("title", "")
    description = subtask.get("description", "")
    target_files = subtask.get("target_files", [])

    sys_prompt = ORCHESTRATOR_AGENT_PROMPT.format(
        role=role, task_id=task_id, title=title,
        target_files=", ".join(target_files) if target_files else "(없음)",
        description=description,
    )
    if base_system_prompt:
        sys_prompt = base_system_prompt + "\n\n" + sys_prompt

    messages = [{"role": "user", "content": [{"text": f"작업 {task_id} ({role}): {title}\n\n{description}"}]}]
    max_turns = int(os.environ.get("AE_MAX_ORCH_TURNS", "50"))
    final_text_parts = []
    tool_log = []

    await emit_queue.put({"type": "agent_start", "taskId": task_id, "role": role, "title": title, "targetFiles": target_files})

    try:
        for turn in range(max_turns):
            text_parts = []
            tool_use_blocks = []
            current_tool = {}
            stop_reason = ""

            try:
                async for evt in gw.stream_sse_realtime(
                    model_id=stream_model, messages=messages,
                    system_prompt=sys_prompt, tool_config=AGENT_TOOLS,
                ):
                    etype = evt.get("type", "")
                    if etype == "content_block_delta":
                        delta = evt.get("delta", {})
                        if "text" in delta:
                            text_parts.append(delta["text"])
                            await emit_queue.put({"type": "agent_delta", "taskId": task_id, "text": delta["text"]})
                        elif "toolUse" in delta:
                            if current_tool:
                                current_tool["_input_json"] = current_tool.get("_input_json", "") + delta["toolUse"].get("input", "")
                    elif etype == "content_block_start":
                        cb = evt.get("content_block") or evt.get("contentBlock") or {}
                        if "toolUse" in cb:
                            tu = cb["toolUse"]
                            current_tool = {"toolUseId": tu.get("toolUseId", ""), "name": tu.get("name", ""), "_input_json": ""}
                    elif etype == "content_block_stop":
                        if current_tool and current_tool.get("name"):
                            try:
                                inp = json.loads(current_tool.get("_input_json", "{}"))
                            except json.JSONDecodeError:
                                inp = {}
                            tool_use_blocks.append({"toolUse": {"toolUseId": current_tool["toolUseId"], "name": current_tool["name"], "input": inp}})
                            current_tool = {}
                    elif etype in ("message_delta", "message_stop"):
                        stop_reason = evt.get("delta", {}).get("stopReason", "") or evt.get("stop_reason", "") or evt.get("stopReason", "")
                    elif etype == "error":
                        await emit_queue.put({"type": "agent_error", "taskId": task_id, "error": evt.get("message", "")})
            except Exception as e:
                await emit_queue.put({"type": "agent_error", "taskId": task_id, "error": str(e)})
                break

            content_blocks = []
            if text_parts:
                content_blocks.append({"text": "".join(text_parts)})
                final_text_parts.extend(text_parts)
            content_blocks.extend(tool_use_blocks)
            if not content_blocks:
                break
            messages.append({"role": "assistant", "content": content_blocks})
            if not tool_use_blocks:
                if stop_reason == "max_tokens" and text_parts and turn < max_turns - 1:
                    print(f"[Orchestrator] max_tokens 도달 — 이어서 생성 (task={task_id}, turn={turn+1})")
                    messages.append({"role": "user", "content": [{"text": "계속 이어서 작성해주세요."}]})
                    continue
                break

            tool_results = []
            for block in tool_use_blocks:
                tu = block["toolUse"]
                tname = tu.get("name", "")
                tid = tu.get("toolUseId", "")
                tinput = tu.get("input", {})
                await emit_queue.put({"type": "agent_tool", "taskId": task_id, "tool": tname, "input": tinput, "status": "running"})
                tout = await asyncio.to_thread(_execute_tool, tname, tinput, project_path, aws_profile, bedrock_user)  # [patched-credentials]
                tool_log.append({"name": tname, "input": tinput, "output": tout[:400]})
                await emit_queue.put({"type": "agent_tool", "taskId": task_id, "tool": tname, "status": "done", "output": tout[:300]})
                _tr_max = int(os.environ.get("AE_TOOL_RESULT_MAX", "80000"))
                tool_results.append({"toolResult": {"toolUseId": tid, "content": [{"text": tout[:_tr_max]}]}})
            messages.append({"role": "user", "content": tool_results})

        final_text = "".join(final_text_parts).strip()
        await emit_queue.put({"type": "agent_done", "taskId": task_id, "summary": final_text[-1200:], "toolCount": len(tool_log)})
        return {"taskId": task_id, "role": role, "title": title, "status": "done", "summary": final_text, "tools": tool_log}
    except Exception as e:
        await emit_queue.put({"type": "agent_error", "taskId": task_id, "error": str(e)})
        return {"taskId": task_id, "role": role, "title": title, "status": "error", "summary": str(e), "tools": tool_log}


async def _orchestrator_merge(gw, stream_model, user_prompt, agent_results: list, base_system_prompt: str) -> str:
    """Merger 호출 — 최종 보고서 생성."""
    summary_input = {
        "userRequest": user_prompt[:2000],
        "agents": [
            {
                "taskId": r["taskId"], "role": r["role"], "title": r["title"],
                "status": r["status"], "summary": (r.get("summary") or "")[:1500],
                "toolCount": len(r.get("tools", [])),
            }
            for r in agent_results
        ],
    }
    sys_prompt = ORCHESTRATOR_MERGER_PROMPT
    if base_system_prompt:
        sys_prompt = base_system_prompt + "\n\n" + sys_prompt
    messages = [{"role": "user", "content": [{"text": json.dumps(summary_input, ensure_ascii=False, indent=2)}]}]
    try:
        result = await asyncio.wait_for(
            gw.converse(model_id=stream_model, messages=messages, system_prompt=sys_prompt),
            timeout=120,
        )
        if result.get("decision") == "ALLOW":
            output = result.get("output", {}).get("message", {}).get("content", [])
            return "\n".join(c.get("text", "") for c in output if "text" in c).strip()
        return f"(Merger 실패: {result.get('error') or result.get('decision')})"
    except Exception as e:
        return f"(Merger 예외: {e})"


@app.post("/api/agents/run-orchestrated")
async def run_agent_orchestrated(request: Request):
    """Multi-Agent Role Split — Planner → N Parallel Agents (with tools) → Merger.

    요청 본문:
    {
      "prompt": "...",
      "plannerModel": "claude-opus-4-...",   // 선택
      "workerModel":  "claude-sonnet-4-6",   // 선택
      "mergerModel":  "claude-opus-4-...",   // 선택
      "maxAgents": 4,
      "awsProfile": "bedrock-gw",
      "bedrockUser": "...",
      "projectPath": "...",
      "openFile": "...", "openFileContent": "...",
      "systemPrompt": "...",                 // 선택 (스킬)
      "chatHistory": [...], "sessionId": "..."
    }

    응답: SSE
      data: {"type":"plan", "subtasks":[...]}
      data: {"type":"agent_start", "taskId":"A", ...}
      data: {"type":"agent_delta", "taskId":"A", "text":"..."}
      data: {"type":"agent_tool",  "taskId":"A", "tool":"write_file", "status":"running|done", ...}
      data: {"type":"agent_done",  "taskId":"A", "summary":"...", "toolCount":3}
      data: {"type":"merge", "report":"..."}
      data: [DONE]
    """
    body = await request.json()
    user_prompt = body.get("prompt", "")
    planner_model = body.get("plannerModel") or body.get("model") or "claude-sonnet-4-6"
    worker_model = body.get("workerModel") or body.get("model") or "claude-sonnet-4-6"
    merger_model = body.get("mergerModel") or planner_model
    max_agents = int(body.get("maxAgents", 4))
    base_sys = body.get("systemPrompt", "") or ""
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    project_path = body.get("projectPath", "")
    open_file = body.get("openFile", "")
    open_file_content = body.get("openFileContent", "")

    gw = _get_gw(aws_profile, bedrock_user)

    def _with_prefix(mid: str) -> str:
        return mid if mid.startswith("us.") or mid.startswith("eu.") else f"us.{mid}"

    planner_id = _with_prefix(planner_model)
    worker_id = _with_prefix(worker_model)
    merger_id = _with_prefix(merger_model)

    # RAG (코드 관련 질문만)
    if project_path and _is_code_related(user_prompt):
        try:
            from ai_engine.rag.context_builder import build_system_prompt
            base_sys = build_system_prompt(
                project_path=project_path, query=user_prompt,
                open_file=open_file, open_file_content=open_file_content,
                base_system_prompt=base_sys,
                aws_profile=aws_profile, bedrock_user=bedrock_user, gateway_client=gw,
            )
        except Exception as e:
            print(f"[Orchestrator] RAG 실패(무시): {e}")

    async def orchestrated_stream():
        emit_queue: asyncio.Queue = asyncio.Queue()

        async def pipeline():
            # 1) Planner
            try:
                plan = await _orchestrator_plan(gw, planner_id, user_prompt, base_sys, max_agents)
            except Exception as e:
                await emit_queue.put({"type": "error", "stage": "planner", "message": str(e)})
                await emit_queue.put({"type": "__END__"})
                return

            subtasks = plan["subtasks"][:max_agents]
            await emit_queue.put({"type": "plan", "subtasks": subtasks})

            # 2) Parallel Agents
            tasks = [
                asyncio.create_task(
                    _orchestrator_run_agent(gw, worker_id, st, project_path, base_sys, emit_queue)
                )
                for st in subtasks
            ]
            agent_results = await asyncio.gather(*tasks, return_exceptions=False)

            # 3) Merger
            report = await _orchestrator_merge(gw, merger_id, user_prompt, agent_results, base_sys)
            await emit_queue.put({"type": "merge", "report": report, "results": agent_results})
            await emit_queue.put({"type": "__END__"})

        pipe_task = asyncio.create_task(pipeline())

        try:
            while True:
                evt = await emit_queue.get()
                if evt.get("type") == "__END__":
                    break
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
        finally:
            if not pipe_task.done():
                pipe_task.cancel()
            yield "data: [DONE]\n\n"

    return StreamingResponse(orchestrated_stream(), media_type="text/event-stream")


@app.post("/api/agents/run")
async def run_agent(request: Request):
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-sonnet-4-6")
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    try:
        gw = _get_gw(aws_profile, bedrock_user)
        result = await gw.converse(
            model_id=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/agents/workflow")
async def run_workflow(request: Request):
    """Run full agent workflow: Plan → Code → Review → Execute."""
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-3-5-sonnet-20241022-v2:0")

    try:
        from ai_engine.gateway_module import GatewayClient
        from ai_engine.agent_system.agent_graph import build_graph

        gw = GatewayClient(
            gateway_url=os.environ.get(
                "GATEWAY_URL",
                "https://5l764dh7y9.execute-api.us-west-2.amazonaws.com/v1",
            ),
            aws_profile=os.environ.get("AWS_PROFILE", "default"),
        )
        graph = build_graph(gw)
        from ai_engine.agent_system.state import AgentState

        state = AgentState(task=prompt)
        result = await graph.ainvoke(state)
        await gw.close()
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


_quota_cache = {"used_krw": 0, "remaining_krw": 0, "limit_krw": 0, "last_updated": "", "user": "", "fetching": False}

@app.get("/api/quota")
async def get_quota(request: Request):
    profile = request.query_params.get("profile", os.environ.get("AWS_PROFILE", "default"))
    user = request.query_params.get("user", "")
    # 첫 호출이면 백그라운드에서 quota 조회 시작 (즉시 응답 반환)
    if _quota_cache["remaining_krw"] == 0 and user and not _quota_cache["fetching"]:
        _quota_cache["fetching"] = True
        asyncio.create_task(_fetch_quota_background(profile, user))
    return {
        "user": _quota_cache["user"] or user,
        "used_krw": round(_quota_cache["used_krw"], 2),
        "remaining_krw": round(_quota_cache["remaining_krw"], 2),
        "limit_krw": round(_quota_cache["limit_krw"], 2),
        "last_updated": _quota_cache["last_updated"],
    }


async def _fetch_quota_background(profile, user):
    """백그라운드에서 Gateway 호출하여 quota 정보 캐시."""
    try:
        gw = _get_gw(profile, user)
        print(f"[Quota] 백그라운드 quota 조회 시작...")
        # Gateway /converse 직접 호출 — maxTokens:1로 최소 비용
        # us. prefix haiku 4.5 우선, 실패 시 haiku 3 fallback
        quota_models = [
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            "us.anthropic.claude-3-haiku-20240307-v1:0",
        ]
        for mid in quota_models:
            try:
                result = await asyncio.wait_for(
                    gw.converse_quota_only(
                        model_id=mid,
                        messages=[{"role": "user", "content": [{"text": "hi"}]}],
                    ),
                    timeout=15
                )
                print(f"[Quota] {mid}: decision={result.get('decision')}, remaining_quota={result.get('remaining_quota')}, error={result.get('error', '')[:100]}")
                _extract_quota(result, user)
                if _quota_cache["remaining_krw"] > 0:
                    print(f"[Quota] 성공! remaining={_quota_cache['remaining_krw']}")
                    return
            except Exception as e:
                print(f"[Quota] {mid} 실패: {e}")
                continue
        print(f"[Quota] 모든 모델 실패 — 첫 채팅 후 자동 갱신")
    except Exception as e:
        print(f"[Quota] 백그라운드 조회 실패: {e}")
    finally:
        _quota_cache["fetching"] = False


def _extract_quota(result, user=""):
    """Gateway 응답에서 quota 정보를 추출하여 캐시에 저장."""
    rq = result.get("remaining_quota", {})
    if rq:
        # 다양한 키 이름 대응
        cost_val = 0
        if isinstance(rq, (int, float)):
            cost_val = rq
        elif isinstance(rq, dict):
            cost_val = rq.get("cost_krw") or rq.get("remaining_cost_krw") or rq.get("remaining_krw") or rq.get("remaining") or 0
            if not cost_val:
                for v in rq.values():
                    if isinstance(v, (int, float)) and v > 0:
                        cost_val = v
                        break
        if cost_val > 0:
            _quota_cache["remaining_krw"] = cost_val
            _quota_cache["limit_krw"] = cost_val + _quota_cache["used_krw"]
            _quota_cache["user"] = user
            _quota_cache["last_updated"] = datetime.utcnow().isoformat()
            print(f"[Quota] 캐시 갱신 성공: remaining={cost_val}")
    if result.get("estimated_cost_krw"):
        _quota_cache["used_krw"] += result["estimated_cost_krw"]
        if _quota_cache["remaining_krw"] > 0:
            _quota_cache["limit_krw"] = _quota_cache["remaining_krw"] + _quota_cache["used_krw"]
