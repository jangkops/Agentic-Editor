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
            },
            {
                "toolSpec": {
                    "name": "generate_xlsx",
                    "description": "Generate an Excel workbook (XLSX) using openpyxl. Each sheet has headers (first row, bold) and rows of data. Saves to .generated/.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "File title (used in slug + first-sheet header). e.g., 'sales-report'"},
                                "sheets": {
                                    "type": "array",
                                    "description": "List of sheets",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string", "description": "Sheet name (max 31 chars)"},
                                            "headers": {"type": "array", "items": {"type": "string"}, "description": "Header row (bold, accent fill)"},
                                            "rows": {
                                                "type": "array",
                                                "description": "Data rows — array of arrays. Cells can be string/number/bool.",
                                                "items": {"type": "array"}
                                            }
                                        }
                                    }
                                }
                            },
                            "required": ["title", "sheets"]
                        }
                    }
                }
            },
            {
                "toolSpec": {
                    "name": "generate_docx",
                    "description": "Generate a Word document (DOCX) using python-docx. Sections support headings (h1/h2/h3) and body paragraphs. Saves to .generated/.",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string", "description": "Document title (cover heading)"},
                                "sections": {
                                    "type": "array",
                                    "description": "List of sections",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "heading": {"type": "string"},
                                            "level": {"type": "integer", "description": "Heading level 1-3 (default 2)"},
                                            "body": {"type": "string", "description": "Section body. Newlines split into paragraphs."},
                                            "bullets": {"type": "array", "items": {"type": "string"}, "description": "Optional bullet list"}
                                        }
                                    }
                                }
                            },
                            "required": ["title", "sections"]
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

# Image generation model preference chain — ordered by general quality/recency
IMAGE_MODELS = [
    "stability.stable-image-ultra-v1:1",   # 최고 품질 (사진/리얼리즘)
    "stability.sd3-5-large-v1:0",          # 범용 고품질 (SD 3.5 Large)
    "stability.stable-image-core-v1:1",    # 고속/저비용
    "amazon.nova-canvas-v1:0",             # AWS 네이티브 (안정성)
    "amazon.titan-image-generator-v2:0",   # 보수적 fallback
]
IMAGE_EDIT_MODELS = [
    "amazon.titan-image-generator-v2:0",
    "amazon.nova-canvas-v1:0",
]


def _select_image_models(prompt: str, hint: str = "") -> list:
    """프롬프트 분석으로 이미지 모델 우선순위 결정.

    사용자가 "claude로 그려줘" 라고 해도 이 함수가 실제 이미지 생성 모델을
    프롬프트 키워드 기반으로 다시 선정합니다. 반환 리스트는 fallback 순서.

    Args:
        prompt: 이미지 생성 프롬프트
        hint: optional style/category hint (e.g., "photo", "diagram")

    Returns:
        list of model ids in preference order — first one is "best fit",
        나머지는 첫 번째 실패 시 fallback.
    """
    p = (prompt or "").lower()
    h = (hint or "").lower()
    text = p + " " + h

    # Photographic / 사진 / 리얼리즘 → Stability Ultra (최고 사진 품질)
    photo_kw = ("photo", "photograph", "realistic", "사진", "리얼리즘", "리얼한",
                "human", "portrait", "초상", "얼굴", "people", "사람",
                "cinematic", "영화", "studio", "현실적")
    # 다이어그램 / 차트 / UI / 스크린샷 → SD 3.5 Large (텍스트 렌더링 강함)
    diagram_kw = ("diagram", "chart", "flowchart", "architecture", "아키텍처",
                  "ui", "wireframe", "screenshot", "scheme", "schematic",
                  "다이어그램", "차트", "플로우차트", "구조도", "도식", "와이어프레임")
    # 일러스트 / 애니메이션 → Stability Ultra (스타일 다양)
    art_kw = ("illustration", "anime", "cartoon", "manga", "vector", "애니",
              "일러스트", "만화", "벡터", "그림체", "디지털 아트")
    # 로고 / 아이콘 / 단순 그래픽 → Stable Image Core (빠르고 저렴)
    logo_kw = ("logo", "icon", "emblem", "favicon", "아이콘", "로고",
               "심볼", "엠블럼", "minimal", "심플")
    # 기업 / 회의 / 비즈니스 자료 → Nova Canvas (AWS, 안전)
    biz_kw = ("business", "corporate", "presentation", "slide",
              "기업", "회사", "비즈니스", "회의", "보고서")

    def has_any(words):
        return any(w in text for w in words)

    # 우선순위 계산
    if has_any(diagram_kw):
        # 다이어그램 — SD 3.5 Large가 텍스트 렌더링이 가장 우수
        return [
            "stability.sd3-5-large-v1:0",
            "stability.stable-image-ultra-v1:1",
            "amazon.titan-image-generator-v2:0",
            "amazon.nova-canvas-v1:0",
        ]
    if has_any(photo_kw):
        # 사진 — Ultra가 최고 사진 품질
        return [
            "stability.stable-image-ultra-v1:1",
            "stability.sd3-5-large-v1:0",
            "amazon.nova-canvas-v1:0",
            "amazon.titan-image-generator-v2:0",
        ]
    if has_any(logo_kw):
        # 로고 — Core가 빠르고 단순한 그래픽에 적합
        return [
            "stability.stable-image-core-v1:1",
            "stability.sd3-5-large-v1:0",
            "amazon.titan-image-generator-v2:0",
        ]
    if has_any(art_kw):
        # 일러스트 — Ultra의 스타일 다양성
        return [
            "stability.stable-image-ultra-v1:1",
            "stability.sd3-5-large-v1:0",
            "amazon.nova-canvas-v1:0",
        ]
    if has_any(biz_kw):
        # 비즈니스 자료 — Nova Canvas (안전, 보수적)
        return [
            "amazon.nova-canvas-v1:0",
            "stability.sd3-5-large-v1:0",
            "stability.stable-image-ultra-v1:1",
        ]
    # 기본 — 일반 fallback chain
    return list(IMAGE_MODELS)


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

    # 프롬프트 분석으로 이미지 모델 우선순위 동적 결정
    selected_models = _select_image_models(prompt, hint=style)

    last_error = ""
    for model_id in selected_models:
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
    # [hint-image-route] 게이트웨이 라우트/IAM 차단 시 사용자에게 명확한 안내 제공
    hint = ""
    if any(t in detail for t in ("execute-api:Invoke", "principal identity", "HTTP 403", "HTTP 404")):
        hint = "현재 게이트웨이가 이미지 생성 라우트(/invoke-model)를 지원하지 않습니다. 관리자에게 활성화를 요청하세요."
    payload = {"error": "model-unavailable", "detail": detail}
    if hint:
        payload["hint"] = hint
    return json.dumps(payload)


def _normalize_doc_input(tool_input: dict, default_kind: str = "sections"):
    """모델이 도구 input의 shape을 헷갈려서 보내도 정상 형태로 정규화.

    PDF는 sections, PPTX는 slides, XLSX는 sheets, DOCX는 sections를 기대하지만
    LLM이 종종 다른 키 이름이나 잘못된 구조로 보냄. 이 함수가 유연하게 받아
    공통 형태로 변환한다.

    Args:
        tool_input: 모델이 보낸 raw input dict
        default_kind: 'sections' | 'slides' | 'sheets'

    Returns:
        (title: str, items: list[dict])
        items의 형태:
        - sections: [{heading, body, bullets, level?}]
        - slides:   [{title, bullets, body?}]
        - sheets:   [{name, headers, rows}]
    """
    if not isinstance(tool_input, dict):
        return "", []

    title = (
        tool_input.get("title")
        or tool_input.get("name")
        or tool_input.get("heading")
        or tool_input.get("file_title")
        or "Untitled"
    )
    title = str(title).strip()

    # 후보 키 — LLM이 다양한 이름으로 보낼 수 있음
    candidate_keys = ["sections", "slides", "sheets", "pages", "items",
                      "content", "data", "body", "rows", default_kind]
    raw_items = None
    for k in candidate_keys:
        v = tool_input.get(k)
        if isinstance(v, list) and v:
            raw_items = v
            break

    # 단일 dict로 보낸 경우 (예: {"section": {...}})
    if raw_items is None:
        for k in ("section", "slide", "sheet", "page"):
            v = tool_input.get(k)
            if isinstance(v, dict):
                raw_items = [v]
                break

    # 문자열로 본문만 보낸 경우 — 단일 섹션으로 wrap
    if raw_items is None:
        body = tool_input.get("body") or tool_input.get("text") or tool_input.get("description")
        if body:
            return title, [{"heading": title, "body": str(body)}]
        return title, []

    # 각 item 정규화
    out = []
    for item in raw_items:
        if not isinstance(item, dict):
            # 문자열 item — 본문으로 처리
            out.append({"heading": "", "body": str(item)})
            continue
        n = dict(item)  # shallow copy
        # Common: heading aliases
        if "heading" not in n:
            n["heading"] = n.get("title") or n.get("name") or n.get("header") or ""
        # Common: body aliases
        if "body" not in n:
            n["body"] = n.get("content") or n.get("text") or n.get("description") or ""
        # Common: bullets aliases
        if "bullets" not in n:
            n["bullets"] = n.get("points") or n.get("items") or n.get("list") or []
        if not isinstance(n.get("bullets"), list):
            n["bullets"] = []
        # Slides: ensure title (PPTX uses 'title' as slide title)
        if default_kind == "slides":
            n["title"] = n.get("title") or n.get("heading") or ""
        # Sheets: ensure name/headers/rows
        if default_kind == "sheets":
            n["name"] = n.get("name") or n.get("title") or n.get("sheet") or "Sheet1"
            n["headers"] = n.get("headers") or n.get("columns") or n.get("header") or []
            if not isinstance(n["headers"], list):
                n["headers"] = []
            n["rows"] = n.get("rows") or n.get("data") or []
            if not isinstance(n["rows"], list):
                n["rows"] = []
        out.append(n)

    return title, out


async def _tool_generate_pdf(tool_input: dict, project_path: str) -> str:
    """Generate a PDF document using reportlab. Accepts lenient input shapes."""
    title, sections = _normalize_doc_input(tool_input, default_kind="sections")

    if not title:
        return json.dumps({"error": "title is required"})
    if not sections:
        return json.dumps({"error": "sections is required"})

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
    """Generate a PowerPoint presentation using python-pptx. Accepts lenient input shapes."""
    title, slides_data = _normalize_doc_input(tool_input, default_kind="slides")

    if not title:
        return json.dumps({"error": "title is required"})
    if not slides_data:
        return json.dumps({"error": "slides is required"})

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


async def _tool_generate_xlsx(tool_input: dict, project_path: str) -> str:
    """Generate an Excel workbook (.xlsx) using openpyxl. Accepts lenient input shapes."""
    title, sheets_data = _normalize_doc_input(tool_input, default_kind="sheets")

    if not title:
        return json.dumps({"error": "title is required"})
    if not sheets_data:
        return json.dumps({"error": "sheets is required"})

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        return json.dumps({"error": "missing-dep", "lib": "openpyxl", "hint": "pip install openpyxl"})

    import time as _t, re as _re
    _local_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
    gen_dir = os.path.join(_local_root, ".generated")
    os.makedirs(gen_dir, exist_ok=True)
    slug = _re.sub(r"[^a-z0-9]+", "-", title.lower())[:30].strip("-") or "workbook"
    ts = str(int(_t.time() * 1000))
    filename = f"{slug}-{ts}.xlsx"
    output_path = os.path.join(gen_dir, filename)
    relative_path = f".generated/{filename}"

    try:
        wb = Workbook()
        wb.remove(wb.active)  # remove default empty sheet
        header_font = Font(bold=True, color="FFFFFFFF")
        header_fill = PatternFill(start_color="FF007ACC", end_color="FF007ACC", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center")
        thin = Side(border_style="thin", color="FFCCCCCC")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for i, sd in enumerate(sheets_data):
            if not isinstance(sd, dict):
                sd = {"name": f"Sheet{i+1}", "headers": [], "rows": []}
            sheet_name = (sd.get("name") or f"Sheet{i+1}")[:31]
            ws = wb.create_sheet(title=sheet_name)
            headers = sd.get("headers") or []
            rows = sd.get("rows") or []
            # Headers
            for c, h in enumerate(headers, start=1):
                cell = ws.cell(row=1, column=c, value=str(h))
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align
                cell.border = border
            # Data rows
            for r, row in enumerate(rows, start=2):
                if not isinstance(row, list):
                    row = [row]
                for c, val in enumerate(row, start=1):
                    cell = ws.cell(row=r, column=c, value=val)
                    cell.border = border
            # Auto column widths (cap at 60 chars)
            for c in range(1, max(1, len(headers)) + 1):
                col_letter = get_column_letter(c)
                max_len = len(str(headers[c-1])) if c <= len(headers) else 8
                for row in rows:
                    if isinstance(row, list) and c <= len(row):
                        max_len = max(max_len, len(str(row[c-1])))
                ws.column_dimensions[col_letter].width = min(max_len + 2, 60)

        if not wb.sheetnames:
            # Empty input — create a placeholder sheet so the file is valid
            ws = wb.create_sheet(title="Sheet1")
            ws.cell(row=1, column=1, value=title)

        wb.save(output_path)
        size_bytes = os.path.getsize(output_path)
        return json.dumps({
            "path": relative_path,
            "model": "openpyxl",
            "sheetCount": len(sheets_data),
            "sizeBytes": size_bytes,
        })
    except Exception as e:
        return json.dumps({"error": "xlsx-generation-failed", "detail": str(e)[:200]})


async def _tool_generate_docx(tool_input: dict, project_path: str) -> str:
    """Generate a Word document (.docx) using python-docx. Accepts lenient input shapes."""
    title, sections = _normalize_doc_input(tool_input, default_kind="sections")

    if not title:
        return json.dumps({"error": "title is required"})
    if not sections:
        return json.dumps({"error": "sections is required"})

    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError:
        return json.dumps({"error": "missing-dep", "lib": "python-docx", "hint": "pip install python-docx"})

    import time as _t, re as _re
    _local_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()
    gen_dir = os.path.join(_local_root, ".generated")
    os.makedirs(gen_dir, exist_ok=True)
    slug = _re.sub(r"[^a-z0-9]+", "-", title.lower())[:30].strip("-") or "doc"
    ts = str(int(_t.time() * 1000))
    filename = f"{slug}-{ts}.docx"
    output_path = os.path.join(gen_dir, filename)
    relative_path = f".generated/{filename}"

    try:
        doc = Document()
        # Default font tweak — readable size
        try:
            style = doc.styles["Normal"]
            style.font.size = Pt(11)
        except Exception:
            pass

        doc.add_heading(title, level=0)

        for sec in sections:
            if not isinstance(sec, dict):
                doc.add_paragraph(str(sec))
                continue
            heading = sec.get("heading", "")
            level = int(sec.get("level") or 2)
            level = max(1, min(level, 3))
            body = sec.get("body", "")
            bullets = sec.get("bullets") or []

            if heading:
                doc.add_heading(heading, level=level)
            if body:
                for para in str(body).split("\n"):
                    if para.strip():
                        doc.add_paragraph(para)
            for b in bullets:
                doc.add_paragraph(str(b), style="List Bullet")

        doc.save(output_path)
        size_bytes = os.path.getsize(output_path)
        # python-docx exposes paragraphs via doc.paragraphs
        para_count = len(doc.paragraphs)
        return json.dumps({
            "path": relative_path,
            "model": "python-docx",
            "sectionCount": len(sections),
            "paragraphCount": para_count,
            "sizeBytes": size_bytes,
        })
    except Exception as e:
        return json.dumps({"error": "docx-generation-failed", "detail": str(e)[:200]})


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

        # [hint-image-route]
        _det = (last_error or "all inpaint models failed")[:200]
        _payload = {"error": "model-unavailable", "detail": _det}
        if any(t in _det for t in ("execute-api:Invoke", "principal identity", "HTTP 403", "HTTP 404")):
            _payload["hint"] = "현재 게이트웨이가 이미지 편집(inpaint) 라우트(/invoke-model)를 지원하지 않습니다. 관리자에게 활성화를 요청하세요."
        return json.dumps(_payload)

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

    # [hint-image-route]
    _det = (last_error or "all outpaint models failed")[:200]
    _payload = {"error": "model-unavailable", "detail": _det}
    if any(t in _det for t in ("execute-api:Invoke", "principal identity", "HTTP 403", "HTTP 404")):
        _payload["hint"] = "현재 게이트웨이가 이미지 편집(outpaint) 라우트(/invoke-model)를 지원하지 않습니다. 관리자에게 활성화를 요청하세요."
    return json.dumps(_payload)



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
    if tool_name in ("generate_image", "generate_pdf", "generate_pptx", "generate_xlsx", "generate_docx", "edit_image"):
        try:
            import asyncio as _asyncio
            if tool_name == "generate_image":
                return _asyncio.run(_tool_generate_image(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))
            if tool_name == "generate_pdf":
                return _asyncio.run(_tool_generate_pdf(tool_input, project_path))
            if tool_name == "generate_pptx":
                return _asyncio.run(_tool_generate_pptx(tool_input, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user))
            if tool_name == "generate_xlsx":
                return _asyncio.run(_tool_generate_xlsx(tool_input, project_path))
            if tool_name == "generate_docx":
                return _asyncio.run(_tool_generate_docx(tool_input, project_path))
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

        skip_output = []  # IMAGE/VIDEO/EMBEDDING 별도 카탈로그로 분리
        image_catalog = {}
        seen_image_keys = set()
        video_catalog = {}
        seen_video_keys = set()
        embed_catalog = {}
        seen_embed_keys = set()
        rerank_catalog = {}
        seen_rerank_keys = set()
        for m in resp.get("modelSummaries", []):
            modes = m.get("outputModalities", [])
            if m.get("modelLifecycle", {}).get("status") in ["EOL"]:
                continue
            input_modes = m.get("inputModalities", [])

            _mid_raw = m["modelId"]
            _no_region = _mid_raw
            for _pfx in ("us.", "eu.", "global."):
                if _no_region.startswith(_pfx):
                    _no_region = _no_region[len(_pfx):]
                    break

            # === Embedding models ===
            if "EMBEDDING" in modes or "embed" in _no_region.lower():
                if _no_region in seen_embed_keys:
                    continue
                seen_embed_keys.add(_no_region)
                inference_types = m.get("inferenceTypesSupported", [])
                if inference_types and "ON_DEMAND" not in inference_types and "INFERENCE_PROFILE" not in inference_types:
                    continue
                callable_id = _mid_raw
                if callable_profile_ids is not None and "ON_DEMAND" not in inference_types:
                    for pid in callable_profile_ids:
                        if pid.endswith(_mid_raw) or pid.endswith(f"{_mid_raw}:0") or _mid_raw in pid:
                            callable_id = pid
                            break
                _prov = m.get("providerName", "Unknown")
                if _prov not in embed_catalog:
                    embed_catalog[_prov] = []
                embed_catalog[_prov].append({"id": callable_id, "name": m.get("modelName", _mid_raw)})
                continue

            # === Rerank models ===
            if "rerank" in _no_region.lower():
                if _no_region in seen_rerank_keys:
                    continue
                seen_rerank_keys.add(_no_region)
                inference_types = m.get("inferenceTypesSupported", [])
                if inference_types and "ON_DEMAND" not in inference_types and "INFERENCE_PROFILE" not in inference_types:
                    continue
                callable_id = _mid_raw
                if callable_profile_ids is not None and "ON_DEMAND" not in inference_types:
                    for pid in callable_profile_ids:
                        if pid.endswith(_mid_raw) or pid.endswith(f"{_mid_raw}:0") or _mid_raw in pid:
                            callable_id = pid
                            break
                _prov = m.get("providerName", "Unknown")
                if _prov not in rerank_catalog:
                    rerank_catalog[_prov] = []
                rerank_catalog[_prov].append({"id": callable_id, "name": m.get("modelName", _mid_raw)})
                continue

            # === Video generation models ===
            if "VIDEO" in modes:
                if _no_region in seen_video_keys:
                    continue
                seen_video_keys.add(_no_region)
                inference_types = m.get("inferenceTypesSupported", [])
                if inference_types and "ON_DEMAND" not in inference_types and "INFERENCE_PROFILE" not in inference_types:
                    continue
                callable_id = _mid_raw
                if callable_profile_ids is not None and "ON_DEMAND" not in inference_types:
                    for pid in callable_profile_ids:
                        if pid.endswith(_mid_raw) or pid.endswith(f"{_mid_raw}:0") or _mid_raw in pid:
                            callable_id = pid
                            break
                _prov = m.get("providerName", "Unknown")
                if _prov not in video_catalog:
                    video_catalog[_prov] = []
                video_catalog[_prov].append({"id": callable_id, "name": m.get("modelName", _mid_raw)})
                continue

            # === Image generation/edit models ===
            if "IMAGE" in modes:
                _mid = m["modelId"]
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
            if "TEXT" not in input_modes:
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
            "video_models": video_catalog,
            "embed_models": embed_catalog,
            "rerank_models": rerank_catalog,
            "count": sum(len(v) for v in catalog.values()),
            "image_count": sum(len(v) for v in image_catalog.values()),
            "video_count": sum(len(v) for v in video_catalog.values()),
            "embed_count": sum(len(v) for v in embed_catalog.values()),
            "rerank_count": sum(len(v) for v in rerank_catalog.values()),
        })
    except Exception as e:
        return JSONResponse(content={"models": {}, "error": str(e)})


# ─────────────────────────────────────────────────────────────────
# Intent Classifier — LLM 기반 의도 분류
# 사용자 메시지를 분석하여 최적 실행 전략을 결정한다.
# ─────────────────────────────────────────────────────────────────

INTENT_CLASSIFIER_PROMPT = """사용자 요청을 분석하여 최적 실행 전략을 결정하세요.

[분류 카테고리]
- file_generation: 실제 파일을 생성/수정해야 함 (PDF/XLSX/DOCX/PPTX/HWP/이미지/코드파일)
- code_change: 기존 코드 파일을 수정/리팩토링/디버깅
- analysis: 코드/문서/데이터 분석, 설명, 리뷰
- generation_text: 글/콘텐츠/아이디어 생성 (파일 저장 불필요)
- simple_qa: 간단한 질문, 사실 확인, 짧은 응답

[복잡도]
- simple: 1-2 문단 응답으로 충분
- moderate: 여러 단계 추론 필요
- complex: 다중 파일/모듈/긴 작업

[병렬 유용성]
- true: 여러 모델의 다른 관점 비교가 가치 있음 (분석, 창작, 리뷰)
- false: 정답이 하나거나 도구 실행이 필수 (파일 생성, 코드 수정)

반드시 아래 JSON 형식으로만 응답하세요 (마크다운 없이):
{
  "intent": "file_generation" | "code_change" | "analysis" | "generation_text" | "simple_qa",
  "needs_tools": true | false,
  "complexity": "simple" | "moderate" | "complex",
  "parallel_useful": true | false,
  "file_types": ["pdf", "xlsx", ...],
  "reasoning": "한 문장 이유"
}
"""


@app.post("/api/agents/classify-intent")
async def classify_intent(request: Request):
    """사용자 메시지의 의도를 LLM으로 분류한다. Haiku로 빠르게(<1초)."""
    body = await request.json()
    prompt = body.get("prompt", "")
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))

    if not prompt or len(prompt.strip()) < 2:
        return JSONResponse({
            "intent": "simple_qa", "needs_tools": False,
            "complexity": "simple", "parallel_useful": False,
            "file_types": [], "reasoning": "empty or trivial prompt"
        })

    gw = _get_gw(aws_profile, bedrock_user)
    classifier_model = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    try:
        result = await asyncio.wait_for(
            gw.converse(
                model_id=classifier_model,
                messages=[{"role": "user", "content": [{"text": prompt[:1500]}]}],
                system_prompt=INTENT_CLASSIFIER_PROMPT,
            ),
            timeout=10
        )
        if result.get("decision") != "ALLOW":
            raise RuntimeError(f"classifier failed: {result.get('error') or result.get('decision')}")

        output = result.get("output", {}).get("message", {}).get("content", [])
        text = "\n".join(c.get("text", "") for c in output if "text" in c).strip()
        # JSON 추출
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise RuntimeError(f"no JSON in classifier response: {text[:200]}")
        parsed = json.loads(m.group(0))
        # 기본값 보정
        return JSONResponse({
            "intent": parsed.get("intent", "simple_qa"),
            "needs_tools": bool(parsed.get("needs_tools", False)),
            "complexity": parsed.get("complexity", "simple"),
            "parallel_useful": bool(parsed.get("parallel_useful", False)),
            "file_types": parsed.get("file_types", []) or [],
            "reasoning": parsed.get("reasoning", "")[:200],
        })
    except Exception as e:
        # 분류 실패 시 안전한 기본값 (단순 QA로 처리)
        print(f"[Intent] 분류 실패: {e}")
        return JSONResponse({
            "intent": "simple_qa", "needs_tools": False,
            "complexity": "simple", "parallel_useful": False,
            "file_types": [], "reasoning": f"classifier failed: {str(e)[:100]}"
        })


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


    # 에이전트 모드 기본 지시 — 도구 사용 강제 + 작업 완료 후 보고
    _AGENT_BASE = (
        "\n\n[에이전트 모드 지시]\n"
        "- 사용자의 요청을 완수하기 위해 제공된 도구를 반드시 실행하세요.\n"
        "- 도구를 실행하지 않고 텍스트로만 답변하지 마세요.\n"
        "- 작업을 모두 완료한 후 결과를 간결하게 보고하세요.\n"
        "- 이미지/PDF/PPTX 생성 요청 시 generate_image/generate_pdf/generate_pptx 도구를 즉시 호출하세요.\n"
        "- 파일 읽기/쓰기/검색이 필요하면 read_file/write_file/search_files 도구를 사용하세요.\n"
    )
    system_prompt = (system_prompt or "") + _AGENT_BASE

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
    """에이전트 모드 — 도구 실행 루프 포함. 모델이 tool_use로 응답하면 실행 후 재호출.

    사용자가 도구 호출 미지원 모델(Llama/DeepSeek/Cohere 등)을 선택해도
    프롬프트가 도구 사용을 필요로 하면 Claude Sonnet으로 자동 라우팅한다.
    """
    body = await request.json()
    prompt = body.get("prompt", "")
    model = body.get("model", "anthropic.claude-sonnet-4-6")
    system_prompt = body.get("systemPrompt", "")
    aws_profile = body.get("awsProfile", os.environ.get("AWS_PROFILE", "bedrock-gw"))
    bedrock_user = body.get("bedrockUser", os.environ.get("BEDROCK_USER", ""))
    project_path = body.get("projectPath", "")
    open_file = body.get("openFile", "")
    open_file_content = body.get("openFileContent", "")

    # ── 자동 모델 라우팅 ──
    # 사용자가 도구 호출 미지원/불안정 모델(Llama/DeepSeek/Cohere/Nova-Lite 등)을 선택했으면
    # Claude Sonnet으로 자동 대체 — 어떤 모델을 골라도 도구 호출이 동작하도록 보장.
    def _is_tool_capable(mid: str) -> bool:
        if not mid:
            return False
        m = mid.lower()
        if "claude" in m: return True
        if "mistral-large" in m or "pixtral" in m: return True
        if "nova-pro" in m: return True
        return False

    if not _is_tool_capable(model):
        original = model
        model = "anthropic.claude-sonnet-4-6-20250929-v1:0"
        print(f"[Agent] 도구 호출 미지원 모델 감지 — {original} → {model} 자동 대체")

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
        # 자동 라우팅 알림 — body의 model과 stream_model이 다르면 사용자 알림
        _orig = body.get("model", "")
        if _orig and _orig.lower() not in (stream_model or "").lower():
            try:
                yield f"data: {json.dumps({'model_routing': True, 'original': _orig, 'routedTo': stream_model, 'reason': '도구 호출 안정성을 위해 Claude로 자동 라우팅됨'}, ensure_ascii=False)}\n\n"
            except Exception:
                pass
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

                    # 생성 파일에 .meta.json 사이드카 기록
                    if tool_name in ("generate_image", "generate_pdf", "generate_pptx", "generate_xlsx", "generate_docx", "edit_image", "write_file"):
                        try:
                            _meta_paths = []
                            _actual_model = ""
                            try:
                                _parsed = json.loads(tool_output) if isinstance(tool_output, str) else None
                            except (json.JSONDecodeError, TypeError):
                                _parsed = None
                            if isinstance(_parsed, dict) and "path" in _parsed and "error" not in _parsed:
                                _meta_paths.append(_parsed["path"])
                                _actual_model = _parsed.get("model") or ""
                            elif isinstance(_parsed, dict) and isinstance(_parsed.get("images"), list):
                                for _it in _parsed["images"]:
                                    if isinstance(_it, dict) and "path" in _it:
                                        _meta_paths.append(_it["path"])
                                        if not _actual_model and _it.get("model"):
                                            _actual_model = _it["model"]
                            if tool_name == "write_file" and "path" in tool_input:
                                _meta_paths.append(tool_input["path"])
                            _tool_default_model = {
                                "generate_pdf":  "reportlab (Python)",
                                "generate_pptx": "python-pptx",
                                "generate_xlsx": "openpyxl",
                                "generate_docx": "python-docx",
                                "write_file":    "filesystem",
                            }
                            for _rel in _meta_paths:
                                _abs = _rel if os.path.isabs(_rel) else os.path.join(
                                    project_path if (project_path and os.path.isdir(project_path)) else os.getcwd(),
                                    _rel,
                                )
                                if not os.path.isfile(_abs):
                                    continue
                                _model_label = _actual_model or _tool_default_model.get(tool_name) or stream_model
                                _meta_obj = {
                                    "tool": tool_name,
                                    "model": _model_label,
                                    "chatModel": stream_model,
                                    "agentId": "single",
                                    "agentRole": "Agent",
                                    "agentTitle": prompt[:80],
                                    "createdAt": datetime.utcnow().isoformat() + "Z",
                                    "promptHint": prompt[:200],
                                }
                                try:
                                    with open(_abs + ".meta.json", "w", encoding="utf-8") as _mf:
                                        json.dump(_meta_obj, _mf, ensure_ascii=False, indent=2)
                                except Exception as _me:
                                    print(f"[Agent] meta write 실패 {_abs}: {_me}")
                        except Exception as _e:
                            print(f"[Agent] meta 처리 예외: {_e}")

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
        # 할루시네이션 sanitizer — 도구 호출 시뮬레이션과 거짓 완료 주장을 제거
        def _sanitize_hallucination(text: str) -> tuple:
            """모델이 도구 사용을 시뮬레이션한 흔적이나 거짓 주장을 감지하고 정리.
            실제 파일 존재 여부를 .generated/ 폴더에서 검증하여 거짓 경로는 제거.
            반환: (cleaned_text, was_modified)
            """
            if not text:
                return text, False
            original = text

            # 0) 실제 .generated/ 폴더에 어떤 파일이 있는지 확인
            _generated_dir = os.path.join(project_path or os.getcwd(), ".generated") if project_path else os.path.join(os.getcwd(), ".generated")
            _existing_files = set()
            try:
                if os.path.isdir(_generated_dir):
                    for f in os.listdir(_generated_dir):
                        _existing_files.add(f)
            except Exception:
                pass

            # 1) function_calls/tool_call XML 태그 제거 (앞뒤 공백 포함)
            text = re.sub(r'<function_calls>.*?</function_calls>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<invoke[^>]*>.*?</invoke>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<tool_call>.*?</tool_call>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<parameter[^>]*>.*?</parameter>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<function_calls>|</function_calls>|<invoke[^>]*>|</invoke>|<tool_call>|</tool_call>', '', text, flags=re.IGNORECASE)

            # 2) 가짜 .generated/ 경로 검증 — 실제 존재하지 않는 파일은 표시 변경
            def _replace_fake_path(match):
                full = match.group(0)
                fname = match.group(1)
                if fname in _existing_files:
                    return full  # 실제 존재 → 그대로
                return f"`(없음: {fname})`"  # 가짜 → 명시적으로 없다고 표시
            text = re.sub(r'`?\.generated/([\w\-.]+)`?', _replace_fake_path, text)

            # 3) ![이미지](.generated/xxx.png) 마크다운 — 실제 파일 검증
            def _replace_md_img(match):
                fname = match.group(1)
                if fname in _existing_files:
                    return match.group(0)
                return f"[*이미지 없음: {fname}*]"
            text = re.sub(r'!\[[^\]]*\]\(\.generated/([\w\-.]+)\)', _replace_md_img, text)

            # 4) 표 형태의 거짓 완료 주장 제거 — "✅ 완료" 행
            # | 1 | PNG 이미지 | .generated/xxx.png | ✅ 완료 |  같은 패턴
            text = re.sub(r'\|[^\n|]*?(✅|완료|완성|생성됨|created|done)[^\n|]*?\|', '| (실제 파일 없음) |', text)

            # 5) 거짓 완료 주장 약화
            text = re.sub(r'(✅|✓|🎉)\s*[^\n.]{0,30}(생성|저장|작성|만들).{0,10}(완료|되었|됨)[!.]?', '*다음 내용으로 생성 가능:*', text, flags=re.IGNORECASE)
            text = re.sub(r'(이전 작업|위 작업).*?(이미|모두)\s*완료[^.]*\.?', '*(주의: 이전 대화에서 실제 파일이 생성되지 않았습니다.)*', text)

            # 6) "모든 파일이 .generated/에 저장됩니다" 같은 거짓 안내
            text = re.sub(r'(모든\s*)?파일.{0,20}\.generated/.{0,30}(저장|생성|만들|작성).{0,20}(됩니다|되었|완료|돼요)', '*아래 코드를 실행하면 .generated/에 파일이 생성됩니다*', text, flags=re.IGNORECASE)

            modified = (text != original)
            # 7) 너무 많이 잘려나갔으면 경고 추가
            if modified and len(text.strip()) < 50 and len(original) > 200:
                text = ("*[알림] 이 모델의 응답에서 도구 호출 시뮬레이션 또는 거짓 주장이 감지되어 정리되었습니다. "
                        "실제로는 파일이 생성되지 않았습니다. "
                        "실제 파일 생성을 원하시면 '에이전트로 작업 진행' 버튼을 사용하세요.*\n\n"
                        + text)
            return text.strip(), modified

        async def call_model(slot):
            model_id = slot.get("modelId", "")
            slot_id = slot.get("slotId", "")
            sp = slot.get("systemPrompt", "")
            # CRIS profile 존재 여부에 따라 us. prefix 적용 (ON_DEMAND 모델은 prefix 없이)
            sid = _resolve_callable_model_id(model_id, aws_profile, bedrock_user)
            # RAG 컨텍스트를 시스템 프롬프트에 추가
            if rag_context:
                sp = (sp + "\n\n" + rag_context) if sp else rag_context

            # 할루시네이션 방지 — 거짓 주장은 막되, 실제 내용은 충실히 작성하도록 균형
            _anti = (
                "\n\n[병렬 모드 응답 가이드 — 매우 중요]\n"
                "이 호출에서는 파일 생성/수정 도구를 사용할 수 없습니다.\n"
                "이전 대화에 '파일이 생성되었다' 같은 응답이 있었더라도 **그것은 거짓**이었습니다 — 실제로 .generated/ 폴더에 파일은 만들어지지 않았습니다.\n"
                "\n"
                "올바른 응답 방식:\n"
                "1. 사용자가 원하는 결과물의 **실제 내용**(코드, 텍스트, 구조, 설계)을 마크다운으로 직접 작성\n"
                "2. 코드 블록(```python, ```markdown 등)으로 충분히 상세하게 제공\n"
                "3. 사용자가 그대로 활용 가능한 완성도 높은 내용\n"
                "\n"
                "절대 금지 사항:\n"
                "- '파일을 생성했습니다', '저장 완료', '✅ 완료' 같은 거짓 주장\n"
                "- '.generated/xxx.png' 같은 가짜 파일 경로 출력\n"
                "- 표 형태로 '✅ 완료', '생성됨' 같은 가짜 상태 표시\n"
                "- <function_calls>, <invoke>, <tool_call> 등 도구 호출 시뮬레이션\n"
                "- '이전 작업이 모두 완료되었습니다' 같은 과거 거짓 사실 인정\n"
                "\n"
                "사용자가 원하는 것은 **실제 내용**입니다. '도구가 없다'는 변명만 하지 말고 내용을 충실히 작성하세요.\n"
                "사용자는 다음 단계로 '에이전트 모드'를 사용해 실제 파일을 만들 수 있으며, 당신의 응답이 그 원천이 됩니다."
            )
            sp = (sp + _anti) if sp else _anti.strip()

            # 병렬 실행 검증 로그
            import time as _time
            _t_start = _time.time()
            print(f"[Parallel] START slot={slot_id} model={model_id} t=0.000s")

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
                        # 할루시네이션 후처리 — 도구 호출 시뮬레이션, 거짓 완료 주장 제거
                        text, _was_sanitized = _sanitize_hallucination(text)
                        if _was_sanitized:
                            print(f"[Parallel] SANITIZED slot={slot_id} model={model_id}")
                        print(f"[Parallel] DONE  slot={slot_id} model={model_id} elapsed={_time.time()-_t_start:.2f}s")
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
        # asyncio.create_task로 명시적 즉시 스케줄링 → 진짜 동시 실행 보장
        import time as _time
        batch_size = 10
        for i in range(0, len(models), batch_size):
            batch = models[i:i+batch_size]
            _batch_t0 = _time.time()
            print(f"[Parallel] BATCH start size={len(batch)} models={[s.get('modelId') for s in batch]}")
            # 모든 task를 즉시 스케줄링 — 이벤트 루프가 다음 await에서 모두 시작
            tasks = [asyncio.create_task(call_model(slot)) for slot in batch]
            # as_completed로 완료 순서대로 yield
            for coro in asyncio.as_completed(tasks):
                result = await coro
                yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
            print(f"[Parallel] BATCH done elapsed={_time.time()-_batch_t0:.2f}s")
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
2. 각 subtask는 하나의 에이전트가 도구(read_file, write_file, list_directory, run_command, search_files, generate_image, generate_pdf, generate_pptx, generate_xlsx, generate_docx)를 사용해 독립적으로 완료할 수 있어야 합니다.
3. subtask 개수는 요청 내용에 맞게 결정하되, 최대 {max_agents}개를 넘지 마세요.
4. 사용자가 "수정 1~4" 처럼 명시한 번호/단계가 있으면 그대로 따르세요.
5. **파일 생성 요청의 경우 — 절대 규칙**:
   - 사용자가 명시한 **모든 파일 형식마다 별도 subtask**를 만드세요.
   - "PDF, PPTX, DOCX, XLSX 만들어줘" → 정확히 4개 subtask (PDF 1개 + PPTX 1개 + DOCX 1개 + XLSX 1개)
   - 형식을 임의로 통합/축소/누락하면 안 됩니다 (PNG/SVG로 대체 금지).
   - 형식별 매핑은 아래 6번 primary_tool과 정확히 1:1로 일치해야 합니다.
6. **primary_tool ↔ 파일 형식 매핑 (엄격)**:
   - PDF (.pdf)   → primary_tool: "generate_pdf"
   - PPTX (.pptx) → primary_tool: "generate_pptx"
   - XLSX (.xlsx) → primary_tool: "generate_xlsx"
   - DOCX (.docx) → primary_tool: "generate_docx"
   - PNG/JPG (.png/.jpg) → primary_tool: "generate_image"
   - 이미지 편집 → primary_tool: "edit_image"
   - 코드/마크다운/텍스트 (.md/.py/.js/.txt) → primary_tool: "write_file"
   - 셸 명령 → primary_tool: "run_command"
   - 코드 분석/리팩토링 → primary_tool: "code"
7. 각 subtask의 description에는 **반드시 도구를 사용해 실제 파일을 생성/저장**하라고 명시하세요. "텍스트로 출력"이 아니라 "<primary_tool> 도구로 .generated/ 폴더에 저장".
8. target_files에는 결과물 파일의 절대/상대 경로를 명시하세요. **확장자는 primary_tool과 일치해야 함** (예: primary_tool=generate_xlsx → ".generated/...xlsx").

반드시 아래 JSON 형식으로만 응답하세요 (마크다운 코드블록 없이):
{{
  "subtasks": [
    {{
      "id": "A",
      "role": "역할명 (예: PDF Generator, XLSX Builder, Image Creator, Code Refactorer)",
      "title": "간결한 제목",
      "description": "이 에이전트가 수행해야 할 작업의 상세 지시. 반드시 도구를 사용해 실제 파일을 생성/저장하라고 명시.",
      "primary_tool": "generate_image|generate_pdf|generate_pptx|generate_xlsx|generate_docx|edit_image|write_file|run_command|code",
      "target_files": [".generated/result.pdf", ...]
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
[핵심 도구] {primary_tool}

[지시사항]
{description}

[작업 규칙 — 매우 중요]
1. **반드시 도구를 사용하세요**. 텍스트로만 답변하지 마세요. 다음 도구들을 적극 활용:
   - read_file: 기존 파일 내용 확인
   - list_directory: 폴더 구조 파악
   - search_files: 코드/문서 검색
   - run_command: 셸 명령 실행 (Python 스크립트 등)
   - write_file: 텍스트/코드 파일 작성
   - generate_image: 이미지 생성 — 시스템이 프롬프트를 분석해 최적 모델을 자동 선택
     · 사진/리얼리즘 → Stability Stable Image Ultra
     · 다이어그램/차트/UI → Stability SD 3.5 Large (텍스트 렌더링 우수)
     · 일러스트/아트 → Stability Ultra
     · 로고/아이콘 → Stable Image Core (빠름)
     · 비즈니스 자료 → Amazon Nova Canvas
     **프롬프트는 영어로 작성하면 모델 자동 선택 정확도가 향상됩니다.**
   - generate_pdf:  PDF 생성 (reportlab)
   - generate_pptx: PPTX 생성 (python-pptx)
   - generate_xlsx: XLSX 엑셀 생성 (openpyxl) — sheets[{name, headers, rows}] 형태로 호출
   - generate_docx: DOCX 워드 생성 (python-docx) — sections[{heading, body, bullets}] 형태로 호출
   - edit_image:    이미지 inpaint/outpaint

2. **파일 형식과 도구는 정확히 일치해야 합니다 (절대 규칙)**:
   - .pdf  → generate_pdf  (절대 generate_image 금지)
   - .pptx → generate_pptx (절대 generate_image 금지)
   - .xlsx → generate_xlsx (절대 generate_image 금지)
   - .docx → generate_docx (절대 generate_image 금지)
   - .png/.jpg → generate_image
   사용자가 "PDF + PPTX + XLSX + DOCX 만들어줘"라고 했으면 위 4개 도구를 각각 호출하세요. PNG/SVG로 대체하면 작업 실패입니다.

3. **위 [핵심 도구]가 명시되어 있다면 가장 먼저 그 도구부터 호출하세요**. 텍스트 설명은 도구 호출 후에 추가하세요.

4. **이미지 생성 작업의 경우**: 사용자가 어떤 채팅 모델(Claude 등)을 선택했더라도, 실제 이미지는 generate_image 도구가 시스템 내부에서 Stability/Amazon 이미지 모델을 자동 호출해서 생성합니다. Claude로 이미지를 직접 그리려고 하지 마세요 — generate_image 도구를 호출하면 됩니다.

5. **파일 생성 작업의 표준 절차**:
   a. 필요시 list_directory/read_file로 컨텍스트 수집
   b. 적절한 generate_* 또는 write_file 도구로 실제 파일 생성
   c. 생성된 파일 경로를 응답에 포함
   d. **반드시 .generated/ 폴더에 저장** (없으면 run_command로 mkdir)

6. **대상 파일 외의 파일은 수정하지 마세요**.

7. **다른 에이전트의 작업 영역을 침범하지 마세요**.

8. 작업이 끝나면 "[완료] <생성된 파일 경로 + 한 줄 요약>" 형태로 마무리하세요.

9. 도구 호출 없이 텍스트만 출력하면 작업이 실패한 것으로 간주됩니다. 반드시 도구를 사용해 실제 결과물을 만들어내세요.
"""

ORCHESTRATOR_MERGER_PROMPT = """당신은 멀티-에이전트 결과를 통합하는 리뷰어입니다.

[입력 데이터의 verifiedFiles 필드는 시스템이 디스크에서 직접 확인한 실제 파일 목록입니다 — 이 목록만 신뢰하세요.]
[에이전트의 summary 텍스트에 "✅ 완료" "생성됨" 등이 적혀 있어도 verifiedFiles에 없으면 실패입니다.]

아래 각 에이전트의 작업 결과를 검토하고:
1. 성공/실패 여부 — verifiedFiles에 파일이 있고 status="done"이면 성공, 그 외는 모두 실패
2. 충돌이나 누락 지적
3. 사용자에게 전달할 최종 요약 보고서를 마크다운으로 작성

보고서 형식:
## 최종 통합 결과
| 에이전트 | 역할 | 상태 | 생성된 파일 (디스크 검증됨) | 도구 사용 횟수 |
|---------|------|------|--------------------------|--------------|
...

### 생성된 파일 목록 (verifiedFiles 기반 — 실제 존재)
- `.generated/file1.pdf` — 설명
- `.generated/file2.xlsx` — 설명
...

### 세부 사항
- (각 에이전트별 핵심 작업 내역)

### 주의/후속 작업
- (있다면)

**규칙**:
- verifiedFileCount=0인 에이전트는 무조건 "실패"로 표시.
- summary에 "✅", "완료", "생성됨"이 있어도 verifiedFiles에 없으면 그 주장은 무시하고 실패로 분류.
- 거짓 파일 경로(verifiedFiles에 없는 경로)는 절대 보고서에 포함하지 마세요.

**오류 추정 절대 금지**:
- 입력 데이터에 명시되지 않은 오류 메시지를 만들어내지 마세요.
- 'KeyError', 'TypeError', 'ValidationException' 같은 구체적 예외 이름은 입력 summary에 그대로 적혀있을 때만 인용하세요.
- summary에 오류가 안 적혀 있으면 "도구를 호출하지 않음" 또는 "원인 불명"으로만 기재하세요.
- 절대 그럴듯해 보이는 가짜 KeyError나 스택트레이스를 작성하지 마세요.
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


async def _enrich_content_via_gateway(
    gw, model_id: str, primary_tool: str, title: str,
    description: str, final_text: str, max_tokens: int = 2000,
) -> str:
    """게이트웨이 호출로 파일 본문 콘텐츠 보강 — 퀄리티 보장.

    forced fallback 직전에 호출해서 빈약한 final_text를 풍부한 본문으로 확장.
    형식별 최소 분량 임계치를 만족할 때까지 최대 2회 재시도.
    게이트웨이를 반드시 경유 (비용/사용량 측정 유지).

    Args:
        gw: GatewayClient instance
        model_id: 호출할 모델 ID (이미 자동 라우팅된 Claude 권장)
        primary_tool: 작업 종류 (generate_pdf 등)
        title, description, final_text: 원본 컨텍스트

    Returns:
        보강된 마크다운 텍스트. 모든 시도 실패 시 final_text 그대로 반환.
    """
    pt = (primary_tool or "").lower()
    # 형식별 퀄리티 임계치 — 사용자가 보고서로 사용해도 충분한 분량
    quality_min = {
        "generate_pdf":   1500,
        "generate_docx":  1500,
        "generate_pptx":   800,   # 슬라이드는 압축적
        "generate_xlsx":   400,   # 표는 데이터 위주
        "generate_image":  150,   # 이미지 prompt
        "write_file":      400,
    }.get(pt, 600)

    style_hint = {
        "generate_pdf":  "다단 마크다운 보고서 형식 (## 헤더, 본문 단락, 필요 시 표).",
        "generate_pptx": "슬라이드 헤더와 불릿 위주 (## 섹션 = 슬라이드 제목, 각 섹션 4-6개 불릿).",
        "generate_xlsx": "마크다운 표 형식 우선 (| 헤더 | ... |), 표 없으면 카테고리별 데이터.",
        "generate_docx": "다단 마크다운 문서 (## 섹션, 본문 단락, 불릿 리스트).",
        "generate_image": "이미지 생성용 영문 prompt 한 줄 (구체적 스타일/구성/조명/시점).",
        "write_file":    "코드/마크다운/텍스트 콘텐츠.",
    }.get(pt, "마크다운 형식의 충실한 본문.")

    # 형식별 추가 퀄리티 가이드라인
    quality_guide = {
        "generate_pdf":   "최소 4개 섹션, 각 섹션 2-4 문단의 깊이 있는 설명. Lorem ipsum이나 placeholder 금지 — 실제 정보로 채울 것.",
        "generate_docx":  "최소 4개 섹션, 각 섹션 2-4 문단의 풍부한 내용. 도입→본론→결론 구조. 실제 정보 위주.",
        "generate_pptx":  "최소 5개 슬라이드(권장 7-10개). 각 슬라이드 3-5개 구체적 불릿. 첫 슬라이드 개요, 마지막 결론/Next Steps.",
        "generate_xlsx":  "헤더 행 + 최소 5행 이상의 의미있는 데이터. 가능하면 10-20행. 합리적인 실제 값.",
        "generate_image": "스타일/구성/조명/색감/시점을 모두 포함한 구체적 영문 prompt 1줄.",
        "write_file":     "실제 사용 가능한 풍부한 콘텐츠. 빈 placeholder 금지.",
    }.get(pt, "충실하고 구체적인 콘텐츠로 작성.")

    sys_prompt = f"""당신은 파일 생성 작업의 콘텐츠 작성자입니다.
주제와 지시사항에 맞는 충실한 본문을 작성하세요.

[출력 형식] {style_hint}
[퀄리티 기준] {quality_guide}
[제약] 도구 호출은 하지 않습니다. 마크다운 본문만 출력하세요.
[중요] '생성 완료' 같은 거짓 주장 금지. 실제 콘텐츠만 작성.
"""

    user_msg_base = f"""작업: {title}
지시사항: {description}

이전 응답 일부 (참고):
{(final_text or '')[:1500]}

위 정보를 바탕으로 [{pt}] 작업에 사용할 본문을 마크다운으로 작성해주세요. 최소 {quality_min}자 이상의 풍부한 내용을 작성하세요."""

    # 최대 2회 시도 — 첫 시도 결과가 임계치 미만이면 재시도
    best = ""
    for attempt in range(2):
        user_msg = user_msg_base
        if attempt > 0 and best:
            # 두 번째 시도 — 첫 결과보다 더 풍부하게 작성하라고 지시
            user_msg += f"\n\n[재시도] 이전 시도는 분량이 부족했습니다 ({len(best)}자). 더 깊이 있고 풍부하게 작성해주세요."
        messages = [{"role": "user", "content": [{"text": user_msg}]}]
        try:
            result = await asyncio.wait_for(
                gw.converse(model_id=model_id, messages=messages, system_prompt=sys_prompt),
                timeout=90,
            )
            if result.get("decision") != "ALLOW":
                print(f"[Enrich] gateway 거부(attempt {attempt+1}): {result.get('error') or result.get('decision')}")
                continue
            output = result.get("output", {}).get("message", {}).get("content", [])
            text = "\n".join(c.get("text", "") for c in output if "text" in c).strip()
            if text and len(text) > len(best):
                best = text
            if best and len(best) >= quality_min:
                # 퀄리티 임계치 만족 — 즉시 반환
                if attempt > 0:
                    print(f"[Enrich] 재시도로 퀄리티 임계치 달성 ({len(best)}자 >= {quality_min}자)")
                return best
        except Exception as e:
            print(f"[Enrich] 예외(attempt {attempt+1}): {e}")
            continue

    # 임계치 미달이지만 가지고 있는 best가 final_text보다 풍부하면 그것 사용
    if best and len(best) > len(final_text or ""):
        print(f"[Enrich] 임계치({quality_min}) 미달이지만 best({len(best)}자) 사용")
        return best
    return final_text or description or title or "내용 없음"


async def _force_generate_from_text(
    primary_tool: str,
    target_files: list,
    title: str,
    description: str,
    final_text: str,
    project_path: str,
    aws_profile: str,
    bedrock_user: str,
):
    """결정적 도구 디스패처 — final_text(보통 _enrich_content_via_gateway로 보강됨)를
    파일로 변환한다.

    퀄리티 보장:
    - 호출 전에 _enrich_content_via_gateway로 Claude가 풍부한 콘텐츠를 만들어 둠
    - 이 함수는 마크다운 → 결정적 라이브러리(reportlab/python-pptx/openpyxl/python-docx) 변환만 수행
    - 따라서 결과 파일의 내용 퀄리티 = Claude 출력 퀄리티 그대로

    primary_tool과 target_files의 확장자를 보고 어떤 도구를 호출할지 결정한다.

    Returns:
        list of (relative_path, file_info_dict) tuples for verified files.
    """
    pt = (primary_tool or "").lower()
    # target_files에서 확장자 결정 → 없으면 primary_tool로 결정
    needed_exts = []
    if target_files:
        for tf in target_files:
            ext = (str(tf).split(".")[-1] or "").lower()
            if ext and ext not in needed_exts:
                needed_exts.append(ext)
    if not needed_exts:
        # primary_tool로 매핑
        tool_to_ext = {
            "generate_pdf": "pdf",
            "generate_pptx": "pptx",
            "generate_xlsx": "xlsx",
            "generate_docx": "docx",
            "generate_image": "png",
            "write_file": "md",
            # 코드 분석/리팩토링도 결과물을 마크다운 보고서로 저장 — 빈손 종료 방지
            "code": "md",
            "run_command": "md",
        }
        if pt in tool_to_ext:
            needed_exts.append(tool_to_ext[pt])
        else:
            # 알 수 없는 primary_tool — 일단 마크다운으로라도 저장
            needed_exts.append("md")

    if not needed_exts:
        return []

    # 본문 텍스트 — final_text가 비어있으면 description, title 순으로 fallback
    base_text = (final_text or "").strip() or (description or "").strip() or (title or "문서")

    # 섹션 단위로 분리 (마크다운 헤더 + 빈 줄)
    sections = _split_into_sections(base_text, fallback_title=title or "본문")

    out = []
    project_root = project_path if (project_path and os.path.isdir(project_path)) else os.getcwd()

    for ext in needed_exts:
        try:
            if ext == "pdf":
                inp = {
                    "title": title or "Document",
                    "sections": [{"heading": s["heading"], "body": s["body"]} for s in sections],
                }
                tout = await _tool_generate_pdf(inp, project_path)
            elif ext == "pptx":
                inp = {
                    "title": title or "Presentation",
                    "slides": [
                        {"title": s["heading"][:80], "bullets": _extract_bullets(s["body"])[:6] or [s["body"][:200]]}
                        for s in sections[:10]
                    ] or [{"title": title or "Slide 1", "bullets": [base_text[:200]]}],
                }
                tout = await _tool_generate_pptx(inp, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user)
            elif ext == "xlsx":
                # 마크다운 표가 있으면 그것을 사용, 없으면 섹션 헤더 + 본문 요약
                rows = _extract_md_table(base_text) or [
                    [s["heading"][:60], s["body"][:300]] for s in sections
                ]
                headers = ["Heading", "Content"] if not _extract_md_table(base_text) else None
                if headers is None:
                    # 첫 행이 헤더로 추출됨
                    md_table = _extract_md_table(base_text)
                    headers = md_table[0] if md_table else ["Col1", "Col2"]
                    rows = md_table[1:] if md_table and len(md_table) > 1 else []
                inp = {
                    "title": title or "Workbook",
                    "sheets": [{"name": "Summary", "headers": headers, "rows": rows or [["(empty)", "(empty)"]]}],
                }
                tout = await _tool_generate_xlsx(inp, project_path)
            elif ext == "docx":
                inp = {
                    "title": title or "Document",
                    "sections": [
                        {"heading": s["heading"], "level": 2, "body": s["body"], "bullets": _extract_bullets(s["body"])}
                        for s in sections
                    ],
                }
                tout = await _tool_generate_docx(inp, project_path)
            elif ext in ("png", "jpg", "jpeg"):
                inp = {"prompt": (description or title or "Architecture diagram")[:1500], "size": "1024x1024"}
                tout = await _tool_generate_image(inp, project_path, aws_profile=aws_profile, bedrock_user=bedrock_user)
            elif ext in ("md", "txt"):
                # write_file로 직접 작성
                fname = (target_files[0] if target_files else f".generated/{_safe_slug(title or 'note')}.{ext}")
                if not fname.startswith(".generated/") and not os.path.isabs(fname):
                    fname = ".generated/" + os.path.basename(fname)
                abs_path = fname if os.path.isabs(fname) else os.path.join(project_root, fname)
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(base_text)
                tout = json.dumps({"path": fname, "model": "filesystem", "sizeBytes": os.path.getsize(abs_path)})
            else:
                continue

            try:
                parsed = json.loads(tout) if isinstance(tout, str) else tout
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if not isinstance(parsed, dict) or "path" not in parsed or "error" in parsed:
                print(f"[ForceGenerate] {ext} 실패: {tout[:300]}")
                continue
            rel = parsed["path"]
            abs_path = rel if os.path.isabs(rel) else os.path.join(project_root, rel)
            if not os.path.isfile(abs_path):
                continue
            out.append((rel, {
                "path": rel,
                "absPath": abs_path,
                "size": os.path.getsize(abs_path),
                "tool": "generate_" + (ext if ext != "jpg" else "image"),
                "model": parsed.get("model", "system_fallback"),
            }))
        except Exception as e:
            print(f"[ForceGenerate] {ext} 예외: {e}")
            continue

    return out


def _split_into_sections(text: str, fallback_title: str = "본문") -> list:
    """마크다운 텍스트를 헤더 단위로 분리. 헤더가 없으면 단일 섹션."""
    if not text:
        return [{"heading": fallback_title, "body": ""}]
    lines = text.splitlines()
    sections = []
    current = {"heading": "", "body": []}
    for line in lines:
        m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if m:
            if current["heading"] or current["body"]:
                sections.append({"heading": current["heading"] or fallback_title,
                                 "body": "\n".join(current["body"]).strip()})
            current = {"heading": m.group(2).strip(), "body": []}
        else:
            current["body"].append(line)
    if current["heading"] or current["body"]:
        sections.append({"heading": current["heading"] or fallback_title,
                         "body": "\n".join(current["body"]).strip()})
    return sections or [{"heading": fallback_title, "body": text.strip()}]


def _extract_bullets(text: str) -> list:
    """텍스트에서 불릿 항목(- / * / 숫자.)을 추출."""
    if not text:
        return []
    bullets = []
    for line in text.splitlines():
        stripped = line.strip()
        m = re.match(r"^(?:[-*•]\s+|\d+\.\s+)(.+)$", stripped)
        if m:
            bullets.append(m.group(1).strip())
    return bullets


def _extract_md_table(text: str) -> list:
    """마크다운 표를 [[헤더...], [행1...], ...] 형태로 추출. 없으면 빈 리스트."""
    if not text:
        return []
    lines = text.splitlines()
    rows = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # 구분선 (---|---) 스킵
            if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                in_table = True
                continue
            rows.append(cells)
            in_table = True
        elif in_table:
            break
    return rows


def _safe_slug(s: str) -> str:
    """파일명용 안전한 슬러그."""
    if not s:
        return "doc"
    s = re.sub(r"[^a-zA-Z0-9가-힣\-_\s]+", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    return (s[:40] or "doc").lower()


async def _orchestrator_run_agent(
    gw, stream_model: str, subtask: dict, project_path: str,
    base_system_prompt: str, emit_queue: asyncio.Queue,
    aws_profile: str = "", bedrock_user: str = "", is_remote: bool = False,
):
    """하나의 하위 에이전트 실행 — 도구 루프 포함. 무한루프 방지를 위해 hard timeout."""
    # 환경 변수로 조정 가능 — 기본 5분, 도구 루프 50회 제한과 함께 동작
    agent_timeout = float(os.environ.get("AE_AGENT_TIMEOUT", "300"))
    task_id = subtask.get("id", "?")
    role = subtask.get("role", "Worker")
    title = subtask.get("title", "")

    async def _run_inner():
        return await _orchestrator_run_agent_inner(
            gw, stream_model, subtask, project_path,
            base_system_prompt, emit_queue,
            aws_profile=aws_profile, bedrock_user=bedrock_user, is_remote=is_remote,
        )

    try:
        return await asyncio.wait_for(_run_inner(), timeout=agent_timeout)
    except asyncio.TimeoutError:
        msg = f"에이전트 시간 초과 ({int(agent_timeout)}초)"
        try:
            await emit_queue.put({"type": "agent_error", "taskId": task_id, "error": msg})
        except Exception:
            pass
        return {"taskId": task_id, "role": role, "title": title, "status": "error",
                "summary": msg, "tools": []}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        try:
            await emit_queue.put({"type": "agent_error", "taskId": task_id, "error": msg})
        except Exception:
            pass
        return {"taskId": task_id, "role": role, "title": title, "status": "error",
                "summary": msg, "tools": []}


async def _orchestrator_run_agent_inner(
    gw, stream_model: str, subtask: dict, project_path: str,
    base_system_prompt: str, emit_queue: asyncio.Queue,
    aws_profile: str = "", bedrock_user: str = "", is_remote: bool = False,
):
    """실제 에이전트 실행 본체."""
    task_id = subtask.get("id", "?")
    role = subtask.get("role", "Worker")
    title = subtask.get("title", "")
    description = subtask.get("description", "")
    target_files = subtask.get("target_files", [])
    primary_tool = subtask.get("primary_tool", "")

    sys_prompt = ORCHESTRATOR_AGENT_PROMPT.format(
        role=role, task_id=task_id, title=title,
        target_files=", ".join(target_files) if target_files else "(없음)",
        primary_tool=primary_tool or "(자동 선택)",
        description=description,
    )
    if base_system_prompt:
        sys_prompt = base_system_prompt + "\n\n" + sys_prompt

    messages = [{"role": "user", "content": [{"text": f"작업 {task_id} ({role}): {title}\n\n{description}"}]}]
    max_turns = int(os.environ.get("AE_MAX_ORCH_TURNS", "50"))
    final_text_parts = []
    tool_log = []
    # 실제 디스크에 존재 확인된 파일들 — 할루시네이션 방지의 핵심
    verified_files = []

    await emit_queue.put({"type": "agent_start", "taskId": task_id, "role": role, "title": title, "targetFiles": target_files})

    stream_failed = False
    stream_error_msg = ""

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
                stream_failed = True
                stream_error_msg = str(e)
                await emit_queue.put({"type": "agent_error", "taskId": task_id, "error": stream_error_msg})
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

                # 생성 파일에 .meta.json 사이드카 기록 — 어떤 모델/에이전트가 만들었는지 추적
                if tname in ("generate_image", "generate_pdf", "generate_pptx", "generate_xlsx", "generate_docx", "edit_image", "write_file"):
                    try:
                        _meta_paths = []
                        _actual_model = ""  # 실제 생성에 사용된 모델 (도구 응답에서 추출)
                        try:
                            _parsed = json.loads(tout) if isinstance(tout, str) else None
                        except (json.JSONDecodeError, TypeError):
                            _parsed = None
                        if isinstance(_parsed, dict) and "path" in _parsed and "error" not in _parsed:
                            _meta_paths.append(_parsed["path"])
                            _actual_model = _parsed.get("model") or ""
                        elif isinstance(_parsed, dict) and isinstance(_parsed.get("images"), list):
                            for _it in _parsed["images"]:
                                if isinstance(_it, dict) and "path" in _it:
                                    _meta_paths.append(_it["path"])
                                    if not _actual_model and _it.get("model"):
                                        _actual_model = _it["model"]
                        # write_file: tool_input["path"]
                        if tname == "write_file" and "path" in tinput:
                            _meta_paths.append(tinput["path"])
                        # 도구 카테고리별 기본 모델명 (도구 응답에 model이 없을 때)
                        _tool_default_model = {
                            "generate_pdf":  "reportlab (Python)",
                            "generate_pptx": "python-pptx",
                            "generate_xlsx": "openpyxl",
                            "generate_docx": "python-docx",
                            "write_file":    "filesystem",
                        }
                        for _rel in _meta_paths:
                            _abs = _rel if os.path.isabs(_rel) else os.path.join(
                                project_path if (project_path and os.path.isdir(project_path)) else os.getcwd(),
                                _rel,
                            )
                            if not os.path.isfile(_abs):
                                continue
                            # 실제 디스크 검증 통과 → verified_files에 추가
                            try:
                                _stat = os.stat(_abs)
                                verified_files.append({
                                    "path": _rel,
                                    "absPath": _abs,
                                    "size": _stat.st_size,
                                    "tool": tname,
                                    "model": _actual_model or "",
                                })
                            except Exception:
                                pass
                            # 실제 사용 모델 = (이미지 생성 모델) 또는 (도구별 기본) 또는 (chat 모델)
                            _model_label = _actual_model or _tool_default_model.get(tname) or stream_model
                            _meta_obj = {
                                "tool": tname,
                                "model": _model_label,           # 실제 작업 수행 모델/엔진
                                "chatModel": stream_model,       # 도구 호출을 결정한 chat 모델
                                "agentId": task_id,
                                "agentRole": role,
                                "agentTitle": title,
                                "createdAt": datetime.utcnow().isoformat() + "Z",
                                "promptHint": (subtask.get("description") or "")[:200],
                            }
                            try:
                                with open(_abs + ".meta.json", "w", encoding="utf-8") as _mf:
                                    json.dump(_meta_obj, _mf, ensure_ascii=False, indent=2)
                            except Exception as _me:
                                print(f"[Orchestrator] meta write 실패 {_abs}: {_me}")
                    except Exception as _e:
                        print(f"[Orchestrator] meta 처리 예외: {_e}")

                await emit_queue.put({"type": "agent_tool", "taskId": task_id, "tool": tname, "status": "done", "output": tout[:300]})
                _tr_max = int(os.environ.get("AE_TOOL_RESULT_MAX", "80000"))
                tool_results.append({"toolResult": {"toolUseId": tid, "content": [{"text": tout[:_tr_max]}]}})
            messages.append({"role": "user", "content": tool_results})

        final_text = "".join(final_text_parts).strip()
        if stream_failed:
            # SSE 실패 — agent_error 이미 emit됨, error 결과로 반환
            return {"taskId": task_id, "role": role, "title": title, "status": "error",
                    "summary": stream_error_msg or "스트리밍 실패", "tools": tool_log,
                    "verifiedFiles": []}

        # 파일 생성 작업이었는지 판정 — primary_tool이나 target_files로 판단
        primary_tool = (subtask.get("primary_tool") or "").lower()
        wanted_files = bool(target_files) or primary_tool in (
            "generate_image", "generate_pdf", "generate_pptx",
            "generate_xlsx", "generate_docx", "edit_image", "write_file"
        )

        # 할루시네이션 차단 + 강제 생성:
        # 파일 생성을 요구받았는데 실제 디스크에 검증된 파일이 0개면 →
        # (1) 게이트웨이 1회 추가 호출로 본문 보강 → (2) 시스템이 도구 디스패처로 파일 강제 생성.
        # 게이트웨이 경유 유지 (비용/사용량 측정).
        if wanted_files and not verified_files:
            print(f"[Orchestrator] {task_id} 도구 미호출 감지 — 게이트웨이 보강 + 강제 fallback (primary_tool={primary_tool})")
            await emit_queue.put({
                "type": "agent_tool", "taskId": task_id,
                "tool": "system_fallback", "status": "running",
                "input": {"reason": "no tool calls — gateway enrich + force generate"},
            })
            try:
                # (1) 게이트웨이로 콘텐츠 보강 — 짧은 final_text를 풍부한 본문으로 확장.
                #     stream_model은 이미 자동 라우팅되어 도구 호출 가능 모델.
                enriched_text = await _enrich_content_via_gateway(
                    gw=gw,
                    model_id=stream_model,
                    primary_tool=primary_tool,
                    title=title,
                    description=description,
                    final_text=final_text,
                )
                # (2) 보강된 본문으로 결정적 도구 디스패처 호출 → 실제 파일 생성
                _forced = await _force_generate_from_text(
                    primary_tool=primary_tool,
                    target_files=target_files,
                    title=title,
                    description=description,
                    final_text=enriched_text,
                    project_path=project_path,
                    aws_profile=aws_profile,
                    bedrock_user=bedrock_user,
                )
                for fpath, finfo in _forced:
                    verified_files.append(finfo)
                    tool_log.append({
                        "name": "system_fallback:" + finfo.get("tool", "?"),
                        "input": {"forced": True},
                        "output": fpath,
                    })
                    # meta sidecar
                    try:
                        _meta_obj = {
                            "tool": finfo.get("tool", "system_fallback"),
                            "model": finfo.get("model", "system_fallback"),
                            "chatModel": stream_model,
                            "agentId": task_id,
                            "agentRole": role,
                            "agentTitle": title,
                            "createdAt": datetime.utcnow().isoformat() + "Z",
                            "promptHint": "[forced fallback] " + (description or "")[:180],
                            "forced": True,
                        }
                        with open(finfo["absPath"] + ".meta.json", "w", encoding="utf-8") as _mf:
                            json.dump(_meta_obj, _mf, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
                await emit_queue.put({
                    "type": "agent_tool", "taskId": task_id,
                    "tool": "system_fallback", "status": "done",
                    "output": f"forced {len(_forced)} files",
                })
            except Exception as _fe:
                err_msg = f"강제 생성 실패: {_fe}"
                print(f"[Orchestrator] {task_id} {err_msg}")
                await emit_queue.put({
                    "type": "agent_error", "taskId": task_id,
                    "error": err_msg,
                })
                return {
                    "taskId": task_id, "role": role, "title": title,
                    "status": "error",
                    "summary": f"{final_text}\n\n[시스템] {err_msg}",
                    "tools": tool_log, "verifiedFiles": [],
                }

        # 강제 fallback 후에도 파일이 0개면 정말 실패
        if wanted_files and not verified_files:
            err_msg = "파일 생성 실패 — 강제 fallback도 결과 0건"
            print(f"[Orchestrator] {task_id} {err_msg}")
            await emit_queue.put({
                "type": "agent_error", "taskId": task_id,
                "error": err_msg,
            })
            return {
                "taskId": task_id, "role": role, "title": title,
                "status": "error",
                "summary": f"{final_text}\n\n[시스템] {err_msg}",
                "tools": tool_log, "verifiedFiles": [],
            }

        await emit_queue.put({
            "type": "agent_done", "taskId": task_id,
            "summary": final_text[-1200:],
            "toolCount": len(tool_log),
            "verifiedFiles": [vf["path"] for vf in verified_files],
        })
        return {
            "taskId": task_id, "role": role, "title": title,
            "status": "done", "summary": final_text, "tools": tool_log,
            "verifiedFiles": verified_files,
        }
    except Exception as e:
        await emit_queue.put({"type": "agent_error", "taskId": task_id, "error": str(e)})
        return {"taskId": task_id, "role": role, "title": title, "status": "error",
                "summary": str(e), "tools": tool_log, "verifiedFiles": []}


async def _orchestrator_merge(gw, stream_model, user_prompt, agent_results: list, base_system_prompt: str) -> str:
    """Merger 호출 — 최종 보고서 생성. verifiedFiles 기반으로 거짓 완료 주장 차단."""
    # 실제로 디스크에 검증된 파일들만 추출
    all_verified_files = []
    for r in agent_results:
        for vf in r.get("verifiedFiles", []) or []:
            if isinstance(vf, dict) and vf.get("path"):
                all_verified_files.append({
                    "agentId": r.get("taskId"),
                    "agentRole": r.get("role"),
                    "path": vf["path"],
                    "size": vf.get("size", 0),
                    "tool": vf.get("tool", "?"),
                    "model": vf.get("model", ""),
                })

    summary_input = {
        "userRequest": user_prompt[:2000],
        "agents": [
            {
                "taskId": r["taskId"], "role": r["role"], "title": r["title"],
                "status": r["status"],
                "summary": (r.get("summary") or "")[:1500],
                "toolCount": len(r.get("tools", [])),
                "verifiedFileCount": len(r.get("verifiedFiles") or []),
                # 실제 도구 호출 명단 — Merger가 가짜 오류 만들어내지 않도록
                "toolsCalled": [t.get("name", "?") for t in (r.get("tools") or [])][:10],
                # 도구 호출 0회면 분명히 표시 — Merger가 추측 금지
                "noToolsCalled": (len(r.get("tools", [])) == 0),
            }
            for r in agent_results
        ],
        "verifiedFiles": all_verified_files,  # 실제 디스크 검증된 파일만
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
    is_remote = bool(body.get("isRemote", False)) or (_BRIDGE_URL and _bridge_is_remote() if _BRIDGE_URL else False)

    gw = _get_gw(aws_profile, bedrock_user)

    def _with_prefix(mid: str) -> str:
        return mid if mid.startswith("us.") or mid.startswith("eu.") else f"us.{mid}"

    planner_id = _with_prefix(planner_model)
    worker_id = _with_prefix(worker_model)
    merger_id = _with_prefix(merger_model)

    # ── Known Claude model IDs (latest gen first) ──
    # 게이트웨이가 활성화한 모델만 호출 가능. 우선순위 순으로 시도.
    _KNOWN_OPUS = [
        "anthropic.claude-opus-4-7-20251015-v1:0",
        "anthropic.claude-opus-4-20250514-v1:0",
        "anthropic.claude-3-opus-20240229-v1:0",
    ]
    _KNOWN_SONNET = [
        "anthropic.claude-sonnet-4-6-20250929-v1:0",
        "anthropic.claude-sonnet-4-20250514-v1:0",
        "anthropic.claude-3-5-sonnet-20241022-v2:0",
    ]
    _KNOWN_HAIKU = [
        "anthropic.claude-haiku-4-5-20251001-v1:0",
        "anthropic.claude-3-5-haiku-20241022-v1:0",
    ]

    def _find_model_by_keywords(keywords):
        """keywords 리스트 중 첫 번째 매칭되는 모델의 호출 가능 ID 반환."""
        # 먼저 사용자 선택 worker_model이 키워드와 일치하면 그것 사용
        for kw in keywords:
            if kw.lower() in (worker_model or "").lower():
                return worker_model
        # 알려진 모델 후보를 순회
        for kw in keywords:
            for known in _KNOWN_OPUS + _KNOWN_SONNET + _KNOWN_HAIKU:
                if kw.lower() in known.lower():
                    return known
        return None

    # ── 자동 모델 라우팅 헬퍼 ──
    # 사용자가 어떤 모델을 선택했든, 작업 특성에 따라 최적 chat 모델로 강제 라우팅.
    # 도구 호출이 안정적인 Claude 패밀리를 우선하고, 그 외는 Claude로 대체.
    def _auto_chat_model(primary_tool: str, fallback: str) -> str:
        """primary_tool에 따라 최적 chat 모델 ID 반환."""
        pt = (primary_tool or "").lower()
        opus = _find_model_by_keywords(["claude-opus-4-7", "claude-opus-4"]) \
            or _find_model_by_keywords(["claude-opus"])
        sonnet = _find_model_by_keywords(["claude-sonnet-4-6", "claude-sonnet-4"]) \
            or _find_model_by_keywords(["claude-sonnet"])
        haiku = _find_model_by_keywords(["claude-haiku-4-5", "claude-haiku-4"]) \
            or _find_model_by_keywords(["claude-haiku"])
        # 작업별 매핑
        if pt in ("code", "edit_image"):
            # 복잡한 추론 → Opus (없으면 Sonnet)
            picked = opus or sonnet or fallback
        elif pt in ("generate_pdf", "generate_pptx", "generate_xlsx", "generate_docx",
                    "generate_image", "write_file", "run_command"):
            # 도구 호출 안정성 → Sonnet (없으면 Haiku, fallback)
            picked = sonnet or haiku or fallback
        else:
            picked = fallback
        return _with_prefix(picked)

    # 사용자가 비-Claude(예: Llama/DeepSeek) 채팅 모델을 선택해도 도구 호출 가능한 Claude로 라우팅.
    # 이렇게 해야 "PDF 만들어줘" 같은 도구 호출 작업이 실패하지 않음.
    def _is_tool_capable_chat_model(model_id: str) -> bool:
        """chat 모델이 Bedrock toolConfig를 안정적으로 지원하는지 확인."""
        if not model_id:
            return False
        mid = model_id.lower()
        # Claude는 모두 도구 호출 안정적
        if "claude" in mid:
            return True
        # Mistral Large/Pixtral도 toolConfig 지원
        if "mistral-large" in mid or "pixtral" in mid:
            return True
        # Nova Pro도 도구 호출 가능 (Nova Lite/Micro는 제한적)
        if "nova-pro" in mid:
            return True
        # 그 외 (Llama, DeepSeek-R1, Cohere Command 등) — 도구 호출 미지원/불안정
        return False

    # 사용자가 도구 호출 미지원 모델을 선택했으면 worker_id를 자동으로 Claude Sonnet으로 교체
    if not _is_tool_capable_chat_model(worker_id):
        original = worker_id
        sonnet_pref = _find_model_by_keywords(["claude-sonnet-4-6", "claude-sonnet-4", "claude-sonnet"])
        if sonnet_pref:
            worker_id = _with_prefix(sonnet_pref)
            print(f"[Orchestrator] worker 자동 변경 — {original} → {worker_id} (도구 호출 안정성)")

    # Planner도 도구 호출/JSON 출력이 안정적이어야 함 — 비-Claude면 Opus로 대체
    if not _is_tool_capable_chat_model(planner_id):
        original = planner_id
        opus_pref = _find_model_by_keywords(["claude-opus-4-7", "claude-opus-4", "claude-opus"])
        sonnet_pref = _find_model_by_keywords(["claude-sonnet-4-6", "claude-sonnet-4", "claude-sonnet"])
        if opus_pref or sonnet_pref:
            planner_id = _with_prefix(opus_pref or sonnet_pref)
            merger_id = planner_id  # merger도 동일한 강한 모델
            print(f"[Orchestrator] planner 자동 변경 — {original} → {planner_id} (JSON 분해 안정성)")

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
            # 자동 모델 라우팅 알림 — worker_model과 worker_id가 다르면 사용자에게 알림
            if worker_model and worker_id and worker_model.lower() not in worker_id.lower():
                await emit_queue.put({
                    "type": "model_routing",
                    "original": worker_model,
                    "routedTo": worker_id,
                    "reason": "도구 호출 안정성을 위해 Claude로 자동 라우팅됨",
                })

            # 1) Planner
            try:
                plan = await _orchestrator_plan(gw, planner_id, user_prompt, base_sys, max_agents)
            except Exception as e:
                await emit_queue.put({"type": "error", "stage": "planner", "message": str(e)})
                await emit_queue.put({"type": "__END__"})
                return

            subtasks = plan["subtasks"][:max_agents]
            await emit_queue.put({"type": "plan", "subtasks": subtasks})

            # primary_tool 기반 자동 모델 라우팅 — 사용자 선택과 무관하게 작업에 최적 모델 사용.
            # _auto_chat_model이 도구 호출 미지원 모델을 Claude로 자동 대체.
            def _pick_worker(st_dict):
                pt = (st_dict.get("primary_tool") or "").lower()
                picked = _auto_chat_model(pt, worker_model)
                return picked

            # 2) Parallel Agents — total timeout으로 무한 대기 방지
            tasks = [
                asyncio.create_task(
                    _orchestrator_run_agent(gw, _pick_worker(st), st, project_path, base_sys, emit_queue,
                                             aws_profile=aws_profile, bedrock_user=bedrock_user, is_remote=is_remote)
                )
                for st in subtasks
            ]
            # 전체 오케스트레이션 timeout — 환경변수로 조정 (기본 7분)
            total_timeout = float(os.environ.get("AE_ORCH_TOTAL_TIMEOUT", "420"))
            try:
                raw_results = await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=total_timeout,
                )
            except asyncio.TimeoutError:
                print(f"[Orchestrator] 전체 timeout ({total_timeout}s) — 미완료 태스크 취소")
                # 완료된 태스크 결과만 수집, 미완료는 cancel
                raw_results = []
                for t in tasks:
                    if t.done():
                        try:
                            raw_results.append(t.result())
                        except Exception as ex:
                            raw_results.append(ex)
                    else:
                        t.cancel()
                        raw_results.append(asyncio.TimeoutError(f"timeout {int(total_timeout)}s"))
            except Exception as e:
                print(f"[Orchestrator] gather 예외: {e}")
                raw_results = [Exception(str(e))] * len(subtasks)

            # 예외를 결과 dict로 정규화 (모든 subtask가 반드시 결과 가짐)
            agent_results = []
            for st, r in zip(subtasks, raw_results):
                tid = st.get("id", "?")
                if isinstance(r, Exception):
                    err_msg = f"{type(r).__name__}: {r}"
                    print(f"[Orchestrator] agent {tid} 예외: {err_msg}")
                    await emit_queue.put({"type": "agent_error", "taskId": tid, "error": err_msg})
                    agent_results.append({
                        "taskId": tid, "role": st.get("role", "Worker"),
                        "title": st.get("title", ""), "status": "error",
                        "summary": err_msg, "tools": [],
                    })
                elif isinstance(r, dict):
                    agent_results.append(r)
                else:
                    # 알 수 없는 반환 — 빈 결과 정규화
                    agent_results.append({
                        "taskId": tid, "role": st.get("role", "Worker"),
                        "title": st.get("title", ""), "status": "error",
                        "summary": "에이전트가 결과를 반환하지 않음", "tools": [],
                    })

            # 3) Merger
            report = await _orchestrator_merge(gw, merger_id, user_prompt, agent_results, base_sys)
            await emit_queue.put({"type": "merge", "report": report, "results": agent_results})
            await emit_queue.put({"type": "__END__"})

        pipe_task = asyncio.create_task(pipeline())

        # Heartbeat — 30초마다 클라이언트 idle timeout 방지
        last_send = asyncio.get_event_loop().time()
        try:
            while True:
                try:
                    evt = await asyncio.wait_for(emit_queue.get(), timeout=20)
                    if evt.get("type") == "__END__":
                        break
                    yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                    last_send = asyncio.get_event_loop().time()
                except asyncio.TimeoutError:
                    # 20초 동안 이벤트 없음 → heartbeat
                    yield f"data: {json.dumps({'heartbeat': True, 'ts': int(asyncio.get_event_loop().time())})}\n\n"
                    last_send = asyncio.get_event_loop().time()
                    # pipeline이 끝났는지 확인
                    if pipe_task.done():
                        break
        finally:
            if not pipe_task.done():
                pipe_task.cancel()
                try: await pipe_task
                except Exception: pass
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
