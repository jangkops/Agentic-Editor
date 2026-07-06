# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — AI Editor backend (cross-platform: macOS / Windows / Linux).

동결 전략(onedir):
- 엔트리: ai_engine/run_server.py (uvicorn + ai_engine.server:app)
- ai_engine 전체 서브모듈 + 주요 서드파티(동적 import 포함)를 collect_all로 수집
- 산출물: ai_engine_dist/ai-engine-server/ (폴더), 내부에 ai-engine-server[.exe]
- onefile이 아닌 onedir — 매 실행 임시압축해제(2~5s 지연) 없이 빠르고 안정적

OS별로 각 러너에서 빌드해야 한다(크로스컴파일 불가). GitHub Actions matrix가 담당.
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_all

hiddenimports = []
datas = []
binaries = []

# ── 우리 백엔드 패키지 전체 ──────────────────────────────────────
hiddenimports += collect_submodules('ai_engine')
try:
    datas += collect_data_files('ai_engine', include_py_files=False)
except Exception:
    pass

# ── 서드파티 — 동적 import/데이터 파일/네이티브 바이너리까지 포함 ──
# 릴리스-크리티컬(RELEASE-CRITICAL) 4모듈: matplotlib, scipy, langgraph, pptx.
#   (R1.3) 이 4모듈은 반드시 동결 산출물에 포함되어야 하며, 누락 시 런타임 import 오류로
#   기능(다이어그램/차트·과학연산·에이전트 그래프·PPTX 생성)이 깨진다.
#   collect_all은 미설치 패키지를 조용히 건너뛰므로(아래 except: pass), 실제 존재 보장은
#   release.yml의 "Verify release-critical Python modules" 빌드 전 import 게이트
#   (커밋된 scripts/check_frozen_imports.py 실행)가 담당한다 — 누락 시 동결 이전에 잡 실패.
#   → 아래 리스트에서 4모듈(matplotlib, scipy, langgraph, pptx)이 모두 collect_all 대상에
#     포함되어 있는지 유지할 것(기존 항목 제거 금지).
_THIRD_PARTY = [
    'uvicorn', 'fastapi', 'starlette', 'pydantic', 'pydantic_core',
    'httpx', 'httpcore', 'anyio', 'sniffio', 'h11', 'click',
    'boto3', 'botocore', 's3transfer', 'jmespath', 'dateutil',
    'yaml', 'multipart',
    'pptx', 'reportlab', 'openpyxl', 'docx',  # python-pptx / python-docx  ← 'pptx' RELEASE-CRITICAL
    'PIL', 'matplotlib', 'numpy', 'sklearn', 'scipy',  # ← 'matplotlib', 'scipy' RELEASE-CRITICAL
    'google', 'google_auth_httplib2', 'requests', 'urllib3',
    'certifi', 'charset_normalizer', 'idna',
    'langgraph', 'langchain_core',  # ← 'langgraph' RELEASE-CRITICAL
    # RAG 다국어 신경망 임베딩(ONNX·CPU). 오프라인 교차언어 검색용 —
    # onnxruntime 네이티브 라이브러리/tokenizers/모델 메타를 collect_all로 포함.
    'fastembed', 'onnxruntime', 'tokenizers', 'huggingface_hub',
]
for _pkg in _THIRD_PARTY:
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception:
        # 설치되지 않은 선택 패키지는 건너뛴다(예: scipy 없을 수 있음)
        pass

block_cipher = None

a = Analysis(
    ['ai_engine/run_server.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PySide2', 'PyQt6', 'PySide6'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ai-engine-server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ai-engine-server',
)
