const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const aiDir = path.join(__dirname, '..', 'ai_engine');
const distDir = path.join(__dirname, '..', 'dist-python');

console.log('[build-python] Building Python backend with PyInstaller...');

// Ensure dist directory
if (!fs.existsSync(distDir)) {
  fs.mkdirSync(distDir, { recursive: true });
}

try {
  // Install dependencies
  execSync('pip install -r requirements.txt pyinstaller', {
    cwd: aiDir,
    stdio: 'inherit',
  });

  // Build with PyInstaller
  execSync(
    `pyinstaller --onefile --name ai-engine-server ` +
    `--add-data "agent_system:agent_system" ` +
    `--hidden-import uvicorn --hidden-import fastapi ` +
    `--hidden-import httpx --hidden-import boto3 ` +
    `--distpath "${distDir}" ` +
    `server.py`,
    {
      cwd: aiDir,
      stdio: 'inherit',
    }
  );

  console.log('[build-python] ✓ Build complete:', distDir);
} catch (err) {
  console.error('[build-python] Build failed:', err.message);
  process.exit(1);
}
