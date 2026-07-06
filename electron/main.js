/**
 * Electron Main Process Entry Point
 * 책임: 앱 초기화, 윈도우 생성, IPC 라우팅
 *
 * 기존 3,883줄 main.js를 모듈화한 버전
 * 각 IPC 카테고리를 별도 파일로 분리
 */

const { app, ipcMain } = require('electron');
const path = require('path');

// Core managers
const { ProcessManager } = require('./core/process-manager');
const { DataStore } = require('./core/data-store');
const { AwsSsoManager, resolveDefaultSsoPreset } = require('./core/aws-sso-manager');

// Window management
const { WindowManager } = require('./src/window-manager');

// IPC handlers (modularized)
const { registerFsHandlers } = require('./src/ipc-fs-handlers');
const { registerStoreHandlers } = require('./src/ipc-store-handlers');
const { registerSsoHandlers } = require('./src/ipc-sso-handlers');
const { registerTerminalHandlers } = require('./src/ipc-terminal-handlers');
const { registerProjectHandlers } = require('./src/ipc-project-handlers');
const { registerGitHandlers } = require('./src/ipc-git-handlers');
const { registerRemoteHandlers } = require('./src/ipc-remote-handlers');
// Slides — HTML → PNG via headless BrowserWindow (Genspark/Gamma-class output).
// Loaded here so the bridge-server (which exposes it over HTTP for ai_engine)
// can require the same module without circular deps.
const { registerSlidesHandlers, renderHtmlToPng } = require('./src/ipc-slides-handler');
// Templates — `template:*` channels proxied to the FastAPI backend
// (/api/templates ...). See .kiro/specs/pptx-template-styling/tasks.md §13.4.
const { registerTemplateHandlers } = require('./src/ipc-template-handlers');

// Remote SSH — session manager & router
// The RemoteSessionManager owns the set of live SSH sessions and the
// "active" routing target; the session-router is the single surface
// that downstream IPC handlers (fs/terminal/git/project) and the
// apiBase() helper use to decide local-vs-remote dispatch. Wiring the
// two together here in main.js is the bootstrap contract required by
// spec Task 23.2 (see .kiro/specs/remote-ssh/tasks.md §23.2).
const { RemoteSessionManager } = require('./src/remote/remote-session-manager');
const sessionRouter = require('./src/remote/session-router');

// ========================================
// Initialization
// ========================================

const windowManager = new WindowManager();
const processManager = new ProcessManager();
const dataStore = new DataStore();
const ssoManager = new AwsSsoManager();

// Remote SSH session manager — instantiated at whenReady() so we can
// load persisted host preferences from `userData/settings/remote-hosts.json`
// before the first connect. Kept at module scope so tests and other
// bootstrap hooks can reach it (see `module.exports` at the bottom).
/** @type {import('./src/remote/remote-session-manager').RemoteSessionManager|null} */
let remoteSessionManager = null;

// ========================================
// App Lifecycle
// ========================================

/**
 * 앱 준비 완료 시
 */
app.whenReady().then(() => {
  // 윈도우 생성
  windowManager.createWindow();

  // Remote SSH 부트스트랩 (Task 23.2) — 반드시 IPC 등록 전에 완료해야
  // `registerRemoteHandlers` 및 렌더러의 첫 `remote:status` 호출이
  // 완성된 매니저를 통해 라우팅된다.
  bootstrapRemoteSessionManager();

  // Bridge server: enables AI agent tools to operate on remote files via SSH.
  // Must start AFTER sessionRouter is wired so bridge can route requests.
  const { startBridgeServer } = require('./src/remote/bridge-server');
  startBridgeServer({ sessionRouter, logger: console, renderHtmlToPng }).then((bridge) => {
    processManager.setBridgeEnv(bridge.url, bridge.token);
    // Write a discovery file so an externally-started ai_engine (e.g.
    // `npm run dev:python` started before Electron) can find the bridge.
    // ai_engine polls this file lazily on each request.
    try {
      const fs = require('fs');
      const os = require('os');
      const path = require('path');
      const discoveryPath = path.join(os.tmpdir(), 'ae-bridge.json');
      fs.writeFileSync(discoveryPath, JSON.stringify({
        url: bridge.url,
        token: bridge.token,
        pid: process.pid,
        ts: Date.now(),
      }), { mode: 0o600 });
      console.log(`[bridge] discovery file written: ${discoveryPath}`);
      // Clean up on quit
      app.on('before-quit', () => {
        try { fs.unlinkSync(discoveryPath); } catch {}
      });
    } catch (e) {
      console.warn('[bridge] failed to write discovery file:', e && e.message);
    }
    console.log(`[bridge] ready at ${bridge.url}`);
  }).catch((err) => {
    console.error('[bridge] failed to start:', err && err.message);
  });

  // Python 백엔드 시작 (개발 모드 확인)
  const isDev =
    process.argv.includes('--dev') ||
    process.env.NODE_ENV === 'development' ||
    process.env.npm_lifecycle_event === 'dev:electron';

  if (!isDev) {
    // 포트 확인: 이미 실행 중인지 확인
    const http = require('http');
    const checkReq = http.request(
      {
        host: '127.0.0.1',
        port: 8765,
        method: 'HEAD',
        path: '/health',
        timeout: 2000,
      },
      (res) => {
        console.log('[ProcessManager] Python backend already running, skipping start');
      }
    );

    checkReq.on('error', () => {
      console.log('[ProcessManager] Starting Python backend...');
      processManager.startPython();
    });

    checkReq.end();
  } else {
    console.log('[ProcessManager] Dev mode — skipping Python start (dev:python handles it)');
  }
});

/**
 * 모든 윈도우 닫혔을 때
 */
app.on('window-all-closed', () => {
  processManager.stopAll();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

/**
 * 앱 종료 전
 */
app.on('before-quit', () => {
  // Tear down every remote session so ssh sockets are closed cleanly
  // before Node exits. Swallowed errors — we MUST NOT block quit.
  if (remoteSessionManager) {
    try { remoteSessionManager.shutdown(); } catch (_e) { /* ignore */ }
  }
  processManager.stopAll();
});

/**
 * 앱 활성화 시 (macOS)
 */
app.on('activate', () => {
  if (windowManager.allWindowsClosed()) {
    windowManager.createWindow();
  }
});

// ========================================
// Remote SSH Bootstrap (Task 23.2)
// ========================================

/**
 * Instantiate the RemoteSessionManager, load persisted host preferences,
 * wire its lifecycle to the ProcessManager (so remote sessions stop the
 * local Python backend, and losing the remote session restarts it), and
 * forward manager events to the renderer.
 *
 * This is the single authoritative wire-up point per security.md
 * (IPC handlers and lifecycle hooks live in electron/main.js only).
 * `ipc-remote-handlers.js` still owns the actual `remote:*` channel
 * handlers (Task 22.1 wired them there); this function consolidates
 * the *manager* lifecycle concerns that cross-cut those channels.
 *
 * Requirements: 5.4, 5.5, 9.5
 *
 * @returns {void}
 */
function bootstrapRemoteSessionManager() {
  if (remoteSessionManager) return;

  // Load persisted hosts (favorite, lastWorkspace, ad-hoc entries).
  // `loadRemoteHosts` returns `{schemaVersion, hosts}`; we forward the
  // inner `hosts` map to the manager as its initial preference set.
  let hostsPrefs = {};
  try {
    const stored = typeof dataStore.loadRemoteHosts === 'function'
      ? dataStore.loadRemoteHosts()
      : { hosts: {} };
    hostsPrefs = (stored && typeof stored.hosts === 'object' && stored.hosts) || {};
  } catch (err) {
    console.warn('[Remote] loadRemoteHosts failed:', err && err.message);
    hostsPrefs = {};
  }

  remoteSessionManager = new RemoteSessionManager();
  // Attach preferences as a plain field — Task 29.1 will replace this
  // with a dedicated HostsStore API; for now every consumer reads
  // `remoteSessionManager.hostsPrefs` or talks to dataStore directly.
  remoteSessionManager.hostsPrefs = hostsPrefs;

  // Wire the router → read-through to the manager. From this point on,
  // sessionRouter.getActive() / apiBase() / dispatch() reflect the
  // manager's switchActive(...) calls automatically.
  try { sessionRouter.setManager(remoteSessionManager); } catch (_e) { /* ignore */ }

  // --- ProcessManager lifecycle integration (Req 5.4, 5.5) -----------------
  //
  // When the ACTIVE session becomes connected the local Python backend
  // must stop so every `localhost:8765` fetch is served by the forwarded
  // remote port instead (Req 5.4: "WHILE connected, Local_Editor SHALL
  // NOT launch or communicate with a local ai_engine/server.py").
  //
  // When the active session transitions away from `connected` to a
  // non-recovering terminal state, we restart the local backend so the
  // editor returns to local routing (Req 5.5). We explicitly skip the
  // restart while reconnecting — the session is expected to come back,
  // and the request queue will replay queued calls once it does.
  //
  // We listen on `state-change` (the manager proxies session `state`
  // events under this name in the spec; internally the manager emits
  // `state` too — we subscribe to both to stay robust to the exact
  // event name). ProcessManager.start/stopPython are idempotent, so
  // overlapping signals from the legacy `ipc-remote-handlers.js`
  // inline hooks cannot cause duplicate spawns or double-kills.
  const onSessionState = (evt) => {
    if (!evt || typeof evt !== 'object') return;
    const { alias, from, to } = evt;
    // Only the active session's state drives the local Python
    // lifecycle. A background (non-active) session going connected
    // MUST NOT stop the local backend because the user is still
    // routing traffic locally.
    const activeAlias = remoteSessionManager.getActiveAlias();
    if (alias !== activeAlias) return;

    if (to === 'connected') {
      try { processManager.stopPython(); }
      catch (err) { console.warn('[Remote] stopPython failed on connect:', err && err.message); }
      return;
    }

    if (from === 'connected' && (to === 'disconnected' || to === 'failed' || to === 'reconnecting')) {
      if (to === 'reconnecting') return; // wait for the reconnect loop
      try { processManager.startPython(); }
      catch (err) { console.warn('[Remote] startPython failed on session loss:', err && err.message); }
    }
  };
  remoteSessionManager.on('state', onSessionState);
  remoteSessionManager.on('state-change', onSessionState);

  // Active-session change also restarts local Python when the user
  // switches back to "no remote" (switchActive(null)) — keeps Req 5.5
  // holding even when the previous session is still alive but idle.
  remoteSessionManager.on('active-changed', (evt) => {
    if (!evt) return;
    if (evt.current === null) {
      try { processManager.startPython(); }
      catch (err) { console.warn('[Remote] startPython on switch-to-local failed:', err && err.message); }
    } else {
      // Switched TO a session. If it is already connected, stop local
      // Python; otherwise the `state` listener above will handle it
      // when the session reaches `connected`.
      const active = remoteSessionManager.getActive();
      if (active && active.state === 'connected') {
        try { processManager.stopPython(); }
        catch (err) { console.warn('[Remote] stopPython on switch failed:', err && err.message); }
      }
    }
  });

  // --- Event forwarding to the renderer (preload contract) ----------------
  //
  // The renderer subscribes via `electronAPI.onRemoteState`,
  // `onRemoteAuthRequest`, `onRemoteHostKeyPrompt` (see electron/preload.js).
  // We forward the manager's aggregated events so renderer code does not
  // need to know about per-session EventEmitters.
  //
  // NOTE: `ipc-remote-handlers.js` also forwards these events on the
  // lazily-created internal manager it owns (today that internal
  // manager is separate from this one — see the `manager = new ...`
  // block in that file). The duplicate-emit is harmless: the renderer
  // `remote:event:*` channels are idempotent (state snapshots, not
  // deltas) and the preload cleanup API lets callers deregister at
  // will. Task 22.x (full consolidation) will remove the inner manager
  // so that this file is the sole emitter.
  const fwd = (channel) => (evt) => {
    const win = windowManager.getMainWindow();
    try {
      if (win && !win.isDestroyed()) win.webContents.send(channel, evt);
    } catch (_e) { /* renderer gone — ignore */ }
  };
  remoteSessionManager.on('state', fwd('remote:event:state'));
  remoteSessionManager.on('state-change', fwd('remote:event:state'));
  remoteSessionManager.on('auth-prompt', fwd('remote:event:auth-request'));
  remoteSessionManager.on('auth-request', fwd('remote:event:auth-request'));
  remoteSessionManager.on('host-key-prompt', fwd('remote:event:host-key-prompt'));
  // `fs-change` events are per-session and forwarded by RemoteFileBridge
  // directly in ipc-remote-handlers.js; nothing to do here.

  console.log('[Remote] RemoteSessionManager bootstrapped (hosts=%d)', Object.keys(hostsPrefs).length);
}

// ========================================
// IPC Handlers Registration
// ========================================

/**
 * 모든 IPC 핸들러 등록
 */
function registerAllIpcHandlers() {
  const mainWindow = windowManager.getMainWindow();

  // File System
  registerFsHandlers(mainWindow);

  // Data Store (Settings, History, etc.)
  registerStoreHandlers(dataStore);

  // AWS SSO
  registerSsoHandlers(ssoManager);

  // AWS SSO 온보딩 — ~/.aws/config에 SSO 프로파일 블록을 기록 (spec app-deployment-readiness §6.1).
  // security.md("All handlers registered in electron/main.js only")를 엄격히 준수하기 위해
  // 이 핸들러는 별도 모듈이 아닌 main.js에서 직접 등록한다. 실제 config 파일 쓰기 로직과
  // secret-free 블록 생성(buildSsoProfileBlock)은 AwsSsoManager.writeSsoProfile에 위치한다.
  // 반환 계약: 성공 {success:true, profile}, 중복 {success:false, duplicate:true, error},
  //            권한 오류 {success:false, error, manualHint:<수동 붙여넣기용 ini 블록>}.
  ipcMain.handle('aws:write-sso-profile', (_event, input) => {
    try {
      return ssoManager.writeSsoProfile(input || {});
    } catch (error) {
      console.error('[aws:write-sso-profile] Error:', error && error.message);
      return { success: false, error: (error && error.message) || String(error) };
    }
  });

  // AWS SSO zero-config 온보딩 — 무입력 자동 프로파일 생성.
  // 최종 사용자가 아무 값도 입력하지 않고 "로그인" 버튼만 눌러 사용할 수 있도록,
  // 확정된 조직 기본 SSO 프리셋(resolveDefaultSsoPreset)으로 ~/.aws/config에 프로파일을
  // 자동 생성한다. security.md("All handlers registered in electron/main.js only") 준수를 위해
  // 이 핸들러도 main.js에서 직접 등록한다. 실제 config 쓰기와 secret-free 블록 생성은
  // AwsSsoManager.writeSsoProfile에 위치(자격증명 절대 미기록 — 이미 secret-free).
  // 반환 계약:
  //   신규 생성 → { success:true, profile, created:true }
  //   이미 존재 → { success:true, profile, created:false }
  //   실패      → { success:false, profile, error, manualHint? }
  ipcMain.handle('aws:ensure-default-sso-profile', (_event) => {
    try {
      const preset = resolveDefaultSsoPreset(process.env);
      // 대상 프로파일이 이미 존재하면 그대로 성공 반환(재생성하지 않음).
      let existing = [];
      try { existing = ssoManager.listProfiles(); } catch (_) { existing = []; }
      if (Array.isArray(existing) && existing.includes(preset.name)) {
        return { success: true, profile: preset.name, created: false };
      }
      // 없으면 secret-free 프로파일 블록을 생성한다.
      const r = ssoManager.writeSsoProfile(preset);
      if (r && r.success) {
        return { success: true, profile: r.profile || preset.name, created: true };
      }
      // writeSsoProfile이 중복으로 판단한 경우(레이스 등)도 성공으로 취급.
      if (r && r.duplicate) {
        return { success: true, profile: r.profile || preset.name, created: false };
      }
      return {
        success: false,
        profile: preset.name,
        error: (r && r.error) || '기본 SSO 프로파일 생성에 실패했습니다',
        ...(r && r.manualHint ? { manualHint: r.manualHint } : {}),
      };
    } catch (error) {
      console.error('[aws:ensure-default-sso-profile] Error:', error && error.message);
      return { success: false, error: (error && error.message) || String(error) };
    }
  });

  // Terminal
  registerTerminalHandlers(processManager);

  // Project Analysis
  registerProjectHandlers();

  // Git
  registerGitHandlers();

  // Remote SSH — all `remote:*` channel handlers are registered here
  // (per security.md "All handlers registered in electron/main.js only").
  // The actual ipcMain.handle(...) calls live in ipc-remote-handlers.js
  // because that file already implements the 11 channels listed in
  // tasks.md §23.2 (list-hosts, connect, disconnect, switch-active,
  // status, respond-auth, set-workspace, clear-credentials, show-log,
  // add-ad-hoc-host, set-favorite). Registering them from this
  // bootstrap chain keeps main.js as the single root of the IPC
  // surface — no other file calls registerRemoteHandlers().
  registerRemoteHandlers({
    mainWindow,
    dataStore,
    processManager,
    sessionManager: remoteSessionManager,
    localAiEngineRoot: path.join(__dirname, '..', 'ai_engine'),
  });

  // Slides — HTML → PNG capture via hidden BrowserWindow.
  // Used by ai_engine's _force_generate_from_text to produce Genspark/Gamma
  // -class slide backgrounds for PPTX/PDF embedding. Registered AFTER the
  // remote handlers so the bridge-server (which depends on this) can serve
  // /bridge/render-html-to-png as soon as the IPC handler is live.
  registerSlidesHandlers(mainWindow);

  // Templates — `template:*` channels proxied to FastAPI (/api/templates ...).
  // Registered here (main.js only) per security.md; the backend resolves the
  // userData store root from AE_GENERATED_ROOT and the handlers use the
  // AE_ENGINE_URL-based FastAPI base. See .kiro/specs/pptx-template-styling
  // tasks.md §13.4 (requirements 8.1, 8.8).
  registerTemplateHandlers(mainWindow);

  console.log('[IPC] All handlers registered');
}

// IPC 핸들러 등록 (앱 준비 후)
app.whenReady().then(() => {
  registerAllIpcHandlers();
});

// ========================================
// Error Handling
// ========================================

/**
 * 처리되지 않은 예외
 */
process.on('uncaughtException', (error) => {
  console.error('[UNCAUGHT EXCEPTION]', error);
  // 로그 저장 또는 에러 리포팅 로직 추가 가능
});

/**
 * 처리되지 않은 Promise 거부
 */
process.on('unhandledRejection', (reason, promise) => {
  console.error('[UNHANDLED REJECTION]', reason, promise);
});

// ========================================
// Exports (테스트용)
// ========================================

module.exports = {
  windowManager,
  processManager,
  dataStore,
  ssoManager,
  // Remote SSH — getters (not direct refs) so tests can read the
  // manager after bootstrap rather than at module-load time.
  getRemoteSessionManager: () => remoteSessionManager,
  sessionRouter,
};
