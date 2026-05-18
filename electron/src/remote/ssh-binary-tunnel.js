'use strict';
/**
 * SSH Binary Tunnel — fallback to OS `ssh` CLI for complex configs.
 *
 * Use case: ssh2 pure-JS client cannot handle:
 *   - ProxyCommand (e.g. `aws ssm start-session --target ...`)
 *   - ProxyJump chains (limited support)
 *   - Custom auth helpers
 *
 * Solution: spawn `ssh -N -L <localPort>:<remote>:22 <alias>` which uses
 * the user's native OpenSSH client (and all of ~/.ssh/config including
 * ProxyCommand / ProxyJump / SSM Session Manager). Then ssh2 can connect
 * to `127.0.0.1:<localPort>` as a regular TCP endpoint.
 *
 * This mirrors VS Code Remote-SSH's approach: it also shells out to the
 * OS `ssh` binary to leverage the full OpenSSH feature set.
 */

const { spawn } = require('child_process');
const net = require('net');
const os = require('os');
const path = require('path');
const { EventEmitter } = require('events');

const logger = require('./logger');

/**
 * Check if an alias needs the binary fallback (ProxyCommand or ProxyJump present).
 * @param {Object} hostEntry Parsed HostEntry from ssh-config-parser.
 * @returns {boolean}
 */
function needsBinaryFallback(hostEntry) {
  if (!hostEntry) return false;
  if (hostEntry.proxyCommand) return true;
  if (Array.isArray(hostEntry.proxyJump) && hostEntry.proxyJump.length > 0) return true;
  return false;
}

/**
 * Spawn `ssh -N -o ExitOnForwardFailure=yes -L <localPort>:127.0.0.1:22 <alias>`
 * which establishes the tunnel using the user's native OpenSSH + config.
 *
 * Resolves when the local port is actually accepting connections (proved
 * by a successful `net.connect()` probe).
 *
 * @param {{alias: string, localPort: number, signal?: AbortSignal}} opts
 * @returns {Promise<{child: import('child_process').ChildProcess, close: () => void}>}
 */
function spawnSshTunnel(opts) {
  const { alias, localPort } = opts;
  if (!alias || !Number.isInteger(localPort)) {
    throw new TypeError('spawnSshTunnel: alias and localPort required');
  }

  const sshArgs = [
    '-N',                              // no remote command
    '-o', 'ExitOnForwardFailure=yes',  // fail fast on forward bind error
    '-o', 'ServerAliveInterval=30',    // keepalive
    '-o', 'ServerAliveCountMax=3',
    '-o', 'ConnectTimeout=15',
    '-o', 'BatchMode=no',              // allow interactive prompts
    '-o', 'StrictHostKeyChecking=accept-new',
    '-L', `127.0.0.1:${localPort}:127.0.0.1:22`,
    alias,
  ];

  const env = { ...process.env, LC_ALL: 'C.UTF-8', LANG: 'C.UTF-8' };
  // macOS: ensure brew/aws paths are present (for ProxyCommand: aws ssm ...).
  if (process.platform === 'darwin') {
    const extra = '/opt/homebrew/bin:/usr/local/bin';
    env.PATH = env.PATH ? `${extra}:${env.PATH}` : extra;
  }

  const child = spawn('ssh', sshArgs, { env, stdio: ['ignore', 'pipe', 'pipe'] });
  const stderrChunks = [];
  child.stdout.on('data', (d) => logger.info('ssh-tunnel-stdout', { alias, line: d.toString('utf8').trim().slice(0, 300) }));
  child.stderr.on('data', (d) => {
    const txt = d.toString('utf8');
    stderrChunks.push(txt);
    logger.info('ssh-tunnel-stderr', { alias, line: txt.trim().slice(0, 300) });
  });

  let resolved = false;
  return new Promise((resolve, reject) => {
    const onExit = (code) => {
      if (resolved) return;
      const stderr = stderrChunks.join('').slice(-1000);
      reject(Object.assign(new Error(
        `ssh tunnel exited (code ${code}) before ready.\n${stderr || '(no stderr)'}`
      ), { code: 'ssh-tunnel-failed', exitCode: code, stderr }));
    };
    const onError = (err) => {
      if (resolved) return;
      reject(Object.assign(err, { code: 'ssh-tunnel-spawn-failed' }));
    };
    child.once('exit', onExit);
    child.once('error', onError);

    // Probe the local port until it accepts connections, or give up after ~20s.
    const deadline = Date.now() + 20000;
    const probe = () => {
      if (resolved) return;
      if (Date.now() > deadline) {
        resolved = true;
        try { child.kill('SIGTERM'); } catch {}
        const stderr = stderrChunks.join('').slice(-1000);
        reject(Object.assign(new Error(
          `ssh tunnel not ready within 20s.\n${stderr || '(no stderr)'}`
        ), { code: 'ssh-tunnel-timeout', stderr }));
        return;
      }
      const probeSock = net.connect({ host: '127.0.0.1', port: localPort }, () => {
        if (resolved) { probeSock.destroy(); return; }
        resolved = true;
        probeSock.end();
        child.removeListener('exit', onExit);
        child.removeListener('error', onError);
        const close = () => {
          try { child.kill('SIGTERM'); } catch {}
        };
        // Future crashes should reject outstanding work — expose as 'tunnel-down' event.
        const emitter = new EventEmitter();
        child.on('exit', (c) => emitter.emit('tunnel-down', { exitCode: c }));
        resolve({ child, close, emitter });
      });
      probeSock.on('error', () => {
        probeSock.destroy();
        setTimeout(probe, 500);
      });
    };
    // Wait 500ms before first probe so ssh has time to bind the port.
    setTimeout(probe, 500);
  });
}

module.exports = {
  needsBinaryFallback,
  spawnSshTunnel,
};
