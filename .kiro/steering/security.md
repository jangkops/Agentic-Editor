# Security Steering  ## Electron - contextIsolation: true - nodeIntegration: false - CSP in index.html:   "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'"  ## Credentials - NEVER store AWS credentials in any file - settings.json stores profile NAME only - Credentials fetched at runtime per-request via aws-sso-manager.js - Credentials passed to Python via IPC, not stored anywhere  ## IPC - All handlers registered in electron/main.js only - preload.js exposes only whitelisted methods via contextBridge - Never expose ipcRenderer to renderer  ## API Token - Mask in all logs: token.substring(0,4) + '****' - Load from userData/settings/settings.json at runtime only

## Research provider API keys (all OPTIONAL — keyless paths exist)
- Keys are NOT required to use research. Tavily runs keyless (`X-Tavily-Access-Mode: keyless`); OpenAlex / Europe PMC / PubMed / arXiv are keyless. Only Exa and Brave require a key (`backend._REQUIRES_KEY`). Do NOT reintroduce Tavily into `_REQUIRES_KEY` — it forces per-user signup on a 30-seat deployment
- A key, when present, only raises rate limits. Response schema and parsers are unchanged
- NEVER store in settings.json or any plaintext file
- Encrypt with Electron `safeStorage` (OS keychain) → `userData/settings/research-credentials.json` holds base64 ciphertext only, file mode 0600
- If `safeStorage.isEncryptionAvailable()` is false, REFUSE to save. No plaintext fallback
- Renderer sees booleans only (`research-creds:status`). There is intentionally NO channel that returns a key value
- Decrypted values live in main-process memory and are pushed to the Python sidecar at runtime via `POST /api/research/credentials`, which sets `os.environ` only (never a file). Runtime push (not spawn-time env) because dev mode does not start Python from Electron
- `ai_engine/research/security.py` `load_credential()` reads `os.environ` only; log via `mask_secret()` only
- Owner: `electron/core/research-credentials.js` (single source). Handlers registered in `electron/main.js` only