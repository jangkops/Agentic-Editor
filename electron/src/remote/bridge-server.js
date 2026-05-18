'use strict';
/**
 * Bridge HTTP Server — exposes remote SFTP/SSH operations to the
 * locally-running ai_engine (Python) so AI agent tools can transparently
 * operate on the remote host when a Remote SSH session is active.
 *
 * Security:
 *  - Only binds to 127.0.0.1
 *  - Requires X-AE-Bridge-Token header on every request
 *  - Token is regenerated on each Electron start (32-char hex)
 *  - Token is never logged in full (first 4 chars + ****)
 *
 * Endpoints (all POST):
 *   /bridge/status        → {remote, alias}
 *   /bridge/read_file     → {ok, content} or {ok:false, error}
 *   /bridge/write_file    → {ok}
 *   /bridge/list_directory → {ok, entries:[{name,path,isDirectory}]}
 *   /bridge/run_command   → {ok, stdout, stderr, code}
 *   /bridge/search_files  → {ok, output}
 */

const http = require('http');
const crypto = require('crypto');

/**
 * Start the bridge HTTP server.
 *
 * @param {Object} opts
 * @param {Object} opts.sessionRouter - the shared sessionRouter singleton
 * @param {Object} [opts.logger] - logger with .info/.warn/.error
 * @returns {Promise<{url: string, token: string, stop: Function}>}
 */
function startBridgeServer(opts) {
  const { sessionRouter, logger } = opts || {};
  if (!sessionRouter) throw new Error('bridge-server: sessionRouter required');

  const token = crypto.randomBytes(16).toString('hex');
  const maskedToken = token.slice(0, 4) + '****';

  const server = http.createServer((req, res) => {
    // IP restriction
    const remoteAddr = (req.socket && req.socket.remoteAddress) || '';
    const isLocal =
      remoteAddr === '127.0.0.1' ||
      remoteAddr === '::1' ||
      remoteAddr === '::ffff:127.0.0.1';
    if (!isLocal) {
      res.writeHead(403, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'forbidden' }));
      return;
    }

    // Token auth
    const hdr = req.headers['x-ae-bridge-token'];
    if (!hdr || hdr !== token) {
      res.writeHead(401, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, error: 'unauthorized' }));
      return;
    }

    // Read body
    let body = '';
    req.on('data', (chunk) => {
      body += chunk.toString('utf8');
      // Prevent unbounded memory
      if (body.length > 50 * 1024 * 1024) {
        req.destroy();
      }
    });
    req.on('end', async () => {
      let payload = {};
      try { payload = body ? JSON.parse(body) : {}; } catch { payload = {}; }

      const url = req.url || '/';
      try {
        const result = await handleRequest(url, payload, sessionRouter);
        if (result === null) {
          res.writeHead(404, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, error: 'not found' }));
          return;
        }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(result));
      } catch (err) {
        if (logger && logger.warn) {
          try { logger.warn('bridge-request-failed', { url, message: err && err.message }); } catch {}
        }
        res.writeHead(500, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, error: (err && err.message) || String(err) }));
      }
    });
    req.on('error', () => { /* client aborted */ });
  });

  return new Promise((resolve, reject) => {
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      const url = `http://127.0.0.1:${port}`;
      if (logger && logger.info) {
        try { logger.info('bridge-server-started', { port, token: maskedToken }); } catch {}
      } else {
        console.log('[bridge] server started on', port, 'token:', maskedToken);
      }
      resolve({
        url,
        token,
        stop: () => new Promise((r) => server.close(() => r())),
      });
    });
  });
}

/**
 * Dispatch a bridge request to the appropriate sessionRouter operation.
 * @private
 */
async function handleRequest(url, payload, sessionRouter) {
  const active = sessionRouter.getActive();
  // Treat the session as remote-usable as soon as the SSH channel is
  // live, not only after `connected`. Provisioning runs in the
  // background after auth completes (see ipc-remote-handlers), so
  // gating on `state === 'connected'` would lock out file/exec ops
  // for the first few seconds of every session. SessionRouter already
  // implements this policy; reuse it as the single source of truth.
  const isRemote = typeof sessionRouter.isRemoteActive === 'function'
    ? !!sessionRouter.isRemoteActive()
    : !!(active && active.state === 'connected');

  if (url === '/bridge/status') {
    return {
      remote: isRemote,
      alias: isRemote ? (active.alias || null) : null,
    };
  }

  if (url === '/bridge/read_file') {
    if (!isRemote) return { ok: false, error: 'no remote session' };
    const bridge = sessionRouter.getFileBridge();
    if (!bridge) return { ok: false, error: 'no file bridge' };
    const content = await bridge.read(String(payload.path || ''), 'utf8');
    return { ok: true, content };
  }

  if (url === '/bridge/write_file') {
    if (!isRemote) return { ok: false, error: 'no remote session' };
    const bridge = sessionRouter.getFileBridge();
    if (!bridge) return { ok: false, error: 'no file bridge' };
    // mkdir -p of parent
    const path = String(payload.path || '');
    const parent = path.substring(0, path.lastIndexOf('/'));
    if (parent && typeof bridge.mkdir === 'function') {
      try { await bridge.mkdir(parent, { recursive: true }); } catch {}
    }
    await bridge.write(path, String(payload.content || ''));
    return { ok: true };
  }

  if (url === '/bridge/list_directory') {
    if (!isRemote) return { ok: false, error: 'no remote session' };
    const bridge = sessionRouter.getFileBridge();
    if (!bridge) return { ok: false, error: 'no file bridge' };
    const entries = await bridge.list(String(payload.path || '/'));
    return {
      ok: true,
      entries: (entries || []).map((e) => ({
        name: e.name,
        path: e.path,
        isDirectory: !!e.isDirectory,
        size: e.size || 0,
      })),
    };
  }

  if (url === '/bridge/run_command') {
    if (!isRemote) return { ok: false, error: 'no remote session' };
    const cmd = String(payload.command || '');
    const cwd = payload.cwd ? String(payload.cwd) : undefined;
    const r = await sessionRouter.exec(cmd, cwd ? { cwd } : {});
    return {
      ok: true,
      stdout: r.stdout || '',
      stderr: r.stderr || '',
      code: typeof r.code === 'number' ? r.code : 0,
    };
  }

  if (url === '/bridge/search_files') {
    if (!isRemote) return { ok: false, error: 'no remote session' };
    const query = String(payload.query || '').replace(/"/g, '\\"');
    const searchPath = String(payload.path || '.').replace(/"/g, '\\"');
    const pattern = payload.file_pattern ? `--include="${String(payload.file_pattern).replace(/"/g, '\\"')}"` : '';
    const cmd = `grep -rn ${pattern} --color=never "${query}" "${searchPath}" 2>/dev/null | head -50`;
    const r = await sessionRouter.exec(cmd, {});
    return { ok: true, output: r.stdout || '검색 결과 없음' };
  }

  return null; // unknown endpoint
}

module.exports = { startBridgeServer };
