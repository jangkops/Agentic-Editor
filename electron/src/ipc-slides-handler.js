'use strict';
/**
 * Slides IPC Handler — HTML → PNG via headless BrowserWindow.
 *
 * Why this exists:
 *   matplotlib/Mermaid PNG embeds were producing low-quality PPTX/PDF output
 *   ("text on the left, tiny diagram on the right, alignment broken").
 *   Genspark/Gamma-class output requires a *full-bleed* slide background
 *   (1920×1080) rendered from a real HTML/CSS layout. We get that for free
 *   from Electron's bundled Chromium — no Marp/Slidev/Puppeteer install.
 *
 * Pipeline:
 *   1. caller passes raw HTML (no external URLs allowed)
 *   2. spawn a hidden BrowserWindow at the requested dimensions
 *   3. load via data: URL (so file://, http:// and any external resource
 *      hit a brick wall — security goal: no SSRF, no DNS exfil)
 *   4. wait for did-finish-load + 500ms (web-fonts loaded into the canvas)
 *   5. webContents.capturePage() → NativeImage → PNG bytes → fs.writeFileSync
 *   6. destroy the window and resolve { ok, path, width, height, sizeBytes }
 *
 * Concurrency:
 *   We cap simultaneous captures at 4 with a tiny FIFO queue. Without a cap
 *   the GPU process happily hands out 20+ offscreen surfaces and capturePage
 *   starts dropping frames silently — tested on macOS Sonoma + M2.
 *
 * Security (echoes .kiro/steering/security.md):
 *   - sandbox: true, contextIsolation: true, nodeIntegration: false
 *   - reject HTML containing http:// or https:// in `<img src>` / `<link href>`
 *     before we ever load it (cheap pre-flight)
 *   - data: URL means the renderer cannot read file:// either
 *   - 30s hard timeout — if the renderer hangs, kill the window and reject
 */

const { ipcMain, BrowserWindow } = require('electron');
const fs = require('fs');
const path = require('path');

// ---- concurrency limiter (max 4 parallel renders) ----------------------
const MAX_CONCURRENT = 4;
let _inflight = 0;
const _waitQueue = [];

/**
 * Acquire a slot in the render queue. Returns a release() function that
 * MUST be called in `finally` to keep the queue draining.
 */
function _acquireSlot() {
  return new Promise((resolve) => {
    const tryAcquire = () => {
      if (_inflight < MAX_CONCURRENT) {
        _inflight += 1;
        resolve(() => {
          _inflight -= 1;
          // hand the slot to the next waiter, if any
          const next = _waitQueue.shift();
          if (next) next();
        });
      } else {
        _waitQueue.push(tryAcquire);
      }
    };
    tryAcquire();
  });
}

// ---- security pre-flight ----------------------------------------------
const _EXTERNAL_URL_PATTERNS = [
  // <img src="http://..."> or src='https://...' (single OR double quoted)
  /<img[^>]+src\s*=\s*["']https?:\/\//i,
  // <link href="http(s)://...">
  /<link[^>]+href\s*=\s*["']https?:\/\//i,
  // <script src="http(s)://...">
  /<script[^>]+src\s*=\s*["']https?:\/\//i,
  // CSS @import url("http(s)://...")
  /@import\s+url\(\s*["']?https?:\/\//i,
  // inline url(http(s)://...)
  /url\(\s*["']?https?:\/\//i,
];

function _detectExternalResource(html) {
  for (const pattern of _EXTERNAL_URL_PATTERNS) {
    const match = pattern.exec(html);
    if (match) {
      // return the offending fragment (max 80 chars) so the error is debuggable
      return match[0].slice(0, 80);
    }
  }
  return null;
}

// ---- core render function ----------------------------------------------
/**
 * Render a single HTML document into a PNG file via a hidden BrowserWindow.
 *
 * @param {Object} opts
 * @param {string} opts.html         raw HTML (must be self-contained)
 * @param {number} [opts.width=1920]
 * @param {number} [opts.height=1080]
 * @param {string} opts.outputPath   absolute file path for the PNG
 * @param {number} [opts.timeoutMs=30000]
 * @returns {Promise<{ok: boolean, path?: string, width?: number, height?: number, sizeBytes?: number, error?: string}>}
 */
async function renderHtmlToPng(opts) {
  const html = String(opts.html || '');
  const width = Math.max(320, Math.min(7680, parseInt(opts.width, 10) || 1920));
  const height = Math.max(240, Math.min(4320, parseInt(opts.height, 10) || 1080));
  const outputPath = String(opts.outputPath || '');
  const timeoutMs = Math.max(2000, Math.min(120000, parseInt(opts.timeoutMs, 10) || 30000));

  if (!html.trim()) {
    return { ok: false, error: 'html is required' };
  }
  if (!outputPath) {
    return { ok: false, error: 'outputPath is required' };
  }
  if (!path.isAbsolute(outputPath)) {
    return { ok: false, error: 'outputPath must be absolute' };
  }

  // Pre-flight: refuse external resource references — data: URL would block
  // them anyway, but failing fast gives the caller a useful error message.
  const offending = _detectExternalResource(html);
  if (offending) {
    return {
      ok: false,
      error: `external resource not allowed: ${offending}`,
    };
  }

  // Make sure the output directory exists. fs.mkdirSync({recursive:true}) is
  // idempotent so we don't need to check first.
  try {
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  } catch (err) {
    return { ok: false, error: `mkdir failed: ${err.message}` };
  }

  const release = await _acquireSlot();
  let win = null;
  let timeoutHandle = null;

  try {
    win = new BrowserWindow({
      width,
      height,
      show: false,
      frame: false,
      transparent: false,
      backgroundColor: '#FFFFFF',
      // capture quality is independent of devicePixelRatio at this size,
      // and offscreen rendering on macOS sometimes returns blank captures —
      // keep it disabled so we use the standard hidden-window path.
      webPreferences: {
        offscreen: false,
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
        // disable plugins, java, anything we won't need; reduces attack surface
        plugins: false,
        webgl: false,
        // explicit empty preload — the slide HTML never needs IPC
        preload: undefined,
      },
    });
    // Force 1.0 zoom regardless of system settings — ensures pixel-accurate capture.
    try {
      win.webContents.setZoomFactor(1.0);
      win.webContents.setVisualZoomLevelLimits(1, 1);
    } catch (_e) { /* old electron versions ignore — fine */ }

    // Encode HTML as base64 data URL — avoids URL-escaping pitfalls with
    // multibyte Korean characters and `#` / `%` glyphs.
    const dataUrl = 'data:text/html;charset=utf-8;base64,' + Buffer.from(html, 'utf8').toString('base64');

    // Wait for did-finish-load. We add an extra 500ms because system fonts
    // (Apple SD Gothic Neo on macOS, Malgun Gothic on Windows) take a frame
    // or two to land in the canvas after the DOM reports ready.
    const loaded = new Promise((resolve, reject) => {
      win.webContents.once('did-finish-load', () => resolve());
      win.webContents.once('did-fail-load', (_evt, errCode, errDesc, validatedURL) => {
        // ignore cancellations of the about:blank initial load
        if (errCode === -3) return;
        reject(new Error(`did-fail-load ${errCode} ${errDesc} (${validatedURL})`));
      });
    });

    // Hard timeout: if the renderer never fires did-finish-load (e.g. an
    // infinite-loop <script>), we want to abort cleanly rather than leak
    // a window. We race the timeout against the load+capture pipeline.
    const timeoutPromise = new Promise((_resolve, reject) => {
      timeoutHandle = setTimeout(() => {
        reject(new Error(`timeout after ${timeoutMs}ms`));
      }, timeoutMs);
    });

    win.loadURL(dataUrl);

    await Promise.race([loaded, timeoutPromise]);

    // 500ms grace period for font/layout settling. Tested with Korean
    // glyph-heavy templates — without this, the first ~5% of captures
    // come back with fallback DejaVu glyphs rendered instead of CJK.
    await new Promise((r) => setTimeout(r, 500));

    const captured = await Promise.race([
      win.webContents.capturePage({ x: 0, y: 0, width, height }),
      timeoutPromise,
    ]);

    if (!captured || typeof captured.toPNG !== 'function') {
      return { ok: false, error: 'capturePage returned invalid image' };
    }
    const pngBuf = captured.toPNG();
    if (!pngBuf || pngBuf.length < 200) {
      return { ok: false, error: `png buffer too small (${pngBuf ? pngBuf.length : 0} bytes)` };
    }

    fs.writeFileSync(outputPath, pngBuf);
    const sizeBytes = pngBuf.length;

    return {
      ok: true,
      path: outputPath,
      width,
      height,
      sizeBytes,
    };
  } catch (err) {
    return { ok: false, error: (err && err.message) || String(err) };
  } finally {
    if (timeoutHandle) clearTimeout(timeoutHandle);
    try { if (win && !win.isDestroyed()) win.destroy(); } catch (_e) { /* ignore */ }
    release();
  }
}

// ---- IPC registration --------------------------------------------------
/**
 * Register the slides IPC handler. Idempotent — safe to call multiple times
 * during dev hot-reload (we remove any previous handler first).
 *
 * @param {BrowserWindow} _mainWindow unused, kept for API symmetry with
 *                                    the other IPC registrars
 */
function registerSlidesHandlers(_mainWindow) {
  try { ipcMain.removeHandler('slides:render-html-to-png'); } catch (_e) { /* fresh */ }

  ipcMain.handle('slides:render-html-to-png', async (_evt, opts) => {
    // basic shape check — IPC is sandboxed but we still validate
    if (!opts || typeof opts !== 'object') {
      return { ok: false, error: 'opts must be an object' };
    }
    return renderHtmlToPng(opts);
  });
}

module.exports = {
  registerSlidesHandlers,
  renderHtmlToPng, // exported for the bridge-server endpoint
};
