"""mermaid.ink 통합 테스트 — 한국어 라벨 + 다양한 다이어그램 종류 렌더링 확인."""
import sys, os, asyncio, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'ai_engine'))

from server import (
    _render_mermaid_to_png,
    _resolve_local_root,
    _encode_mermaid_for_ink,
)

PROJECT = os.path.join(tempfile.gettempdir(), 'ae_mermaid_test')
os.makedirs(PROJECT + '/.generated', exist_ok=True)

CASES = [
    ("architecture", """graph TB
    subgraph Frontend
        A[React 18<br/>SPA]
        B[Redux Toolkit]
    end
    subgraph Backend
        C[Node.js<br/>Express API]
        D[API Gateway]
    end
    subgraph Data
        E[PostgreSQL]
        F[MongoDB]
        G[Redis Cache]
    end
    A --> D
    B --> A
    D --> C
    C --> E
    C --> F
    C --> G
    classDef primary fill:#cfe2f3,stroke:#3c78d8,color:#1e1e1e
    classDef accent fill:#fff2cc,stroke:#bf9000,color:#1e1e1e
    classDef data fill:#d9ead3,stroke:#6aa84f,color:#1e1e1e
    class A,B primary
    class C,D accent
    class E,F,G data"""),
    ("tree", """graph TD
    Root[프로젝트 루트] --> Src[/src]
    Root --> Config[/config]
    Root --> Tests[/tests]
    Root --> Docs[/docs]
    Src --> Components[/components]
    Src --> Services[/services]
    Src --> Utils[/utils]
    Components --> Button[Button.tsx]
    Components --> Card[Card.tsx]
    Services --> Auth[auth.ts]
    Services --> API[api.ts]
    classDef dir fill:#cfe2f3,stroke:#3c78d8,color:#1e1e1e
    classDef file fill:#f3f3f3,stroke:#999,color:#1e1e1e
    class Root,Src,Config,Tests,Docs,Components,Services,Utils dir
    class Button,Card,Auth,API file"""),
    ("flow", """flowchart LR
    A[사용자 요청] --> B{인증 확인}
    B -->|성공| C[API 처리]
    B -->|실패| D[401 응답]
    C --> E[(PostgreSQL)]
    C --> F[(Redis 캐시)]
    E --> G[응답 반환]
    F --> G
    classDef start fill:#d9ead3,stroke:#6aa84f
    classDef decision fill:#fff2cc,stroke:#bf9000
    classDef end_node fill:#cfe2f3,stroke:#3c78d8
    class A start
    class B decision
    class G end_node"""),
]


async def main():
    for kind, code in CASES:
        print(f'=== {kind} ===')
        result = await _render_mermaid_to_png(code, project_path=PROJECT, timeout=30)
        parsed = json.loads(result)
        if "error" in parsed:
            print(f'  FAIL: {parsed}')
        else:
            abs_p = os.path.join(_resolve_local_root(PROJECT), parsed["path"])
            print(f'  OK: {parsed["path"]} ({parsed.get("width")}×{parsed.get("height")}, {parsed.get("sizeBytes")} bytes)')
            if os.path.isfile(abs_p) and os.path.getsize(abs_p) > 1000:
                print(f'    파일 검증: {os.path.getsize(abs_p)} bytes')
            else:
                print(f'    WARN: 파일이 없거나 너무 작음')


asyncio.run(main())
