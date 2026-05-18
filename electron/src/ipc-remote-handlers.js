'use strict';
/**
 * IPC Remote Handlers — remote:* 채널 등록.
 *
 * Feature: remote-ssh · Tasks 22-23
 * Registers all remote:* IPC channels (list-hosts, connect, disconnect,
 * status, respond-auth, etc.) and bridges them to RemoteSessionManager.
 *
 * NOTE on this session: keep this file simple and dependency-light so a
 * missing optional module (ssh2) does not break list-hosts. list-hosts
 * must work even if ssh2 is not installed yet.
 */

const { ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');

const sshConfigParser = require('./remote/ssh-config-parser');
const logger = require('./remote/logger');
const credentialCache = require('./remote/credential-cache');
const sshBinaryTunnel = require('./remote/ssh-binary-tunnel');
const portAllocator = require('./remote/port-allocator');

// Lazy load modules that depend on ssh2 — only when user actually connects.
let _connectDeps = null;
function getConnectDeps() {
  if (_connectDeps) return _connectDeps;
  _connectDeps = {
    RemoteSession: require('./remote/remote-session').RemoteSession,
    RemoteSessionManager: require('./remote/remote-session-manager').RemoteSessionManager,
  };
  try { _connectDeps.PortForwarder = require('./remote/port-forwarder').PortForwarder; } catch (_e) {}
  try { _connectDeps.RemoteFileBridge = require('./remote/remote-file-bridge').RemoteFileBridge; } catch (_e) {}
  try { _connectDeps.RemoteTerminalBridge = require('./remote/remote-terminal-bridge').RemoteTerminalBridge; } catch (_e) {}
  try { _connectDeps.Provisioner = require('./remote/provisioner').Provisioner; } catch (_e) {}
  try { _connectDeps.sessionRouter = require('./remote/session-router'); } catch (_e) {}
  return _connectDeps;
}

function _loadRemoteHosts(dataStore) {
  const p = path.join(dataStore.basePath, 'settings', 'remote-hosts.json');
  if (!fs.existsSync(p)) return { schemaVersion: 1, hosts: {} };
  try {
    const data = JSON.parse(fs.readFileSync(p, 'utf-8'));
    return { schemaVersion: data.schemaVersion || 1, hosts: data.hosts || {} };
  } catch { return { schemaVersion: 1, hosts: {} }; }
}

function _saveRemoteHosts(dataStore, data) {
  const dir = path.join(dataStore.basePath, 'settings');
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  const p = path.join(dir, 'remote-hosts.json');
  fs.writeFileSync(p, JSON.stringify(data, null, 2), 'utf-8');
}

/**
 * Register all remote:* IPC handlers.
 *
 * @param {{mainWindow: Electron.BrowserWindow, dataStore: Object, processManager: Object, localAiEngineRoot: string}} deps
 */
function registerRemoteHandlers(deps) {
  const { mainWindow, dataStore, processManager, localAiEngineRoot } = deps;

  let manager = null;
  const bridges = new Map(); // alias -> {forwarder, fileBridge, termBridge}
  const binaryTunnels = new Map(); // alias -> {child, close, emitter, tunnelPort}

  const send = (channel, data) => {
    try {
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, data);
    } catch (_e) { /* ignore */ }
  };

  // --- remote:list-hosts ---
  ipcMain.handle('remote:list-hosts', () => {
    try {
      const { entries, diagnostics } = sshConfigParser.loadFromDisk({});
      const stored = _loadRemoteHosts(dataStore);
      const adHocEntries = [];
      for (const [alias, info] of Object.entries(stored.hosts || {})) {
        if (info && info.source === 'ad-hoc' && info.adHoc) {
          adHocEntries.push({
            alias, hostName: info.adHoc.hostName || alias,
            user: info.adHoc.user || '', port: info.adHoc.port || 22,
            source: 'ad-hoc', favorite: Boolean(info.favorite),
          });
        }
      }
      const sshEntries = entries
        .filter(e => !e.isWildcardOnly)
        .map(e => ({
          alias: e.alias,
          hostName: e.hostName || e.alias,
          user: e.user || '',
          port: e.port || 22,
          source: 'ssh-config',
          favorite: Boolean((stored.hosts || {})[e.alias] && (stored.hosts || {})[e.alias].favorite),
        }));
      return { entries: [...adHocEntries, ...sshEntries], diagnostics: diagnostics || [] };
    } catch (err) {
      try { logger.error('remote-list-hosts-error', { message: err && err.message }); } catch {}
      return { entries: [], diagnostics: [{ severity: 'error', message: (err && err.message) || String(err) }] };
    }
  });

  // --- remote:add-ad-hoc-host ---
  ipcMain.handle('remote:add-ad-hoc-host', (_, host) => {
    try {
      if (!host || !host.alias || !host.hostName) return { ok: false, error: 'alias and hostName required' };
      const stored = _loadRemoteHosts(dataStore);
      stored.hosts[host.alias] = {
        favorite: false,
        source: 'ad-hoc',
        adHoc: {
          hostName: host.hostName, user: host.user || '',
          port: Number(host.port) || 22, identityFile: host.identityFile || '',
        },
      };
      _saveRemoteHosts(dataStore, stored);
      return { ok: true };
    } catch (err) { return { ok: false, error: err && err.message }; }
  });

  // --- remote:set-favorite ---
  ipcMain.handle('remote:set-favorite', (_, { alias, favorite }) => {
    try {
      const stored = _loadRemoteHosts(dataStore);
      if (!stored.hosts[alias]) stored.hosts[alias] = { source: 'ssh-config' };
      stored.hosts[alias].favorite = Boolean(favorite);
      _saveRemoteHosts(dataStore, stored);
      return { ok: true };
    } catch (err) { return { ok: false, error: err && err.message }; }
  });

  // --- remote:connect ---
  ipcMain.handle('remote:connect', async (_, { alias }) => {
    try {
      const { entries } = sshConfigParser.loadFromDisk({});
      const stored = _loadRemoteHosts(dataStore);
      let hostEntry = entries.find(e => e.alias === alias);
      if (!hostEntry && stored.hosts[alias] && stored.hosts[alias].adHoc) {
        const ah = stored.hosts[alias].adHoc;
        hostEntry = {
          alias, hostName: ah.hostName, user: ah.user || '',
          port: ah.port || 22,
          identityFiles: ah.identityFile ? [ah.identityFile] : [],
          proxyJump: [], sourcePaths: [], isWildcardOnly: false,
        };
      }
      if (!hostEntry) return { ok: false, error: `Host "${alias}" not found` };

      const deps = getConnectDeps();
      if (!manager) {
        manager = new deps.RemoteSessionManager();
        manager.on('state', (ev) => send('remote:event:state', ev));
        manager.on('auth-prompt', (ev) => send('remote:event:auth-request', ev));
        manager.on('host-key-prompt', (ev) => send('remote:event:host-key-prompt', ev));
        manager.on('error', (ev) => {
          // Only surface errors to the renderer if the session is NOT already connected.
          // Background provisioning failures should not show error popups.
          const s = manager.get(ev.alias);
          if (s && s.state === 'connected') return; // suppress — already working
          send('remote:event:state', { alias: ev.alias, from: 'connecting', to: 'failed', reason: (ev.error && ev.error.message) || String(ev.error) });
        });
      }

      // Clean up any stale session (prior dial failure, disconnect race, etc.)
      // This is essential because RemoteSessionManager.connect() refuses
      // to replace an existing alias, but the session state machine may
      // have ended up in `failed`/`disconnected` after a partial setup.
      if (manager.get(alias)) {
        try { await manager.disconnect(alias, 'cleanup-before-reconnect'); } catch {}
        const b = bridges.get(alias);
        if (b) {
          if (b.forwarder) try { await b.forwarder.close(); } catch {}
          if (b.fileBridge) try { await b.fileBridge.close(); } catch {}
          if (b.termBridge) try { await b.termBridge.close(); } catch {}
          bridges.delete(alias);
        }
        const tun = binaryTunnels.get(alias);
        if (tun) { try { tun.close(); } catch {} binaryTunnels.delete(alias); }
      }

      // If the config uses ProxyCommand or ProxyJump, spawn the OS `ssh`
      // binary to establish a local TCP tunnel (this leverages the full
      // OpenSSH feature set — SSM Session Manager, bastion jumps, etc.).
      // ssh2 then connects to 127.0.0.1:<tunnelPort> like a plain host.
      let connectTarget = hostEntry;
      if (sshBinaryTunnel.needsBinaryFallback(hostEntry)) {
        try {
          const tunnelPort = await portAllocator.allocatePort({ range: [28765, 28865] });
          try { logger.info('remote-binary-tunnel-spawn', { alias, tunnelPort }); } catch {}
          const tun = await sshBinaryTunnel.spawnSshTunnel({ alias, localPort: tunnelPort });
          tun.tunnelPort = tunnelPort;
          binaryTunnels.set(alias, tun);
          // Override the target so ssh2 dials the tunnel instead of the real host.
          connectTarget = {
            ...hostEntry,
            hostName: '127.0.0.1',
            port: tunnelPort,
            proxyJump: [], // already handled by OS ssh
            proxyCommand: undefined,
            // Preserve original host/port for host-key verification so the
            // known_hosts entry is stable across tunnel port changes.
            _originalHost: hostEntry.hostName || alias,
            _originalPort: hostEntry.port || 22,
          };
          // If the tunnel dies, we surface it as a session error.
          tun.emitter.once('tunnel-down', (ev) => {
            try { logger.warn('remote-binary-tunnel-down', { alias, exitCode: ev.exitCode }); } catch {}
            send('remote:event:state', { alias, from: 'connected', to: 'failed', reason: 'ssh tunnel closed' });
          });
        } catch (tunnelErr) {
          try { logger.error('remote-binary-tunnel-failed', { alias, message: tunnelErr && tunnelErr.message }); } catch {}
          return { ok: false, error: `SSH tunnel 실패: ${(tunnelErr && tunnelErr.message) || tunnelErr}` };
        }
      }

      const session = await manager.connect(alias, connectTarget, [], {});

      let localPort = null;
      let remoteHome = '';

      // === IMMEDIATE: Mark session as connected right after SSH auth ===
      // Provisioning (ai_engine install) runs in the background — it should
      // NOT block file/terminal access. The user wants to browse remote
      // directories and use the terminal immediately after SSH connects.
      try { session.markProvisioned(); } catch {}
      try { session.markForwarded(); } catch {}

      // Mount file + terminal bridges FIRST so they're available immediately.
      // RemoteFileBridge MUST be initialised before any list/read/stat call
      // because the SFTP channel is opened lazily by init() — without this
      // the very first file-explorer click throws `sftp-not-initialized`.
      const fileBridge = deps.RemoteFileBridge ? new deps.RemoteFileBridge(session) : null;
      if (fileBridge) {
        try {
          await fileBridge.init();
        } catch (initErr) {
          try { logger.warn('remote-file-bridge-init-failed', { alias, message: initErr && initErr.message }); } catch {}
          // Non-fatal — terminal still works; the next list() will surface
          // a clearer error to the renderer than a silent no-op would.
        }
      }
      const termBridge = deps.RemoteTerminalBridge ? new deps.RemoteTerminalBridge(session) : null;
      // NOTE: Do NOT wire termBridge data/exit events here — ipc-terminal-handlers.js
      // handles forwarding via _wireBridgeEvents() when a terminal is created.
      // Wiring here causes DUPLICATE events (every keystroke echoed twice).
      if (fileBridge) fileBridge.on('fs-change', (ev) => send('remote:event:fs-change', ev));
      bridges.set(alias, { forwarder: null, fileBridge, termBridge });

      // Resolve the user's remote $HOME so the renderer can open it as a workspace.
      // AWS instances with /fsx/home/<user> should prefer that over /home/<user>.
      try {
        if (session && session.client && typeof session.client.exec === 'function') {
          const homeRes = await new Promise((resolve, reject) => {
            // Check /fsx/home/$USER first (AWS FSx-backed home), fall back to $HOME
            const cmd = 'if [ -d "/fsx/home/$USER" ]; then echo "/fsx/home/$USER"; else echo "$HOME"; fi';
            session.client.exec(cmd, (err, stream) => {
              if (err) { reject(err); return; }
              let out = '';
              stream.on('data', d => out += d.toString('utf8'));
              stream.on('close', () => resolve(out.trim()));
              stream.on('error', reject);
            });
          });
          remoteHome = String(homeRes || '').trim() || '/';
        } else {
          remoteHome = '/';
        }
      } catch (_e) { remoteHome = '/'; }

      if (deps.sessionRouter) deps.sessionRouter.setActive({ session, fileBridge, termBridge, localPort });
      try { manager.switchActive(alias); } catch {}

      // Persist last workspace so next connect opens the same folder.
      try {
        const stored2 = _loadRemoteHosts(dataStore);
        if (!stored2.hosts[alias]) stored2.hosts[alias] = { source: 'ssh-config' };
        stored2.hosts[alias].lastWorkspace = stored2.hosts[alias].lastWorkspace || remoteHome;
        _saveRemoteHosts(dataStore, stored2);
      } catch {}

      // Emit 'connected' notification IMMEDIATELY so the renderer shows
      // the file tree and status bar light. This is the VS Code behavior:
      // remote file browsing works instantly, ai_engine is optional.
      try {
        send('remote:event:connected', {
          alias,
          user: hostEntry.user || '',
          hostName: hostEntry.hostName || alias,
          remoteHome,
          workspace: ((_loadRemoteHosts(dataStore).hosts || {})[alias] || {}).lastWorkspace || remoteHome,
          localPort,
        });
      } catch (_e) { /* never let event emission crash the handler */ }

      // === BACKGROUND: Provision ai_engine + port forward (non-blocking) ===
      // This enables the AI chat features but does NOT block file/terminal.
      if (deps.Provisioner && localAiEngineRoot) {
        (async () => {
          try {
            const provisioner = new deps.Provisioner(session, { localAiEngineRoot });
            provisioner.on('progress', (ev) => {
              send('remote:event:state', { alias, from: 'connected', to: 'connected', reason: `provisioning: ${ev.stage}` });
            });
            await provisioner.ensureProvisioned();
            if (deps.PortForwarder) {
              try {
                const forwarder = new deps.PortForwarder(session, provisioner.remotePort, portAllocator);
                localPort = await forwarder.open();
                const existing = bridges.get(alias) || {};
                bridges.set(alias, Object.assign(existing, { forwarder }));
                // Update the router with the new localPort
                if (deps.sessionRouter) deps.sessionRouter.setActive({ session, fileBridge, termBridge, localPort });
                // Stop local Python — remote ai_engine is now reachable
                if (processManager && typeof processManager.stopPython === 'function') {
                  processManager.stopPython();
                }
                try { logger.info('remote-provision-complete', { alias, localPort }); } catch {}
              } catch (pfe) {
                try { logger.warn('remote-portforward-failed', { alias, message: pfe && pfe.message }); } catch {}
              }
            }
          } catch (pe) {
            try { logger.warn('remote-provision-failed', { alias, message: pe && pe.message }); } catch {}
            // ai_engine unavailable — file/terminal still work fine
          }
        })();
      }

      return { ok: true, sessionId: alias, localPort, remoteHome };
    } catch (err) {
      try { logger.error('remote-connect-error', { alias, message: err && err.message }); } catch {}
      return { ok: false, error: (err && err.message) || String(err) };
    }
  });

  // --- remote:disconnect ---
  ipcMain.handle('remote:disconnect', async (_, { alias }) => {
    try {
      if (manager) await manager.disconnect(alias, 'user-disconnect').catch(() => {});
      const b = bridges.get(alias);
      if (b) {
        if (b.forwarder) try { await b.forwarder.close(); } catch {}
        if (b.fileBridge) try { await b.fileBridge.close(); } catch {}
        if (b.termBridge) try { await b.termBridge.close(); } catch {}
        bridges.delete(alias);
      }
      const tun = binaryTunnels.get(alias);
      if (tun) { try { tun.close(); } catch {} binaryTunnels.delete(alias); }
      try { credentialCache.clear(alias); } catch {}
      const deps = getConnectDeps();
      if (deps.sessionRouter && (!manager || manager.getActiveAlias() === null)) {
        deps.sessionRouter.setActive(null);
        if (processManager && typeof processManager.startPython === 'function') processManager.startPython();
      }
      return { ok: true };
    } catch (err) { return { ok: false, error: err && err.message }; }
  });

  // --- remote:switch-active ---
  ipcMain.handle('remote:switch-active', (_, { alias }) => {
    try {
      if (!manager) return { ok: false, error: 'no manager' };
      manager.switchActive(alias);
      const b = bridges.get(alias);
      const deps = getConnectDeps();
      const session = manager.get(alias);
      if (b && deps.sessionRouter && session) {
        deps.sessionRouter.setActive({ session, fileBridge: b.fileBridge, termBridge: b.termBridge, localPort: b.forwarder && b.forwarder.localPort });
      }
      return { ok: true };
    } catch (err) { return { ok: false, error: err && err.message }; }
  });

  // --- remote:status ---
  ipcMain.handle('remote:status', (_, opts) => {
    try {
      if (!manager) return { _active: null, _apiBase: 'http://localhost:8765' };
      const alias = opts && opts.alias;
      const result = {};
      if (alias) {
        const s = manager.get(alias);
        const b = bridges.get(alias);
        return { [alias]: { state: s ? s.state : 'disconnected', localPort: b && b.forwarder ? b.forwarder.localPort : null } };
      }
      // Iterate via list() if available, else skip
      if (typeof manager.list === 'function') {
        for (const row of manager.list()) {
          const b = bridges.get(row.alias);
          result[row.alias] = { state: row.state, localPort: b && b.forwarder ? b.forwarder.localPort : null };
        }
      }
      result._active = manager.getActiveAlias();
      const deps = getConnectDeps();
      result._apiBase = deps.sessionRouter ? deps.sessionRouter.apiBase() : 'http://localhost:8765';
      return result;
    } catch (err) { return { _error: err && err.message }; }
  });

  // --- remote:respond-auth (Req 10.4) ---
  ipcMain.handle('remote:respond-auth', (_, { alias, kind, payload }) => {
    try {
      if (!manager) return { ok: false, error: 'no manager' };
      const session = manager.get(alias);
      if (!session) return { ok: false, error: 'session not found' };
      session.respondAuth(kind, payload);
      return { ok: true };
    } catch (err) { return { ok: false, error: err && err.message }; }
  });

  // --- remote:set-workspace ---
  ipcMain.handle('remote:set-workspace', (_, { alias, remotePath }) => {
    try {
      const stored = _loadRemoteHosts(dataStore);
      if (!stored.hosts[alias]) stored.hosts[alias] = { source: 'ssh-config' };
      stored.hosts[alias].lastWorkspace = remotePath;
      _saveRemoteHosts(dataStore, stored);
      return { ok: true };
    } catch (err) { return { ok: false, error: err && err.message }; }
  });

  // --- remote:clear-credentials ---
  ipcMain.handle('remote:clear-credentials', () => {
    try { credentialCache.clear(); return { ok: true }; } catch (err) { return { ok: false, error: err && err.message }; }
  });

  // --- remote:show-log ---
  ipcMain.handle('remote:show-log', () => {
    try { return { path: (logger && logger.logFilePath) || '' }; }
    catch { return { path: '' }; }
  });

  return { manager, bridges };
}

module.exports = { registerRemoteHandlers };
