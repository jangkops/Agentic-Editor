/* ===== src/lib/utils.js =====
 * 순수 유틸 함수 — 외부 상태(state/monaco/DOM)에 의존하지 않음.
 *
 * 이 파일은 <script src="lib/utils.js"> 태그로 main.js보다 먼저 로드되며,
 * 정의된 함수들은 전역 스코프에 올라가 main.js에서 그대로 호출됩니다.
 *
 * 제공:
 *   - esc(t)          HTML 이스케이프
 *   - fmtNum(n)       숫자 축약 (1.2K / 3.4M)
 *   - fmtElapsed(s)   경과 시간 (초 기준)
 *   - fmtElapsedMs(ms) 경과 시간 (ms 기준, 소수점)
 *   - fmtMd(t)        아주 작은 마크다운 → HTML
 *
 * 이식 원칙: main.js의 원본 구현과 "문자 단위 동일". 동작 변경 금지.
 */

// HTML 이스케이프 — '&', '<', '>' 만 변환 (과거 main.js와 동일)
function esc(t){if(!t)return'';return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// 숫자 축약
function fmtNum(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

// 경과 시간 — 초 단위 입력
function fmtElapsed(secs) {
  if (secs === 0) return '0s';
  if (!secs || secs < 1) return '';
  if (secs >= 3600) return `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;
  if (secs >= 60) return `${Math.floor(secs/60)}m ${secs%60}s`;
  return `${secs}s`;
}

// 경과 시간 — ms 단위 입력, 1초 미만은 소수점 1자리
function fmtElapsedMs(ms) {
  if (ms == null || ms < 0) return '';
  const secs = ms / 1000;
  if (secs < 1) return `${secs.toFixed(1)}s`;
  if (secs < 60) return `${Math.floor(secs)}s`;
  if (secs < 3600) return `${Math.floor(secs/60)}m ${Math.floor(secs%60)}s`;
  return `${Math.floor(secs/3600)}h ${Math.floor((secs%3600)/60)}m`;
}

// 아주 작은 마크다운 → HTML (esc 의존)
function fmtMd(t){
  let h=esc(t);
  // 코드 블록
  h=h.replace(/```(\w*)\n([\s\S]*?)```/g,'<pre style="background:var(--color-bg-primary);padding:8px;border-radius:var(--radius-md);margin:3px 0;font-family:var(--font-mono);font-size:11px;overflow-x:auto;border:1px solid var(--color-border)"><code>$2</code></pre>');
  // 인라인 코드
  h=h.replace(/`([^`]+)`/g,'<code style="background:var(--color-bg-input);padding:1px 4px;border-radius:3px;font-family:var(--font-mono);font-size:11px">$1</code>');
  // 헤딩
  h=h.replace(/^### (.+)$/gm,'<div style="font-size:13px;font-weight:700;color:var(--color-text-primary);margin:10px 0 3px">$1</div>');
  h=h.replace(/^## (.+)$/gm,'<div style="font-size:14px;font-weight:700;color:var(--color-text-primary);margin:12px 0 4px">$1</div>');
  h=h.replace(/^# (.+)$/gm,'<div style="font-size:15px;font-weight:700;color:var(--color-text-primary);margin:14px 0 4px">$1</div>');
  // 볼드/이탤릭
  h=h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  h=h.replace(/\*(.+?)\*/g,'<em>$1</em>');
  // 리스트
  h=h.replace(/^- (.+)$/gm,'<div style="padding-left:12px">· $1</div>');
  h=h.replace(/^\d+\. (.+)$/gm,'<div style="padding-left:12px">$&</div>');
  // 인용
  h=h.replace(/^&gt; (.+)$/gm,'<div style="border-left:2px solid var(--color-accent);padding-left:10px;color:var(--color-text-secondary);margin:2px 0">$1</div>');
  // 구분선
  h=h.replace(/^---$/gm,'<hr style="border:none;border-top:1px solid var(--color-border);margin:4px 0">');
  // 테이블 (간단)
  h=h.replace(/\|(.+)\|/g, (match) => {
    const cells = match.split('|').filter(c => c.trim());
    if (cells.every(c => /^[-:]+$/.test(c.trim()))) return '';
    return '<div style="display:flex;gap:8px;padding:1px 0;font-size:12px">' + cells.map(c => `<span style="flex:1">${c.trim()}</span>`).join('') + '</div>';
  });
  // 연속 빈 줄 → 단일 br, 단일 줄바꿈 → br
  h=h.replace(/\n{3,}/g,'\n');
  h=h.replace(/\n\n/g,'<br>');
  h=h.replace(/\n/g,'<br>');
  h=h.replace(/(<br>){3,}/g,'<br>');
  return h;
}

// 명시적으로 window에도 올려 둠 (sanity check + 향후 모듈화 대비)
if (typeof window !== 'undefined') {
  window.esc = esc;
  window.fmtNum = fmtNum;
  window.fmtElapsed = fmtElapsed;
  window.fmtElapsedMs = fmtElapsedMs;
  window.fmtMd = fmtMd;
}

// ===========================================================================
// apiBase() — renderer-side helper
// ===========================================================================
// Feature: remote-ssh · Task 21.2 · Requirements 5.3, 5.5
//
// Returns the base URL the renderer should use when calling ai_engine:
//   - When a remote session is connected and has a live forwarded port,
//     returns `http://127.0.0.1:<localPort>` (routes through the SSH tunnel).
//   - Otherwise returns the local default `http://localhost:8765`.
//
// The remote state is published into the renderer by the Status_Bar wiring
// (Task 23.1 / 23.3) as `window.__remoteStatus = {state, localPort, ...}`
// whenever the main process sends a `remote:event:state` update. Until that
// global is populated (no remote session active) apiBase() stays on the
// local default — the exact contract Property 10 in design.md formalises.
//
// main.js will replace `fetch('http://localhost:8765/...')` with
// `fetch(\`${apiBase()}/...\`)` in Task 23.1. This helper must therefore be
// dependency-free, synchronous, and safe to call on every fetch.
function apiBase() {
  try {
    const s = (typeof window !== 'undefined' && window.__remoteStatus) || null;
    if (s && s.state === 'connected' && s.localPort) {
      return `http://127.0.0.1:${s.localPort}`;
    }
  } catch (_e) { /* ignore — never let the helper crash a fetch */ }
  return 'http://localhost:8765';
}

// Expose on window so main.js (loaded after lib/utils.js via <script> tag)
// can call it as a plain global, matching the pattern used by esc/fmtNum/etc.
if (typeof window !== 'undefined') {
  window.apiBase = apiBase;
}
