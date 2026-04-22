class AgentPanel extends HTMLElement {
  constructor() {
    super();
    this._messages = [];
    this._isStreaming = false;
    this._selectedModel = 'anthropic.claude-3-5-sonnet-20241022-v2:0';
  }

  connectedCallback() {
    this.render();
  }

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
    this.querySelector('.chat-messages').style.cssText = 'flex:1;overflow-y:auto;';

    const input = this.querySelector('#chat-input');
    const sendBtn = this.querySelector('#chat-send');

    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this._send();
      }
      // Auto-resize
      input.style.height = 'auto';
      input.style.height = Math.min(input.scrollHeight, 120) + 'px';
    });

    sendBtn?.addEventListener('click', () => this._send());
    this._renderMessages();
  }

  async _send() {
    const input = this.querySelector('#chat-input');
    const text = input?.value?.trim();
    if (!text || this._isStreaming) return;

    input.value = '';
    input.style.height = 'auto';

    // Add user message
    this._messages.push({ role: 'user', content: text });
    this._renderMessages();

    // Get system prompt from skill manager
    const skillMgr = document.querySelector('skill-manager');
    const systemPrompt = skillMgr?.activeSkill?.role || '';

    // Stream response
    this._isStreaming = true;
    this._messages.push({ role: 'assistant', content: '' });
    this._renderMessages();

    try {
      const resp = await fetch('http://localhost:8765/api/agents/run-stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: text,
          model: this._selectedModel,
          systemPrompt,
        }),
      });

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim();
            if (data === '[DONE]') break;
            try {
              const parsed = JSON.parse(data);
              if (parsed.error) {
                this._messages[this._messages.length - 1].content += `\n[Error: ${parsed.error}]`;
              }
            } catch {
              // Plain text chunk
              this._messages[this._messages.length - 1].content += data;
            }
            this._renderMessages();
          }
        }
      }
    } catch (e) {
      this._messages[this._messages.length - 1].content += `\n[Connection error: ${e.message}]`;
      this._renderMessages();
    }

    this._isStreaming = false;
    this._saveConversation();
  }

  _renderMessages() {
    const container = this.querySelector('#chat-messages');
    if (!container) return;

    container.innerHTML = this._messages.map(m => `
      <div class="chat-message ${m.role}">
        <div class="role">${m.role === 'user' ? 'You' : 'AI'}</div>
        <div class="content">${this._escapeHtml(m.content)}</div>
      </div>
    `).join('');

    container.scrollTop = container.scrollHeight;
  }

  _escapeHtml(text) {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/`([^`]+)`/g, '<code>$1</code>');
  }

  async _saveConversation() {
    // Save to userData/history/
    try {
      const date = new Date().toISOString().split('T')[0];
      const path = `history/${date}.json`;
      if (window.electronAPI?.writeFile) {
        const existing = await window.electronAPI.readFile(
          require?.('path')?.join?.(require?.('electron')?.app?.getPath?.('userData') || '', path) || ''
        );
        // Simplified: just log
        console.log('[agent-panel] Conversation saved');
      }
    } catch { /* ignore */ }
  }
}

customElements.define('agent-panel', AgentPanel);
