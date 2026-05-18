// Feature: remote-ssh, Integration Test: Multi-host context switch
// Validates: Requirement 9.3 — switch ≤500ms

'use strict';

const path = require('path');
const fs = require('fs');

const DOCKER_COMPOSE_PATH = path.join(__dirname, 'docker-compose.yml');
const KEYS_DIR = path.join(__dirname, 'keys');
const SSHD_PORT = 2222;
const SSHD_HOST = '127.0.0.1';
const BASTION_PORT = 2223;

// Modules under test
let RemoteSessionManager;
let CredentialCache;
let HostKeyStore;

describe.skip('Context Switch Performance (Docker environment required)', () => {
  let manager;
  let credentialCache;
  let hostKeyStore;

  beforeAll(async () => {
    if (!fs.existsSync(DOCKER_COMPOSE_PATH)) {
      throw new Error('docker-compose.yml not found. Run setup.sh first.');
    }

    const privateKeyPath = path.join(KEYS_DIR, 'id_ed25519');
    if (!fs.existsSync(privateKeyPath)) {
      throw new Error('Test keys not found. Run setup.sh to generate them.');
    }

    RemoteSessionManager = require('../../../electron/src/remote/remote-session-manager');
    CredentialCache = require('../../../electron/src/remote/credential-cache');
    HostKeyStore = require('../../../electron/src/remote/host-key-store');

    credentialCache = new CredentialCache();
    hostKeyStore = new HostKeyStore({ storePath: path.join(__dirname, 'keys', 'known_hosts_test') });

    manager = new RemoteSessionManager({
      credentialCache,
      hostKeyStore,
      logger: { info() {}, warn() {}, error() {} },
    });

    // Connect to both sshd containers
    const hostEntry1 = {
      alias: 'host-a',
      hostName: SSHD_HOST,
      user: 'testuser',
      port: SSHD_PORT,
      identityFiles: [path.join(KEYS_DIR, 'id_ed25519')],
      proxyJump: [],
      strictHostKeyChecking: 'no',
      sourcePaths: [],
      isWildcardOnly: false,
    };

    const hostEntry2 = {
      alias: 'host-b',
      hostName: SSHD_HOST,
      user: 'testuser',
      port: BASTION_PORT,
      identityFiles: [path.join(KEYS_DIR, 'id_ed25519')],
      proxyJump: [],
      strictHostKeyChecking: 'no',
      sourcePaths: [],
      isWildcardOnly: false,
    };

    await manager.connect('host-a', hostEntry1);
    await manager.connect('host-b', hostEntry2);
  });

  afterAll(async () => {
    if (manager) {
      await manager.disconnect('host-a');
      await manager.disconnect('host-b');
    }
    if (credentialCache) {
      credentialCache.clear();
    }
  });

  test('context switch between two connected hosts completes within 500ms', async () => {
    // Ensure host-a is active
    await manager.switchActive('host-a');
    expect(manager.getActive().alias).toBe('host-a');

    // Measure switch to host-b
    const start = Date.now();
    await manager.switchActive('host-b');
    const elapsed = Date.now() - start;

    expect(manager.getActive().alias).toBe('host-b');
    expect(elapsed).toBeLessThanOrEqual(500);
  });

  test('rapid back-and-forth switching stays within 500ms each', async () => {
    const switches = ['host-a', 'host-b', 'host-a', 'host-b'];
    const latencies = [];

    for (const alias of switches) {
      const start = Date.now();
      await manager.switchActive(alias);
      latencies.push(Date.now() - start);
    }

    for (const latency of latencies) {
      expect(latency).toBeLessThanOrEqual(500);
    }
  });

  test('inactive session keeps SSH connection alive', async () => {
    await manager.switchActive('host-a');

    // host-b is now inactive but should still be connected
    const sessions = manager.all();
    const hostB = sessions.find((s) => s.alias === 'host-b');

    expect(hostB).toBeDefined();
    expect(hostB.state).toBe('connected');
  });

  test('file watchers are suspended on inactive session', async () => {
    await manager.switchActive('host-a');

    // Verify host-b watchers are paused
    const sessions = manager.all();
    const hostB = sessions.find((s) => s.alias === 'host-b');

    expect(hostB.watchersSuspended).toBe(true);
  });

  test('file watchers resume on reactivation', async () => {
    await manager.switchActive('host-b');

    const sessions = manager.all();
    const hostB = sessions.find((s) => s.alias === 'host-b');

    expect(hostB.watchersSuspended).toBe(false);
  });
});
