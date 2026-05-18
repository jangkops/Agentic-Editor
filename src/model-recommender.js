/* ===== Smart Model Recommender =====
 * 채팅 메시지 내용을 분석하여 현재 사용 가능한 모델 중 최적 모델을 추천.
 * Bedrock에서 로드된 실제 모델 목록(state.availableModels)을 기반으로 동작.
 *
 * 추천은 채팅 영역에 인라인 카드로 표시되며:
 *   - 사용자가 "전환" 클릭 → 모델 전환 후 실행
 *   - "현재 모델로 계속" 또는 5초 경과 → 원래 모델로 진행
 */

// ─── 모델 특성 분류 (Bedrock 모델 ID 기반) ──────────────────────────────

const MODEL_TRAITS = {
  // === 이미지 생성 전용 ===
  'stability.sd3-5-large': { category: 'image-gen', tier: 1, name: 'Stable Diffusion 3.5' },
  'stability.stable-image-core': { category: 'image-gen', tier: 2, name: 'Stable Image Core' },
  'amazon.titan-image-generator-v2': { category: 'image-gen', tier: 2, name: 'Titan Image v2' },
  'amazon.nova-canvas': { category: 'image-gen', tier: 1, name: 'Nova Canvas' },

  // === 비디오 생성 ===
  'amazon.nova-reel': { category: 'video-gen', tier: 1, name: 'Nova Reel' },
  'luma.ray': { category: 'video-gen', tier: 1, name: 'Luma Ray' },

  // === 추론/분석 최강 (복잡한 구조화, 아키텍처 설계, 긴 문서) ===
  'anthropic.claude-opus-4': { category: 'reasoning', tier: 1, name: 'Claude Opus 4', speed: 'slow' },
  'deepseek.r1': { category: 'reasoning', tier: 1, name: 'DeepSeek R1', speed: 'slow' },
  'qwen.qwen3-235b': { category: 'reasoning', tier: 2, name: 'Qwen3 235B', speed: 'slow' },

  // === 코딩 특화 ===
  'anthropic.claude-sonnet-4': { category: 'coding', tier: 1, name: 'Claude Sonnet 4', speed: 'medium' },
  'qwen.qwen3-coder-480b': { category: 'coding', tier: 1, name: 'Qwen3 Coder 480B', speed: 'slow' },
  'mistral.devstral': { category: 'coding', tier: 2, name: 'Devstral', speed: 'fast' },
  'deepseek.v3': { category: 'coding', tier: 2, name: 'DeepSeek V3', speed: 'medium' },

  // === 빠른 응답 (간단한 질문, 번역, 요약) ===
  'anthropic.claude-haiku-4': { category: 'fast', tier: 1, name: 'Claude Haiku 4.5', speed: 'fast' },
  'amazon.nova-micro': { category: 'fast', tier: 1, name: 'Nova Micro', speed: 'fast' },
  'amazon.nova-lite': { category: 'fast', tier: 2, name: 'Nova Lite', speed: 'fast' },
  'mistral.mistral-large-3': { category: 'general', tier: 2, name: 'Mistral Large 3', speed: 'medium' },

  // === 멀티모달 (이미지 분석 입력) ===
  'mistral.pixtral-large': { category: 'vision', tier: 1, name: 'Pixtral Large', speed: 'medium' },
  'amazon.nova-pro': { category: 'general', tier: 1, name: 'Nova Pro', speed: 'medium' },

  // === 범용 균형 ===
  'meta.llama4-': { category: 'general', tier: 2, name: 'Llama 4', speed: 'medium' },
  'meta.llama3-3-': { category: 'general', tier: 2, name: 'Llama 3.3', speed: 'medium' },
  'moonshot.kimi-k2': { category: 'reasoning', tier: 2, name: 'Kimi K2', speed: 'medium' },
  'writer.palmyra-x': { category: 'general', tier: 2, name: 'Palmyra X5', speed: 'medium' },
};

// ─── 작업 유형 감지 규칙 ─────────────────────────────────────────────────

const TASK_PATTERNS = [
  {
    id: 'image-gen',
    pattern: /이미지\s*(생성|만들|그려|그리|create|generate)|사진.*만들|일러스트|로고.*디자인|배너.*만들|아이콘.*만들|썸네일/i,
    bestCategory: 'image-gen',
    description: '이미지 생성',
    parallelSuggestion: true,
  },
  {
    id: 'video-gen',
    pattern: /비디오\s*(생성|만들)|영상.*만들|동영상.*생성|video.*generate/i,
    bestCategory: 'video-gen',
    description: '비디오 생성',
  },
  {
    id: 'pptx-gen',
    pattern: /pptx|파워포인트|프레젠테이션|슬라이드.*만들|발표.*자료|deck.*create/i,
    bestCategory: 'reasoning',
    description: 'PPTX 생성 (구조화 능력 중요)',
  },
  {
    id: 'pdf-report',
    pattern: /pdf.*생성|pdf.*만들|리포트.*생성|보고서.*만들|문서.*생성|report.*generate/i,
    bestCategory: 'reasoning',
    description: 'PDF/리포트 생성',
  },
  {
    id: 'code-write',
    pattern: /코드.*작성|함수.*만들|클래스.*구현|리팩토링|refactor|implement|구현해|코딩해/i,
    bestCategory: 'coding',
    description: '코드 작성/구현',
  },
  {
    id: 'code-review',
    pattern: /코드.*리뷰|코드.*비교|버그.*찾|디버그|debug|review.*code|분석해.*코드/i,
    bestCategory: 'coding',
    description: '코드 리뷰/디버깅',
    parallelSuggestion: true,
  },
  {
    id: 'architecture',
    pattern: /아키텍처.*설계|시스템.*디자인|마이크로서비스|분산.*시스템|대규모.*설계|system.*design/i,
    bestCategory: 'reasoning',
    description: '아키텍처/시스템 설계',
  },
  {
    id: 'math-logic',
    pattern: /수학.*문제|증명|알고리즘.*설계|최적화.*문제|math|proof|algorithm.*design/i,
    bestCategory: 'reasoning',
    description: '수학/논리 추론',
  },
  {
    id: 'translation',
    pattern: /번역해|translate|영어로|한국어로|일본어로|중국어로|번역.*해줘/i,
    bestCategory: 'fast',
    description: '번역',
  },
  {
    id: 'simple-qa',
    pattern: /^.{0,40}(뭐야|뭐지|알려줘|설명해|what is|explain|요약해|summarize)\s*[?.!]?\s*$/i,
    bestCategory: 'fast',
    description: '간단한 질문/요약',
  },
  {
    id: 'image-analysis',
    pattern: /이미지.*분석|사진.*분석|스크린샷.*분석|이.*이미지|analyze.*image|describe.*image/i,
    bestCategory: 'vision',
    description: '이미지 분석',
  },
  {
    id: 'long-document',
    pattern: /긴.*문서|대량.*텍스트|전체.*분석|100페이지|논문.*요약|book.*summary/i,
    bestCategory: 'reasoning',
    description: '긴 문서 처리',
  },
];

// ─── 추천 엔진 ───────────────────────────────────────────────────────────

/**
 * 현재 모델의 카테고리를 판별
 */
function _getCurrentModelCategory(modelId) {
  if (!modelId) return 'unknown';
  const id = modelId.toLowerCase();
  for (const [prefix, traits] of Object.entries(MODEL_TRAITS)) {
    if (id.includes(prefix) || id.startsWith(prefix)) return traits.category;
  }
  return 'general';
}

/**
 * 사용 가능한 모델 목록에서 특정 카테고리의 최적 모델 찾기
 */
function _findBestModel(category, availableModels) {
  if (!availableModels || !availableModels.length) return null;

  // MODEL_TRAITS에서 해당 카테고리의 모델들을 tier 순으로 정렬
  const candidates = Object.entries(MODEL_TRAITS)
    .filter(([_, t]) => t.category === category)
    .sort((a, b) => a[1].tier - b[1].tier);

  // 사용 가능한 모델 목록에서 매칭
  for (const [prefix, traits] of candidates) {
    const match = availableModels.find(m => {
      const mid = (m.id || '').toLowerCase();
      return mid.includes(prefix) || mid.startsWith(prefix);
    });
    if (match) return { ...match, traits };
  }
  return null;
}

/**
 * 메시지 내용을 분석하여 추천을 반환.
 * @param {string} text - 사용자 메시지
 * @param {string} currentModelId - 현재 선택된 모델 ID
 * @returns {object|null} - 추천 객체 또는 null
 */
function getModelRecommendation(text, currentModelId) {
  if (!text || text.length < 3) return null;
  const availableModels = state.availableModels || [];
  if (!availableModels.length) return null;

  const currentCategory = _getCurrentModelCategory(currentModelId);

  for (const task of TASK_PATTERNS) {
    if (!task.pattern.test(text)) continue;

    // 이미 최적 카테고리의 모델을 사용 중이면 스킵
    if (currentCategory === task.bestCategory) continue;

    // 병렬 모드 추천
    if (task.parallelSuggestion && state.mode !== 'parallel') {
      // 병렬 추천 + 모델 전환 동시 제안
      const bestModel = _findBestModel(task.bestCategory, availableModels);
      return {
        id: task.id,
        recommend: 'parallel',
        reason: `${task.description} — 여러 모델의 결과를 비교하면 더 정확합니다.`,
        bestModel: bestModel,
        icon: '⚡',
      };
    }

    // 모델 전환 추천
    const bestModel = _findBestModel(task.bestCategory, availableModels);
    if (!bestModel) continue;

    // 현재 모델과 같으면 스킵
    if (bestModel.id === currentModelId) continue;

    // 빠른 모델 추천: 현재 느린 모델 사용 중일 때만
    if (task.bestCategory === 'fast') {
      const currentTraits = Object.entries(MODEL_TRAITS).find(([p]) =>
        (currentModelId || '').toLowerCase().includes(p)
      );
      if (currentTraits && currentTraits[1].speed === 'fast') continue;
    }

    return {
      id: task.id,
      recommend: 'model-switch',
      targetModel: bestModel,
      reason: `${task.description} — ${bestModel.traits.name}이(가) 이 작업에 더 적합합니다.`,
      icon: _getCategoryIcon(task.bestCategory),
    };
  }
  return null;
}

function _getCategoryIcon(category) {
  const icons = {
    'image-gen': '🎨',
    'video-gen': '🎬',
    'reasoning': '🧠',
    'coding': '💻',
    'fast': '⚡',
    'vision': '👁️',
    'general': '💡',
  };
  return icons[category] || '💡';
}

/**
 * 추천 카드를 채팅 영역에 표시하고 사용자 응답을 기다림.
 * @param {object} recommendation - getModelRecommendation 결과
 * @returns {Promise<'accept'|'dismiss'>}
 */
function showRecommendationCard(recommendation) {
  return new Promise((resolve) => {
    const container = document.getElementById('chat-messages');
    if (!container) { resolve('dismiss'); return; }

    const card = document.createElement('div');
    card.className = 'model-recommend-card';

    let actionBtn = '';
    if (recommendation.recommend === 'model-switch' && recommendation.targetModel) {
      actionBtn = `<button class="recommend-btn accept">✓ ${esc(recommendation.targetModel.name || recommendation.targetModel.traits.name)}로 전환</button>`;
    } else if (recommendation.recommend === 'parallel') {
      actionBtn = `<button class="recommend-btn accept">⚡ 병렬 호출로 실행</button>`;
    }

    card.innerHTML = `
      <div class="recommend-header">
        <span class="recommend-icon">${recommendation.icon || '💡'}</span>
        <span class="recommend-title">모델 추천</span>
        <span class="recommend-dismiss" title="무시">✕</span>
      </div>
      <div class="recommend-reason">${esc(recommendation.reason)}</div>
      <div class="recommend-actions">
        ${actionBtn}
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

    card.querySelector('.recommend-btn.accept').addEventListener('click', () => { cleanup(); resolve('accept'); });
    card.querySelector('.recommend-btn.dismiss').addEventListener('click', () => { cleanup(); resolve('dismiss'); });
    card.querySelector('.recommend-dismiss').addEventListener('click', () => { cleanup(); resolve('dismiss'); });

    // 5초 후 자동 dismiss
    setTimeout(() => { if (!resolved) { cleanup(); resolve('dismiss'); } }, 5000);
  });
}

/**
 * 추천을 적용 (모델 전환 또는 병렬 모드 전환)
 */
function applyRecommendation(recommendation) {
  if (recommendation.recommend === 'model-switch' && recommendation.targetModel) {
    const match = recommendation.targetModel;
    state.selectedModel = match;
    const btn = document.getElementById('model-dropdown-btn');
    if (btn) btn.textContent = (match.name || match.id) + ' ▾';
    const statusModel = document.getElementById('status-model');
    if (statusModel) statusModel.textContent = match.name || match.id;
  } else if (recommendation.recommend === 'parallel') {
    state.mode = 'parallel';
    const modeBtn = document.getElementById('mode-toggle-btn');
    if (modeBtn) {
      modeBtn.textContent = '병렬';
      modeBtn.classList.add('active');
    }
  }
}

// window에 노출
if (typeof window !== 'undefined') {
  window.getModelRecommendation = getModelRecommendation;
  window.showRecommendationCard = showRecommendationCard;
  window.applyRecommendation = applyRecommendation;
}
