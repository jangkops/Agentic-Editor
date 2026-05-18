// Feature: remote-ssh, Integration Test: Remote AI Engine Provisioning
// Validates: Requirements 4.5, 4.8 — probe → /health 200 ≤120s

'use strict';

const path = require('path');
const fs = require('fs');

const DOCKER_COMPOSE_PATH = path.join(__dirname, 'docker-compose.yml');
const KEYS_DIR = path.join(__dirname, 'keys');
const SSHD_PORT = 2222;
const SSHD_HOST = '127.0.0.1';

// Modules under test
let RemoteSession;
let Provisioner;
let CredentialCache;
let HostKeyStore;

describe.skip('Provisioning E2E (Docker environment required)', () => {
  let session;
  let provisioner;
  let credentialCache;
  let hostKeyStore;

  beforeAll(async () => {
    // Verify Docker Compose config exists
    if (!fs.existsSync(DOCKER_COMPOSE_PATH)) {
      throw new Error('docker-compose.yml not found. Run setup.sh first.');
    }

    // Verify test keys exist
    const privateKeyPath = path.join(KEYS_DIR, 'id_ed25519');
    if (!fs.existsSync(privateKeyPath)) {
      throw new Error('Test keys not found. Run setup.sh to generate them.');
    }

    // Load modules under test
    RemoteSession = require('../../../electron/src/remote/remote-session');
    Provisioner = require('../../../electron/src/remote/provisioner');
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

    provisioner = new Provisioner(session, {
      localAiEngineRoot: path.resolve(__dirname, '../../../ai_engine'),
      schemaVersion: 1,
    });
  });

  afterAll(async () => {
    if (session) {
      await session.disconnect();
    }
    if (credentialCache) {
      credentialCache.clear();
    }
  });

  test('probe detects no existing ai_engine on fresh container', async () => {
    const healthy = await provisioner.probe();
    // Fresh container should not have ai_engine running
    expect(healthy).toBe(false);
  });

  test('ensureProvisioned completes within 120 seconds', async () => {
    const start = Date.now();
    await provisioner.ensureProvisioned();
    const elapsed = Date.now() - start;

    expect(elapsed).toBeLessThanOrEqual(120000);
  }, 130000); // Jest timeout slightly above 120s

  test('/health returns 200 after provisioning', async () => {
    const healthy = await provisioner.probe();
    expect(healthy).toBe(true);
  });

  test('supervisor restarts ai_engine after kill', async () => {
    // Kill the server process
    await session.exec('kill $(cat ~/.agentic-editor/server.pid)', {});

    // Wait for supervisor to restart (sleep 2 in supervisor loop + startup)
    await new Promise((resolve) => setTimeout(resolve, 5000));

    const healthy = await provisioner.probe();
    expect(healthy).toBe(true);
  }, 15000);

  test('second ensureProvisioned skips upload when version matches', async () => {
    const start = Date.now();
    await provisioner.ensureProvisioned();
    const elapsed = Date.now() - start;

    // Should be fast since upload is skipped
    expect(elapsed).toBeLessThan(10000);
  });
});
