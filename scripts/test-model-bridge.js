#!/usr/bin/env node
/**
 * Model-call simulation: end-to-end exercise of the exact path the AI
 * agent's tools take when a remote session is active.
 *
 *   Renderer → ipc-remote-handlers (connect)
 *           → bridge-server (HTTP, 127.0.0.1, X-AE-Bridge-Token)
 *           → ai_engine/bridge_client.py (urllib.request)
 *           → renderer model receives directory listing / file content
 *
 * What this script does:
 *   1) SSH-connects to <alias> the same way Electron does.
 *   2) Starts the bridge HTTP server with the active session.
 *   3) Spawns Python and invokes ai_engine/bridge_client.py functions
 *      with AE_BRIDGE_URL / AE_BRIDGE_TOKEN env vars set, exactly
 *      matching the runtime an Electron-spawned ai_engine would see.
 *   4) Prints the model's view of the remote directory tree.
 *
 * Usage:  node scripts/test-model-bridge.js <alias>
 */
'use strict';

const path = require('path');
const { spawn } = require('child_process');
const ROOT = path.resolve(__dirname, '..');

const sshConfigParser = require(path.join(ROOT, 'electron/src/remote/ssh-config-parser'));
const { RemoteSessionManager } = require(path.join(ROOT, 'electron/src/remote/remote-session-manager'));
const sshBinaryTunnel = require(path.join(ROOT, 'electron/src/remote/ssh-binary-tunnel'));
const portAllocator = require(path.join(ROOT, 'electron/src/remote/port-allocator'));
const { RemoteFileBridge } = require(path.join(ROOT, 'electron/src/remote/remote-file-bridge'));
const sessionRouter = require(path.join(ROOT, 'electron/src/remote/session-router'));
const { startBridgeServer } = require(path.join(ROOT, 'electron/src/remote/bridge-server'));

const alias = process.argv[2];
if (!alias) {
  console.error('Usage: node scripts/test-model-bridge.js <alias>');
  process.exit(2);
}

const t0 = Date.now();
const log = (...a) => console.log('[' + ((Date.now() - t0) / 1000).toFixed(2) + 's]', ...a);

(async () => {
  // 1) SSH connect
  log('parse ~/.ssh/config + connect ' + alias);
  const { entries } = sshConfigParser.loadFromDisk({});
  const hostEntry = entries.find((e) => e.alias === alias);
  if (!hostEntry) { console.error('alias not found:', alias); process.exit(1); }

  let connectTarget = hostEntry;
  let tun = null;
  if (sshBinaryTunnel.needsBinaryFallback(hostEntry)) {
    const tunnelPort = await portAllocator.allocatePort({ range: [28765, 28865] });
    tun = await sshBinaryTunnel.spawnSshTunnel({ alias, localPort: tunnelPort });
    connectTarget = {
      ...hostEntry,
      hostName: '127.0.0.1', port: tunnelPort,
      proxyJump: [], proxyCommand: undefined,
      _originalHost: hostEntry.hostName || alias,
      _originalPort: hostEntry.port || 22,
    };
  }

  const mgr = new RemoteSessionManager();
  mgr.on('host-key-prompt', (ev) => { const s = mgr.get(ev.alias); if (s) s.respondAuth('host-key', { accept: true }); });
  const session = await mgr.connect(alias, connectTarget, [], {});
  log('SSH connected, state=' + session.state);

  // 2) File bridge + sessionRouter wiring (same as ipc-remote-handlers)
  const fileBridge = new RemoteFileBridge(session);
  await fileBridge.init();
  sessionRouter.setActive({ session, fileBridge, termBridge: null, localPort: null });

  // 3) Bridge HTTP server
  const bridge = await startBridgeServer({
    sessionRouter,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  log('bridge HTTP server up at ' + bridge.url);

  // 4) Spawn Python that imports bridge_client and asks the remote tree
  const pyScript = `
import os, sys, json
sys.path.insert(0, ${JSON.stringify(path.join(ROOT, 'ai_engine'))})
import bridge_client as bc

print('::BRIDGE-ACTIVE::', bc.is_active())
print('::REMOTE-SESSION::', bc.is_remote_session())
print('::ALIAS::', bc.alias())

# Resolve home then list it — the same calls the model's tools make.
out = bc.run_command('if [ -d "/fsx/home/$USER" ]; then echo "/fsx/home/$USER"; else echo "$HOME"; fi')
home = (out.get('stdout') or '').strip() or '/'
print('::HOME::', home)

entries = bc.list_directory(home)
print('::ENTRY-COUNT::', len(entries))
for e in entries[:8]:
    kind = 'd' if e.get('isDirectory') else '-'
    print('   ', kind, e.get('name'))

# Run a command the model would use to inspect the remote.
res = bc.run_command('uname -a && pwd && ls -la | head -5', cwd=home)
print('::CMD-CODE::', res.get('code'))
print('::CMD-STDOUT::')
print(res.get('stdout', '').strip())
`;

  log('spawning Python with AE_BRIDGE_URL/TOKEN env');
  const py = spawn(process.env.PYTHON || 'python3', ['-c', pyScript], {
    env: {
      ...process.env,
      AE_BRIDGE_URL: bridge.url,
      AE_BRIDGE_TOKEN: bridge.token,
      PYTHONUNBUFFERED: '1',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });

  let stdout = '';
  let stderr = '';
  py.stdout.on('data', (d) => { const t = d.toString('utf8'); stdout += t; process.stdout.write('  py> ' + t.replace(/\n(?!$)/g, '\n  py> ')); });
  py.stderr.on('data', (d) => { stderr += d.toString('utf8'); });

  const exitCode = await new Promise((resolve) => py.once('close', resolve));

  log('Python exited code=' + exitCode);
  if (stderr.trim()) {
    console.error('--- python stderr ---');
    console.error(stderr.trim().split('\n').slice(0, 20).join('\n'));
  }

  await bridge.stop();
  await mgr.disconnect(alias, 'test-done');
  if (tun) try { tun.close(); } catch {}

  // Check that the model-side actually saw the remote view.
  const ok = exitCode === 0
    && /::REMOTE-SESSION:: True/.test(stdout)
    && /::ENTRY-COUNT:: [1-9]/.test(stdout)
    && /::CMD-CODE:: 0/.test(stdout);
  if (ok) { log('PASS — model can read remote directory tree via bridge'); process.exit(0); }
  log('FAIL — model bridge path did not return expected output');
  process.exit(1);
})().catch((e) => {
  console.error('FATAL:', e && e.message ? e.message : e);
  if (e && e.stack) console.error(e.stack);
  process.exit(1);
});
