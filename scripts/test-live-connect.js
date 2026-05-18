#!/usr/bin/env node
/**
 * Live SSH connection probe — exercises the same code path the Electron
 * app uses, so failures here mirror what the user sees in the editor.
 *
 * Usage:  node scripts/test-live-connect.js <alias>
 *         node scripts/test-live-connect.js Bastion
 */
'use strict';

const path = require('path');
const ROOT = path.resolve(__dirname, '..');

const sshConfigParser = require(path.join(ROOT, 'electron/src/remote/ssh-config-parser'));
const { RemoteSessionManager } = require(path.join(ROOT, 'electron/src/remote/remote-session-manager'));
const sshBinaryTunnel = require(path.join(ROOT, 'electron/src/remote/ssh-binary-tunnel'));
const portAllocator = require(path.join(ROOT, 'electron/src/remote/port-allocator'));

const alias = process.argv[2];
if (!alias) {
  console.error('Usage: node scripts/test-live-connect.js <alias>');
  process.exit(2);
}

(async () => {
  const start = Date.now();
  const log = (...a) => console.log('[' + ((Date.now() - start) / 1000).toFixed(2) + 's]', ...a);

  log('parse ~/.ssh/config');
  const { entries, diagnostics } = sshConfigParser.loadFromDisk({});
  log('  entries:', entries.length, 'diagnostics:', diagnostics.length);

  const hostEntry = entries.find((e) => e.alias === alias);
  if (!hostEntry) {
    console.error('alias not found in ~/.ssh/config:', alias);
    console.error('available:', entries.map((e) => e.alias).join(', '));
    process.exit(1);
  }
  log('host entry:', JSON.stringify({
    alias: hostEntry.alias,
    hostName: hostEntry.hostName,
    user: hostEntry.user,
    port: hostEntry.port,
    proxyJump: hostEntry.proxyJump,
    proxyCommand: hostEntry.proxyCommand ? '<set>' : null,
  }));

  let connectTarget = hostEntry;
  let tun = null;
  if (sshBinaryTunnel.needsBinaryFallback(hostEntry)) {
    log('binary tunnel needed (ProxyCommand or ProxyJump present)');
    const tunnelPort = await portAllocator.allocatePort({ range: [28765, 28865] });
    log('  spawning ssh -L 127.0.0.1:' + tunnelPort + ':127.0.0.1:22 ' + alias);
    tun = await sshBinaryTunnel.spawnSshTunnel({ alias, localPort: tunnelPort });
    log('  tunnel ready on 127.0.0.1:' + tunnelPort);
    connectTarget = {
      ...hostEntry,
      hostName: '127.0.0.1',
      port: tunnelPort,
      proxyJump: [],
      proxyCommand: undefined,
      _originalHost: hostEntry.hostName || alias,
      _originalPort: hostEntry.port || 22,
    };
  }

  const mgr = new RemoteSessionManager();
  mgr.on('state', (ev) => log('STATE', ev.alias, ev.from, '→', ev.to, ev.reason ? '(' + ev.reason + ')' : ''));
  mgr.on('auth-prompt', (ev) => {
    log('AUTH-PROMPT kind=' + ev.kind, ev.payload && ev.payload.text ? ev.payload.text : '');
    // For the live test, we cannot supply real interactive answers — fail fast.
    log('  (interactive auth not supported in this probe; aborting)');
    process.exit(2);
  });
  mgr.on('host-key-prompt', (ev) => {
    log('HOST-KEY-PROMPT fingerprint=' + ev.fingerprint, 'host=' + ev.host + ':' + ev.port);
    // Auto-accept for the live probe so we exercise the full handshake.
    const sess = mgr.get(ev.alias);
    if (sess) {
      log('  auto-accepting host key (test probe)');
      sess.respondAuth('host-key', { accept: true });
    }
  });
  mgr.on('error', (ev) => log('ERROR', ev.alias, ev.error && ev.error.message));

  log('manager.connect()');
  try {
    const session = await mgr.connect(alias, connectTarget, [], {});
    log('CONNECTED, state=', session.state);

    // Run pwd to prove the SSH channel works
    const exec = (cmd) => new Promise((resolve, reject) => {
      session.client.exec(cmd, (err, stream) => {
        if (err) return reject(err);
        let stdout = '';
        let stderr = '';
        stream.on('data', (d) => { stdout += d.toString('utf8'); });
        stream.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
        stream.on('close', (code) => resolve({ stdout: stdout.trim(), stderr: stderr.trim(), code }));
      });
    });

    const home = await exec('pwd');
    log('remote pwd:', home.stdout);
    const ls = await exec('ls -la ' + home.stdout + ' | head -10');
    log('remote ls:');
    console.log(ls.stdout);

    log('disconnecting');
    await mgr.disconnect(alias, 'test-done');
  } catch (err) {
    console.error('CONNECT FAILED:', err && err.message);
    if (err && err.stack) console.error(err.stack);
    process.exit(1);
  } finally {
    if (tun) try { tun.close(); } catch {}
  }

  log('PASS — live connection works end-to-end');
  process.exit(0);
})().catch((e) => {
  console.error('FATAL:', e);
  process.exit(1);
});
