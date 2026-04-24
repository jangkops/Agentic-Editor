/**
 * <agent-panel> — AI 채팅 패널
 *
 * 개선 내역 (v2):
 *  1. SSE `[DONE]` 처리 버그 수정 (inner for → 바깥 while까지 정확히 종료)
 *  2. `parsed.text` 스트리밍 텍스트 반영 (이전엔 error만 체크하고 text를 버렸음)
 *  3. projectPath / openFile / openFileContent / chatHistory / sessionId / awsProfile 전달
 *     → src/main.js 의 window.state / window.monacoEditor 재사용
 *  4. AbortController 로 중단(Stop) 지원, 버튼 ▶ ↔ ■ 토글
 *  5. 깨진 _saveConversation 제거 (Electron IPC 는 preload 에 채널 없음)
 *  6. 기본 모델 ID를 서버 기본값(claude-sonnet-4-6)과 맞춤 + state.selectedModel 우선
 *  7. 스트리밍 중 입력/전송 잠금, 에러 표시 개선
 */
class AgentPanel extends HTMLElement {
  constructor() {
    super();
    this._messages = [];
    this._isStreaming = false;
    this._abortCtrl = null;
    this._defaultModel = 'claude-sonnet-4-6';
  }

  connectedCallback() {
    this.render();
  }

  // ─────────────────────────────────────────────────────────────
  // 렌더
  // ─────────────────────────────────────────────────────────────
  render() {
    this.innerHTML = `
      <div class="agent-chat">
        <div class="chat-messages" id="chat-messages"></div>
        <div class="chat-input-container">
          <div class="chat-input-wrapper">
            <textarea class="chat-input" id="chat-input" placeholder="Ask anything..." rows="1"></textarea>
            <button class="chat-send-btn" id="chat-send" title="Send (Enter)">▶</button>
          </div>
        </div>
      </div>
    `;

    this.style.cssText = 'display:flex;flex-direction:column;flex:1;overflow:hidden;';
    this.querySelector('.agent-chat').style.cssText = 'display:flex;flex-direction:column;flex:1;overflow:hidden;';
    this.querySelector('.chat-messages').style.cssText = 'flex:1;overflow-y:auto;padding:8px;';

    const input = this.querySelector('#chat-input');
    const sendBtn = this.querySelector('#chat-send');

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._onSendClick();
        return;
      }
    });
    input?.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });

    sendBtn?.addEventListener('click', () => this._onSendClick());
    this._renderMessages();
  }

  // ─────────────────────────────────────────────────────────────
  // 전송 / 중단 토글
  // ─────────────────────────────────────────────────────────────
  _onSendClick() {
    if (this._isStreaming) {
      // Stop
      try { this._abortCtrl?.abort(); } catch {}
      return;
    }
    this._send();
  }

  _setStreaming(on) {
    this._isStreaming = on;
    const btn = this.querySelector('#chat-send');
    const input = this.querySelector('#chat-input');
    if (btn) {
      btn.textContent = on ? '■' : '▶';
      btn.title = on ? 'Stop' : 'Send (Enter)';
    }
    if (input) input.disabled = false; // 입력은 항상 허용 (다음 메시지 준비)
  }

  // ─────────────────────────────────────────────────────────────
  // 컨텍스트 수집 — src/main.js 의 _apiBody() 와 동일 규약
  // ─────────────────────────────────────────────────────────────
  _buildRequestBody(prompt, model, systemPrompt) {
    const st = window.state || {};
    const settings = st.settings || {};
    const body = {
      prompt,
      model,
      systemPrompt,
      awsProfile: settings.awsProfile || 'bedrock-gw',
      bedrockUser: settings.bedrockUser || '',
    };

    // 프로젝트 경로
    if (st.folderPath) body.projectPath = st.folderPath;

    // 현재 열린 파일
    try {
      if (st.activeTab && window.monacoEditor) {
        body.openFile = st.folderPath
          ? st.activeTab.replace(st.folderPath + '/', '')
          : st.activeTab;
        const mdl = window.monacoEditor.getModel();
        if (mdl) body.openFileContent = mdl.getValue().substring(0, 15000);
      }
    } catch {}

    // 최근 대화 히스토리 (마지막 빈 assistant placeholder 제외)
    const history = this._messages
      .filter((m, i) => {
        if (i === this._messages.length - 1 && m.role === 'assistant' && !m.content) return false;
        return m.role === 'user' || (m.role === 'assistant' && m.content && !m.content.includes('[오류:') && !m.content.includes('[Error:'));
      })
      .slice(-10)
      .map(m => ({ role: m.role, content: (m.content || '').substring(0, 2000) }));
    if (history.length) body.chatHistory = history;

    // 세션 ID (전역 chatSessions 있으면 재사용, 아니면 agent-panel 자체 세션)
    try {
      if (Array.isArray(window.chatSessions) && typeof window.activeSessionIdx === 'number') {
        body.sessionId = window.chatSessions[window.activeSessionIdx]?.id || 'agent-panel';
      } else {
        body.sessionId = 'agent-panel';
      }
    } catch { body.sessionId = 'agent-panel'; }

    return body;
  }

  async _send() {
    const input = this.querySelector('#chat-input');
    const text = input?.value?.trim();
    if (!text || this._isStreaming) return;

    input.value = '';
    input.style.height = 'auto';

    // User 메시지 + assistant placeholder
    this._messages.push({ role: 'user', content: text });
    this._messages.push({ role: 'assistant', content: '' });
    this._renderMessages();

    // 스킬(시스템 프롬프트)
    const skillMgr = document.querySelector('skill-manager');
    const systemPrompt = skillMgr?.activeSkill?.role || '';

    // 모델 ID — 전역 state.selectedModel 우선
    const model =
      (window.state?.selectedModel?.id) ||
      this._selectedModel ||
      this._defaultModel;

    // 중단 컨트롤러
    this._abortCtrl = new AbortController();
    this._setStreaming(true);

    const assistantMsg = this._messages[this._messages.length - 1];

    try {
      const body = this._buildRequestBody(text, model, systemPrompt);
      const resp = await fetch('http://localhost:8765/api/agents/run-stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: this._abortCtrl.signal,
      });

      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
      }

      await this._readSSEStream(resp, assistantMsg);
    } catch (e) {
      if (e.name === 'AbortError') {
        assistantMsg.content += '\n\n[중단됨]';
      } else {
        assistantMsg.content += `\n\n[연결 오류: ${e.message}]`;
      }
      this._renderMessages();
    } finally {
      this._abortCtrl = null;
      this._setStreaming(false);
    }
  }

  // ─────────────────────────────────────────────────────────────
  // SSE 스트림 파서 — src/main.js:readSSEStream 규약 준수
  //   서버 포맷: `data: {"text": "..."}\n\n`, 종료 `data: [DONE]\n\n`
  // ─────────────────────────────────────────────────────────────
  async _readSSEStream(resp, assistantMsg) {
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let done = false;

    while (!done) {
      const { done: rdone, value } = await reader.read();
      if (rdone) break;
      buf += dec.decode(value, { stream: true });

      // SSE 이벤트 구분자: \n\n
      const events = buf.split('\n\n');
      buf = events.pop() || '';

      for (const event of events) {
        const trimmed = event.trim();
        if (!trimmed || !trimmed.startsWith('data: ')) continue;

        const d = trimmed.slice(6);
        if (d === '[DONE]') { done = true; break; }

        try {
          const parsed = JSON.parse(d);
          if (parsed.error) {
            assistantMsg.content += `\n[오류: ${parsed.error}]`;
          } else if (typeof parsed.text === 'string') {
            assistantMsg.content += parsed.text;
          }
          // 기타 필드(type, phase 등)는 무시
        } catch {
          // JSON이 아니면 원문 텍스트로 간주
          assistantMsg.content += d;
        }
        this._renderMessages();
      }
    }
  }

  // ─────────────────────────────────────────────────────────────
  // 렌더링
  // ─────────────────────────────────────────────────────────────
  _renderMessages() {
    const container = this.querySelector('#chat-messages');
    if (!container) return;

    container.innerHTML = this._messages.map(m => `
      <div class="chat-message ${m.role}" style="margin-bottom:10px;">
        <div class="role" style="font-weight:600;font-size:11px;opacity:0.7;margin-bottom:2px;">
          ${m.role === 'user' ? 'You' : 'AI'}
        </div>
        <div class="content" style="white-space:pre-wrap;word-break:break-word;">${this._escapeHtml(m.content || '')}</div>
      </div>
    `).join('');

    container.scrollTop = container.scrollHeight;
  }

  _escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }

  // ─────────────────────────────────────────────────────────────
  // 외부 API
  // ─────────────────────────────────────────────────────────────
  clearMessages() {
    this._messages = [];
    this._renderMessages();
  }

  getMessages() {
    return [...this._messages];
  }
}

customElements.define('agent-panel', AgentPanel);
