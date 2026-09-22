/* Build the Python backend into a frozen onedir binary via PyInstaller.
 *
 * 산출물: ai_engine_dist/ai-engine-server/ai-engine-server[.exe]
 *   (electron-builder.yml의 extraResources `ai_engine_dist`와 경로 일치)
 *
 * OS별로 각 러너에서 실행해야 한다(PyInstaller는 크로스컴파일 불가).
 * GitHub Actions matrix(macos-latest, windows-latest)가 각 OS에서 이 스크립트를 돌린다.
 *
 * 빌드 본문은 main() 안에 있고 직접 실행(`node scripts/build-python.js`)일 때만 돈다.
 * 테스트는 이 파일을 require 해 순수 함수(materializeSymlinks 등)만 검사한다.
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

/** dir 아래(재귀)의 심볼릭 링크 개수. Dirent 타입은 파일시스템에 따라 UNKNOWN 일 수 있어 lstat 로 판정. */
function countSymlinks(dir) {
  let n = 0;
  for (const name of fs.readdirSync(dir)) {
    const p = path.join(dir, name);
    const st = fs.lstatSync(p);
    if (st.isSymbolicLink()) n++;
    else if (st.isDirectory()) n += countSymlinks(p);
  }
  return n;
}

/**
 * Hugging Face 캐시 레이아웃의 심볼릭 링크를 실제 파일로 바꾼다.
 *
 * fastembed(huggingface_hub)는 모델 파일을 `models--<이름>/blobs/<sha>` 에 저장하고
 * `models--<이름>/snapshots/<rev>/<파일>` 을 그 blob 을 가리키는 상대 심볼릭 링크로 만든다
 * (실측: config.json, model_optimized.onnx, special_tokens_map.json, tokenizer.json,
 * tokenizer_config.json 5개가 `../../blobs/<sha>` 링크). Windows 러너에서 electron-builder
 * 의 7-Zip 이 이 링크를 따라가지 못해 "WARNING: The directory name is invalid." 로 패키징이
 * 실패했다(2026-09-17 릴리스 검증 빌드). 최종 사용자 PC 가 심볼릭 링크를 지원하지 않을 수도 있다.
 *
 * 링크를 실체화한 뒤, 링크가 하나도 남지 않은 모델 디렉터리의 `blobs/` 는 중복이므로 지운다
 * (안 지우면 번들이 두 배 — MiniLM 기준 240MB → 480MB). huggingface_hub 의 오프라인 해석은
 * `refs/<rev>` → `snapshots/<rev>/<파일>` 존재만 확인하고 blobs 는 보지 않으므로 로드에 영향이 없다.
 * 깨진 링크(대상 없음)는 패키저도 못 따라가므로 제거한다.
 *
 * @param {string} cacheRoot fastembed_models 디렉터리
 * @returns {number} 실체화 또는 제거한 링크 수 (디렉터리가 없으면 0)
 */
function materializeSymlinks(cacheRoot) {
  if (!fs.existsSync(cacheRoot)) return 0;
  let count = 0;

  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const p = path.join(dir, name);
      const st = fs.lstatSync(p);
      if (st.isSymbolicLink()) {
        let real = null;
        try { real = fs.realpathSync(p); } catch (_) { real = null; }
        if (!real) {
          fs.unlinkSync(p);
          count++;
          continue;
        }
        const tmp = `${p}.materialize-tmp`;
        fs.rmSync(tmp, { recursive: true, force: true });
        if (fs.statSync(real).isDirectory()) {
          fs.cpSync(real, tmp, { recursive: true, dereference: true });
        } else {
          fs.copyFileSync(real, tmp);
        }
        fs.unlinkSync(p);
        fs.renameSync(tmp, p);
        count++;
      } else if (st.isDirectory()) {
        walk(p);
      }
    }
  };
  walk(cacheRoot);

  for (const name of fs.readdirSync(cacheRoot)) {
    if (!name.startsWith('models--')) continue;
    const modelDir = path.join(cacheRoot, name);
    if (!fs.lstatSync(modelDir).isDirectory()) continue;
    const blobs = path.join(modelDir, 'blobs');
    if (fs.existsSync(blobs) && countSymlinks(modelDir) === 0) {
      fs.rmSync(blobs, { recursive: true, force: true });
    }
  }
  return count;
}

function main() {
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
    console.log(`[build-python] ✓ frozen binary: ${binPath}`);

    // ── 오프라인 RAG 임베딩 모델 사전 번들 ──────────────────────────
    // fastembed는 런타임에 모델을 다운로드하므로, 오프라인/사내망 배포를 위해 선택한
    // 다국어 모델을 실행파일 옆 fastembed_models/ 에 사전 다운로드한다. 런타임에는
    // FastEmbedProvider가 이 디렉터리를 자동 인식(_bundled_fastembed_cache)한다.
    // 기본 모델은 벤치 승자(용량/품질 균형)인 MiniLM(0.22GB). AE_BUNDLE_EMBED_MODEL로 교체.
    const bundleModel = process.env.AE_BUNDLE_EMBED_MODEL
      || 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2';
    const modelCacheDir = path.join(outDir, 'ai-engine-server', 'fastembed_models');
    try {
      console.log(`[build-python] pre-downloading embed model → ${modelCacheDir}`);
      fs.mkdirSync(modelCacheDir, { recursive: true });
      const dlCode = [
        'import sys',
        'from fastembed import TextEmbedding',
        `m = TextEmbedding(model_name=${JSON.stringify(bundleModel)}, cache_dir=${JSON.stringify(modelCacheDir)})`,
        'v = list(m.embed(["passage: warmup"]))[0]',
        'print("[build-python] model ready, dim=", len(v))',
      ].join('; ');
      execSync(`"${py}" -c ${JSON.stringify(dlCode)}`, { cwd: root, stdio: 'inherit' });

      // HF 캐시의 심볼릭 링크 실체화 (Windows 7-Zip 패키징 실패 방지, 링크 미지원 PC 대비).
      // 도중에 실패하면 반쯤 바뀐 캐시를 남기지 않고 통째로 지워 런타임 폴백(LSA)이 깨끗하게 동작하게 한다.
      let materialized;
      try {
        materialized = materializeSymlinks(modelCacheDir);
      } catch (e) {
        fs.rmSync(modelCacheDir, { recursive: true, force: true });
        throw new Error(`symlink materialization failed, bundle removed: ${e.message}`);
      }
      console.log(`[build-python] ✓ embed model bundled (offline-ready); symlinks materialized: ${materialized}`);
    } catch (e) {
      // 모델 번들 실패는 치명적이지 않다 — 런타임에 LSA/TF-IDF로 폴백(무회귀).
      console.warn('[build-python] ⚠ embed model bundle skipped (runtime LSA fallback):', e.message);
    }

    console.log(`[build-python] ✓ done: ${binPath}`);
  } catch (err) {
    console.error('[build-python] FAILED:', err.message);
    process.exit(1);
  }
}

if (require.main === module) main();

module.exports = { resolvePython, countSymlinks, materializeSymlinks };
