# API_NOTES.md — LangGraph / langchain_core 1.x 인터페이스 실측 확정

> Task 1.1 산출물. design.md의 의사코드는 langgraph 0.2 / langchain-core 0.3 시절
> 문서를 참고했을 가능성이 있어, **실제 설치 버전 소스 introspection** + **공식 문서**로
> 대조해 정확한 1.x 시그니처를 확정했다.

## 실측 환경 (introspection 기준)

| 패키지 | 버전 | 확인 방법 |
|---|---|---|
| langgraph | **1.1.9** | `importlib.metadata.version` |
| langchain-core | **1.3.0** | `importlib.metadata.version` |
| langgraph-checkpoint | **4.0.2** | `importlib.metadata.version` |
| pydantic | 2.13.1 | `pydantic.VERSION` |
| Python (venv) | **3.14** | site-packages 경로에서 확인 |

- introspection 실행기: `ai_engine/.venv/bin/python`
- 모든 명령은 timeout을 두고 실행. (⚠️ **wait_for 관련 주의사항은 항목 5 참조**)
- _Requirements: 2.1, 4.1, 5.1_

---

## 1. `BaseChatModel` (langchain_core.language_models.chat_models) — 1.3.0

### 실측 결과 (introspection)

- **MRO:** `BaseChatModel → BaseLanguageModel → RunnableSerializable → Serializable →
  pydantic.main.BaseModel → Runnable → ABC → Generic → object`
- **pydantic v2 BaseModel 이다** (`issubclass(BaseChatModel, pydantic.BaseModel) == True`, pydantic 2.13.1).
- **필수 추상 메서드(`__abstractmethods__`)는 단 2개:** `_generate`, `_llm_type`.
  - `_agenerate`, `_stream`, `_astream`는 **추상이 아니며 기본 구현이 존재**(오버라이드 선택).
  - 단, LangGraph를 async(`ainvoke`/`astream`/`astream_events`)로 돌릴 것이므로
    **`_agenerate`와 `_astream`는 반드시 오버라이드**해야 실질 동작한다(기본 `_agenerate`는
    sync `_generate`를 스레드로 위임하므로 성능/이벤트 계측을 위해 직접 구현 권장).

실측 시그니처:

```text
_generate (self, messages: list[BaseMessage], stop: list[str] | None = None,
           run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult
_agenerate(self, messages: list[BaseMessage], stop: list[str] | None = None,
           run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any) -> ChatResult
_stream  (self, messages, stop=None, run_manager: CallbackManagerForLLMRun | None = None,
           **kwargs) -> Iterator[ChatGenerationChunk]
_astream (self, messages, stop=None, run_manager: AsyncCallbackManagerForLLMRun | None = None,
           **kwargs) -> AsyncIterator[ChatGenerationChunk]
_llm_type -> property (str 반환)
bind_tools(self, tools: Sequence[dict[str, Any] | type | Callable | BaseTool], *,
           tool_choice: str | None = None, **kwargs: Any) -> Runnable[LanguageModelInput, AIMessage]
```

### Pydantic 필드 선언 방식 (실측)

- `BaseChatModel.model_config` = `{'extra': 'ignore', 'protected_namespaces': (),
  'arbitrary_types_allowed': True}`
  - `arbitrary_types_allowed=True` → **`gateway: Any` 같은 임의 타입 필드 선언 가능**.
  - `protected_namespaces=()` → **`model_id` 같이 `model_` 접두 필드명 사용 가능**(경고 없음).
- **커스텀 필드(= pydantic model field):** 클래스 본문에 타입 어노테이션으로 선언.
  예) `gateway: Any = None`, `model_id: str = ''`, `request_timeout: float = 300.0`.
- **private attr:** 선행 밑줄(`_bound_tools`) 이름은 **pydantic private attribute로 처리**되며
  `model_fields`에 포함되지 않고 `__private_attributes__`로 관리된다.
  - 실측: `class MyModel(BaseChatModel): _bound: Optional[list] = None` →
    `__private_attributes__ == ['_bound']`, `'_bound' in model_fields == False`, 인스턴스에서
    `m._bound == None` 접근 가능.

### 공식문서 확인 여부
- 예. reference.langchain.com의 chat_models 문서와 일치(콜백 매니저 인자/반환 타입).

### ⚠️ design.md와의 차이
- design.md의 `bind_tools(self, tools, **kwargs)` → **실제는 `*, tool_choice: str | None = None`
  키워드 인자가 추가**되어 있음. 오버라이드 시 시그니처를 맞추는 것이 안전.
- design.md는 `_bound_tools: Optional[list[dict]] = None`을 **일반 필드처럼** 선언했으나,
  선행 밑줄 이름은 pydantic이 **private attr로 처리**한다. 도구 바인딩 상태는 필드가 아니라
  `self.bind(...)`(RunnableBinding)로 넘기고 `_agenerate`/`_astream`의 `**kwargs`에서
  `kwargs.get("_bedrock_tool_config")`로 읽는 설계가 정합적(디자인의 `bind_tools` 본문과 일치).

---

## 2. `BaseCheckpointSaver` (langgraph.checkpoint.base) — langgraph-checkpoint 4.0.2

파일: `.venv/.../langgraph/checkpoint/base/__init__.py`

### 실측 결과 (introspection)

- **`__abstractmethods__` == []** → 추상 메서드가 없다. 모든 메서드는 기본 구현(대부분
  `raise NotImplementedError`)을 가지며, **사용하는 메서드만 오버라이드**하면 된다.

실측 시그니처:

```text
put        (self, config: RunnableConfig, checkpoint: Checkpoint,
            metadata: CheckpointMetadata, new_versions: ChannelVersions) -> RunnableConfig
put_writes (self, config: RunnableConfig, writes: Sequence[tuple[str, Any]],
            task_id: str, task_path: str = '') -> None
get_tuple  (self, config: RunnableConfig) -> CheckpointTuple | None
list       (self, config: RunnableConfig | None, *, filter: dict[str, Any] | None = None,
            before: RunnableConfig | None = None, limit: int | None = None) -> Iterator[CheckpointTuple]

aput        (self, config, checkpoint, metadata, new_versions) -> RunnableConfig
aput_writes (self, config, writes, task_id, task_path: str = '') -> None
aget_tuple  (self, config) -> CheckpointTuple | None
alist       (self, config, *, filter=None, before=None, limit=None) -> AsyncIterator[CheckpointTuple]

get_next_version(self, current: V | None, channel: None) -> V
__init__(self, *, serde: SerializerProtocol | None = None) -> None
```

- **`CheckpointTuple` 필드(NamedTuple):** `('config', 'checkpoint', 'metadata',
  'parent_config', 'pending_writes')`
- **`Checkpoint` (TypedDict) 키:** `channel_values, channel_versions, id, ts, updated_channels,
  v, versions_seen`
- **`CheckpointMetadata` 키:** `parents, run_id, source, step`
- **`ChannelVersions` 타입:** `dict[str, str | int | float]`
- **`get_next_version` 기본 구현:** 정수 버전 증가(`None→1`, `int→+1`). `str`이 들어오면
  `NotImplementedError`. → JSON 저장 시 정수 버전 그대로 두면 문제 없음. **오버라이드 불필요.**

### ⚠️⚠️ 설계 보정 필요 (CRITICAL)

1. **async 메서드는 sync로 자동 위임되지 않는다.** 실측상 `BaseCheckpointSaver.aput`의
   기본 구현은 **`raise NotImplementedError`** 이다(`aput_writes`/`aget_tuple`/`alist` 동일).
   본 프로젝트 그래프는 `astream_events`/`ainvoke`(async)로 실행되므로,
   **`JsonFileCheckpointSaver`는 async 4종(`aput`/`aput_writes`/`aget_tuple`/`alist`)을 반드시
   구현**해야 한다(sync 메서드만 구현하면 런타임에 `NotImplementedError`).
   - 권장: async 메서드에서 sync 구현을 `await asyncio.to_thread(self.put, ...)` 등으로 위임.
   - 공식 포럼(langchain) 확인: "You need to implement four core **async** methods:
     aput / aput_writes / aget_tuple / alist" — 실측과 일치.
2. **`put_writes`/`aput_writes`에 `task_path: str = ''` 인자가 추가**됐다(design.md는
   `put_writes(config, writes, task_id)`로 3인자만 표기). 오버라이드 시 `task_path`를 포함할 것.
3. `__init__`는 `serde` 키워드 인자를 받는다. 커스텀 saver에서 `super().__init__(serde=...)`로
   직렬화기를 지정할 수 있다(기본은 JsonPlusSerializer 계열).

### 공식문서 확인 여부
- 예. reference.langchain.com/langgraph.checkpoint/base + langchain forum(커스텀 checkpointer)
  로 async 4종 구현 필요성 및 메서드 목적 확인.

---

## 3. `JsonPlusSerializer` (langgraph.checkpoint.serde.jsonplus) — 4.0.2

파일: `.venv/.../langgraph/checkpoint/serde/jsonplus.py`

### 실측 결과 (introspection)

- **`SerializerProtocol`은 `dumps_typed` / `loads_typed` 두 개만 요구**한다.
  (`SerializerProtocol` 멤버: `['dumps_typed', 'loads_typed']`)
- **`JsonPlusSerializer`에는 `dumps` / `loads`가 존재하지 않는다** (`AttributeError`).
  → design.md가 언급한 `dumps`/`loads`는 이 클래스엔 없음.

실측 시그니처:

```text
dumps_typed(self, obj: Any) -> tuple[str, bytes]
loads_typed(self, data: tuple[str, bytes]) -> Any
__init__(self, *, pickle_fallback: bool = False,
         allowed_json_modules=..., allowed_msgpack_modules=..., __unpack_ext_hook__=None)
```

- **기본 인코딩은 `msgpack`(바이너리 bytes)** 이다. 실측:
  - `dumps_typed({'a':1})` → `('msgpack', b'\x81\xa1a\x01')`
  - `dumps_typed('hello')` → `('msgpack', b'\xa5hello')`
  - LangChain 메시지도 왕복 보존됨: `AIMessage(tool_calls=[...])` →
    `loads_typed` 후 `tool_calls` 그대로 복원.

### JSON 파일 저장에 적합한 방식 (권장)

- `dumps_typed`가 **`(type_tag: str, data: bytes)` 튜플**을 반환하므로, JSON 파일에 그대로 담을 수
  없다(바이너리). **권장 저장 규약:**
  ```json
  { "type": "<type_tag>", "data": "<base64(data)>" }
  ```
  저장 시 `base64.b64encode(data).decode()`, 로드 시 `loads_typed((type, b64decode(data)))`.
- 이렇게 하면 `.json` 파일 텍스트 유지 + LangChain 객체(메시지/ToolCall 등) 무손실 복원.
- SQLite 미사용 제약(steering)과 부합: 파일은 JSON, 값만 base64 인코딩된 msgpack.

### 공식문서 확인 여부
- 예. reference.langchain.com/langgraph.checkpoint/base의 보안 노트(신뢰불가 객체 역직렬화 주의)
  확인 → 체크포인트 파일은 `userData` 하위 신뢰 경로에만 저장(외부 쓰기 금지).

### ⚠️ design.md와의 차이
- design.md의 `dumps`/`loads`/`dumps_typed`/`loads_typed` 4종 언급 → **실제는 `dumps_typed`/
  `loads_typed` 2종만 존재.** `dumps`/`loads` 사용 코드 작성 금지.
- 직렬화 결과가 JSON 텍스트가 아니라 **msgpack bytes** 라는 점 → 위 base64 래핑 규약 필요.

---

## 4. `ToolNode` / `tools_condition` (langgraph.prebuilt) — 1.1.9

### 실측 결과 (introspection)

```text
ToolNode.__init__(self, tools: Sequence[BaseTool | Callable], *, name: str = 'tools',
    tags: list[str] | None = None,
    handle_tool_errors: bool | str | Callable | type[Exception] | tuple[...] = <default>,
    messages_key: str = 'messages',
    wrap_tool_call: ToolCallWrapper | None = None,
    awrap_tool_call: AsyncToolCallWrapper | None = None) -> None

tools_condition(state: list[AnyMessage] | dict[str, Any] | BaseModel,
    messages_key: str = 'messages') -> Literal['tools', '__end__']
```

- `prebuilt` exports: `InjectedState, InjectedStore, ToolNode, ToolRuntime, ValidationNode,
  chat_agent_executor, create_react_agent, tool_node, tool_validator, tools_condition`.
- **상태 계약(state contract):**
  - `ToolNode`는 `state[messages_key]`(기본 `"messages"`)의 **마지막 `AIMessage.tool_calls`**
    를 읽어 각 도구를 실행하고, **`{messages_key: [ToolMessage, ...]}`** 형태로 반환한다.
    각 `ToolMessage.tool_call_id`는 해당 `ToolCall["id"]`와 일치해야 한다.
  - `ToolNode(tools=...)`의 `tools`는 **`BaseTool | Callable`** 만 받는다(**dict 불가**).
  - `tools_condition`은 **마지막 메시지에 `tool_calls`가 있으면 `"tools"`, 없으면 `"__end__"`**
    반환. dict/BaseModel/list 상태 모두 지원.

### 커스텀 `GatewayToolNode`가 맞춰야 하는 반환 형식

- 노드는 plain callable(또는 async callable)로서 다음을 만족:
  - 입력: `state`(dict), `state["messages"][-1].tool_calls` 존재.
  - 출력: `{"messages": [ToolMessage(content=str(...), tool_call_id=tc["id"]), ...],
    "verified_files": [...]}`.
    - `tool_call_id`는 반드시 원본 `tool_calls[i]["id"]`와 매칭.
    - `content`는 문자열(비문자열은 str 변환).
- 라우팅은 `tools_condition`(→ `'tools'`/`'__end__'`)를 그대로 쓰거나, design의
  `tools_condition_or_verify`(→ `'tools'`/`'verify'`)처럼 커스텀 매핑 dict와 함께 사용 가능.
  단 반환 라벨이 `add_conditional_edges`의 매핑 키와 일치해야 함.

### 공식문서 확인 여부
- 예(소스 docstring 포함). design.md와 계약 일치.

### ⚠️ design.md와의 차이
- design.md는 `GatewayToolNode(CODING_TOOLS, ...)`에 도구를 dict로 넘기는 뉘앙스가 있으나,
  **표준 `ToolNode`는 dict를 받지 않음**(BaseTool/Callable). 커스텀 노드는 자체 규약이므로
  자유지만, `tool_names` 집합 계산 시 dict/BaseTool 양쪽을 방어적으로 처리할 것(디자인 코드
  `t["name"] if isinstance(t, dict) else t.name`은 OK).

---

## 5. `astream_events` — langgraph 1.1.9 CompiledGraph (Runnable 상속)

### 실측 결과 (introspection + 실행)

```text
astream_events(self, input: Any, config: RunnableConfig | None = None, *,
    version: Literal['v1', 'v2'] = 'v2',
    include_names=None, include_types=None, include_tags=None,
    exclude_names=None, exclude_types=None, exclude_tags=None, **kwargs) -> AsyncIterator[StreamEvent]
```

- **`version` 인자 유효값: `'v1'` / `'v2'`, 기본값 `'v2'`** (langchain-core 1.3.0).
  → **`version="v2"` 사용 권장.** deprecated 아님(정식 API).
- **StreamEvent 최상위 키(실측):** `['data', 'event', 'metadata', 'name', 'parent_ids',
  'run_id', 'tags']`
- **이벤트 타입별 `data` 키(실측):**

  | event | data 키 | 비고 |
  |---|---|---|
  | `on_chain_start` | `input` | 노드/그래프 진입 |
  | `on_chain_stream` | `chunk` | 노드/그래프 부분 출력(dict) |
  | `on_chain_end` | `input`, `output` | 노드/그래프 종료 |
  | `on_chat_model_start` | `input` | 모델 호출 시작 |
  | `on_chat_model_stream` | `chunk` (**AIMessageChunk**) | **토큰 스트림** — content=토큰 텍스트 |
  | `on_chat_model_end` | `input`, `output` (**AIMessage**) | 모델 호출 종료 |
  | `on_tool_start` | `input` | 도구 실행 시작 |
  | `on_tool_end` | `input`, `output` | 도구 실행 종료 |

- **중요(SSE 매핑에 유리):** `on_chat_model_stream`은 **노드가 `llm.ainvoke(...)`(비스트리밍)**
  를 호출해도 astream_events가 모델 Runnable을 자동 계측하여 **토큰 단위로 emit**된다(실측
  확인: GenericFakeChatModel + 노드가 ainvoke만 호출해도 토큰 chunk 7개 방출).
  → design의 `_astream` 직접 스트리밍이 없어도 토큰 SSE가 가능하나, Gateway 실시간성을 위해
  `_astream` 구현은 여전히 권장.
- `ev["name"]`으로 어떤 노드/서브그래프인지 식별 가능(예: `'LangGraph'`(최상위), `'model'`,
  `'tools'`, 도구명 `'echo'`, 서브그래프명). `ev["metadata"]["langgraph_node"]`도 활용 가능.

### ⚠️⚠️ 설계 보정 필요 (운영 안정성 — 과거 hang 이력과 직접 관련)

- **`asyncio.wait_for(...)`로 astream_events/astream 루프 "전체"를 감싸면 hang이 발생**할 수
  있다(Python 3.14 venv 실측). wait_for가 타임아웃으로 async generator를 취소할 때,
  generator의 `aclose()`/루프 종료 단계에서 블로킹되어 프로세스가 종료되지 않았다.
  - ✅ 권장 패턴: **노드 내부에서 `asyncio.wait_for(llm.ainvoke(...), MODEL_NODE_TIMEOUT)`**
    처럼 개별 await만 감싸고, **스트림 소비 루프 자체는 wait_for로 감싸지 말 것**.
  - 그래프 전체 상한이 필요하면 `astream_events(..., config={"recursion_limit": N})` +
    per-node timeout + (필요 시) `async for` 루프에 자체 카운터/deadline 체크로 `break`.
  - design.md task 3.6의 "그래프 전체를 `asyncio.wait_for(GRAPH_TOTAL_TIMEOUT)`로 래핑"은
    **스트리밍 경로에선 위험** → deadline 기반 수동 중단 또는 `anyio` 취소 스코프 검토 필요.
    (참고: `ainvoke`는 wait_for로 감싸도 정상 종료 확인됨. 문제는 스트리밍 제너레이터 취소.)
- `stream_mode` 대안: `astream(input, stream_mode='updates')`(노드별 상태 업데이트) 및
  `stream_mode='messages'`(토큰+메타)도 정상 동작 확인. astream_events가 과하면 대안 가능.

### 공식문서 확인 여부
- 예. reference.langchain.com streamEvents 문서(StreamEvent 스키마: `event`/`name`/`run_id`/
  `tags`/`metadata`/`data`/`parent_ids`) 및 v1/v2 version 인자 확인.

---

## 6. `StateGraph` / `add_messages` / conditional edges / compiled subgraph as node — 1.1.9

### 실측 결과 (introspection + 실행)

```text
StateGraph.add_node(self, node: str | StateNode, action: StateNode | None = None, *,
    defer: bool = False, metadata: dict | None = None, input_schema: type | None = None,
    retry_policy: RetryPolicy | Sequence[RetryPolicy] | None = None,
    cache_policy: CachePolicy | None = None,
    destinations: dict[str, str] | tuple[str, ...] | None = None, **kwargs) -> Self

add_messages(left: Messages | None = None, right: Messages | None = None, **kwargs)
    -> Messages | Callable[[Messages, Messages], Messages]
```

- **`add_node("child", compiled_subgraph)` 유효함(실측 확인).** `sg.compile()` 결과는
  `CompiledStateGraph` 이며, 이를 부모 `StateGraph.add_node`에 넘겨 **graph-of-graphs**가
  정상 실행됨(부모 ainvoke → 자식 서브그래프 노드 실행 → messages 병합 확인).
- `add_messages`는 reducer로 `Annotated[list[BaseMessage], add_messages]` 형태로 사용.
- conditional edges: `add_conditional_edges(source, path_fn, path_map_dict)` — path_fn 반환
  라벨이 path_map의 키와 일치해야 함. (design의 router/서브그래프 패턴과 정합.)
- `START` / `END`는 `langgraph.graph`에서 import.

### 공식문서 확인 여부
- 예. graph-of-graphs(서브그래프를 노드로 add)는 langgraph 공식 subgraph 패턴과 일치.

### ⚠️ design.md와의 차이
- 없음(핵심 계약 일치). 서브그래프는 부모의 checkpointer/스트리밍을 상속하므로,
  `build_*_subgraph`의 `sg.compile()`에는 checkpointer를 넘기지 않고 **부모
  `build_top_graph`의 `compile(checkpointer=...)`에서만 주입**하는 design 방침이 맞다.

---

## 7. `langchain_core.messages` — ToolCall / AIMessage.tool_calls / AIMessageChunk.tool_call_chunks

### 실측 결과 (introspection)

- **`ToolCall` (TypedDict) 어노테이션:**
  ```text
  {'name': str, 'args': dict[str, Any], 'id': str | None,
   'type': NotRequired[Literal['tool_call']]}
  ```
- **`ToolCallChunk` (TypedDict) 어노테이션:**
  ```text
  {'name': str | None, 'args': str | None, 'id': str | None,
   'index': int | None, 'type': NotRequired[Literal['tool_call_chunk']]}
  ```
- **`AIMessage.tool_calls`:** `list[ToolCall]` — 각 항목은 **dict**(TypedDict)로 노출.
  실측: `[{'name': 'gen_pptx', 'args': {'topic': 'x'}, 'id': 'tid1', 'type': 'tool_call'}]`
  → `tc["id"]`, `tc["name"]`, `tc["args"]`로 접근(디자인 코드 `tc["id"]/tc["name"]/tc["args"]`와 일치).
- **`AIMessageChunk.tool_call_chunks`:** 스트리밍 중 부분 toolUse 조각을 담음. `args`는
  **문자열 조각(JSON 일부)**. 청크를 `+`로 누적하면 args 문자열이 이어붙고, `.tool_calls`
  프로퍼티가 완성된 JSON을 파싱해 dict args로 변환.
  - 실측 누적: `c1(args='{"topic":') + c2(args='"x"}')` →
    `tool_call_chunks[0].args == '{"topic":"x"}'`, 파싱된 `tool_calls == [{'name':'gen_pptx',
    'args':{'topic':'x'}, 'id':'tid1', 'type':'tool_call'}]`.

### 커스텀 어댑터 매핑 규약 (Bedrock ↔ LangChain)

- Bedrock converse `output.message.content[].toolUse{toolUseId,name,input}` →
  `ToolCall(id=toolUseId, name=name, args=input)` (AIMessage.tool_calls에 적재).
- 스트리밍 부분 `toolUse` → `AIMessageChunk(tool_call_chunks=[{name,args(str조각),id,index}])`.
  design의 `_tooluse_delta_to_chunk`는 이 규약을 따르면 됨.

### 공식문서 확인 여부
- 예(타입 소스 어노테이션 직접 확인). design.md의 ToolCall/tool_calls 사용과 일치.

### ⚠️ design.md와의 차이
- 없음. 다만 `tool_calls` 항목이 dataclass/pydantic 객체가 아니라 **dict**라는 점을 명확히
  인지(속성 접근 `.id`가 아니라 `["id"]`).

---

## ⚠️ 설계 보정 필요 — 종합 정리 (구현 전 반영)

1. **[CRITICAL] checkpointer async 메서드 필수 구현.** `BaseCheckpointSaver`의
   `aput`/`aput_writes`/`aget_tuple`/`alist` 기본 구현은 `NotImplementedError`이며 sync로
   자동 위임되지 않는다. async 그래프 실행 경로이므로 `JsonFileCheckpointSaver`는 **async 4종을
   반드시 구현**(sync 로직을 `asyncio.to_thread`로 위임 권장). → **task 1.7 범위 확대**.
2. **[CRITICAL] 스트리밍 루프를 `asyncio.wait_for`로 통째 감싸지 말 것.** Python 3.14 venv에서
   astream/astream_events 소비 루프를 wait_for로 감싸면 취소 시 hang. per-node timeout +
   recursion_limit + deadline 기반 수동 break로 대체. → **task 3.6 / 5.4 / 5.6 방침 수정**.
3. **[HIGH] `put_writes`/`aput_writes` 시그니처에 `task_path: str = ''` 추가.** design의 3인자
   표기를 4인자로 보정. → **task 1.7**.
4. **[HIGH] JsonPlusSerializer는 `dumps_typed`/`loads_typed`만 존재(기본 msgpack bytes).**
   `dumps`/`loads` 없음. JSON 파일 저장은 `{"type": tag, "data": base64(bytes)}` 래핑 규약
   사용. → **task 1.7**.
5. **[MED] `bind_tools` 시그니처에 `tool_choice` 키워드 존재**, 도구 바인딩 상태는 model field가
   아니라 private attr/`self.bind(...)`로 관리. `_bound_tools` 필드 선언 방식 보정. → **task 1.3**.
6. **[MED] `ToolNode`(prebuilt)는 dict 도구를 받지 않음**(BaseTool/Callable). 커스텀
   `GatewayToolNode`는 자체 규약이므로 무방하나, 표준 노드 대체 시 반환 형식
   `{"messages":[ToolMessage(tool_call_id=원본 id)]}` 준수. → **task 1.10**.
7. **[INFO] `on_chat_model_stream`은 노드가 비스트리밍 `ainvoke`를 써도 자동 계측**되어 토큰 SSE
   가능. SSE 브리지는 `on_chat_model_stream.data.chunk`(AIMessageChunk).content를 text로 매핑.
   → **task 5.4** 근거.

---

## 부록 — 재현용 introspection 요약 (사용 명령 개요)

- 모든 검증은 `ai_engine/.venv/bin/python -u`로 실행. (임시 스크립트는 검증 후 삭제)
- 버전: `importlib.metadata.version('langgraph'|'langchain-core'|'langgraph-checkpoint')`
- 시그니처: `inspect.signature(...)`, `Class.__abstractmethods__`, `Class.model_config`,
  `Class.model_fields`, `Class.__private_attributes__`, `TypedDict.__annotations__`
- 런타임 이벤트: 최소 `StateGraph`(model + `ToolNode` + `GenericFakeChatModel`)를 조립해
  `astream_events(version="v2")` 실제 방출 이벤트/데이터 키 수집.
- ⚠️ 스트리밍 검증 시 `asyncio.wait_for`로 루프를 감싸면 hang → wait_for 없이 실행하고
  이벤트 개수 cap 또는 자연 종료로 확인.
