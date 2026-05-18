'use strict';
/**
 * Provisioner — remote ai_engine install + supervisor boot.
 *
 * Feature: remote-ssh · Tasks 18.1 – 18.6 · Requirements 4.1–4.9, 5.2, 11.5, 12.4
 *
 * Public surface (as consumed by `ipc-remote-handlers.js` and tests):
 *
 *   new Provisioner(session, fileBridge?, opts)
 *     session    — RemoteSession exposing `.client` (ssh2 Client) and `.alias`.
 *     fileBridge — optional RemoteFileBridge. Currently unused by the default
 *                  upload path (we go to SFTP directly) but accepted for
 *                  future tree-diff uploads and to match the design doc.
 *     opts       — {aiEnginePath, remotePort=8765, mode='auto'}.
 *                  `localAiEngineRoot` is accepted as a legacy alias for
 *                  `aiEnginePath` so the old 2-arg form
 *                  `new Provisioner(session, { localAiEngineRoot })` still
 *                  works until all callers migrate.
 *
 *   provisioner.provision()  → {uploaded, service, version}
 *   provisioner.probe()      → {service, version} | null
 *   provisioner.remotePort   getter
 *
 * Errors (all inherit from `ProvisioningError`):
 *   PythonUnsupportedError          — remote Python < 3.11 or missing.
 *   ManualProvisioningHealthError   — manual mode but nothing on /health.
 *   PortOccupiedByOtherServiceError — /health responded but it is not us.
 *
 * Design anchors:
 *   - §Key Technical Choices: `nohup + PID file` supervisor (no systemd).
 *   - §Data Models: `~/.agentic-editor/version` JSON
 *                    {schemaVersion, aiEngineContentHash, installedAt, …}.
 *   - Property 7 (upload tree equivalence + content-hash skip).
 *   - Property 8 (Python version compatibility).
 *
 * This file MUST parse as plain CommonJS (`node --check` passes). No ESM.
 */

const fs = require('fs').promises;
const fsSync = require('fs');
const path = require('path');
const crypto = require('crypto');
const { EventEmitter } = require('events');
const logger = require('./logger');

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const REMOTE_BASE_REL = '.agentic-editor';        // relative to $HOME
const DEFAULT_REMOTE_PORT = 8765;
const PROBE_TIMEOUT_SEC = 3;                       // curl --max-time
const SUPERVISOR_BOOT_TIMEOUT_MS = 30_000;         // wait for /health after start
const SUPERVISOR_POLL_MS = 1000;

/** Directories we never upload. Keeps the tree hash stable and avoids
 *  shipping `.venv` back onto a remote that just built one. */
const UPLOAD_SKIP_DIRS = new Set([
  '__pycache__',
  '.venv',
  '.pytest_cache',
  '.mypy_cache',
  'node_modules',
  '.git',
]);

/** Per-file skip patterns. */
function shouldSkipFile(name) {
  return name.endsWith('.pyc');
}

// ---------------------------------------------------------------------------
// Error classes
// ---------------------------------------------------------------------------

/** Base class so callers can catch provisioning errors en bloc without
 *  importing each subclass. */
class ProvisioningError extends Error {
  constructor(message, remediationHint) {
    super(message);
    this.name = 'ProvisioningError';
    this.remediationHint = remediationHint || '';
  }
}

class PythonUnsupportedError extends ProvisioningError {
  constructor(message, remediationHint) {
    super(message, remediationHint);
    this.name = 'PythonUnsupportedError';
    this.code = 'python-unsupported';
  }
}

class ManualProvisioningHealthError extends ProvisioningError {
  constructor(message, remediationHint) {
    super(message, remediationHint);
    this.name = 'ManualProvisioningHealthError';
    this.code = 'manual-provisioning-health';
  }
}

class PortOccupiedByOtherServiceError extends ProvisioningError {
  constructor(message, remediationHint) {
    super(message, remediationHint);
    this.name = 'PortOccupiedByOtherServiceError';
    this.code = 'port-occupied-by-other-service';
  }
}

// ---------------------------------------------------------------------------
// Pure helpers (exported for unit/property tests)
// ---------------------------------------------------------------------------

/**
 * Parse a `python --version` output line and decide whether it satisfies
 * `major >= 3 AND minor >= 11`. Tolerates leading/trailing whitespace,
 * locale chatter, and trailing patch/pre-release tags.
 *
 * Examples:
 *   "Python 3.11.7"        → true
 *   "Python 3.12.0rc1"     → true
 *   "Python 3.10.14"       → false
 *   "Python 2.7.18"        → false
 *   "bash: python3: command not found" → false
 *
 * @param {string} verString
 * @returns {boolean}
 */
function isPythonCompatible(verString) {
  if (typeof verString !== 'string') return false;
  // Accept either "Python X.Y.Z" or bare "X.Y.Z" — we only need the numbers.
  const m = verString.match(/(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!m) return false;
  const major = Number(m[1]);
  const minor = Number(m[2]);
  if (!Number.isFinite(major) || !Number.isFinite(minor)) return false;
  if (major < 3) return false;
  if (major === 3 && minor < 11) return false;
  return true;
}

/**
 * Walk the ai_engine tree and compute a stable content hash.
 *
 * Hash construction (Property 7 anchor):
 *   files = walk(root, skipping UPLOAD_SKIP_DIRS and *.pyc)
 *   relPaths = files.map(f => relative(root, f)).sort()   // POSIX separator
 *   h = sha256()
 *   for rel in relPaths:
 *     h.update(rel + "\0" + size + "\0" + sha256(content))
 *   return h.hex
 *
 * The per-file prefix (path + size + content-sha256) keeps the hash
 * stable against filesystem-order nondeterminism while still rejecting
 * path renames, size changes, and content edits. We avoid mtime because
 * it perturbs on every `cp -R` / CI checkout.
 *
 * @param {string} aiEnginePath  Absolute path to local ai_engine dir.
 * @returns {Promise<string>} SHA-256 hex digest.
 */
async function computeLocalHash(aiEnginePath) {
  if (!aiEnginePath) throw new TypeError('computeLocalHash: aiEnginePath required');
  const collected = await _collectFiles(aiEnginePath);
  // Sort by POSIX-normalized relative path for cross-platform determinism.
  collected.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));
  const h = crypto.createHash('sha256');
  for (const { abs, rel } of collected) {
    const [content, st] = await Promise.all([fs.readFile(abs), fs.stat(abs)]);
    const contentSha = crypto.createHash('sha256').update(content).digest('hex');
    h.update(rel);
    h.update('\0');
    h.update(String(st.size));
    h.update('\0');
    h.update(contentSha);
    h.update('\0');
  }
  return h.digest('hex');
}

/**
 * @private
 * @param {string} root
 * @returns {Promise<Array<{abs:string, rel:string}>>}
 */
async function _collectFiles(root) {
  const out = [];
  async function walk(dir) {
    const entries = await fs.readdir(dir, { withFileTypes: true });
    for (const ent of entries) {
      if (UPLOAD_SKIP_DIRS.has(ent.name)) continue;
      if (shouldSkipFile(ent.name)) continue;
      const abs = path.join(dir, ent.name);
      if (ent.isDirectory()) {
        await walk(abs);
      } else if (ent.isFile()) {
        out.push({ abs, rel: path.relative(root, abs).split(path.sep).join('/') });
      }
      // symlinks and other types intentionally skipped — v1 contract.
    }
  }
  await walk(root);
  return out;
}

/**
 * Select the remote base path for an optional sub-entry, matching the
 * remote OS. For Unix we use `~/.agentic-editor/<name>` which bash / sh
 * expand; for Windows OpenSSH we use `%USERPROFILE%\.agentic-editor\<name>`.
 *
 * Note: callers that need an *absolute* POSIX path (e.g. for SFTP which
 * does not expand `~`) should use the resolved $HOME instead — see
 * `Provisioner._resolveHome()`.
 *
 * @param {string} [name]        Sub-path under the base. `''` or missing → base only.
 * @param {'unix'|'windows'} [remoteOs='unix']
 * @returns {string}
 */
function remotePath(name, remoteOs) {
  const isWin = remoteOs === 'windows';
  const base = isWin ? '%USERPROFILE%\\.agentic-editor' : '~/.agentic-editor';
  if (!name) return base;
  const sep = isWin ? '\\' : '/';
  const normalized = String(name).replace(/[\\/]+/g, sep).replace(new RegExp(`^\\${sep}+`), '');
  return base + sep + normalized;
}

/** POSIX-style path join that never collapses to `/` — good for remote $HOME/... */
function posixJoin(...parts) {
  return parts
    .map((p, i) => (i === 0 ? p.replace(/\/+$/, '') : p.replace(/^\/+/, '').replace(/\/+$/, '')))
    .filter((p) => p.length > 0)
    .join('/');
}

/** Shell-escape a string into a bash single-quoted literal. */
function shq(s) {
  return `'${String(s).replace(/'/g, "'\\''")}'`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------------------
// Provisioner
// ---------------------------------------------------------------------------

class Provisioner extends EventEmitter {
  /**
   * Flexible constructor. Accepts both the new
   *   new Provisioner(session, fileBridge, opts)
   * form and the legacy 2-arg form
   *   new Provisioner(session, {localAiEngineRoot, ...})
   * so `ipc-remote-handlers.js` keeps working while it migrates.
   *
   * @param {object} session
   * @param {object|null} [arg2]
   * @param {object} [arg3]
   */
  constructor(session, arg2, arg3) {
    super();
    if (!session || !session.client) {
      throw new TypeError('Provisioner: session.client is required');
    }
    this._session = session;
    this._alias = session.alias || 'unknown';

    let fileBridge = null;
    let opts;
    if (arg3 !== undefined) {
      fileBridge = arg2 || null;
      opts = arg3 || {};
    } else {
      opts = arg2 || {};
    }
    this._fileBridge = fileBridge;

    const aiEnginePath = opts.aiEnginePath || opts.localAiEngineRoot || null;
    if (!aiEnginePath) {
      throw new TypeError('Provisioner: opts.aiEnginePath (or legacy opts.localAiEngineRoot) is required');
    }
    this._aiEnginePath = aiEnginePath;
    this._remotePort = Number(opts.remotePort) > 0 ? Number(opts.remotePort) : DEFAULT_REMOTE_PORT;
    this._mode = opts.mode === 'manual' ? 'manual' : 'auto';

    /** @private */ this._homeCache = null;
    /** @private */ this._remoteOs = null;           // 'unix' | 'windows'
    /** @private */ this._sftpCache = null;
    /** @private */ this._sftpPending = null;
  }

  get remotePort() { return this._remotePort; }
  get aiEnginePath() { return this._aiEnginePath; }
  get mode() { return this._mode; }

  // -------------------------------------------------------------------------
  // Top-level orchestration (18.5 Manual mode + 18.6 Port occupied detection)
  // -------------------------------------------------------------------------

  /**
   * Drive the full provisioning flow.
   *
   *   mode='manual' → probe() ONLY.
   *       - null response           → ManualProvisioningHealthError
   *       - service !== our engine  → PortOccupiedByOtherServiceError
   *       - otherwise               → {uploaded:false, service, version}
   *
   *   mode='auto' → probe() first. If our engine is already up, short-circuit.
   *       If some other service holds the port, throw PortOccupied.
   *       Otherwise: python check → upload → venv → pip → supervisor → /health.
   *
   * @returns {Promise<{uploaded:boolean, service:string, version:?string}>}
   */
  async provision() {
    if (this._mode === 'manual') {
      this._progress('probe');
      const p = await this.probe();
      if (!p) {
        throw new ManualProvisioningHealthError(
          `Manual provisioning: no service responded on 127.0.0.1:${this._remotePort}`,
          'Manual provisioning mode is enabled. Start ai_engine on the remote host yourself, for example:\n' +
          '  source ~/.agentic-editor/venv/bin/activate && cd ~/.agentic-editor/ai_engine && \\\n' +
          `  python -m uvicorn server:app --host 127.0.0.1 --port ${this._remotePort}`
        );
      }
      if (p.service !== 'ai-editor-engine') {
        throw new PortOccupiedByOtherServiceError(
          `Manual provisioning: port ${this._remotePort} is occupied by a different service` +
            (p.service ? ` (${p.service})` : ''),
          `Stop the conflicting service on the remote host or change the ai_engine port via per-host settings (remotePortOverride).`
        );
      }
      return { uploaded: false, service: p.service, version: p.version || null };
    }

    // auto mode
    this._progress('probe');
    const existing = await this.probe();
    if (existing) {
      if (existing.service === 'ai-editor-engine') {
        return { uploaded: false, service: existing.service, version: existing.version || null };
      }
      // HTTP-level error from our own service (e.g. server still warming up,
      // transient 500) — treat as "needs (re)install" rather than port
      // conflict. We only flag port conflict when a recognizable non-us
      // service responds. Empty body + http error → fall through to full
      // provisioning, which will reinstall and restart the supervisor.
      if (!existing.service && !existing.version) {
        try {
          logger.info('remote-provision-probe-ambiguous', {
            alias: this._alias,
            port: this._remotePort,
            note: 'probe returned a response but no service tag — treating as unhealthy ai-editor-engine, will re-provision',
          });
        } catch (_e) { /* ignore */ }
        // fall through to full install path
      } else {
        throw new PortOccupiedByOtherServiceError(
          `Port ${this._remotePort} is occupied by a different service` +
            (existing.service ? ` (${existing.service})` : ''),
          `Stop the conflicting service on the remote host or change the ai_engine port via per-host settings (remotePortOverride).`
        );
      }
    }

    // Detect remote OS up front so every subsequent step can branch without
    // re-probing.
    this._progress('detect-os');
    await this._detectRemoteOs();

    this._progress('python-check');
    const python = await this.checkPython();

    this._progress('upload');
    const upload = await this.uploadAiEngine();

    this._progress('venv');
    await this.setupVenv({ pythonBin: python.bin });

    this._progress('install-deps');
    await this.installDeps();

    this._progress('supervisor-deploy');
    await this.deploySupervisor();

    this._progress('supervisor-start');
    const booted = await this.startSupervisor();

    return {
      uploaded: upload.uploaded,
      service: 'ai-editor-engine',
      version: booted && booted.version ? booted.version : null,
    };
  }

  /** Back-compat alias kept for existing `ipc-remote-handlers.js`. */
  ensureProvisioned() { return this.provision(); }

  // -------------------------------------------------------------------------
  // 18.1 — Probe & Python compatibility check
  // -------------------------------------------------------------------------

  /**
   * `curl 127.0.0.1:<port>/health` over SSH exec with a 3 s timeout.
   *
   *   returns {service, version}  — reachable, JSON parsed OK
   *   returns null                 — unreachable (timeout, refused, DNS)
   *
   * If a service responds but with an empty body or non-JSON, we
   * intentionally return an object with `service=null` so provision()
   * can decide how to classify it (manual vs auto path both treat it
   * as "port occupied").
   *
   * @returns {Promise<{service:string|null, version:string|null}|null>}
   */
  async probe() {
    const port = this._remotePort;
    // We want to distinguish "nothing listening" (curl exit != 0 and empty
    // stdout) from "responded but weird" (stdout has content). Embed the
    // curl exit status so we can tell them apart even over ssh2 which
    // does not expose the child exit via the top-level bash exec's exit
    // code alone.
    const cmd =
      `bash -lc 'curl -sS --max-time ${PROBE_TIMEOUT_SEC} ` +
      `http://127.0.0.1:${port}/health; ` +
      `printf "\\n__CURL_EXIT__:%s\\n" $?'`;
    let res;
    try {
      res = await this._exec(cmd);
    } catch (_e) {
      return null;
    }
    const out = String(res.stdout || '');
    const exitMatch = out.match(/__CURL_EXIT__:(\d+)\s*$/);
    const exitCode = exitMatch ? Number(exitMatch[1]) : 1;
    const body = out.replace(/\n?__CURL_EXIT__:\d+\s*$/, '').trim();

    if (exitCode !== 0 && !body) {
      return null; // timeout / connection refused / DNS — nothing there.
    }
    if (!body) {
      // Responded with empty body — treat as "port occupied" flavour.
      return { service: null, version: null };
    }
    let json;
    try { json = JSON.parse(body); } catch { return { service: null, version: null }; }
    if (!json || typeof json !== 'object') {
      return { service: null, version: null };
    }
    return {
      service: typeof json.service === 'string' ? json.service : null,
      version: typeof json.version === 'string' ? json.version : null,
    };
  }

  /**
   * Try `python3 --version`, fall back to `python --version`. Throws
   * `PythonUnsupportedError` with a remediation hint if nothing usable
   * is found or every candidate is pre-3.11.
   *
   * @returns {Promise<{bin:string, version:string, compatible:true}>}
   * @throws {PythonUnsupportedError}
   */
  async checkPython() {
    const hint =
      'Install Python 3.11+ on the remote host.\n' +
      '  Debian/Ubuntu: sudo apt install python3.11 python3.11-venv\n' +
      '  Fedora/RHEL:   sudo dnf install python3.11\n' +
      '  macOS:         brew install python@3.11\n' +
      '  Windows:       winget install Python.Python.3.11';

    let lastFoundVersion = null;
    let lastFoundBin = null;
    for (const bin of ['python3', 'python']) {
      let out;
      try {
        const r = await this._exec(`bash -lc ${shq(`${bin} --version 2>&1 || true`)}`);
        out = String(r.stdout || '').trim();
      } catch {
        continue;
      }
      if (!out) continue;
      // Skip shell "command not found" style responses.
      if (/command not found|No such file|not recognized/i.test(out)) continue;
      if (!/Python\s+\d/.test(out)) continue;
      const m = out.match(/Python\s+(\S+)/i);
      const version = m ? m[1] : out;
      if (isPythonCompatible(out)) {
        return { bin, version, compatible: true };
      }
      lastFoundVersion = version;
      lastFoundBin = bin;
    }

    if (lastFoundVersion) {
      throw new PythonUnsupportedError(
        `Remote Python ${lastFoundVersion} (${lastFoundBin}) is below the required 3.11.`,
        hint
      );
    }
    throw new PythonUnsupportedError(
      'No Python 3 interpreter found on the remote host.',
      hint
    );
  }

  // -------------------------------------------------------------------------
  // 18.2 — ai_engine tree content hash + upload-skip
  // -------------------------------------------------------------------------

  /**
   * Compute the local ai_engine tree content hash. Thin wrapper around
   * the module-level `computeLocalHash` so tests can stub the instance
   * method while property tests exercise the pure function directly.
   *
   * @param {string} [aiEnginePath]
   * @returns {Promise<string>}
   */
  computeLocalHash(aiEnginePath) {
    return computeLocalHash(aiEnginePath || this._aiEnginePath);
  }

  /**
   * Read `~/.agentic-editor/version` and return the parsed JSON object,
   * or `null` if the file is missing or unparseable. We never throw —
   * a missing manifest simply means "first time setup".
   *
   * @returns {Promise<{aiEngineContentHash?:string, installedAt?:string}|null>}
   */
  async readRemoteVersion() {
    const home = await this._resolveHome();
    const manifestPath = posixJoin(home, REMOTE_BASE_REL, 'version');
    let body = '';
    try {
      const r = await this._exec(`cat ${shq(manifestPath)} 2>/dev/null || true`);
      body = String(r.stdout || '').trim();
    } catch { return null; }
    if (!body) return null;
    try {
      const obj = JSON.parse(body);
      return obj && typeof obj === 'object' ? obj : null;
    } catch { return null; }
  }

  /**
   * Upload the ai_engine tree (or skip if the content hash already
   * matches). Preserves file modes; directories in UPLOAD_SKIP_DIRS and
   * `.pyc` files are excluded. Always writes a refreshed version
   * manifest when an upload actually happened.
   *
   * @returns {Promise<{uploaded:boolean, contentHash:string}>}
   */
  async uploadAiEngine() {
    const localHash = await this.computeLocalHash();
    const manifest = await this.readRemoteVersion();
    if (manifest && manifest.aiEngineContentHash === localHash) {
      return { uploaded: false, contentHash: localHash };
    }

    const home = await this._resolveHome();
    const remoteRoot = posixJoin(home, REMOTE_BASE_REL, 'ai_engine');
    await this._ensureRemoteDir(remoteRoot);

    const files = await _collectFiles(this._aiEnginePath);
    // Deterministic order: parent directories first.
    files.sort((a, b) => (a.rel < b.rel ? -1 : a.rel > b.rel ? 1 : 0));

    // Pre-create all intermediate directories so fastPut does not race.
    const dirs = new Set();
    for (const { rel } of files) {
      const dirRel = rel.includes('/') ? rel.slice(0, rel.lastIndexOf('/')) : '';
      if (dirRel) dirs.add(posixJoin(remoteRoot, dirRel));
    }
    for (const d of Array.from(dirs).sort((a, b) => a.length - b.length)) {
      await this._ensureRemoteDir(d);
    }

    const sftp = await this._getSftp();
    for (const { abs, rel } of files) {
      const dst = posixJoin(remoteRoot, rel);
      const st = await fs.stat(abs);
      await this._sftpFastPut(sftp, abs, dst);
      // Preserve mode and mtime so re-runs over the same tree do not
      // keep busting the content hash.
      await new Promise((resolve, reject) => {
        sftp.setstat(
          dst,
          { mode: st.mode & 0o7777, atime: Math.floor(st.atimeMs / 1000), mtime: Math.floor(st.mtimeMs / 1000) },
          (err) => (err ? reject(err) : resolve())
        );
      }).catch(() => { /* setstat is best-effort; some servers reject it for non-owners */ });
    }

    const newManifest = {
      schemaVersion: 1,
      aiEngineContentHash: localHash,
      installedAt: new Date().toISOString(),
    };
    const manifestPath = posixJoin(home, REMOTE_BASE_REL, 'version');
    await this._writeRemoteFile(manifestPath, JSON.stringify(newManifest));
    return { uploaded: true, contentHash: localHash };
  }

  // -------------------------------------------------------------------------
  // 18.3 — venv + pip install
  // -------------------------------------------------------------------------

  /**
   * Create `~/.agentic-editor/venv` using `<pythonBin> -m venv`. Idempotent:
   * if the venv directory already exists the venv module is a no-op.
   *
   * @param {{pythonBin:string, remoteOs?:('unix'|'windows')}} opts
   * @returns {Promise<void>}
   */
  async setupVenv(opts) {
    const pythonBin = (opts && opts.pythonBin) || 'python3';
    const remoteOs = (opts && opts.remoteOs) || this._remoteOs || 'unix';
    const venv = remotePath('venv', remoteOs);
    const cmd =
      remoteOs === 'windows'
        ? `${pythonBin} -m venv ${venv}`
        : `bash -lc ${shq(`${pythonBin} -m venv "${venv.replace(/^~/, '$HOME')}"`)}`;
    const r = await this._exec(cmd);
    if (r.code !== 0) {
      throw new ProvisioningError(
        `venv creation failed (exit ${r.code}): ${(r.stderr || r.stdout || '').slice(0, 400)}`,
        'Ensure the remote Python install includes the `venv` module (e.g. apt install python3.11-venv) and the home directory is writable.'
      );
    }
  }

  /**
   * `pip install -r requirements.txt --no-input` against the venv.
   *
   * @param {{remoteOs?:('unix'|'windows')}} [opts]
   * @returns {Promise<void>}
   */
  async installDeps(opts) {
    const remoteOs = (opts && opts.remoteOs) || this._remoteOs || 'unix';
    let cmd;
    if (remoteOs === 'windows') {
      const pip = '%USERPROFILE%\\.agentic-editor\\venv\\Scripts\\pip.exe';
      const req = '%USERPROFILE%\\.agentic-editor\\ai_engine\\requirements.txt';
      cmd = `${pip} install -r ${req} --no-input --disable-pip-version-check`;
    } else {
      const pip = '$HOME/.agentic-editor/venv/bin/pip';
      const req = '$HOME/.agentic-editor/ai_engine/requirements.txt';
      cmd = `bash -lc ${shq(`${pip} install -r ${req} --no-input --disable-pip-version-check`)}`;
    }
    const r = await this._exec(cmd);
    if (r.code !== 0) {
      throw new ProvisioningError(
        `pip install failed (exit ${r.code}): ${(r.stderr || r.stdout || '').slice(0, 400)}`,
        'Check remote network access (PyPI) and pip wheel availability. Consider pre-warming a local pip cache.'
      );
    }
  }

  /** @see remotePath — exposed as an instance method for the test contract. */
  remotePath(name, remoteOs) {
    return remotePath(name, remoteOs || this._remoteOs || 'unix');
  }

  // -------------------------------------------------------------------------
  // 18.4 — Supervisor deploy + start
  // -------------------------------------------------------------------------

  /**
   * Upload `resources/supervisor.sh` to `~/.agentic-editor/supervisor.sh`
   * and make it executable (0755). Reads the bundled resource file lazily
   * so tests can use a shimmed __dirname.
   *
   * @returns {Promise<void>}
   */
  async deploySupervisor() {
    const localResource = path.join(__dirname, 'resources', 'supervisor.sh');
    const content = await fs.readFile(localResource);
    const home = await this._resolveHome();
    const dst = posixJoin(home, REMOTE_BASE_REL, 'supervisor.sh');
    await this._ensureRemoteDir(posixJoin(home, REMOTE_BASE_REL));
    await this._writeRemoteFile(dst, content, { mode: 0o755 });
    // Belt-and-suspenders: explicit chmod in case the server silently
    // ignored the `mode` SFTP attribute (some OpenSSH builds do).
    try { await this._exec(`chmod 0755 ${shq(dst)}`); } catch { /* non-fatal */ }
  }

  /**
   * Start the supervisor. If the existing PID file points at a live
   * process AND `probe()` confirms our engine is responding, we reuse it.
   * Otherwise we `nohup bash supervisor.sh &`, record the PID, disown,
   * and wait up to 30 s for /health to come alive.
   *
   * @returns {Promise<{reused:boolean, service?:string, version?:string|null}>}
   */
  async startSupervisor() {
    const home = await this._resolveHome();
    const pidFile = posixJoin(home, REMOTE_BASE_REL, 'supervisor.pid');
    const supPath = posixJoin(home, REMOTE_BASE_REL, 'supervisor.sh');

    // Reuse branch: PID alive AND /health is ours.
    const alive = await this._exec(
      `bash -lc ${shq(
        `if [ -f ${pidFile} ] && kill -0 "$(cat ${pidFile} 2>/dev/null)" 2>/dev/null; then echo ALIVE; fi`
      )}`
    );
    if (String(alive.stdout || '').includes('ALIVE')) {
      const probed = await this.probe();
      if (probed && probed.service === 'ai-editor-engine') {
        return { reused: true, service: probed.service, version: probed.version || null };
      }
      // PID alive but not us → stale supervisor. Fall through and restart.
    }

    const startCmd =
      `bash -lc ${shq(
        `nohup bash ${supPath} >/dev/null 2>&1 & echo $! > ${pidFile}; disown`
      )}`;
    const r = await this._exec(startCmd);
    if (r.code !== 0) {
      throw new ProvisioningError(
        `supervisor start failed (exit ${r.code}): ${(r.stderr || '').slice(0, 400)}`,
        `Check the supervisor log at ~/.agentic-editor/server.log on the remote host.`
      );
    }

    const deadline = Date.now() + SUPERVISOR_BOOT_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const p = await this.probe();
      if (p && p.service === 'ai-editor-engine') {
        return { reused: false, service: p.service, version: p.version || null };
      }
      if (p && p.service && p.service !== 'ai-editor-engine') {
        // Somebody else grabbed the port under us.
        throw new PortOccupiedByOtherServiceError(
          `Port ${this._remotePort} is occupied by a different service (${p.service}) after supervisor boot`,
          `Stop the conflicting service on the remote host or change the ai_engine port via per-host settings.`
        );
      }
      await sleep(SUPERVISOR_POLL_MS);
    }
    throw new ProvisioningError(
      `supervisor started but /health did not respond within ${SUPERVISOR_BOOT_TIMEOUT_MS}ms`,
      `Inspect ~/.agentic-editor/server.log on the remote host and retry the connection.`
    );
  }

  // -------------------------------------------------------------------------
  // Private helpers
  // -------------------------------------------------------------------------

  /** Emit a progress event (consumed by `ipc-remote-handlers.js`). */
  _progress(stage) {
    try {
      this.emit('progress', { alias: this._alias, stage });
      if (logger && typeof logger.info === 'function') {
        logger.info('remote-provision-progress', { alias: this._alias, stage });
      }
    } catch { /* swallow — progress reporting must never fail provisioning */ }
  }

  /**
   * Resolve `$HOME` on the remote host and cache it. Falls back to `/root`
   * if the command produces no output (should never happen on a real sshd,
   * but keeps tests simpler).
   */
  async _resolveHome() {
    if (this._homeCache) return this._homeCache;
    try {
      const r = await this._exec(`bash -lc 'printf %s "$HOME"'`);
      const home = String(r.stdout || '').trim();
      this._homeCache = home || '/root';
    } catch { this._homeCache = '/root'; }
    return this._homeCache;
  }

  /**
   * Detect whether the remote is Unix-ish or Windows-OpenSSH. We use
   * `uname -s`: Linux/Darwin/FreeBSD etc. all match Unix. If the command
   * fails or output is empty we assume Unix, which is the majority
   * deployment target for v1.
   */
  async _detectRemoteOs() {
    if (this._remoteOs) return this._remoteOs;
    try {
      const r = await this._exec(`bash -lc 'uname -s 2>/dev/null || echo WINDOWS'`);
      const s = String(r.stdout || '').trim().toLowerCase();
      this._remoteOs = /windows|mingw|msys|cygwin/.test(s) ? 'windows' : 'unix';
    } catch { this._remoteOs = 'unix'; }
    return this._remoteOs;
  }

  /**
   * Run a shell command over ssh2 exec. Resolves with the accumulated
   * stdout/stderr and the child exit code. Never throws on non-zero
   * exit — callers inspect `code` and decide.
   */
  _exec(cmd) {
    return new Promise((resolve, reject) => {
      this._session.client.exec(cmd, (err, stream) => {
        if (err) { reject(err); return; }
        let stdout = '';
        let stderr = '';
        let code = 0;
        stream.on('data', (d) => { stdout += d.toString('utf8'); });
        stream.stderr.on('data', (d) => { stderr += d.toString('utf8'); });
        stream.on('exit', (c) => { code = Number.isFinite(c) ? Number(c) : 0; });
        stream.on('close', () => resolve({ stdout, stderr, code }));
        stream.on('error', reject);
      });
    });
  }

  /** Lazily open and cache an SFTP channel on the shared ssh2 client. */
  _getSftp() {
    if (this._sftpCache) return Promise.resolve(this._sftpCache);
    if (this._sftpPending) return this._sftpPending;
    this._sftpPending = new Promise((resolve, reject) => {
      try {
        this._session.client.sftp((err, sftp) => {
          this._sftpPending = null;
          if (err) { reject(err); return; }
          const drop = () => { if (this._sftpCache === sftp) this._sftpCache = null; };
          sftp.once('close', drop);
          sftp.once('end', drop);
          sftp.once('error', drop);
          this._sftpCache = sftp;
          resolve(sftp);
        });
      } catch (e) { this._sftpPending = null; reject(e); }
    });
    return this._sftpPending;
  }

  /** `sftp.fastPut` promisified. */
  _sftpFastPut(sftp, localPath, remotePath_) {
    return new Promise((resolve, reject) => {
      sftp.fastPut(localPath, remotePath_, {}, (err) => (err ? reject(err) : resolve()));
    });
  }

  /**
   * Write bytes to a remote path via SFTP. Used for small control files
   * (version manifest, supervisor script) where atomic-rename overhead is
   * not needed.
   *
   * @param {string} remotePath_
   * @param {Buffer|string} content
   * @param {{mode?:number}} [opts]
   */
  async _writeRemoteFile(remotePath_, content, opts) {
    const sftp = await this._getSftp();
    const buf = Buffer.isBuffer(content) ? content : Buffer.from(String(content), 'utf8');
    await new Promise((resolve, reject) => {
      sftp.writeFile(remotePath_, buf, { encoding: null, mode: opts && opts.mode }, (err) =>
        err ? reject(err) : resolve()
      );
    });
  }

  /** `mkdir -p` on the remote via SFTP with `recursive=true`. */
  async _ensureRemoteDir(absPosixDir) {
    const sftp = await this._getSftp();
    const parts = absPosixDir.split('/').filter((p) => p.length > 0);
    let cur = '';
    for (const p of parts) {
      cur += '/' + p;
      await new Promise((resolve) => {
        sftp.stat(cur, (err) => {
          if (!err) { resolve(); return; }
          sftp.mkdir(cur, { mode: 0o755 }, (mkErr) => {
            // Ignore "already exists" races.
            if (mkErr && !/exists/i.test(String(mkErr.message || mkErr.code || ''))) {
              // Still resolve — we will surface any real problem on the
              // subsequent write, with a clearer error message.
            }
            resolve();
          });
        });
      });
    }
  }
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

// Re-export fsSync so Node's module loader keeps the reference (otherwise the
// bundler can strip it when tree-shaking CJS — harmless but silences lint
// warnings if the file is analyzed by strict tools).
void fsSync;

module.exports = {
  Provisioner,
  ProvisioningError,
  PythonUnsupportedError,
  ManualProvisioningHealthError,
  PortOccupiedByOtherServiceError,
  // Pure helpers (exported for property tests)
  isPythonCompatible,
  computeLocalHash,
  remotePath,
  // Constants
  DEFAULT_REMOTE_PORT,
  REMOTE_BASE_REL,
};
