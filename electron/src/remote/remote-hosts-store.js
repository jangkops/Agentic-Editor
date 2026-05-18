'use strict';
/**
 * RemoteHostsStore — per-host preferences for the remote-ssh feature.
 *
 * Feature: remote-ssh · Task 29.1
 * Requirements: 2.5 (ad-hoc hosts), 10.5 (never persist key material),
 *               13.3 (resilient to malformed/crash-truncated files)
 *
 * Wraps `userData/settings/remote-hosts.json` with a small, typed CRUD
 * API. All disk I/O goes through DataStore.loadRemoteHosts() /
 * saveRemoteHosts() so atomicity (write-to-tmp + rename) and
 * malformed-JSON recovery live in one place.
 *
 * Schema (design.md §Remote_Host Persistence Schema):
 *   {
 *     schemaVersion: 1,
 *     hosts: {
 *       [alias]: {
 *         favorite:            boolean,
 *         lastWorkspace:       string,
 *         remotePortOverride:  number | null,
 *         provisioningMode:    'auto' | 'manual',
 *         source:              'ssh-config' | 'ad-hoc',
 *         adHoc:               null | {hostName, user, port, identityFile}
 *       }
 *     }
 *   }
 *
 * Security rules (Req 10.5):
 *   - identityFile stores a FILESYSTEM PATH only
 *   - privateKey / passphrase / password MUST NEVER be written here
 *   - addAdHoc() strips any such fields defensively before persisting
 */

const SCHEMA_VERSION = 1;

const VALID_PROVISIONING_MODES = new Set(['auto', 'manual']);
const VALID_SOURCES = new Set(['ssh-config', 'ad-hoc']);

// Fields that must never be persisted to remote-hosts.json (Req 10.5).
// Enforced by _sanitizeAdHoc() — belt-and-suspenders against a caller
// accidentally forwarding a form payload that still contains secrets.
const FORBIDDEN_ADHOC_FIELDS = Object.freeze([
  'privateKey', 'passphrase', 'password',
  'privatekey', 'pass_phrase', 'pass-phrase',
]);

function _defaultEntry(source) {
  return {
    favorite: false,
    lastWorkspace: '',
    remotePortOverride: null,
    provisioningMode: 'auto',
    source: source || 'ssh-config',
    adHoc: null,
  };
}

function _mergeEntry(existing, patch) {
  const base = existing && typeof existing === 'object'
    ? { ..._defaultEntry(existing.source), ...existing }
    : _defaultEntry();
  return { ...base, ...patch };
}

class RemoteHostsStore {
  /**
   * @param {object} dataStore - DataStore instance (must expose
   *   loadRemoteHosts() and saveRemoteHosts(data)).
   */
  constructor(dataStore) {
    if (!dataStore || typeof dataStore.loadRemoteHosts !== 'function' ||
        typeof dataStore.saveRemoteHosts !== 'function') {
      throw new Error('RemoteHostsStore: dataStore with loadRemoteHosts/saveRemoteHosts required');
    }
    this._dataStore = dataStore;
  }

  // ──────────────────────────── Load / Save ────────────────────────────

  /**
   * Load the full preferences document, running any needed migrations.
   * Missing / malformed files are handled by DataStore and surface here
   * as a fresh `{schemaVersion:1, hosts:{}}` object.
   *
   * @returns {{schemaVersion: number, hosts: object}}
   */
  loadHosts() {
    const raw = this._dataStore.loadRemoteHosts();
    return this._migrate(raw);
  }

  /**
   * Persist the full preferences document. Rewrites the entire file
   * atomically (DataStore handles tmp-file + rename).
   *
   * @param {{schemaVersion?: number, hosts?: object}} prefs
   */
  saveHosts(prefs) {
    const normalized = this._migrate(prefs);
    // Defensive sanitization on the way out — strip any forbidden key
    // material the caller may have snuck in (Req 10.5).
    const cleanHosts = {};
    for (const [alias, entry] of Object.entries(normalized.hosts || {})) {
      if (!entry || typeof entry !== 'object') continue;
      cleanHosts[alias] = {
        favorite: Boolean(entry.favorite),
        lastWorkspace: typeof entry.lastWorkspace === 'string' ? entry.lastWorkspace : '',
        remotePortOverride: _normalizePort(entry.remotePortOverride),
        provisioningMode: VALID_PROVISIONING_MODES.has(entry.provisioningMode)
          ? entry.provisioningMode : 'auto',
        source: VALID_SOURCES.has(entry.source) ? entry.source : 'ssh-config',
        adHoc: entry.adHoc ? _sanitizeAdHoc(entry.adHoc) : null,
      };
    }
    this._dataStore.saveRemoteHosts({ schemaVersion: SCHEMA_VERSION, hosts: cleanHosts });
    return true;
  }

  // ──────────────────────────── Per-host CRUD ──────────────────────────

  /**
   * @param {string} alias
   * @returns {object|null} entry or null if unknown
   */
  getHost(alias) {
    if (!alias) return null;
    const data = this.loadHosts();
    return data.hosts[alias] || null;
  }

  setFavorite(alias, favorite) {
    _assertAlias(alias);
    const data = this.loadHosts();
    data.hosts[alias] = _mergeEntry(data.hosts[alias], { favorite: Boolean(favorite) });
    this.saveHosts(data);
    return data.hosts[alias];
  }

  setWorkspace(alias, workspacePath) {
    _assertAlias(alias);
    const data = this.loadHosts();
    data.hosts[alias] = _mergeEntry(data.hosts[alias], {
      lastWorkspace: typeof workspacePath === 'string' ? workspacePath : '',
    });
    this.saveHosts(data);
    return data.hosts[alias];
  }

  setProvisioningMode(alias, mode) {
    _assertAlias(alias);
    if (!VALID_PROVISIONING_MODES.has(mode)) {
      throw new Error(`RemoteHostsStore.setProvisioningMode: mode must be 'auto' or 'manual', got ${mode}`);
    }
    const data = this.loadHosts();
    data.hosts[alias] = _mergeEntry(data.hosts[alias], { provisioningMode: mode });
    this.saveHosts(data);
    return data.hosts[alias];
  }

  setRemotePortOverride(alias, port) {
    _assertAlias(alias);
    const normalized = _normalizePort(port);
    if (port !== null && port !== undefined && normalized === null) {
      throw new Error(`RemoteHostsStore.setRemotePortOverride: invalid port ${port}`);
    }
    const data = this.loadHosts();
    data.hosts[alias] = _mergeEntry(data.hosts[alias], { remotePortOverride: normalized });
    this.saveHosts(data);
    return data.hosts[alias];
  }

  // ─────────────────────────── Ad-hoc hosts ────────────────────────────

  /**
   * Register a new ad-hoc host (one that is NOT in the user's ssh config).
   * Only the identityFile PATH is stored — NEVER key contents or
   * passphrases (Req 10.5, Property 4 in design.md).
   *
   * @param {{alias: string, hostName: string, user?: string, port?: number, identityFile?: string}} host
   */
  addAdHoc(host) {
    if (!host || typeof host !== 'object') {
      throw new Error('RemoteHostsStore.addAdHoc: host object required');
    }
    const { alias, hostName } = host;
    _assertAlias(alias);
    if (!hostName || typeof hostName !== 'string') {
      throw new Error('RemoteHostsStore.addAdHoc: hostName required');
    }
    // Req 10.5: reject secret-looking fields loudly on the raw input
    // before we start silently dropping unknown keys.
    for (const forbidden of FORBIDDEN_ADHOC_FIELDS) {
      if (Object.prototype.hasOwnProperty.call(host, forbidden)) {
        throw new Error(`RemoteHostsStore.addAdHoc: refusing to persist forbidden field '${forbidden}'`);
      }
    }
    const adHoc = _sanitizeAdHoc({
      hostName,
      user: host.user,
      port: host.port,
      identityFile: host.identityFile,
    });
    const data = this.loadHosts();
    data.hosts[alias] = _mergeEntry(data.hosts[alias], {
      source: 'ad-hoc',
      adHoc,
    });
    this.saveHosts(data);
    return data.hosts[alias];
  }

  /**
   * Remove an ad-hoc host entry entirely. No-op for ssh-config-sourced
   * entries — callers should not use this to prune user preferences.
   *
   * @param {string} alias
   * @returns {boolean} true if a row was removed, false otherwise
   */
  removeAdHoc(alias) {
    _assertAlias(alias);
    const data = this.loadHosts();
    const entry = data.hosts[alias];
    if (!entry || entry.source !== 'ad-hoc') return false;
    delete data.hosts[alias];
    this.saveHosts(data);
    return true;
  }

  // ───────────────────────────── Migration ─────────────────────────────

  /**
   * Normalize + migrate a raw preferences document to the current
   * schemaVersion. Today only v1 exists, but the hook is wired so future
   * versions can chain: v0 → v1 → v2 → ...
   *
   * @private
   */
  _migrate(raw) {
    const doc = (raw && typeof raw === 'object') ? raw : {};
    let version = Number(doc.schemaVersion) || 0;
    let hosts = (doc.hosts && typeof doc.hosts === 'object') ? doc.hosts : {};

    if (version < 1) {
      // v0 → v1: no-op (introductory version). Callers writing without a
      // schemaVersion still land here, so we just stamp v1 on the way out.
      version = 1;
    }

    // Future: if (version < 2) { hosts = _migrateV1toV2(hosts); version = 2; }

    return { schemaVersion: version, hosts };
  }
}

// ─────────────────────────────── helpers ───────────────────────────────

function _assertAlias(alias) {
  if (!alias || typeof alias !== 'string') {
    throw new Error('RemoteHostsStore: alias (non-empty string) required');
  }
}

function _normalizePort(port) {
  if (port === null || port === undefined || port === '') return null;
  const n = Number(port);
  if (!Number.isFinite(n) || !Number.isInteger(n) || n < 1 || n > 65535) return null;
  return n;
}

function _sanitizeAdHoc(adHoc) {
  if (!adHoc || typeof adHoc !== 'object') return null;
  // Req 10.5: refuse to persist anything that smells like a secret.
  for (const forbidden of FORBIDDEN_ADHOC_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(adHoc, forbidden)) {
      // Hard error rather than silent strip — surfaces the bug to the
      // caller instead of pretending a secret was saved.
      throw new Error(`RemoteHostsStore: refusing to persist forbidden field '${forbidden}' in adHoc entry`);
    }
  }
  const port = _normalizePort(adHoc.port);
  return {
    hostName: typeof adHoc.hostName === 'string' ? adHoc.hostName : '',
    user: typeof adHoc.user === 'string' ? adHoc.user : '',
    port: port === null ? 22 : port,
    // identityFile is a PATH ONLY — never key contents.
    identityFile: typeof adHoc.identityFile === 'string' ? adHoc.identityFile : '',
  };
}

module.exports = { RemoteHostsStore, SCHEMA_VERSION };
