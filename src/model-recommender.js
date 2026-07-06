/* ===== Smart Model Recommender (작업별 + 단일/병렬/파이프라인 전략) ===== */

// ─── 작업별 추천 모델 (1순위 → 후순위) ───────────────────────────────────
const TASK_MODEL_MAP = {
  'image-gen': [
    { prefix: 'stability.stable-image-ultra', name: 'Stable Image Ultra', desc: '최고 품질, 포토리얼리즘' },
    { prefix: 'stability.sd3-5-large',        name: 'Stable Diffusion 3.5', desc: '복잡한 구도·텍스트 우수' },
    { prefix: 'stability.stable-image-core',  name: 'Stable Image Core', desc: '빠른 프로토타이핑' },
    { prefix: 'amazon.nova-canvas',           name: 'Nova Canvas', desc: '인페인팅·아웃페인팅' },
    { prefix: 'amazon.titan-image',           name: 'Titan Image v2', desc: '안정적, 워터마크 내장' },
  ],
  'image-edit': [
    { prefix: 'amazon.titan-image',                       name: 'Titan Image v2', desc: '인페인팅 안정성' },
    { prefix: 'amazon.nova-canvas',                       name: 'Nova Canvas', desc: '아웃페인팅·인페인팅' },
    { prefix: 'stability.stable-image-inpaint',           name: 'Stable Image Inpaint', desc: '정밀 인페인팅' },
    { prefix: 'stability.stable-outpaint',                name: 'Stable Outpaint', desc: '아웃페인팅 전용' },
    { prefix: 'stability.stable-image-erase-object',      name: 'Erase Object', desc: '오브젝트 제거' },
    { prefix: 'stability.stable-image-remove-background', name: 'Remove Background', desc: '배경 제거' },
  ],
  'image-analysis': [
    { prefix: 'anthropic.claude-sonnet-4',  name: 'Claude Sonnet 4', desc: 'vision + 정확한 분석' },
    { prefix: 'anthropic.claude-opus-4',    name: 'Claude Opus 4', desc: '최고 정확도' },
    { prefix: 'mistral.pixtral-large',      name: 'Pixtral Large', desc: '비전 특화' },
    { prefix: 'amazon.nova-pro',            name: 'Nova Pro', desc: '범용 멀티모달' },
    { prefix: 'qwen.qwen3-vl',              name: 'Qwen3 VL', desc: '대규모 비전 추론' },
  ],
  'video-gen': [
    { prefix: 'luma.ray',          name: 'Luma Ray v2', desc: '고품질 자연스러운 모션' },
    { prefix: 'amazon.nova-reel',  name: 'Nova Reel', desc: '6초 비디오, 카메라 제어' },
  ],
  'pdf-gen': [
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '단계별 추론·논리 흐름 최강' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '긴 문서 구조화' },
    { prefix: 'writer.palmyra-x',            name: 'Palmyra X5', desc: '비즈니스 문서 특화' },
    { prefix: 'mistral.mistral-large-3',     name: 'Mistral Large 3', desc: '창의적 레이아웃' },
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '균형잡힌 긴 문서' },
  ],
  'pdf-analysis': [
    { prefix: 'moonshot.kimi-k2',            name: 'Kimi K2', desc: '긴 문서 처리 특화 (200K+)' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '대규모 문서 이해' },
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '단계별 요약' },
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '균형잡힌 분석' },
  ],
  'pptx-gen': [
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '슬라이드 논리 구성' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '대규모 슬라이드 구조' },
    { prefix: 'mistral.mistral-large-3',     name: 'Mistral Large 3', desc: '비주얼 레이아웃' },
    { prefix: 'writer.palmyra-x',            name: 'Palmyra X5', desc: '비즈니스 발표자료' },
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '균형잡힌 옵션' },
  ],
  // 슬라이드 분석 (요약/내용 추출) — vision_input 가능 모델 + 추론력
  'pptx-analysis': [
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: 'vision + 슬라이드 이해' },
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '깊이있는 슬라이드 분석' },
    { prefix: 'qwen.qwen3-vl',               name: 'Qwen3 VL', desc: '대규모 비전 추론' },
    { prefix: 'amazon.nova-pro',             name: 'Nova Pro', desc: '범용 멀티모달' },
  ],
  'xlsx-work': [
    { prefix: 'qwen.qwen3-coder-480b',       name: 'Qwen3 Coder 480B', desc: '대규모 데이터·수식' },
    { prefix: 'deepseek.v3',                 name: 'DeepSeek V3', desc: '수식·표 정확도' },
    { prefix: 'mistral.devstral',            name: 'Devstral', desc: '빠른 데이터 처리' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '균형잡힌 분석' },
  ],
  // 엑셀 분석 (데이터 인사이트, 차트 해석)
  'xlsx-analysis': [
    { prefix: 'deepseek.v3',                 name: 'DeepSeek V3', desc: '수치 분석·인사이트' },
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '논리적 추론' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '대규모 데이터 이해' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '균형잡힌 해설' },
  ],
  // 워드 (.docx) — 비즈니스 문서 특화
  'docx-work': [
    { prefix: 'writer.palmyra-x',            name: 'Palmyra X5', desc: '비즈니스 워드 문서 특화' },
    { prefix: 'mistral.mistral-large-3',     name: 'Mistral Large 3', desc: '범용 문서·다국어' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '대규모 문서 처리' },
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '논리적 문서 구성' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '균형잡힌 옵션' },
  ],
  // 한글 (.hwp/.hwpx) — 한국어 강점 모델 우선
  'hwp-work': [
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '한국어/아시아권 강점' },
    { prefix: 'qwen.qwen3-next',             name: 'Qwen3 Next', desc: '한국어 빠른 처리' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '한국어 자연스러움' },
    { prefix: 'mistral.mistral-large-3',     name: 'Mistral Large 3', desc: '다국어 지원' },
    { prefix: 'writer.palmyra-x',            name: 'Palmyra X5', desc: '비즈니스 문서' },
  ],
  // draw.io / 다이어그램 (XML 구조 + 그리기 명령)
  'drawio-work': [
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '논리적 노드/엣지 구성' },
    { prefix: 'qwen.qwen3-coder-480b',       name: 'Qwen3 Coder 480B', desc: 'XML 정확도·코드' },
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '구조화·도식화' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '대규모 다이어그램' },
  ],
  // 자유 다이어그램 그리기 (mermaid/plantuml/SVG)
  'diagram-gen': [
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '논리 흐름 다이어그램' },
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '시스템 도식화' },
    { prefix: 'qwen.qwen3-coder-480b',       name: 'Qwen3 Coder 480B', desc: '코드 기반 도식 (mermaid/plantuml)' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '빠른 도식 생성' },
  ],
  // legacy doc-work (호환성 — 일반 문서 키워드)
  'doc-work': [
    { prefix: 'writer.palmyra-x',            name: 'Palmyra X5', desc: '비즈니스 문서 특화' },
    { prefix: 'mistral.mistral-large-3',     name: 'Mistral Large 3', desc: '범용 문서·다국어' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '한국어/아시아권 강점' },
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '논리적 문서 구성' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '균형잡힌 옵션' },
  ],
  'code-write': [
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '코드 생성·에이전트' },
    { prefix: 'qwen.qwen3-coder-480b',       name: 'Qwen3 Coder 480B', desc: '대규모 코드베이스' },
    { prefix: 'mistral.devstral',            name: 'Devstral', desc: '빠른 코드 생성' },
    { prefix: 'deepseek.v3',                 name: 'DeepSeek V3', desc: '비용 효율적 코딩' },
  ],
  'code-review': [
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '깊이있는 리뷰' },
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '단계별 추론' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '빠른 리뷰' },
  ],
  'architecture': [
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '시스템 설계 추론' },
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '논리적 분해' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '대규모 분석' },
  ],
  'math-logic': [
    { prefix: 'deepseek.r1',                 name: 'DeepSeek R1', desc: '수학·증명 특화' },
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '논리 추론' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '대규모 추론' },
  ],
  'translation': [
    { prefix: 'anthropic.claude-haiku-4',    name: 'Claude Haiku 4.5', desc: '초고속 번역' },
    { prefix: 'amazon.nova-lite',            name: 'Nova Lite', desc: '저렴·빠른 번역' },
    { prefix: 'mistral.mistral-large-3',     name: 'Mistral Large 3', desc: '유럽어 강점' },
    { prefix: 'qwen.qwen3-235b',             name: 'Qwen3 235B', desc: '중국어·아시아 다국어' },
  ],
  'simple-qa': [
    { prefix: 'anthropic.claude-haiku-4',    name: 'Claude Haiku 4.5', desc: '초고속 응답' },
    { prefix: 'amazon.nova-micro',           name: 'Nova Micro', desc: '최저 비용' },
    { prefix: 'amazon.nova-lite',            name: 'Nova Lite', desc: '빠르고 저렴' },
  ],
  'embedding': [
    { prefix: 'cohere.embed-v4',             name: 'Cohere Embed v4', desc: '최신·다국어' },
    { prefix: 'amazon.titan-embed-text-v2',  name: 'Titan Embed v2', desc: 'AWS 네이티브' },
    { prefix: 'cohere.embed-multilingual',   name: 'Cohere Multilingual', desc: '다국어 검색' },
    { prefix: 'cohere.embed-english',        name: 'Cohere English', desc: '영어 특화' },
  ],
  // === 이미지 생성용 프롬프트/컨셉 정교화 (창의적 시각 묘사) ===
  'image-prompt': [
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '창의적 시각 묘사·디테일' },
    { prefix: 'mistral.pixtral-large',       name: 'Pixtral Large', desc: '비전 이해 + 묘사' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '균형잡힌 묘사' },
    { prefix: 'mistral.mistral-large-3',     name: 'Mistral Large 3', desc: '창의적 표현' },
    { prefix: 'qwen.qwen3-vl',               name: 'Qwen3 VL', desc: '비전 추론' },
  ],
  // === 비디오 시나리오/스토리보드 작성 ===
  'video-prompt': [
    { prefix: 'anthropic.claude-opus-4',     name: 'Claude Opus 4', desc: '스토리텔링·시나리오' },
    { prefix: 'mistral.mistral-large-3',     name: 'Mistral Large 3', desc: '창의적 서사' },
    { prefix: 'anthropic.claude-sonnet-4',   name: 'Claude Sonnet 4', desc: '간결한 시나리오' },
    { prefix: 'writer.palmyra-x',            name: 'Palmyra X5', desc: '광고·마케팅 카피' },
  ],
};

// ─── 작업별 실행 전략 (병렬 / 파이프라인) ────────────────────────────────
const TASK_EXECUTION_STRATEGIES = {
  // === 분석 작업 — 병렬 호출로 관점 다양성 확보 ===
  'image-analysis': {
    parallel: { tasks: ['image-analysis'], count: 3, reason: '여러 비전 모델의 분석을 비교하면 정확도 향상' },
  },
  'pptx-analysis': {
    parallel: { tasks: ['pptx-analysis'], count: 3, reason: '슬라이드 내용을 여러 모델이 다르게 해석 — 누락 방지' },
  },
  'xlsx-analysis': {
    parallel: { tasks: ['xlsx-analysis'], count: 3, reason: '데이터 인사이트를 여러 모델이 교차 검증' },
  },
  'pdf-analysis': {
    parallel: { tasks: ['pdf-analysis'], count: 2, reason: '긴 문서는 두 모델로 교차 요약하면 누락 감소' },
  },
  'code-review': {
    parallel: { tasks: ['code-review'], count: 3, reason: '서로 다른 추론 방식으로 누락된 버그 발견' },
  },
  'architecture': {
    parallel: { tasks: ['architecture'], count: 3, reason: '여러 모델의 설계 관점을 비교' },
  },
  'math-logic': {
    parallel: { tasks: ['math-logic'], count: 3, reason: '교차 검증으로 정답 신뢰도 향상' },
  },

  // === 생성 작업 — 파이프라인 (단계별 전문 모델) ===
  'pdf-gen': {
    pipeline: {
      stages: [
        { stage: 'generate', task: 'pdf-gen', label: '문서 생성 (편집 가능 고품질)' },
      ],
      reason: '단일 생성으로 결과물 1개만 산출 — 단계 중복 생성/품질 편차 방지',
    },
  },
  'pptx-gen': {
    pipeline: {
      stages: [
        { stage: 'generate', task: 'pptx-gen', label: '발표자료 생성 (편집 가능 고품질)' },
      ],
      reason: '단일 생성으로 결과물 1개만 산출 — 단계 중복 생성/품질 편차 방지',
    },
  },
  'xlsx-work': {
    pipeline: {
      stages: [
        { stage: 'generate', task: 'xlsx-work', label: '엑셀 생성 (데이터·해설 포함)' },
      ],
      reason: '단일 생성으로 결과물 1개만 산출',
    },
  },
  'docx-work': {
    pipeline: {
      stages: [
        { stage: 'generate', task: 'docx-work', label: '문서 생성 (편집 가능)' },
      ],
      reason: '단일 생성으로 결과물 1개만 산출',
    },
  },
  'hwp-work': {
    pipeline: {
      stages: [
        { stage: 'generate', task: 'hwp-work', label: '한글 문서 생성' },
      ],
      reason: '단일 생성으로 결과물 1개만 산출',
    },
  },
  'drawio-work': {
    pipeline: {
      stages: [
        { stage: 'plan',  task: 'architecture',  label: '다이어그램 구조 설계' },
        { stage: 'xml',   task: 'drawio-work',   label: 'draw.io XML 생성' },
      ],
      reason: '논리 설계 → XML 변환으로 정확한 도식 생성',
    },
  },
  'diagram-gen': {
    pipeline: {
      stages: [
        { stage: 'plan',     task: 'architecture',  label: '도식 구조 설계' },
        { stage: 'render',   task: 'diagram-gen',   label: 'mermaid/plantuml 코드 생성' },
      ],
      reason: '논리 설계 → 코드 변환으로 정확한 다이어그램',
    },
    parallel: { tasks: ['diagram-gen'], count: 2, reason: '여러 모델이 다른 형식(mermaid vs plantuml)으로 생성해 비교' },
  },

  'image-edit': {
    pipeline: {
      stages: [
        { stage: 'analyze',  task: 'image-analysis', label: '원본 이미지 분석' },
        { stage: 'edit',     task: 'image-edit',     label: '편집 적용' },
      ],
      reason: '편집 전 원본을 정확히 이해해야 의도대로 수정 가능',
    },
  },
  'image-gen': {
    parallel: { tasks: ['image-gen'], count: 3, reason: '여러 이미지 모델을 동시에 호출해 결과 비교' },
    pipeline: {
      stages: [
        { stage: 'plan',  task: 'image-prompt', label: '이미지 컨셉·프롬프트 정교화' },
        { stage: 'image', task: 'image-gen',    label: '이미지 생성' },
      ],
      reason: '창의적 시각 묘사 모델로 컨셉 정리 → 이미지 모델로 생성하면 의도 정확도 ↑',
    },
  },
  'video-gen': {
    pipeline: {
      stages: [
        { stage: 'plan',  task: 'video-prompt', label: '비디오 시나리오·스토리보드 작성' },
        { stage: 'video', task: 'video-gen',    label: '비디오 생성' },
      ],
      reason: '시나리오 → 비디오 분리로 결과물 통제력 향상',
    },
  },
};

// ─── 작업 패턴 ─────────────────────────────────────────────────────────
const TASK_PATTERNS = [
  { id: 'image-edit',    pattern: /(이미지|사진|그림|png|jpg|jpeg).*(수정|편집|지워|제거|바꿔|배경|inpaint|outpaint|erase|remove\s*background)|인페인팅|아웃페인팅/i, description: '이미지 편집/인페인팅' },
  { id: 'image-analysis',pattern: /이미지.*(분석|설명|내용|뭐|읽어|읽기|어떤)|사진.*(분석|설명|내용|뭐)|스크린샷.*(분석|설명)|이.*(이미지|사진|그림).*?(분석|설명|뭐|어떤|내용)|analyze.*image|describe.*image|what.*in.*image/i, extPattern: /\.(png|jpg|jpeg|webp|gif|bmp)$/i, description: '이미지 분석' },
  { id: 'image-gen',     pattern: /이미지\s*(생성|만들|그려|그리|create|generate)|이미지로\s*(만들|생성|그려|변환)|사진.*만들|일러스트|로고.*디자인|배너.*만들|아이콘.*만들|썸네일/i, description: '이미지 생성' },
  { id: 'video-gen',     pattern: /비디오\s*(생성|만들)|영상.*만들|동영상.*생성|video.*generate/i, description: '비디오 생성' },

  // draw.io / 다이어그램 파일 작업 — diagram-gen보다 먼저 (구체적 키워드 우선)
  { id: 'drawio-work',   pattern: /draw\.?io|dio|drawio.*(편집|수정|만들|생성)|다이어그램.*xml/i, extPattern: /\.(drawio|dio)$/i, description: 'draw.io 다이어그램' },
  // 자유 다이어그램 (mermaid/plantuml/SVG) — 키워드 기반
  { id: 'diagram-gen',   pattern: /다이어그램.*(만들|생성|그려)|mermaid|plantuml|flowchart|시퀀스.*다이어그램|클래스.*다이어그램|아키텍처.*도식|관계도|순서도/i, description: '다이어그램 생성 (mermaid/plantuml)' },

  // 슬라이드 분석 — pptx-gen보다 먼저
  { id: 'pptx-analysis', pattern: /pptx?.*(분석|요약|읽|내용|추출)|슬라이드.*(분석|요약|읽|내용)|발표.*(자료.*분석|요약)/i, extPattern: /\.pptx?$/i, description: 'PPTX 슬라이드 분석' },
  { id: 'pptx-gen',      pattern: /pptx?|피피티|파워포인트|프레젠테이션|슬라이드.*(만들|제작|생성)|발표.*자료|deck.*create/i, extPattern: /\.pptx?$/i, description: 'PPTX 슬라이드 생성' },

  // 엑셀 분석 — xlsx-work보다 먼저
  { id: 'xlsx-analysis', pattern: /xlsx.*(분석|요약|읽|내용|인사이트)|엑셀.*(분석|요약|인사이트|차트.*해석)|스프레드시트.*(분석|요약)/i, extPattern: /\.xlsx?$/i, description: '엑셀 데이터 분석' },
  { id: 'xlsx-work',     pattern: /xlsx|엑셀|스프레드시트|spreadsheet|excel|수식|피벗.*테이블|차트.*만들/i, extPattern: /\.xlsx?$/i, description: '엑셀(XLSX) 작업' },

  // 한글 (.hwp/.hwpx) — docx보다 먼저
  { id: 'hwp-work',      pattern: /hwp|hwpx|한글.?파일|한글.?문서|아래아.?한글/i, extPattern: /\.(hwp|hwpx)$/i, description: '한글(HWP) 문서 작업' },
  // 워드 (.docx)
  { id: 'docx-work',     pattern: /docx|워드.?파일|워드.?문서|word.*document|문서.*편집|document.*edit/i, extPattern: /\.(docx?|odt|rtf)$/i, description: '워드(DOCX) 문서 작업' },

  // PDF 분석 — pdf-gen보다 먼저
  { id: 'pdf-analysis',  pattern: /pdf.*(분석|요약|읽|내용|추출)|논문.*(요약|분석)|보고서.*(분석|요약)/i, extPattern: /\.pdf$/i, description: 'PDF 분석/요약' },
  { id: 'pdf-gen',       pattern: /pdf.*(생성|만들)|리포트.*생성|보고서.*만들|문서.*생성|report.*generate/i, description: 'PDF 리포트 생성' },

  { id: 'code-write',    pattern: /코드.*작성|함수.*만들|클래스.*구현|리팩토링|refactor|implement|구현해|코딩해/i, description: '코드 작성/구현' },
  { id: 'code-review',   pattern: /코드.*리뷰|코드.*비교|버그.*찾|디버그|debug|review.*code|분석해.*코드/i, description: '코드 리뷰/디버깅' },
  { id: 'architecture',  pattern: /아키텍처.*설계|시스템.*디자인|마이크로서비스|분산.*시스템|대규모.*설계|system.*design/i, description: '아키텍처/시스템 설계' },
  { id: 'math-logic',    pattern: /수학.*문제|증명|알고리즘.*설계|최적화.*문제|math|proof|algorithm.*design/i, description: '수학/논리 추론' },
  { id: 'translation',   pattern: /번역해|translate|영어로|한국어로|일본어로|중국어로|번역.*해줘/i, description: '번역' },
  { id: 'embedding',     pattern: /임베딩|embedding|벡터.*검색|벡터.*변환|rag.*인덱싱|semantic.*search/i, description: '임베딩/벡터 검색' },
  { id: 'simple-qa',     pattern: /^.{0,40}(뭐야|뭐지|알려줘|설명해|what is|explain|요약해|summarize)\s*[?.!]?\s*$/i, description: '간단한 질문/요약' },
];

// ─── 버전 정렬: 같은 prefix 내에서 최신 버전 우선 ───────────────────────
function _compareVersions(idA, idB, prefix) {
  const p = (prefix || '').toLowerCase();
  const tailA = (idA || '').toLowerCase().split(p)[1] || '';
  const tailB = (idB || '').toLowerCase().split(p)[1] || '';
  const splitVer = (tail) => {
    const all = (tail.match(/\d+/g) || []).map(Number);
    const minor = []; const date = [];
    for (const n of all) { if (n >= 10000000) date.push(n); else minor.push(n); }
    return { minor, date };
  };
  const va = splitVer(tailA), vb = splitVer(tailB);
  const len = Math.max(va.minor.length, vb.minor.length);
  for (let i = 0; i < len; i++) {
    const a = va.minor[i] || 0, b = vb.minor[i] || 0;
    if (a !== b) return b - a;
  }
  const dlen = Math.max(va.date.length, vb.date.length);
  for (let i = 0; i < dlen; i++) {
    const a = va.date[i] || 0, b = vb.date[i] || 0;
    if (a !== b) return b - a;
  }
  return 0;
}

// ─── 모델 ID에서 사용자 친화 버전 라벨 동적 생성 ───────────────────────
// 카탈로그에 어떤 최신 버전이 추가돼도(opus 4-8, sonnet 4-7 등) 라벨이 자동 반영된다.
// 하드코딩된 후보 라벨(c.name) 대신 실제 매칭된 모델 ID의 버전을 파싱한다.
//   anthropic.claude-opus-4-7-20251015-v1:0   → "Claude Opus 4.7"
//   anthropic.claude-opus-4-8-...             → "Claude Opus 4.8"
//   anthropic.claude-opus-4-20250514-v1:0     → "Claude Opus 4"  (마이너 없음·날짜만)
//   anthropic.claude-3-5-sonnet-20241022-v2:0 → "Claude Sonnet 3.5"
//   anthropic.claude-3-opus-20240229-v1:0     → "Claude Opus 3"
function _refineModelLabel(modelId, fallbackName) {
  const id = String(modelId || '').toLowerCase().replace(/^(us|eu|global)\./, '');
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  // 최신 세대: claude-<family>-<major>(-<minor>)?(-<date>)?
  let m = id.match(/claude-(opus|sonnet|haiku)-(\d+)(?:-(\d+))?/);
  if (m) {
    const family = cap(m[1]);
    const major = m[2];
    const maybeMinor = m[3];
    // 마이너 자리가 날짜(YYYYMMDD, 3자리 이상)면 마이너 없음으로 간주
    let ver = major;
    if (maybeMinor && maybeMinor.length <= 2) ver = `${major}.${maybeMinor}`;
    return `Claude ${family} ${ver}`;
  }
  // 레거시: claude-3-5-sonnet / claude-3-opus
  m = id.match(/claude-(\d+)-(\d+)-(opus|sonnet|haiku)/);
  if (m) return `Claude ${cap(m[3])} ${m[1]}.${m[2]}`;
  m = id.match(/claude-(\d+)-(opus|sonnet|haiku)/);
  if (m) return `Claude ${cap(m[2])} ${m[1]}`;

  return fallbackName;
}

function _findTopModelsForTask(taskId, currentModelId, availableModels, maxCount) {
  const candidates = TASK_MODEL_MAP[taskId];
  if (!candidates || !candidates.length) return [];
  if (!availableModels || !availableModels.length) return [];
  const n = maxCount || 3;
  const results = [];
  const seen = new Set();
  for (const c of candidates) {
    if (results.length >= n) break;
    const pfxLower = c.prefix.toLowerCase();
    const matches = availableModels
      .filter(m => {
        const mid = (m.id || '').toLowerCase();
        return mid.includes(pfxLower) || mid.startsWith(pfxLower);
      })
      .sort((a, b) => _compareVersions(a.id, b.id, c.prefix));
    const match = matches.find(m => m.id !== currentModelId && !seen.has(m.id));
    if (!match) continue;
    seen.add(match.id);
    // 라벨은 실제 매칭 모델 ID 기준으로 최신 버전을 동적 표기 (Claude 계열).
    // Claude가 아니면 후보 정의의 이름(c.name)을 그대로 사용.
    results.push({ ...match, traits: { name: _refineModelLabel(match.id, c.name), desc: c.desc } });
  }
  return results;
}

function _detectTaskFromAttachments(attachments) {
  if (!Array.isArray(attachments) || !attachments.length) return null;
  const hasExt = (re) => attachments.some(f => re.test(f.name || ''));
  if (hasExt(/\.(png|jpg|jpeg|webp|gif|bmp)$/i)) return 'image-analysis';
  if (hasExt(/\.(drawio|dio)$/i)) return 'drawio-work';
  if (hasExt(/\.pdf$/i)) return 'pdf-analysis';
  if (hasExt(/\.pptx?$/i)) return 'pptx-analysis';
  if (hasExt(/\.xlsx?$/i)) return 'xlsx-analysis';
  if (hasExt(/\.(hwp|hwpx)$/i)) return 'hwp-work';
  if (hasExt(/\.(docx?|odt|rtf)$/i)) return 'docx-work';
  return null;
}

function _getCategoryIcon(taskId) {
  // 이모지 사용 안 함 — 빈 문자열 반환 (UI는 텍스트로만 표시)
  return '';
}

/**
 * 메시지 + 첨부파일 분석하여 추천 반환 (단일/병렬/파이프라인 전략 모두 포함).
 */
function getModelRecommendation(text, currentModelId) {
  if (!text || text.length < 3) return null;
  const availableModels = (typeof ALL_MODELS !== 'undefined' && ALL_MODELS.length)
    ? ALL_MODELS : (typeof state !== 'undefined' && state.availableModels) || [];
  if (!availableModels.length) return null;

  // 1. 첨부파일 우선
  const attachments = (typeof state !== 'undefined' && state.attachedFiles) || [];
  const fromAttach = _detectTaskFromAttachments(attachments);
  let matchedTask = null;
  if (fromAttach) {
    matchedTask = TASK_PATTERNS.find(t => t.id === fromAttach);
    for (const t of TASK_PATTERNS) {
      if (t.pattern.test(text) && t.id !== fromAttach) {
        if (t.id.split('-')[0] === fromAttach.split('-')[0]) { matchedTask = t; break; }
      }
    }
  }
  if (!matchedTask) {
    for (const t of TASK_PATTERNS) {
      if (t.pattern.test(text)) { matchedTask = t; break; }
    }
  }
  if (!matchedTask) return null;

  let tops = _findTopModelsForTask(matchedTask.id, currentModelId, availableModels, 3);
  // 이미지 생성/편집 — Vertex(Nano Banana Pro)가 항상 1순위로 사용된다.
  // 카탈로그에 Bedrock 이미지 모델(Stability 등)이 없어도 추천이 사라지지 않도록
  // Vertex 가상 엔트리를 후보 맨 앞에 보장한다(사용자 요구: Vertex 당연·이미지 특화 필수).
  if (matchedTask.id === 'image-gen' || matchedTask.id === 'image-edit') {
    const hasVx = tops.some(m => /vertex|nano-banana/i.test(m.id || ''));
    if (!hasVx) {
      tops = [{
        id: 'vertex.nano-banana-pro',
        traits: { name: 'Vertex Nano Banana Pro', desc: '이미지 특화 — Vertex(Gemini), 한글·텍스트 정확도 최고' },
      }, ...tops];
    }
  }
  if (!tops.length) return null;

  const best = tops[0];
  const strategy = TASK_EXECUTION_STRATEGIES[matchedTask.id] || {};
  const options = {
    single: {
      candidates: tops.map((m, idx) => ({
        id: m.id, name: m.traits.name || m.name, desc: m.traits.desc || '',
        rank: idx + 1, modelId: m.id, modelName: m.traits.name || m.name,
      })),
      reason: `${matchedTask.description} — 한 모델로 처리`,
    },
  };

  // 병렬
  if (strategy.parallel) {
    const allTasks = strategy.parallel.tasks || [matchedTask.id];
    const wanted = strategy.parallel.count || 3;
    const parallelModels = [];
    const seen = new Set();
    for (const t of allTasks) {
      const ms = _findTopModelsForTask(t, '', availableModels, wanted);
      for (const m of ms) {
        if (parallelModels.length >= wanted) break;
        if (seen.has(m.id)) continue;
        seen.add(m.id);
        parallelModels.push({ id: m.id, name: m.traits.name || m.name, desc: m.traits.desc || '' });
      }
      if (parallelModels.length >= wanted) break;
    }
    if (parallelModels.length >= 2) {
      options.parallel = {
        models: parallelModels,
        reason: strategy.parallel.reason || `여러 모델 동시 호출로 결과 비교/병합`,
      };
    }
  }

  // 파이프라인
  if (strategy.pipeline) {
    const stages = [];
    for (const s of strategy.pipeline.stages) {
      let stModel = null;
      const ms = _findTopModelsForTask(s.task, '', availableModels, 1);
      if (ms.length) {
        stModel = { id: ms[0].id, name: ms[0].traits.name || ms[0].name, desc: ms[0].traits.desc || '' };
      }
      if (s.task === 'image-gen') {
        // 이미지 단계는 Vertex(Nano Banana Pro)가 항상 우선 사용되고 Stability가 폴백.
        // 카탈로그에 Bedrock 이미지 모델이 없어도 파이프라인이 누락되지 않도록
        // Vertex 가상 엔트리를 보장한다(사용자 요구: 이미지 특화 모델 필수 추천).
        const stab = stModel ? stModel.name : 'Stable Image';
        stModel = {
          id: (ms.length ? ms[0].id : 'vertex.nano-banana-pro'),
          name: `Vertex Nano Banana Pro + ${stab}`,
          desc: '이미지 특화 — Vertex(Gemini) 우선, Stability 자동 폴백',
        };
      }
      if (!stModel) { stages.length = 0; break; }
      stages.push({ stage: s.stage, label: s.label, task: s.task, model: stModel });
    }
    if (stages.length === strategy.pipeline.stages.length) {
      options.pipeline = {
        stages,
        reason: strategy.pipeline.reason || `단계별 전문 모델로 품질 극대화`,
      };
    }
  }

  // 문서·이미지·비디오 생성 작업 — 파이프라인을 최우선 추천(첫 탭·활성).
  // 사용자 요구: 어떤 모델로 물어보든 ppt/pdf/이미지 슬라이드 제작은 파이프라인 우선,
  // 이미지 특화 모델(Vertex/Stability)이 반드시 단계에 포함되게 한다.
  const GEN_TASKS = new Set([
    'pptx-gen', 'pdf-gen', 'docx-work', 'xlsx-work', 'hwp-work',
    'image-gen', 'video-gen', 'diagram-gen', 'drawio-work', 'image-edit',
  ]);
  const pipelineFirst = GEN_TASKS.has(matchedTask.id) && !!options.pipeline;

  return {
    id: matchedTask.id,
    description: matchedTask.description,
    icon: _getCategoryIcon(matchedTask.id),
    options,
    targetModel: best,
    candidates: options.single.candidates,
    recommend: 'multi-strategy',
    pipelineFirst,
    // 이미지/비디오 생성 작업은 채팅 모델 셀렉터를 교체하면 안 된다(도구 호출 불가).
    // informational=true이면 카드는 "어떤 특화 모델이 자동 선택될지" 안내만 하고,
    // 선택 시 채팅 모델을 유지한 채 정상 진행한다 (generate_image 도구가 자동 라우팅).
    informational: (matchedTask.id === 'image-gen' || matchedTask.id === 'video-gen'),
  };
}

// ─── 추천 카드 UI (탭: 단일 / 병렬 / 파이프라인) ─────────────────────────
function showRecommendationCard(recommendation) {
  return new Promise((resolve) => {
    const container = document.getElementById('chat-messages');
    if (!container) { resolve('dismiss'); return; }
    const card = document.createElement('div');
    card.className = 'model-recommend-card';

    if (recommendation.recommend === 'multi-strategy' && recommendation.options) {
      const opts = recommendation.options;
      const tabs = [];
      const _tabSingle = () => ({ key: 'single', label: '단일' });
      const _tabParallel = () => ({ key: 'parallel', label: `병렬 (${opts.parallel.models.length})` });
      const _tabPipeline = () => ({ key: 'pipeline', label: `파이프라인 (${opts.pipeline.stages.length}단계)${recommendation.pipelineFirst ? ' ★추천' : ''}` });
      if (recommendation.pipelineFirst && opts.pipeline) {
        // 생성 작업 — 파이프라인 최우선(첫 탭·활성)
        tabs.push(_tabPipeline());
        if (opts.parallel) tabs.push(_tabParallel());
        if (opts.single)   tabs.push(_tabSingle());
      } else {
        if (opts.single)    tabs.push(_tabSingle());
        if (opts.parallel)  tabs.push(_tabParallel());
        if (opts.pipeline)  tabs.push(_tabPipeline());
      }

      const tabHtml = tabs.map((t, i) =>
        `<button class="recommend-tab${i === 0 ? ' active' : ''}" data-tab="${t.key}">${t.label}</button>`
      ).join('');

      card.innerHTML = `
        <div class="recommend-header">
          <span class="recommend-title">${esc(recommendation.description || '모델 추천')}</span>
          <span class="recommend-dismiss" title="무시">✕</span>
        </div>
        <div class="recommend-tabs">${tabHtml}</div>
        <div class="recommend-tab-body" data-tab-body></div>
        <div class="recommend-actions">
          <button class="recommend-btn dismiss">현재 모델로 계속</button>
        </div>
      `;
      container.appendChild(card);
      container.scrollTop = container.scrollHeight;

      let resolved = false;
      const cleanup = () => {
        if (resolved) return;
        resolved = true;
        card.classList.add('recommend-fade-out');
        setTimeout(() => card.remove(), 300);
      };

      const body = card.querySelector('[data-tab-body]');
      const isInfo = !!recommendation.informational;
      const renderTab = (key) => {
        body.innerHTML = '';
        if (key === 'single') {
          if (isInfo) {
            // 이미지/비디오 생성 — 채팅 모델을 교체하지 않고 어떤 특화 모델이
            // 자동 선택될지 순위로 보여준 뒤, 현재 채팅 모델로 그대로 생성 진행.
            body.innerHTML = `<div class="recommend-reason">${esc(opts.single.reason)} — 아래 특화 모델이 자동 선택됩니다.</div>` +
              `<div class="recommend-strategy-list">` +
              opts.single.candidates.map(m =>
                `<div class="recommend-strategy-item">
                  <span class="recommend-rank">${m.rank}위</span>
                  <span class="recommend-model-name">${esc(m.name)}</span>
                  <span class="recommend-model-desc">${esc(m.desc)}</span>
                </div>`
              ).join('') + `</div>` +
              `<button class="recommend-btn accept" data-action="proceed-info">이 모델로 생성 진행</button>`;
            const piBtn = body.querySelector('[data-action="proceed-info"]');
            if (piBtn) piBtn.addEventListener('click', () => {
              recommendation.recommend = 'image-proceed';
              cleanup(); resolve('accept');
            });
            return;
          }
          body.innerHTML = `<div class="recommend-reason">${esc(opts.single.reason)}</div>` +
            opts.single.candidates.map(m =>
              `<button class="recommend-btn accept recommend-multi" data-action="select-single" data-model-id="${esc(m.id)}" data-model-name="${esc(m.name)}">
                <span class="recommend-rank">${m.rank}위</span>
                <span class="recommend-model-name">${esc(m.name)}</span>
                <span class="recommend-model-desc">${esc(m.desc)}</span>
              </button>`
            ).join('');
        } else if (key === 'parallel') {
          body.innerHTML = `<div class="recommend-reason">${esc(opts.parallel.reason)}</div>` +
            `<div class="recommend-strategy-list">` +
            opts.parallel.models.map(m =>
              `<div class="recommend-strategy-item">
                <span class="recommend-model-name">${esc(m.name)}</span>
                <span class="recommend-model-desc">${esc(m.desc)}</span>
              </div>`
            ).join('') + `</div>` +
            `<button class="recommend-btn accept" data-action="run-parallel">병렬 호출 실행 (${opts.parallel.models.length}개 동시)</button>`;
        } else if (key === 'pipeline') {
          body.innerHTML = `<div class="recommend-reason">${esc(opts.pipeline.reason)}</div>` +
            `<div class="recommend-strategy-list">` +
            opts.pipeline.stages.map((s, i) =>
              `<div class="recommend-strategy-item">
                <span class="recommend-stage">${i + 1}</span>
                <span class="recommend-stage-label">${esc(s.label)}</span>
                <span class="recommend-model-name">${esc(s.model.name)}</span>
              </div>`
            ).join('') + `</div>` +
            `<button class="recommend-btn accept" data-action="run-pipeline">파이프라인 실행 (${opts.pipeline.stages.length}단계 순차)</button>`;
        }
        body.querySelectorAll('[data-action="select-single"]').forEach(btn => {
          btn.addEventListener('click', () => {
            recommendation.recommend = 'model-switch';
            recommendation.targetModel = { id: btn.dataset.modelId, name: btn.dataset.modelName };
            cleanup(); resolve('accept');
          });
        });
        const pBtn = body.querySelector('[data-action="run-parallel"]');
        if (pBtn) pBtn.addEventListener('click', () => {
          recommendation.recommend = 'parallel-run';
          recommendation.parallelModels = opts.parallel.models;
          cleanup(); resolve('accept');
        });
        const plBtn = body.querySelector('[data-action="run-pipeline"]');
        if (plBtn) plBtn.addEventListener('click', () => {
          recommendation.recommend = 'pipeline-run';
          recommendation.pipelineStages = opts.pipeline.stages;
          cleanup(); resolve('accept');
        });
      };

      renderTab(tabs[0].key);
      card.querySelectorAll('.recommend-tab').forEach(t => {
        t.addEventListener('click', () => {
          card.querySelectorAll('.recommend-tab').forEach(x => x.classList.remove('active'));
          t.classList.add('active');
          renderTab(t.dataset.tab);
        });
      });

      card.querySelector('.recommend-btn.dismiss')?.addEventListener('click', () => { cleanup(); resolve('dismiss'); });
      card.querySelector('.recommend-dismiss')?.addEventListener('click', () => { cleanup(); resolve('dismiss'); });
      return;
    }

    // === Legacy fallback (이전 형식 호환) ===
    let actionBtn = '';
    if (recommendation.recommend === 'multi-model' && recommendation.candidates) {
      actionBtn = recommendation.candidates.map(m =>
        `<button class="recommend-btn accept recommend-multi" data-action="select-model" data-model-id="${esc(m.id)}" data-model-name="${esc(m.name)}">
          <span class="recommend-rank">${m.rank}위</span>
          <span class="recommend-model-name">${esc(m.name)}</span>
          <span class="recommend-model-desc">${esc(m.desc || '')}</span>
        </button>`
      ).join('');
    } else if (recommendation.recommend === 'model-switch' && recommendation.targetModel) {
      const t = recommendation.targetModel;
      actionBtn = `<button class="recommend-btn accept" data-action="model-switch">${esc(t.name || t.traits?.name || t.id)}로 전환</button>`;
    }

    card.innerHTML = `
      <div class="recommend-header">
        <span class="recommend-title">모델 추천</span>
        <span class="recommend-dismiss" title="무시">✕</span>
      </div>
      <div class="recommend-reason">${esc(recommendation.reason || '')}</div>
      <div class="recommend-actions">
        ${actionBtn}
        <button class="recommend-btn dismiss">현재 모델로 계속</button>
      </div>
    `;
    container.appendChild(card);
    container.scrollTop = container.scrollHeight;

    let resolved2 = false;
    const cleanup2 = () => {
      if (resolved2) return;
      resolved2 = true;
      card.classList.add('recommend-fade-out');
      setTimeout(() => card.remove(), 300);
    };

    const acceptBtn = card.querySelector('.recommend-btn.accept:not(.recommend-multi)');
    if (acceptBtn) acceptBtn.addEventListener('click', () => { cleanup2(); resolve('accept'); });
    card.querySelectorAll('[data-action="select-model"]').forEach(btn => {
      btn.addEventListener('click', () => {
        recommendation.recommend = 'model-switch';
        recommendation.targetModel = { id: btn.dataset.modelId, name: btn.dataset.modelName };
        cleanup2(); resolve('accept');
      });
    });
    card.querySelector('.recommend-btn.dismiss')?.addEventListener('click', () => { cleanup2(); resolve('dismiss'); });
    card.querySelector('.recommend-dismiss')?.addEventListener('click', () => { cleanup2(); resolve('dismiss'); });
  });
}

// ─── 추천 적용 (단일/병렬/파이프라인) ────────────────────────────────────
function applyRecommendation(recommendation) {
  if (recommendation.recommend === 'model-switch' && recommendation.targetModel) {
    const m = recommendation.targetModel;
    const target = (typeof ALL_MODELS !== 'undefined' && ALL_MODELS.find(x => x.id === m.id)) || m;
    state.selectedModel = target;
    // 단일 모드로 전환 (병렬 모드였다면)
    state.mode = 'single';
    document.querySelectorAll('.mode-toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === 'single'));
    const sBar = document.getElementById('single-model-bar');
    const pBar = document.getElementById('parallel-model-bar');
    if (sBar) sBar.style.display = 'block';
    if (pBar) pBar.style.display = 'none';
    const btn = document.getElementById('model-dropdown-btn');
    if (btn) btn.textContent = (target.name || target.id) + ' ▾';
    const statusModel = document.getElementById('status-model');
    if (statusModel) statusModel.textContent = target.name || target.id;
    if (typeof addLiveLog === 'function') addLiveLog('system', `모델 전환: ${target.name || target.id}`);
    return { type: 'single' };
  }
  if (recommendation.recommend === 'parallel-run' && Array.isArray(recommendation.parallelModels)) {
    state.mode = 'parallel';
    document.querySelectorAll('.mode-toggle-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === 'parallel'));
    const sBar = document.getElementById('single-model-bar');
    const pBar = document.getElementById('parallel-model-bar');
    if (sBar) sBar.style.display = 'none';
    if (pBar) pBar.style.display = 'block';
    if (Array.isArray(state.parallelSlots)) state.parallelSlots.length = 0;
    if (typeof addParallelSlot === 'function') {
      for (const m of recommendation.parallelModels) {
        try { addParallelSlot(m.id); } catch (_e) {}
      }
    }
    if (typeof addLiveLog === 'function') {
      addLiveLog('system', `병렬 호출 모드: ${recommendation.parallelModels.map(m => m.name).join(', ')}`);
    }
    return { type: 'parallel', models: recommendation.parallelModels };
  }
  if (recommendation.recommend === 'pipeline-run' && Array.isArray(recommendation.pipelineStages)) {
    if (typeof addLiveLog === 'function') {
      addLiveLog('system', `파이프라인 모드: ${recommendation.pipelineStages.map(s => s.label).join(' → ')}`);
    }
    return { type: 'pipeline', stages: recommendation.pipelineStages };
  }
  // legacy 'parallel'
  if (recommendation.recommend === 'parallel') {
    state.mode = 'parallel';
    return { type: 'parallel' };
  }
  return { type: 'none' };
}

// window 노출
if (typeof window !== 'undefined') {
  window.getModelRecommendation = getModelRecommendation;
  window.showRecommendationCard = showRecommendationCard;
  window.applyRecommendation = applyRecommendation;
  window.refineModelLabel = _refineModelLabel;
  window.MODEL_RECOMMEND_API = {
    TASK_MODEL_MAP,
    TASK_PATTERNS,
    TASK_EXECUTION_STRATEGIES,
    detectTaskType: (text) => {
      const t = TASK_PATTERNS.find(p => p.pattern.test(text || ''));
      return t ? t.id : 'general_chat';
    },
  };
}
