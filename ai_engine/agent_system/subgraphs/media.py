"""media 서브그래프 — pptx / pdf / image / docx / xlsx 생성·편집 도메인.

Task 3.3 산출물. `_common.build_domain_subgraph` 를 재사용하고 media 도메인의 도구 집합만
바인딩한다(요구사항 1.6). design.md 서브그래프 분할 기준(media 행)의 도구 목록을 따른다.

⚠️ 도구 name 정합성(server.py 실측):
- generate_image / generate_pdf / generate_pptx / generate_docx / generate_xlsx / edit_image
  은 server.py `_execute_tool` 이 정확히 이 이름으로 디스패치한다(AGENT_TOOLS toolSpec 에도
  등록됨). GatewayToolNode 는 이 이름을 그대로 `_execute_tool` 에 전달하므로 정합한다.
- generate_native_diagram 은 design 표에는 media 도구로 명시되지만, 현재 server.py 에는
  독립 도구로 등록되어 있지 않다(내부 헬퍼 `_tool_generate_native_diagram(...)` 이 pptx/pdf
  파이프라인 내부에서 kwargs 로만 호출됨). 즉 `_execute_tool("generate_native_diagram", ...)`
  는 현재 "알 수 없는 도구" 를 반환한다. design 표 정합을 위해 toolSpec 은 포함하되, 실제
  실행 배선은 후속 태스크(server.py `_execute_tool` 에 case 추가)에서 필요하다. 자세한 내용은
  실행 보고 참조.
"""

from __future__ import annotations

from typing import Any, List

from ai_engine.agent_system.subgraphs._common import build_domain_subgraph


# ─────────────────────────────────────────────────────────────────────────────
# MEDIA_TOOLS — Bedrock toolSpec dict 리스트. name 은 server.py `_execute_tool` /
# AGENT_TOOLS toolSpec 과 정확히 일치해야 한다(_execute_tool 이 name 으로 디스패치).
# ─────────────────────────────────────────────────────────────────────────────
MEDIA_TOOLS: List[dict] = [
    {
        "name": "generate_pptx",
        "description": (
            "구조화된 슬라이드로 PowerPoint(PPTX)를 생성한다. 각 슬라이드는 title / bullets 와 "
            "선택적 imagePrompt 를 가질 수 있다. .generated/ 에 저장한다."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "표지 슬라이드 제목"},
                    "slides": {
                        "type": "array",
                        "description": "슬라이드 목록",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "imagePrompt": {
                                    "type": "string",
                                    "description": "선택: 슬라이드용 이미지 자동 생성 프롬프트",
                                },
                                "layout": {
                                    "type": "string",
                                    "description": "title | content | two-column",
                                },
                            },
                        },
                    },
                },
                "required": ["title", "slides"],
            }
        },
    },
    {
        "name": "generate_pdf",
        "description": "구조화된 섹션들로 여러 페이지 PDF 문서를 생성한다. .generated/ 에 저장한다.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "문서 제목(표지)"},
                    "sections": {
                        "type": "array",
                        "description": "heading 과 body 텍스트를 가진 섹션 목록",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "body": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["title", "sections"],
            }
        },
    },
    {
        "name": "generate_image",
        "description": (
            "텍스트 프롬프트로 이미지를 생성한다(Bedrock 이미지 모델). PNG 를 .generated/ 에 "
            "저장한다."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "이미지 설명(최대 2000자)",
                    },
                    "size": {
                        "type": "string",
                        "description": "이미지 크기 (예: 1024x1024, 1024x768, 768x1024)",
                    },
                    "style": {
                        "type": "string",
                        "description": "선택적 스타일 프리셋(photographic, cinematic, anime 등)",
                    },
                },
                "required": ["prompt"],
            }
        },
    },
    {
        "name": "generate_docx",
        "description": (
            "python-docx 로 Word(DOCX) 문서를 생성한다. 섹션은 heading(h1/h2/h3)/body 를 "
            "지원한다. .generated/ 에 저장한다."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "문서 제목(표지 heading)"},
                    "sections": {
                        "type": "array",
                        "description": "섹션 목록",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "level": {
                                    "type": "integer",
                                    "description": "heading 레벨 1-3 (기본 2)",
                                },
                                "body": {
                                    "type": "string",
                                    "description": "섹션 본문(개행은 문단 분리)",
                                },
                                "bullets": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "선택적 불릿 목록",
                                },
                            },
                        },
                    },
                },
                "required": ["title", "sections"],
            }
        },
    },
    {
        "name": "generate_xlsx",
        "description": (
            "openpyxl 로 Excel(XLSX) 워크북을 생성한다. 각 시트는 headers(굵은 첫 행)와 "
            "데이터 rows 를 갖는다. .generated/ 에 저장한다."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "파일 제목(슬러그 + 첫 시트 헤더에 사용)",
                    },
                    "sheets": {
                        "type": "array",
                        "description": "시트 목록",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "시트 이름(최대 31자)",
                                },
                                "headers": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "헤더 행(굵게, 강조 배경)",
                                },
                                "rows": {
                                    "type": "array",
                                    "description": "데이터 행 — 배열의 배열. 셀은 문자열/숫자/불리언.",
                                    "items": {"type": "array"},
                                },
                            },
                        },
                    },
                },
                "required": ["title", "sheets"],
            }
        },
    },
    {
        "name": "edit_image",
        "description": (
            "기존 이미지를 편집한다. inpaint(마스크 영역 교체) 또는 outpaint(캔버스 확장) 모드를 "
            "지원한다. inpaint 는 mask_path 가, outpaint 는 direction/extend_pixels 가 필요하다."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["inpaint", "outpaint"],
                        "description": "편집 모드: inpaint 또는 outpaint",
                    },
                    "image_path": {
                        "type": "string",
                        "description": "원본 이미지 경로(PNG/JPEG, 최대 5MB)",
                    },
                    "prompt": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 1000,
                        "description": "편집 프롬프트(1~1000자)",
                    },
                    "mask_path": {
                        "type": "string",
                        "description": "마스크 이미지 경로(inpaint 필수, 흰색=편집 영역)",
                    },
                    "direction": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"],
                        },
                        "minItems": 1,
                        "maxItems": 4,
                        "description": "확장 방향(outpaint 필수, 1~4개)",
                    },
                    "extend_pixels": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1024,
                        "description": "확장 크기 픽셀(outpaint 필수, 1~1024)",
                    },
                },
                "required": ["mode", "image_path", "prompt"],
            }
        },
    },
    {
        "name": "generate_native_diagram",
        "description": (
            "네이티브(편집 가능) 다이어그램을 생성한다(flow / tree 등). "
            "⚠️ 현재 server.py `_execute_tool` 에 독립 도구로 배선되어 있지 않다 — 후속 "
            "태스크에서 디스패치 case 추가가 필요하다(실행 보고 참조)."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "diagram_type": {
                        "type": "string",
                        "description": "다이어그램 종류(flow, tree 등)",
                    },
                    "title": {"type": "string", "description": "다이어그램 제목"},
                    "content": {
                        "type": "string",
                        "description": "다이어그램으로 표현할 내용/설명",
                    },
                },
                "required": ["diagram_type", "title"],
            }
        },
    },
]


def build_media_subgraph(deps: Any):
    """media 서브그래프를 조립해 compiled Runnable 을 반환.

    구성은 coding 과 동일한 ReAct 루프(retrieve → model → tools → verify)이며 도구 집합만
    MEDIA_TOOLS 로 다르다. model_id 는 deps.model_coding(전 도메인 sonnet-4-5 기본 — design
    8절의 media planner=opus 는 Phase 2+ 옵션이므로 현재는 sonnet 통일).
    """
    return build_domain_subgraph(
        deps,
        tools=MEDIA_TOOLS,
        model_id=deps.model_coding,
        domain="media",
    )
