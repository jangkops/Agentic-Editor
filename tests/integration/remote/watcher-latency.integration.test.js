// Feature: remote-ssh, Integration Test: File watcher latency
// Validates: Requirement 6.7 — change notification ≤1s

'use strict';

const path = require('path');
const fs = require('fs');

const DOCKER_COMPOSE_PATH = path.join(__dirname, 'docker-compose.yml');
const KEYS_DIR = path.join(__dirname, 'keys');
const SSHD_PORT = 2222;
const SSHD_HOST = '127.0.0.1';

// Modules under test
let RemoteSession;
let RemoteFileBridge;
let CredentialCache;
let HostKeyStore;

describe.skip('Watcher Latency (Docker environment required)', () => {
  let session;
  let fileBridge;
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
    RemoteFileBridge = require('../../../electron/src/remote/remote-file-bridge');
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
  });

  afterAll(async () => {
    if (fileBridge) {
      fileBridge.stopWatch('/tmp/watch-test');
    }
    if (session) {
      await session.disconnect();
    }
    if (credentialCache) {
      credentialCache.clear();
    }
  });

  test('active directory change notification arrives within 1 second', async () => {
    const watchDir = '/tmp/watch-test';

    // Create the watch directory
    await session.exec(`mkdir -p ${watchDir}`, {});

    // Start watching (active = 500ms polling)
    fileBridge.startWatch(watchDir);

    // Wait for watcher to initialize
    await new Promise((resolve) => setTimeout(resolve, 600));

    // Listen for change event
    const changePromise = new Promise((resolve) => {
      fileBridge.once('change', (event) => {
        resolve(event);
      });
    });

    // Create a file on the remote host
    const start = Date.now();
    await session.exec(`touch ${watchDir}/new-file.txt`, {});

    const event = await changePromise;
    const elapsed = Date.now() - start;

    expect(event.kind).toBe('created');
    expect(event.remotePath).toContain('new-file.txt');
    expect(elapsed).toBeLessThanOrEqual(1000);
  });

  test('file modification detected within 1 second', async () => {
    const watchDir = '/tmp/watch-test';
    const testFile = `${watchDir}/modify-test.txt`;

    // Create initial file
    await session.exec(`echo "initial" > ${testFile}`, {});
    await new Promise((resolve) => setTimeout(resolve, 600));

    const changePromise = new Promise((resolve) => {
      fileBridge.once('change', (event) => {
        resolve(event);
      });
    });

    const start = Date.now();
    await session.exec(`echo "modified" > ${testFile}`, {});

    const event = await changePromise;
    const elapsed = Date.now() - start;

    expect(event.kind).toBe('modified');
    expect(elapsed).toBeLessThanOrEqual(1000);
  });

  test('file deletion detected within 1 second', async () => {
    const watchDir = '/tmp/watch-test';
    const testFile = `${watchDir}/delete-test.txt`;

    // Create file first
    await session.exec(`echo "to-delete" > ${testFile}`, {});
    await new Promise((resolve) => setTimeout(resolve, 600));

    const changePromise = new Promise((resolve) => {
      fileBridge.once('change', (event) => {
        resolve(event);
      });
    });

    const start = Date.now();
    await session.exec(`rm ${testFile}`, {});

    const event = await changePromise;
    const elapsed = Date.now() - start;

    expect(event.kind).toBe('deleted');
    expect(elapsed).toBeLessThanOrEqual(1000);
  });
});
