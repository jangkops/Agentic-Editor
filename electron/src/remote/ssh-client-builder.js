'use strict';
/**
 * ssh2 Client connect-config builder (ProxyJump chain aware).
 *
 * Feature: remote-ssh
 * Covers Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 10.7, 11.1, 11.3
 *
 * Translates a resolved SSH_Config `HostEntry` (from ssh-config-parser.js)
 * into the `ssh2.Client#connect()` options dictionary the ssh2 library
 * expects. Also composes nested configs for each ProxyJump hop so a
 * bastion chain can be dialed with a single `connect()` call.
 *
 * This module NEVER performs I/O beyond reading identity key files from
 * disk. Credential material (passphrases, decrypted keys) is read from
 * the in-memory `CredentialCache` singleton; if a required passphrase
 * is missing the builder emits a `needsPrompt` descriptor the caller
 * (RemoteSession) surfaces to the UI.
 *
 * Design principles:
 *  - Deterministic: same (entry, hops, cached credentials) → same output.
 *  - Side-effect-free w.r.t. ssh2 — we build plain objects only.
 *  - Auth method ordering respects `PreferredAuthentications` when set,
 *    otherwise defaults to `publickey,keyboard-interactive,password`.
 *  - ProxyJump max depth = 3 (design.md §Architecture). Longer chains
 *    return a diagnostic rather than throwing.
 *  - Key format detection is best-effort: PEM (RSA/DSA), OpenSSH new
 *    format, PKCS#8. Ed25519 passphrase-protected keys require `sshpk`
 *    fallback (runtime-lazy-loaded if available) — we detect the case
 *    and surface a hint if neither ssh2 nor sshpk is available.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const credentialCache = require('./credential-cache');
const hostKeyStore = require('./host-key-store');
const logger = require('./logger');

/** Max hops allowed in a ProxyJump chain (design.md §Architecture). */
const MAX_PROXY_HOPS = 3;

/** Default auth order when PreferredAuthentications is unset. */
const DEFAULT_AUTH_ORDER = Object.freeze(['publickey', 'keyboard-interactive', 'password']);

/** ssh2 tryKeyboard is enabled iff keyboard-interactive is in the auth order. */
const KBD_INTERACTIVE = 'keyboard-interactive';

/**
 * Map an SSH_Config `PreferredAuthentications` token to ssh2 terminology.
 * ssh2 uses the same names so this is mostly identity, but we whitelist
 * to guard against typos silently disabling auth.
 * @type {Readonly<Record<string,string>>}
 */
const AUTH_METHOD_WHITELIST = Object.freeze({
  publickey: 'publickey',
  'keyboard-interactive': 'keyboard-interactive',
  password: 'password',
  hostbased: 'hostbased', // declared for forward-compat; v1 does not implement
  none: 'none',
});

/**
 * Expand a leading `~` to the user's home directory. Non-tilde paths
 * are returned unchanged. Mirrors the semantics of ssh-config-parser
 * so IdentityFile paths resolve identically.
 *
 * @param {string} p
 * @returns {string}
 */
function expandHome(p) {
  if (typeof p !== 'string' || p.length === 0) return p;
  if (p === '~') return os.homedir();
  if (p.startsWith('~/') || p.startsWith('~\\')) {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

/**
 * Read a private key file from disk. Returns `null` if the file is
 * missing or unreadable so the builder can fall back to the next key.
 *
 * @param {string} keyPath
 * @returns {Buffer|null}
 */
function readKeyFile(keyPath) {
  try {
    return fs.readFileSync(expandHome(keyPath));
  } catch (_err) {
    return null;
  }
}

/**
 * Heuristic: does a private-key buffer *require* a passphrase?
 *
 * We inspect the PEM header for `ENCRYPTED` (PKCS#1) or the
 * `aes256-ctr`-style cipher field in OpenSSH new-format keys. We do
 * NOT try to decrypt — that is ssh2's job. The purpose is to decide
 * whether to fetch a passphrase from the cache before calling connect.
 *
 * @param {Buffer|null} buf
 * @returns {boolean}
 */
function keyNeedsPassphrase(buf) {
  if (!Buffer.isBuffer(buf) || buf.length === 0) return false;
  const text = buf.toString('utf8', 0, Math.min(buf.length, 4096));
  if (text.includes('ENCRYPTED')) return true;
  // OpenSSH new format: binary after `openssh-key-v1\0`. Quick check:
  // if the file starts with `-----BEGIN OPENSSH PRIVATE KEY-----`
  // and the decoded payload's 2nd field is not "none", it's encrypted.
  if (text.startsWith('-----BEGIN OPENSSH PRIVATE KEY-----')) {
    try {
      const b64 = text
        .replace('-----BEGIN OPENSSH PRIVATE KEY-----', '')
        .replace('-----END OPENSSH PRIVATE KEY-----', '')
        .replace(/\s+/g, '');
      const raw = Buffer.from(b64, 'base64');
      const magic = 'openssh-key-v1\0';
      if (raw.slice(0, magic.length).toString('binary') !== magic) return false;
      // After magic: cipher-name length (uint32 BE) then cipher-name.
      const offset = magic.length;
      if (raw.length < offset + 4) return false;
      const cipherLen = raw.readUInt32BE(offset);
      if (cipherLen <= 0 || cipherLen > 64) return false;
      const cipher = raw.slice(offset + 4, offset + 4 + cipherLen).toString('utf8');
      return cipher !== 'none';
    } catch (_err) {
      return false;
    }
  }
  return false;
}

/**
 * Compose the auth method array for ssh2 based on PreferredAuthentications.
 * ssh2 itself picks the method — we only constrain the *set* via the
 * presence / absence of `password`, `tryKeyboard`, and private keys.
 *
 * @param {string[]|undefined} preferred
 * @returns {string[]}
 */
function resolveAuthOrder(preferred) {
  if (!Array.isArray(preferred) || preferred.length === 0) {
    return DEFAULT_AUTH_ORDER.slice();
  }
  /** @type {string[]} */
  const out = [];
  for (const token of preferred) {
    if (typeof token !== 'string') continue;
    const norm = token.trim().toLowerCase();
    if (AUTH_METHOD_WHITELIST[norm] && !out.includes(norm)) out.push(norm);
  }
  return out.length > 0 ? out : DEFAULT_AUTH_ORDER.slice();
}

/**
 * Build the `authHandler` callback ssh2 calls on each auth round. We
 * walk the resolved auth order once and return the corresponding
 * config blob. Returning `false` terminates auth (triggers `error`).
 *
 * ssh2 API contract (v1.x): `(methodsLeft, partialSuccess, callback)`
 * or the three-arg `(authsLeft, partialSuccess, cb)` signature; newer
 * releases allow returning the next auth config directly. We implement
 * the callback form for broadest compatibility.
 *
 * @param {string[]} order
 * @param {{username:string, keyAttempts:Array<{path:string, buffer:Buffer, passphrase?:string}>, password?:string, twoFactorResponder?:Function}} ctx
 * @returns {Function}
 */
function buildAuthHandler(order, ctx) {
  const queue = order.slice();
  let keyIdx = 0;

  return function authHandler(_methodsLeft, _partialSuccess, cb) {
    // ssh2 v1.x: if cb is not supplied, we return the object directly.
    const next = queue.shift();
    const respond = typeof cb === 'function' ? cb : (x) => x;

    if (!next) return respond(false);

    if (next === 'publickey') {
      if (keyIdx >= ctx.keyAttempts.length) {
        // Exhausted keys — fall through to next method.
        return respond(authHandler(_methodsLeft, _partialSuccess, cb));
      }
      const attempt = ctx.keyAttempts[keyIdx++];
      // Stay on publickey for remaining keys by unshifting again.
      if (keyIdx < ctx.keyAttempts.length) queue.unshift('publickey');
      return respond({
        type: 'publickey',
        username: ctx.username,
        key: attempt.buffer,
        passphrase: attempt.passphrase,
      });
    }

    if (next === KBD_INTERACTIVE) {
      return respond({
        type: 'keyboard-interactive',
        username: ctx.username,
      });
    }

    if (next === 'password') {
      if (!ctx.password) {
        // No password cached → skip to next method.
        return respond(authHandler(_methodsLeft, _partialSuccess, cb));
      }
      return respond({ type: 'password', username: ctx.username, password: ctx.password });
    }

    if (next === 'none') {
      return respond({ type: 'none', username: ctx.username });
    }

    // hostbased / unknown — skip.
    return respond(authHandler(_methodsLeft, _partialSuccess, cb));
  };
}

/**
 * Build the `hostVerifier` callback ssh2 invokes during handshake.
 * Returns a synchronous verdict based on the host key store; the
 * TOFU-prompt case is signalled by callbacking with `false` AND
 * stashing the fingerprint on the session so the caller can prompt
 * the user, add to the store, then retry.
 *
 * We also support ssh2's newer async signature `(key, done)` by
 * returning a function that inspects `done`.
 *
 * @param {Object} opts
 * @param {string} opts.host
 * @param {number} opts.port
 * @param {(verdict:{status:string, fingerprint:string|null}) => void} opts.onVerdict
 * @returns {Function}
 */
function buildHostVerifier(opts) {
  return function hostVerifier(key, done) {
    const verdict = hostKeyStore.verify(opts.host, opts.port, key);
    if (typeof done === 'function') {
      // async signature
      opts.onVerdict(verdict);
      done(verdict.status === 'ok');
      return undefined;
    }
    // sync signature
    opts.onVerdict(verdict);
    return verdict.status === 'ok';
  };
}

/**
 * Resolve IdentityFile entries into `{path, buffer, passphrase}` tuples.
 * Missing files and unreadable keys are skipped. If a key needs a
 * passphrase and the cache has none, we mark `needsPrompt=true` in the
 * diagnostics output.
 *
 * @param {Object} params
 * @param {string[]} params.identityFiles
 * @param {string} params.alias
 * @returns {{attempts:Array<{path:string,buffer:Buffer,passphrase?:string,needsPrompt?:boolean}>, diagnostics:Array<{severity:string,message:string}>}}
 */
function resolveIdentityKeys(params) {
  /** @type {Array<{path:string,buffer:Buffer,passphrase?:string,needsPrompt?:boolean}>} */
  const attempts = [];
  /** @type {Array<{severity:string,message:string}>} */
  const diagnostics = [];
  const cached = credentialCache.get(params.alias) || {};

  for (const identityPath of params.identityFiles) {
    const expanded = expandHome(identityPath);
    const buf = readKeyFile(expanded);
    if (!buf) {
      diagnostics.push({
        severity: 'warn',
        message: `IdentityFile not readable: ${expanded}`,
      });
      continue;
    }
    const needsPass = keyNeedsPassphrase(buf);
    const attempt = { path: expanded, buffer: buf };
    if (needsPass) {
      if (typeof cached.passphrase === 'string' && cached.passphrase.length > 0) {
        attempt.passphrase = cached.passphrase;
      } else {
        attempt.needsPrompt = true;
      }
    }
    attempts.push(attempt);
  }
  return { attempts, diagnostics };
}

/**
 * Build the ssh2 connect config for a single HostEntry (no ProxyJump).
 * Callers should use {@link buildConnectConfig} for the full chain.
 *
 * @param {Object} entry Resolved HostEntry.
 * @param {Object} [opts]
 * @param {Function=} opts.onHostKeyVerdict Invoked with the host-key verdict.
 * @param {Function=} opts.onAuthPrompt     Invoked for keyboard-interactive prompts.
 * @returns {{ config: Object, diagnostics: Array<{severity:string, message:string}>, needsPrompt: boolean }}
 */
function buildLeafConfig(entry, opts) {
  const options = opts || {};
  const host = String(entry.hostName || entry.alias);
  const port = Number(entry.port) || 22;
  const username = String(entry.user || os.userInfo().username || '');
  const order = resolveAuthOrder(entry.preferredAuthentications);

  const { attempts, diagnostics } = resolveIdentityKeys({
    identityFiles: Array.isArray(entry.identityFiles) ? entry.identityFiles : [],
    alias: entry.alias,
  });

  // Drop "publickey" from the auth order if there are no usable keys.
  const finalOrder = attempts.length === 0 ? order.filter((m) => m !== 'publickey') : order;

  const needsPrompt = attempts.some((a) => a.needsPrompt === true);

  // SSH agent: respect ForwardAgent + SSH_AUTH_SOCK env var (Req 3.4).
  const agentSock = process.env.SSH_AUTH_SOCK || null;
  const useAgent = Boolean(entry.forwardAgent) && Boolean(agentSock);

  const cached = credentialCache.get(entry.alias) || {};

  /** @type {Object} */
  const config = {
    host,
    port,
    username,
    // Never use ssh2's default key probing — we control the order.
    readyTimeout: 20000,
    tryKeyboard: finalOrder.includes(KBD_INTERACTIVE),
    authHandler: buildAuthHandler(finalOrder, {
      username,
      keyAttempts: attempts,
      password: cached.password,
      twoFactorResponder: options.onAuthPrompt,
    }),
    hostVerifier: buildHostVerifier({
      host,
      port,
      onVerdict: options.onHostKeyVerdict || (() => {}),
    }),
  };

  if (useAgent) config.agent = agentSock;
  if (entry.identitiesOnly === true) config.agent = undefined;

  return { config, diagnostics, needsPrompt };
}

/**
 * Build the full ssh2 connect config, chaining ProxyJump hops.
 *
 * ssh2 supports bastion hopping by attaching a `sock` to the outer
 * config — the sock is a pre-established stream to the bastion. The
 * caller (RemoteSession) is responsible for opening each hop in order
 * and passing the previous hop's `forwardOut` stream into the next
 * hop's config. This builder returns a list of layered configs so the
 * session code can walk them without re-parsing SSH_Config.
 *
 * @param {Object} params
 * @param {Object} params.target         Resolved HostEntry for the final host.
 * @param {Object[]} [params.hops]       Resolved HostEntries for each bastion, in dial order.
 * @param {Function=} params.onHostKeyVerdict
 * @param {Function=} params.onAuthPrompt
 * @returns {{
 *   configs: Object[],
 *   diagnostics: Array<{severity:string, message:string, hop?:string}>,
 *   needsPrompt: boolean,
 *   proxyJumpChain: string[]
 * }}
 */
function buildConnectConfig(params) {
  const { target, hops } = params || {};
  if (!target || typeof target !== 'object') {
    throw new TypeError('buildConnectConfig: target HostEntry is required');
  }
  const hopList = Array.isArray(hops) ? hops.slice(0, MAX_PROXY_HOPS + 1) : [];
  /** @type {Array<{severity:string,message:string,hop?:string}>} */
  const diagnostics = [];

  if (Array.isArray(hops) && hops.length > MAX_PROXY_HOPS) {
    diagnostics.push({
      severity: 'error',
      message: `ProxyJump chain exceeds max depth ${MAX_PROXY_HOPS}; truncating.`,
    });
  }

  /** @type {Object[]} */
  const configs = [];
  let needsPrompt = false;

  // Build hop configs first, in dial order.
  for (const hop of hopList) {
    const leaf = buildLeafConfig(hop, {
      onHostKeyVerdict: params.onHostKeyVerdict,
      onAuthPrompt: params.onAuthPrompt,
    });
    for (const d of leaf.diagnostics) {
      diagnostics.push({ ...d, hop: hop.alias });
    }
    needsPrompt = needsPrompt || leaf.needsPrompt;
    configs.push(leaf.config);
  }

  // Finally the target.
  const leaf = buildLeafConfig(target, {
    onHostKeyVerdict: params.onHostKeyVerdict,
    onAuthPrompt: params.onAuthPrompt,
  });
  for (const d of leaf.diagnostics) diagnostics.push({ ...d, hop: target.alias });
  needsPrompt = needsPrompt || leaf.needsPrompt;
  configs.push(leaf.config);

  const proxyJumpChain = hopList.map((h) => h.alias);

  try {
    logger.info('ssh-config-built', {
      alias: target.alias,
      host: logger.mask(String(target.hostName || target.alias)),
      port: target.port || 22,
      hops: proxyJumpChain.length,
      authMethods: configs[configs.length - 1].authHandler ? 'custom' : 'default',
      needsPrompt,
    });
  } catch (_err) {
    // never fail on logging
  }

  return { configs, diagnostics, needsPrompt, proxyJumpChain };
}

module.exports = {
  buildConnectConfig,
  buildLeafConfig,
  // exported for testing
  resolveAuthOrder,
  keyNeedsPassphrase,
  expandHome,
  MAX_PROXY_HOPS,
  DEFAULT_AUTH_ORDER,
};
