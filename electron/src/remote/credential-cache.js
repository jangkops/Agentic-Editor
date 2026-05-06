'use strict';
/**
 * Credential Cache — in-memory only
 *
 * Feature: remote-ssh
 * Covers Requirements: 10.1, 10.2
 *
 * Stores SSH passphrases, decrypted private key material, and one-time
 * 2FA responses for the lifetime of the Electron main process ONLY.
 *
 * Design principles:
 *  - Memory-only. Never writes to disk, IPC, or any external sink.
 *  - Storage is a plain `Map` kept in a private field. The cache instance
 *    refuses to be serialized — `toJSON()` returns `undefined` (stripping
 *    the instance from `JSON.stringify` output) and `Symbol.toPrimitive`
 *    throws so that accidental string/number coercion (common in log
 *    interpolation) cannot leak secrets.
 *  - `set()` strips unknown keys from the credential object so that future
 *    callers adding fields do not accidentally widen the persisted shape.
 *    The stored value references the sanitized object directly (no deep
 *    copy of secret buffers — callers retain ownership).
 *  - `clear()` is called on process exit, SIGINT, SIGTERM, and the explicit
 *    "Clear cached credentials" command so that credentials never outlive
 *    the Electron process that loaded them.
 *
 * Intentional non-goals (see design.md):
 *  - No TTL or eviction. Entries live until `clear()` or process exit.
 *  - No notification when entries change. Call sites that need to react
 *    to credential updates should coordinate through explicit IPC events.
 *
 * @typedef {Object} Credential
 * @property {string=} passphrase     SSH key passphrase (plain text; never logged).
 * @property {string|Buffer=} privateKey  Decrypted private key material.
 * @property {string=} twoFactor      One-time 2FA response captured from the user.
 */

/**
 * Fields that callers may store inside a Credential. Anything else is
 * silently discarded by `set()` so the cache surface stays minimal.
 */
const ALLOWED_CREDENTIAL_FIELDS = Object.freeze(['passphrase', 'privateKey', 'twoFactor']);

/**
 * Return a new object containing only the allowed credential fields from
 * the input, preserving original values by reference. Values that are
 * `undefined` are omitted entirely so a round-tripped credential does not
 * accumulate empty keys.
 *
 * @param {Credential} credential
 * @returns {Credential}
 */
function sanitize(credential) {
  /** @type {Credential} */
  const out = {};
  for (const key of ALLOWED_CREDENTIAL_FIELDS) {
    const value = credential[key];
    if (value !== undefined) {
      out[key] = value;
    }
  }
  return out;
}

/**
 * In-memory credential cache scoped to a single Electron main process.
 */
class CredentialCache {
  constructor() {
    /**
     * @private
     * @type {Map<string, Credential>}
     */
    this._store = new Map();
  }

  /**
   * Fetch the cached credential for a host alias.
   *
   * @param {string} alias
   * @returns {Credential|null}
   */
  get(alias) {
    if (typeof alias !== 'string' || alias.length === 0) return null;
    const hit = this._store.get(alias);
    return hit === undefined ? null : hit;
  }

  /**
   * Store or replace the credential for a host alias.
   *
   * Unknown fields in `credential` are discarded. Passing a non-object
   * value is a no-op so that defensive callers never throw here.
   *
   * @param {string} alias
   * @param {Credential} credential
   * @returns {void}
   */
  set(alias, credential) {
    if (typeof alias !== 'string' || alias.length === 0) return;
    if (credential === null || typeof credential !== 'object') return;
    this._store.set(alias, sanitize(credential));
  }

  /**
   * Clear cached credentials. With no argument, wipe every entry.
   * With an `alias`, clear only that alias.
   *
   * @param {string} [alias]
   * @returns {void}
   */
  clear(alias) {
    if (alias === undefined) {
      this._store.clear();
      return;
    }
    if (typeof alias !== 'string' || alias.length === 0) return;
    this._store.delete(alias);
  }

  /**
   * Number of cached aliases. Exposed for diagnostics and tests only —
   * callers MUST NOT log individual credential values.
   *
   * @returns {number}
   */
  size() {
    return this._store.size;
  }

  /**
   * Serialization blocker. `JSON.stringify` skips keys whose value's
   * `toJSON` returns `undefined`, so a CredentialCache instance embedded
   * in another object disappears from the output rather than exposing
   * its secret store.
   *
   * @returns {undefined}
   */
  toJSON() {
    return undefined;
  }

  /**
   * Coercion blocker. Any attempt to convert the cache to a primitive
   * (template literal, `String(cache)`, `+cache`) throws loudly so a
   * forgotten `console.log(cache)` or log interpolation cannot silently
   * leak the backing store via a default `[object Object]` print.
   *
   * @param {string} hint
   */
  [Symbol.toPrimitive](hint) {
    throw new Error(
      `CredentialCache cannot be converted to a primitive (hint=${hint}); `
      + 'access individual fields explicitly.'
    );
  }
}

/**
 * Process-wide singleton. Every import returns the same instance so that
 * the cache lifetime is bound to the Electron main process. Tests that
 * need isolation can instantiate the class directly.
 */
const instance = new CredentialCache();

/**
 * Register best-effort wipe hooks. `process.on('exit')` fires
 * synchronously during process teardown; SIGINT/SIGTERM fire before
 * Node tears down. We call `clear()` in each and do not re-throw so
 * the cleanup never blocks shutdown.
 */
function registerCleanupHandlers() {
  if (typeof process === 'undefined' || typeof process.on !== 'function') return;

  const wipe = () => {
    try {
      instance.clear();
    } catch (_err) {
      // Never throw during shutdown.
    }
  };

  process.on('exit', wipe);
  process.on('SIGINT', wipe);
  process.on('SIGTERM', wipe);
}

registerCleanupHandlers();

module.exports = instance;
module.exports.CredentialCache = CredentialCache;
module.exports.ALLOWED_CREDENTIAL_FIELDS = ALLOWED_CREDENTIAL_FIELDS;
