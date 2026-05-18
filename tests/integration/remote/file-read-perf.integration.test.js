// Feature: remote-ssh, Integration Test: File bridge read performance
// Validates: Requirements 5.6, 6.2 — 1MB read ≤500ms, forward /health ≤2s

'use strict';

const path = require('path');
const fs = require('fs');
const crypto = require('crypto');

const DOCKER_COMPOSE_PATH = path.join(__dirname, 'docker-compose.yml');
const KEYS_DIR = path.join(__dirname, 'keys');
const SSHD_PORT = 2222;
const SSHD_HOST = '127.0.0.1';

// Modules under test
let RemoteSession;
let RemoteFileBridge;
let PortForwarder;
let CredentialCache;
let HostKeyStore;

describe.skip('File Bridge Performance (Docker environment required)', () => {
  let session;
  let fileBridge;
  let portForwarder;
  let credentialCache;
  let hostKeyStore;

  beforeAll(async () => {
    // Verify Docker Compose config exists
    if (!fs.existsSync(DOCKER_COMPOSE_PATH)) {
      throw new Error('docker-compose.yml not found. Run setup.sh first.');
    }

    const privateKeyPath = path.join(KEYS_DIR, 'id_ed25519');
    if (!fs.existsSync(privateKeyPath)) {
      throw new Error('Test keys not found. Run setup.sh to generate them.');
    }

    // Load modules under test
    RemoteSession = require('../../../electron/src/remote/remote-session');
    RemoteFileBridge = require('../../../electron/src/remote/remote-file-bridge');
    PortForwarder = require('../../../electron/src/remote/port-forwarder');
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

    fileBridge = new RemoteFileBridge(session, { pathSeparator: '/' });
    portForwarder = new PortForwarder(session);
  });

  afterAll(async () => {
    if (portForwarder) {
      await portForwarder.close();
    }
    if (session) {
      await session.disconnect();
    }
    if (credentialCache) {
      credentialCache.clear();
    }
  });

  test('1 MB file read completes within 500ms on 50ms RTT link', async () => {
    // Create a 1MB test file on the remote host
    const testPath = '/tmp/integration-test-1mb.bin';
    const oneMB = crypto.randomBytes(1024 * 1024);

    await fileBridge.write(testPath, oneMB);

    const start = Date.now();
    const content = await fileBridge.read(testPath, { encoding: null });
    const elapsed = Date.now() - start;

    expect(content.length).toBe(1024 * 1024);
    expect(elapsed).toBeLessThanOrEqual(500);
  });

  test('file write + read round-trip preserves content', async () => {
    const testPath = '/tmp/integration-test-roundtrip.txt';
    const testContent = 'Hello, remote world! 🌍\nLine 2\nLine 3\n';

    await fileBridge.write(testPath, testContent);
    const readBack = await fileBridge.read(testPath, { encoding: 'utf8' });

    expect(readBack).toBe(testContent);
  });

  test('port forward first /health response within 2 seconds', async () => {
    await portForwarder.open(8765);
    const localPort = portForwarder.localPort;

    const start = Date.now();
    const http = require('http');

    const response = await new Promise((resolve, reject) => {
      const req = http.get(`http://127.0.0.1:${localPort}/health`, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => resolve({ status: res.statusCode, body: data }));
      });
      req.on('error', reject);
      req.setTimeout(2000, () => { req.destroy(); reject(new Error('Timeout')); });
    });

    const elapsed = Date.now() - start;

    expect(response.status).toBe(200);
    expect(elapsed).toBeLessThanOrEqual(2000);
  });
});
