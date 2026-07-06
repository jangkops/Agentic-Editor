/* Build the Python backend into a frozen onedir binary via PyInstaller.
 *
 * 산출물: ai_engine_dist/ai-engine-server/ai-engine-server[.exe]
 *   (electron-builder.yml의 extraResources `ai_engine_dist`와 경로 일치)
 *
 * OS별로 각 러너에서 실행해야 한다(PyInstaller는 크로스컴파일 불가).
 * GitHub Actions matrix(macos-latest, windows-latest)가 각 OS에서 이 스크립트를 돌린다.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const root = path.join(__dirname, '..');
const aiDir = path.join(root, 'ai_engine');
const outDir = path.join(root, 'ai_engine_dist');
const specFile = path.join(root, 'ai-engine-server.spec');
const isWin = process.platform === 'win32';

// 동결 빌드용 python 선택: venv 우선, 없으면 시스템 python
function resolvePython() {
  const venv = path.join(aiDir, '.venv');
  const cand = isWin
    ? [path.join(venv, 'Scripts', 'python.exe'), 'python']
    : [path.join(venv, 'bin', 'python'), 'python3', 'python'];
  for (const p of cand) {
    try {
      if (p.includes(path.sep)) {
        if (fs.existsSync(p)) return p;
      } else {
        execSync(`${p} --version`, { stdio: 'ignore' });
        return p;
      }
    } catch (_) { /* try next */ }
  }
  return isWin ? 'python' : 'python3';
}

const py = resolvePython();
console.log(`[build-python] python: ${py}`);

try {
  console.log('[build-python] installing deps + pyinstaller...');
  execSync(`"${py}" -m pip install --upgrade pip`, { cwd: root, stdio: 'inherit' });
  execSync(`"${py}" -m pip install -r "${path.join(aiDir, 'requirements.txt')}" pyinstaller`, {
    cwd: root, stdio: 'inherit',
  });

  // 이전 산출물 정리
  if (fs.existsSync(outDir)) fs.rmSync(outDir, { recursive: true, force: true });

  console.log('[build-python] running PyInstaller (spec)...');
  execSync(
    `"${py}" -m PyInstaller --noconfirm --clean ` +
    `--distpath "${outDir}" --workpath "${path.join(root, 'build-python-work')}" ` +
    `"${specFile}"`,
    { cwd: root, stdio: 'inherit' }
  );

  // 검증 — 바이너리 존재 확인
  const binName = isWin ? 'ai-engine-server.exe' : 'ai-engine-server';
  const binPath = path.join(outDir, 'ai-engine-server', binName);
  if (!fs.existsSync(binPath)) {
    throw new Error(`frozen binary not found: ${binPath}`);
  }
  console.log(`[build-python] ✓ done: ${binPath}`);
} catch (err) {
  console.error('[build-python] FAILED:', err.message);
  process.exit(1);
}
