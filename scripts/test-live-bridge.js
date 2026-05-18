#!/usr/bin/env node
/**
 * Live remote bridge probe — exercises the full chain that the
 * Electron file explorer + the AI agent's bridge tools use:
 *
 *   1) SSH connect (ssh-binary-tunnel + ssh2 client)
 *   2) RemoteFileBridge.list / stat (file explorer reads)
 *   3) bridge-server HTTP endpoints (what ai_engine bridge_client calls):
 *        /bridge/status, /bridge/list_directory, /bridge/run_command
 *
 * Usage:  node scripts/test-live-bridge.js <alias> [remote-path]
 *         node scripts/test-live-bridge.js Bastion /home/ec2-user
 *         node scripts/test-live-bridge.js g5
 */
'use strict';

const path = require('path');
const http = require('http');
const ROOT = path.resolve(__dirname, '..');

const sshConfigParser = require(path.join(ROOT, 'electron/src/remote/ssh-config-parser'));
const { RemoteSessionManager } = require(path.join(ROOT, 'electron/src/remote/remote-session-manager'));
const sshBinaryTunnel = require(path.join(ROOT, 'electron/src/remote/ssh-binary-tunnel'));
const portAllocator = require(path.join(ROOT, 'electron/src/remote/port-allocator'));
const { RemoteFileBridge } = require(path.join(ROOT, 'electron/src/remote/remote-file-bridge'));
const sessionRouter = require(path.join(ROOT, 'electron/src/remote/session-router'));
const { startBridgeServer } = require(path.join(ROOT, 'electron/src/remote/bridge-server'));

const alias = process.argv[2];
const remotePathArg = process.argv[3] || null;
if (!alias) {
  console.error('Usage: node scripts/test-live-bridge.js <alias> [remote-path]');
  process.exit(2);
}

const t0 = Date.now();
const log = (...a) => console.log('[' + ((Date.now() - t0) / 1000).toFixed(2) + 's]', ...a);

function postBridge(url, token, endpoint, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body || {});
    const u = new URL(endpoint, url);
    const req = http.request({
      hostname: u.hostname, port: u.port, path: u.pathname,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-AE-Bridge-Token': token,
        'Content-Length': Buffer.byteLength(data),
      },
    }, (res) => {
      let chunks = '';
      res.on('data', (d) => { chunks += d.toString('utf8'); });
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(chunks || '{}') }); }
        catch (e) { resolve({ status: res.statusCode, body: chunks }); }
      });
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

(async () => {
  // ------------------------------------------------------------------
  // 1) SSH connect (same path as electron/src/ipc-remote-handlers.js)
  // ------------------------------------------------------------------
  log('parse ~/.ssh/config');
  const { entries } = sshConfigParser.loadFromDisk({});
  const hostEntry = entries.find((e) => e.alias === alias);
  if (!hostEntry) {
    console.error('alias not found:', alias);
    process.exit(1);
  }

  let connectTarget = hostEntry;
  let tun = null;
  if (sshBinaryTunnel.needsBinaryFallback(hostEntry)) {
    const tunnelPort = await portAllocator.allocatePort({ range: [28765, 28865] });
    log('spawning binary tunnel on 127.0.0.1:' + tunnelPort);
    tun = await sshBinaryTunnel.spawnSshTunnel({ alias, localPort: tunnelPort });
    connectTarget = {
      ...hostEntry,
      hostName: '127.0.0.1', port: tunnelPort,
      proxyJump: [], proxyCommand: undefined,
      _originalHost: hostEntry.hostName || alias,
      _originalPort: hostEntry.port || 22,
    };
    log('tunnel ready');
  }

  const mgr = new RemoteSessionManager();
  mgr.on('host-key-prompt', (ev) => {
    const sess = mgr.get(ev.alias);
    if (sess) sess.respondAuth('host-key', { accept: true });
  });
  mgr.on('error', (ev) => log('SESSION-ERR', ev.error && ev.error.message));

  log('connecting...');
  const session = await mgr.connect(alias, connectTarget, [], {});
  log('connected, state=', session.state);

  // ------------------------------------------------------------------
  // 2) File-explorer simulation: RemoteFileBridge.list (what ipc-fs-handlers calls)
  // ------------------------------------------------------------------
  const fileBridge = new RemoteFileBridge(session);
  await fileBridge.init();

  // Resolve home: check FSx first then $HOME
  const home = await new Promise((resolve, reject) => {
    session.client.exec(
      'if [ -d "/fsx/home/$USER" ]; then echo "/fsx/home/$USER"; else echo "$HOME"; fi',
      (err, stream) => {
        if (err) return reject(err);
        let out = '';
        stream.on('data', (d) => { out += d.toString('utf8'); });
        stream.on('close', () => resolve(out.trim()));
      }
    );
  });
  log('remote home:', home);

  const targetPath = remotePathArg || home;
  log('file explorer test → list', targetPath);
  const listing = await fileBridge.list(targetPath);
  log('  entries:', listing.length);
  for (const e of listing.slice(0, 8)) {
    console.log('   ', e.isDirectory ? 'd' : '-', e.name, '(' + (e.size || 0) + ' bytes)');
  }
  if (listing.length > 8) console.log('   ...', listing.length - 8, 'more');

  // ------------------------------------------------------------------
  // 3) Wire sessionRouter so bridge-server can use the active session
  // ------------------------------------------------------------------
  sessionRouter.setActive({ session, fileBridge, termBridge: null, localPort: null });
  // sanity
  const active = sessionRouter.getActive();
  log('sessionRouter active:', active && active.state);

  // ------------------------------------------------------------------
  // 4) Bridge HTTP server — what ai_engine bridge_client.py talks to
  // ------------------------------------------------------------------
  log('starting bridge HTTP server');
  const bridge = await startBridgeServer({ sessionRouter, logger: { info: () => {}, warn: () => {}, error: () => {} } });
  log('bridge ready', bridge.url);

  // /bridge/status
  let res = await postBridge(bridge.url, bridge.token, '/bridge/status', {});
  log('POST /bridge/status →', res.status, JSON.stringify(res.body));

  // /bridge/list_directory — same call the AI agent's "list_directory" tool makes
  res = await postBridge(bridge.url, bridge.token, '/bridge/list_directory', { path: targetPath });
  log('POST /bridge/list_directory →', res.status, 'entries=', (res.body.entries || []).length);
  for (const e of (res.body.entries || []).slice(0, 5)) {
    console.log('   ', e.isDirectory ? 'd' : '-', e.name);
  }

  // /bridge/run_command — proves model can run arbitrary commands remotely
  res = await postBridge(bridge.url, bridge.token, '/bridge/run_command', {
    command: 'uname -a && python3 --version && df -h ' + JSON.stringify(targetPath),
    cwd: targetPath,
  });
  log('POST /bridge/run_command →', res.status, 'code=', res.body.code);
  if (res.body.stdout) console.log('   stdout:\n' + res.body.stdout.split('\n').slice(0, 8).map(l => '     ' + l).join('\n'));

  // ------------------------------------------------------------------
  // cleanup
  // ------------------------------------------------------------------
  await bridge.stop();
  await mgr.disconnect(alias, 'test-done');
  if (tun) try { tun.close(); } catch {}
  log('PASS — file explorer + bridge tools work end-to-end');
  process.exit(0);
})().catch((e) => {
  console.error('FATAL:', e && e.message ? e.message : e);
  if (e && e.stack) console.error(e.stack);
  process.exit(1);
});
