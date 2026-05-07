/* Phase 2 behavioral smoke test — does not require ssh2 to connect. */
'use strict';

const assert = require('assert');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');

const { shouldStop, effectiveStrictHostKeyChecking } =
  require(path.join(ROOT, 'electron/src/remote/auth-policy'));
const { backoffMs, BASE_MS, CAP_MS } =
  require(path.join(ROOT, 'electron/src/remote/backoff'));
const { KeepalivePolicy } =
  require(path.join(ROOT, 'electron/src/remote/keepalive-policy'));
const { STATES, ALLOWED_TRANSITIONS, isValidTransition } =
  require(path.join(ROOT, 'electron/src/remote/remote-session'));
const { RemoteSessionManager, MAX_SESSIONS, MGR_ERR } =
  require(path.join(ROOT, 'electron/src/remote/remote-session-manager'));
const { buildConnectConfig } =
  require(path.join(ROOT, 'electron/src/remote/ssh-client-builder'));

// ---- auth-policy.shouldStop ----
assert.strictEqual(shouldStop([], Date.now()), false);
assert.strictEqual(shouldStop([1, 2], Date.now()), false);
const now = 1_000_000_000;
assert.strictEqual(shouldStop([now, now - 1000, now - 2000], now), true);
assert.strictEqual(shouldStop([now - 70_000, now - 65_000, now - 80_000], now), false);
assert.strictEqual(shouldStop([now + 10_000, now + 20_000, now + 30_000], now), true);
console.log('PASS  auth-policy.shouldStop');

// ---- auth-policy.effectiveStrictHostKeyChecking ----
assert.strictEqual(
  effectiveStrictHostKeyChecking({ alias: 'a', strictHostKeyChecking: 'no' }),
  'no',
);
assert.strictEqual(
  effectiveStrictHostKeyChecking({ alias: 'gpu-01' }, new Set(['gpu-01'])),
  'yes',
);
assert.strictEqual(
  effectiveStrictHostKeyChecking({ alias: 'new-host' }, new Set()),
  'ask',
);
console.log('PASS  auth-policy.effectiveStrictHostKeyChecking');

// ---- backoff ----
assert.strictEqual(backoffMs(0), BASE_MS);
assert.strictEqual(backoffMs(1), 4000);
assert.strictEqual(backoffMs(2), 8000);
assert.strictEqual(backoffMs(3), 16000);
assert.strictEqual(backoffMs(4), CAP_MS);
assert.strictEqual(backoffMs(10), CAP_MS);
assert.strictEqual(backoffMs(-5), BASE_MS);
assert.strictEqual(backoffMs(NaN), BASE_MS);
console.log('PASS  backoff.backoffMs');

// ---- keepalive-policy ----
const k = new KeepalivePolicy({ intervalMs: 100_000, failureThreshold: 3 });
assert.strictEqual(k.intervalMs, 30_000);
assert.strictEqual(k.shouldReconnect(), false);
k.notifyFailure(); k.notifyFailure();
assert.strictEqual(k.shouldReconnect(), false);
k.notifyFailure();
assert.strictEqual(k.shouldReconnect(), true);
k.notifySuccess();
assert.strictEqual(k.shouldReconnect(), false);
console.log('PASS  keepalive-policy');

// ---- remote-session state machine ----
assert.strictEqual(isValidTransition(STATES.DISCONNECTED, STATES.CONNECTING), true);
assert.strictEqual(isValidTransition(STATES.DISCONNECTED, STATES.CONNECTED), false);
assert.strictEqual(isValidTransition(STATES.FAILED, STATES.DISCONNECTED), true);
assert.strictEqual(isValidTransition(STATES.CONNECTED, STATES.CONNECTING), false);
assert.strictEqual(isValidTransition(STATES.PROVISIONING, STATES.FORWARDING), true);
for (const [from, tos] of Object.entries(ALLOWED_TRANSITIONS)) {
  assert.ok(Array.isArray(tos), `${from} adjacency is array`);
}
console.log('PASS  state-machine transitions');

// ---- session-manager invariants (with fake sessions) ----
function makeFakeSession(alias) {
  const EE = require('events').EventEmitter;
  const s = new EE();
  s.alias = alias;
  s.state = STATES.DISCONNECTED;
  s.endpoint = { host: alias, port: 22, user: 'u' };
  s.connect = async () => {
    s.state = STATES.CONNECTED;
    s.emit('state', { from: STATES.DISCONNECTED, to: STATES.CONNECTED });
  };
  s.close = async () => {
    s.state = STATES.DISCONNECTED;
    s.emit('state', { from: STATES.CONNECTED, to: STATES.DISCONNECTED });
  };
  return s;
}

(async () => {
  const mgr = new RemoteSessionManager({
    sessionFactory: (target) => makeFakeSession(target.alias),
  });
  for (let i = 1; i <= MAX_SESSIONS; i++) {
    await mgr.connect('host' + i, { alias: 'host' + i, hostName: 'host' + i, port: 22 });
  }
  assert.strictEqual(mgr.size(), MAX_SESSIONS);
  try {
    await mgr.connect('overflow', { alias: 'overflow', hostName: 'overflow', port: 22 });
    assert.fail('should have rejected 5th');
  } catch (e) { assert.strictEqual(e.code, MGR_ERR.TOO_MANY_SESSIONS); }
  assert.strictEqual(mgr.getActiveAlias(), 'host1');
  assert.strictEqual(mgr.isRemoteActive(), true);
  mgr.switchActive('host3');
  assert.strictEqual(mgr.getActiveAlias(), 'host3');
  await mgr.disconnect('host3');
  assert.notStrictEqual(mgr.getActiveAlias(), 'host3');
  assert.strictEqual(mgr.size(), MAX_SESSIONS - 1);
  mgr.switchActive(null);
  assert.strictEqual(mgr.getActiveAlias(), null);
  assert.strictEqual(mgr.isRemoteActive(), false);
  try { mgr.switchActive('ghost'); assert.fail('should throw'); }
  catch (e) { assert.strictEqual(e.code, MGR_ERR.UNKNOWN_ALIAS); }
  await mgr.shutdown();
  assert.strictEqual(mgr.size(), 0);
  console.log('PASS  session-manager invariants');

  // ---- ssh-client-builder basic shape ----
  const target = {
    alias: 'dev',
    hostName: 'dev.example.com',
    user: 'deploy',
    port: 2222,
    identityFiles: [],
    proxyJump: [],
    strictHostKeyChecking: 'ask',
  };
  const built = buildConnectConfig({ target, hops: [] });
  assert.ok(Array.isArray(built.configs));
  assert.strictEqual(built.configs.length, 1);
  assert.strictEqual(built.configs[0].host, 'dev.example.com');
  assert.strictEqual(built.configs[0].port, 2222);
  assert.strictEqual(built.configs[0].username, 'deploy');
  assert.strictEqual(typeof built.configs[0].authHandler, 'function');
  assert.strictEqual(typeof built.configs[0].hostVerifier, 'function');
  console.log('PASS  ssh-client-builder.buildConnectConfig shape');

  const built2 = buildConnectConfig({
    target,
    hops: [
      { alias: 'b1', hostName: 'b1', port: 22, identityFiles: [] },
      { alias: 'b2', hostName: 'b2', port: 22, identityFiles: [] },
      { alias: 'b3', hostName: 'b3', port: 22, identityFiles: [] },
      { alias: 'b4', hostName: 'b4', port: 22, identityFiles: [] },
    ],
  });
  assert.ok(built2.diagnostics.some((d) => d.message.includes('ProxyJump')));
  console.log('PASS  ssh-client-builder too-many-hops diagnostic');

  console.log('\nALL PHASE 2 SMOKE TESTS PASSED');
})().catch((e) => { console.error('SMOKE FAILED:', e); process.exit(1); });
