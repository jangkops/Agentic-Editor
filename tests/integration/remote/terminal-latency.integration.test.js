// Feature: remote-ssh, Integration Test: Terminal latency
// Validates: Requirements 7.2, 7.3 — keypress→render ≤80ms, resize ≤200ms

'use strict';

const path = require('path');
const fs = require('fs');

const DOCKER_COMPOSE_PATH = path.join(__dirname, 'docker-compose.yml');
const KEYS_DIR = path.join(__dirname, 'keys');
const SSHD_PORT = 2222;
const SSHD_HOST = '127.0.0.1';

// Modules under test
let RemoteSession;
let RemoteTerminalBridge;
let CredentialCache;
let HostKeyStore;

describe.skip('Terminal Latency (Docker environment required)', () => {
  let session;
  let terminalBridge;
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

    RemoteSession = require('../../../electron/src/remote/remote-session');
    RemoteTerminalBridge = require('../../../electron/src/remote/remote-terminal-bridge');
    CredentialCache = require('../../../electron/src/remote/credential-cache');
    HostKeyStore = require('../../../electron/src/remote/host-key-store');

    credentialCache = new CredentialCache();
    hostKeyStore = new HostKeyStore({ storePath: path.join(__dirname, 'keys', 'known_hosts_test') });

    const hostEntry = {
      alias: 'test-sshd',
      hostName: SSHD_HOST,
      user: 'testuser',
      port: SSHD_PORT,
      identityFiles: [path.join(KEYS_DIR, 'id_ed25519')],
      proxyJump: [],
      strictHostKeyChecking: 'no',
      sourcePaths: [],
      isWildcardOnly: false,
    };

    session = new RemoteSession(hostEntry, {
      credentialCache,
      hostKeyStore,
      logger: { info() {}, warn() {}, error() {} },
    });

    await session.connect();
    terminalBridge = new RemoteTerminalBridge(session);
  });

  afterAll(async () => {
    if (terminalBridge) {
      terminalBridge.kill('test-term-1');
    }
    if (session) {
      await session.disconnect();
    }
    if (credentialCache) {
      credentialCache.clear();
    }
  });

  test('keypress to render latency ≤80ms on 50ms RTT link', async () => {
    const termId = 'test-term-1';

    await terminalBridge.create(termId, { cols: 80, rows: 24, cwd: '/tmp', shell: 'bash' });

    // Wait for shell prompt
    await new Promise((resolve) => setTimeout(resolve, 500));

    // Measure round-trip: send a character, wait for echo
    const dataPromise = new Promise((resolve) => {
      terminalBridge.once('data', (event) => {
        resolve(event);
      });
    });

    const start = Date.now();
    terminalBridge.write(termId, 'a');

    await dataPromise;
    const elapsed = Date.now() - start;

    expect(elapsed).toBeLessThanOrEqual(80);
  });

  test('terminal resize propagates within 200ms', async () => {
    const termId = 'test-term-1';

    // Send resize
    const start = Date.now();
    terminalBridge.resize(termId, 120, 40);

    // Verify by checking COLUMNS/LINES via tput
    const dataChunks = [];
    const dataHandler = (event) => {
      if (event.id === termId) {
        dataChunks.push(event.data);
      }
    };
    terminalBridge.on('data', dataHandler);

    terminalBridge.write(termId, 'tput cols\n');

    await new Promise((resolve) => setTimeout(resolve, 200));
    terminalBridge.off('data', dataHandler);

    const elapsed = Date.now() - start;
    const output = dataChunks.join('');

    // The resize should have propagated within 200ms
    expect(elapsed).toBeLessThanOrEqual(200);
    expect(output).toContain('120');
  });

  test('multiple rapid keypresses maintain ≤80ms average latency', async () => {
    const termId = 'test-term-1';
    const keypresses = 'hello';
    const latencies = [];

    for (const char of keypresses) {
      const dataPromise = new Promise((resolve) => {
        terminalBridge.once('data', () => resolve(Date.now()));
      });

      const sendTime = Date.now();
      terminalBridge.write(termId, char);

      const receiveTime = await dataPromise;
      latencies.push(receiveTime - sendTime);

      // Small delay between keypresses to avoid buffering
      await new Promise((resolve) => setTimeout(resolve, 20));
    }

    const avgLatency = latencies.reduce((a, b) => a + b, 0) / latencies.length;
    expect(avgLatency).toBeLessThanOrEqual(80);
  });
});
