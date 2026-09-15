'use strict';
/**
 * Local-filesystem path guard for renderer-facing `fs:*` IPC.
 *
 * Threat model: a compromised renderer (XSS through model output rendered with
 * innerHTML) could otherwise read or write ANY file as the user through the
 * 19 fs channels. The guard confines local fs IPC to paths the user actually
 * handed the app:
 *   - folders chosen in the open-folder dialog        (allowRoot)
 *   - files chosen in open-file / save dialogs          (allowFile)
 *   - the app's own data: Electron userData, os.tmpdir(), ~/.agentic-editor,
 *     AE_GENERATED_ROOT                                 (static roots)
 * Remote (SFTP) paths never reach this guard — the bridge branch runs first.
 *
 * Symlinks are resolved (deepest existing ancestor → realpath) so a link inside
 * an allowed folder cannot point outside it. `AE_FS_GUARD=0` disables the guard
 * as an emergency escape hatch; a denied access throws `fs-path-not-allowed`
 * inside the handler's existing try/catch, so callers see the same null/false/[]
 * failure shapes as any other fs error.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const _roots = new Set();
const _files = new Set();
let _staticRoots = null;
let _warnedDisabled = false;

function enabled() {
  return String(process.env.AE_FS_GUARD || '').trim() !== '0';
}

/** Absolute path with the deepest existing ancestor resolved through symlinks. */
function canonical(p) {
  const abs = path.resolve(String(p));
  let cur = abs;
  const tail = [];
  while (!fs.existsSync(cur)) {
    const parent = path.dirname(cur);
    if (parent === cur) break;
    tail.unshift(path.basename(cur));
    cur = parent;
  }
  let real = cur;
  try { real = (fs.realpathSync.native || fs.realpathSync)(cur); } catch (_e) { /* keep unresolved */ }
  return tail.length ? path.join(real, ...tail) : real;
}

function within(child, root) {
  const rel = path.relative(root, child);
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

function staticRoots() {
  if (_staticRoots) return _staticRoots;
  const list = [];
  try {
    const { app } = require('electron');
    if (app && typeof app.getPath === 'function') list.push(app.getPath('userData'));
  } catch (_e) { /* not in electron */ }
  list.push(os.tmpdir());
  list.push(path.join(os.homedir(), '.agentic-editor'));
  if (process.env.AE_GENERATED_ROOT) list.push(process.env.AE_GENERATED_ROOT);
  _staticRoots = list.map(canonical);
  return _staticRoots;
}

function allowRoot(p) { if (typeof p === 'string' && p) _roots.add(canonical(p)); }
function allowFile(p) { if (typeof p === 'string' && p) _files.add(canonical(p)); }

function isAllowed(p) {
  if (!enabled()) {
    if (!_warnedDisabled) { _warnedDisabled = true; try { console.warn('[path-guard] AE_FS_GUARD=0 — local fs IPC guard disabled'); } catch (_e) { /* ignore */ } }
    return true;
  }
  if (typeof p !== 'string' || p.length === 0) return false;
  const c = canonical(p);
  if (_files.has(c)) return true;
  for (const r of _roots) if (within(c, r)) return true;
  for (const r of staticRoots()) if (within(c, r)) return true;
  return false;
}

function assertAllowed(p, op) {
  if (isAllowed(p)) return;
  const err = new Error(`fs-path-not-allowed: ${op || 'access'} outside the opened folders: ${p}`);
  err.code = 'fs-path-not-allowed';
  throw err;
}

/** Test hooks. */
function _reset() { _roots.clear(); _files.clear(); _staticRoots = null; }
function _setStaticRootsForTest(list) { _staticRoots = Array.isArray(list) ? list.map(canonical) : null; }

module.exports = { enabled, canonical, allowRoot, allowFile, isAllowed, assertAllowed, _reset, _setStaticRootsForTest };
