// Feature: remote-ssh, Integration Test: SSH handshake performance
// Validates: Requirement 3.10 — SSH handshake ≤10s at 50ms RTT

'use strict';

const path = require('path');
const fs = require('fs');

const DOCKER_COMPOSE_PATH = path.join(__dirname, 'docker-compose.yml');
const KEYS_DIR = path.join(__dirname, 'keys');
const SSHD_PORT = 2222;
const SSHD_HOST = '127.0.0.1';

// Modules under test
let RemoteSession;
let sshConfigParser;
let credentialCache;
let hostKeyStore;

describe.skip('SSH Handshake Performance (Docker environment required)', () => {
  let session;

  beforeAll(() => {
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
    sshConfigParser = require('../../../electron/src/remote/ssh-config-parser');
    const CredentialCache = require('../../../electron/src/remote/credential-cache');
    const HostKeyStore = require('../../../electron/src/remote/host-key-store');

    credentialCache = new CredentialCache();
    hostKeyStore = new HostKeyStore({ storePath: path.join(__dirname, 'keys', 'known_hosts_test') });
  });

  afterAll(async () => {
    if (session) {
      await session.disconnect();
    }
    credentialCache.clear();
  });

  test('SSH handshake completes within 10 seconds at 50ms RTT', async () => {
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

    const start = Date.now();
    await session.connect();
    const elapsed = Date.now() - start;

    expect(session.state).toBe('connected');
    expect(elapsed).toBeLessThanOrEqual(10000);
  });

  test('SSH handshake via bastion (ProxyJump) completes within 10 seconds', async () => {
    const hostEntry = {
      alias: 'test-sshd-via-bastion',
      hostName: SSHD_HOST,
      user: 'testuser',
      port: SSHD_PORT,
      identityFiles: [path.join(KEYS_DIR, 'id_ed25519')],
      proxyJump: ['testuser@127.0.0.1:2223'],
      strictHostKeyChecking: 'no',
      sourcePaths: [],
      isWildcardOnly: false,
    };

    const bastionSession = new RemoteSession(hostEntry, {
      credentialCache,
      hostKeyStore,
      logger: { info() {}, warn() {}, error() {} },
    });

    const start = Date.now();
    await bastionSession.connect();
    const elapsed = Date.now() - start;

    expect(bastionSession.state).toBe('connected');
    expect(elapsed).toBeLessThanOrEqual(10000);

    await bastionSession.disconnect();
  });
});
