"""media 서브그래프 — pptx / pdf / image / docx / xlsx 생성·편집 도메인.

Task 3.3 산출물. `_common.build_domain_subgraph` 를 재사용하고 media 도메인의 도구 집합만
바인딩한다(요구사항 1.6). design.md 서브그래프 분할 기준(media 행)의 도구 목록을 따른다.

⚠️ 도구 name 정합성(server.py 실측):
- generate_image / generate_pdf / generate_pptx / generate_docx / generate_xlsx / edit_image
  은 server.py `_execute_tool` 이 정확히 이 이름으로 디스패치한다(AGENT_TOOLS toolSpec 에도
  등록됨). GatewayToolNode 는 이 이름을 그대로 `_execute_tool` 에 전달하므로 정합한다.
- generate_native_diagram 도 이제 server.py `_execute_tool` 에 독립 case 로 배선되어 있다
  (async 헬퍼 `_tool_generate_native_diagram(diagram_type, title, content, project_path)` 을
  `asyncio.run` 으로 감싸 호출; 기존 generate_* async 도구와 동일 패턴). 헬퍼는 matplotlib
  으로 PNG 를 생성하고 `{path: ".generated/...", model, width, height, sizeBytes}` JSON 을
  반환하므로 GatewayToolNode 의 verified_files 실측(path 추출)이 그대로 동작한다. AGENT_TOOLS
  toolSpec 목록에도 등록되어 있어 도구-디스패처 불일치가 해소되었다(요구사항 1.6/3.7).
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
            "네이티브(편집 가능 스타일) 다이어그램 PNG 를 matplotlib 로 생성한다"
            "(Bedrock 호출 없음). diagram_type: tree(들여쓰기 폴더 트리) / flow(좌→우 화살표) / "
            "architecture / stack / block. .generated/ 에 저장한다. server.py `_execute_tool` 에 "
            "배선되어 있으며 {path, model, width, height, sizeBytes} 를 반환한다."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "diagram_type": {
                        "type": "string",
                        "description": "다이어그램 종류(tree, flow, architecture, stack, block)",
                    },
                    "title": {"type": "string", "description": "다이어그램 제목"},
                    "content": {
                        "type": "string",
                        "description": "다이어그램으로 표현할 내용(tree=줄 목록, flow='A -> B -> C')",
                    },
                },
                # content 는 헬퍼가 비면 error 를 반환하므로 required 로 둔다
                # (AGENT_TOOLS toolSpec 과 정합).
                "required": ["diagram_type", "title", "content"],
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
