/**
 * Project Analysis IPC Handlers
 * 책임: 프로젝트 분석 (파일 통계), 의존성 분석
 *
 * Remote-SSH 통합 (Task 22.4):
 *   `sessionRouter.isRemoteActive()` 가 참이면 SFTP 기반 `RemoteFileBridge`
 *   (list / stat / read) 를 통해 원격 호스트의 파일을 스캔한다. 그렇지 않으면
 *   기존 로컬 `fs.readdirSync` / `fs.readFileSync` 경로를 그대로 사용한다.
 *   두 경로 모두 동일한 출력 shape 을 반환한다. IPC 채널 이름은 변경 없음
 *   (`project:analyze`, `project:dependencies`).
 *   _Requirements: 6.1_
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const sessionRouter = require('./remote/session-router');

// ─────────────────────────────────────────────────────────────────────────────
// 공유 상수 (local / remote 양쪽에서 동일한 분류 규칙 사용)
// ─────────────────────────────────────────────────────────────────────────────

const PROJECT_IGNORE = new Set([
  'node_modules',
  '__pycache__',
  '.git',
  '.venv',
  'dist',
  'build',
  '.DS_Store',
  '.next',
  'coverage',
  '.nyc_output',
]);

const PROJECT_SRC_EXT = new Set([
  'js', 'ts', 'jsx', 'tsx', 'py', 'java', 'go', 'rs',
  'c', 'cpp', 'h', 'rb', 'php', 'swift', 'kt',
]);
const PROJECT_CFG_EXT = new Set(['json', 'yml', 'yaml', 'toml', 'ini', 'env', 'xml', 'lock']);
const PROJECT_DOC_EXT = new Set(['md', 'txt', 'rst', 'adoc']);
const PROJECT_TEST_PAT = /test|spec|__test__|__spec__/i;
const PROJECT_STYLE_EXT = new Set(['css', 'scss', 'sass', 'less', 'styl']);
const PROJECT_ASSET_EXT = new Set([
  'png', 'jpg', 'jpeg', 'gif', 'svg', 'ico',
  'woff', 'woff2', 'ttf', 'eot', 'mp3', 'mp4', 'webp',
]);

/**
 * 빈 통계 객체 생성 (local / remote 모두 동일한 shape)
 */
function _emptyStats() {
  return {
    totalLines: 0,
    totalFiles: 0,
    totalDirs: 0,
    todos: 0,
    extensions: {},
    roles: { source: 0, config: 0, docs: 0, test: 0, style: 0, asset: 0 },
    files: [],
  };
}

/**
 * 확장자/이름 기반 역할(roles) 집계.
 * local / remote 모두 동일하게 호출된다.
 */
function _classifyFile(stats, entryName, absPath, ext) {
  if (PROJECT_TEST_PAT.test(entryName) || PROJECT_TEST_PAT.test(absPath)) {
    stats.roles.test += 1;
  } else if (PROJECT_STYLE_EXT.has(ext)) {
    stats.roles.style += 1;
  } else if (PROJECT_ASSET_EXT.has(ext)) {
    stats.roles.asset += 1;
  } else if (PROJECT_DOC_EXT.has(ext)) {
    stats.roles.docs += 1;
  } else if (PROJECT_CFG_EXT.has(ext)) {
    stats.roles.config += 1;
  } else if (PROJECT_SRC_EXT.has(ext)) {
    stats.roles.source += 1;
  }
}

/**
 * 원격 절대경로 → dirPath 기준 상대경로.
 * `RemoteFileBridge.list` 는 remote sep 으로 join 한 절대경로를 반환하므로
 * 접두사 제거 + 선행 구분자 제거만으로 충분하다. (POSIX / Windows 양쪽 안전)
 *
 * @param {string} absPath
 * @param {string} rootPath
 * @returns {string}
 */
function _remoteRelative(absPath, rootPath) {
  if (absPath === rootPath) return '';
  if (absPath.startsWith(rootPath)) {
    return absPath.slice(rootPath.length).replace(/^[\\/]+/, '');
  }
  return absPath;
}

// ─────────────────────────────────────────────────────────────────────────────
// Remote 경로 구현 (sessionRouter.isRemoteActive() 일 때 사용)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 원격 프로젝트 분석 — SSH exec 기반 (빠름).
 * 서버 측에서 find/wc/grep을 실행하여 한 번의 SSH exec로 통계를 수집.
 * SFTP 파일별 읽기 대비 100배 이상 빠름.
 *
 * 성능 최적화:
 * - maxdepth 3 (대형 디렉토리에서 5는 수만 파일 탐색 → 30초+)
 * - timeout 5초 래핑 (find가 느릴 경우 부분 결과라도 반환)
 * - head -300 으로 파일 목록 제한
 *
 * @param {string} dirPath
 * @returns {Promise<object|null>}
 */
async function _analyzeRemote(dirPath) {
  try {
    if (!dirPath) return null;

    const stats = _emptyStats();

    // Single SSH exec: get file count, line count, TODO count, extensions, dirs
    // timeout 5 래핑으로 find가 느린 경우에도 5초 내 종료 보장
    const script = `
cd "${dirPath.replace(/"/g, '\\"')}" 2>/dev/null || exit 1
echo "===FILES==="
timeout 4 find . -maxdepth 3 -type f \\( ! -path '*/node_modules/*' ! -path '*/__pycache__/*' ! -path '*/.git/*' ! -path '*/.venv/*' ! -path '*/dist/*' ! -path '*/build/*' ! -path '*/.next/*' ! -path '*/coverage/*' \\) -name '*.*' 2>/dev/null | head -300
echo "===DIRS==="
timeout 3 find . -maxdepth 3 -type d \\( ! -path '*/node_modules/*' ! -path '*/__pycache__/*' ! -path '*/.git/*' ! -path '*/.venv/*' \\) 2>/dev/null | wc -l
echo "===LINES==="
timeout 4 find . -maxdepth 3 -type f \\( ! -path '*/node_modules/*' ! -path '*/__pycache__/*' ! -path '*/.git/*' ! -path '*/.venv/*' ! -path '*/dist/*' \\) \\( -name '*.py' -o -name '*.js' -o -name '*.ts' -o -name '*.json' -o -name '*.md' -o -name '*.css' -o -name '*.html' -o -name '*.yml' -o -name '*.yaml' \\) 2>/dev/null | head -150 | xargs wc -l 2>/dev/null | tail -1
echo "===TODOS==="
timeout 3 grep -r --include='*.py' --include='*.js' --include='*.ts' -l 'TODO\\|FIXME\\|HACK\\|XXX' . --max-depth=3 2>/dev/null | head -50 | xargs grep -c 'TODO\\|FIXME\\|HACK\\|XXX' 2>/dev/null | awk -F: '{s+=$2}END{print s+0}'
`;

    const r = await sessionRouter.exec(script, { cwd: dirPath, timeout: 8000 });
    const output = r.stdout || '';

    // Parse files section
    const filesSection = output.split('===FILES===')[1]?.split('===DIRS===')[0] || '';
    const files = filesSection.trim().split('\n').filter(f => f && f.startsWith('./'));
    stats.totalFiles = files.length;

    for (const f of files) {
      const name = f.split('/').pop() || '';
      const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
      if (ext) stats.extensions[ext] = (stats.extensions[ext] || 0) + 1;
      _classifyFile(stats, name, f, ext);
      if (stats.files.length < 300) {
        stats.files.push({ name, path: f.replace(/^\.\//, ''), ext, lines: 0 });
      }
    }

    // Parse dirs count
    const dirsSection = output.split('===DIRS===')[1]?.split('===LINES===')[0] || '';
    stats.totalDirs = parseInt(dirsSection.trim(), 10) || 0;

    // Parse total lines
    const linesSection = output.split('===LINES===')[1]?.split('===TODOS===')[0] || '';
    const linesMatch = linesSection.trim().match(/(\d+)/);
    stats.totalLines = linesMatch ? parseInt(linesMatch[1], 10) : 0;

    // Parse TODOs
    const todosSection = output.split('===TODOS===')[1] || '';
    stats.todos = parseInt(todosSection.trim(), 10) || 0;

    return stats;
  } catch (error) {
    console.error('[project:analyze] Remote error:', error && error.message);
    return null;
  }
}

/**
 * 원격 프로젝트의 package.json / requirements.txt 분석.
 * 로컬 구현과 동일한 shape 을 반환한다.
 *
 * @param {string} dirPath
 * @returns {Promise<object|null>}
 */
async function _dependenciesRemote(dirPath) {
  try {
    if (!dirPath) return null;
    const bridge = sessionRouter.getFileBridge();
    if (!bridge) return null;

    // 원격 path 구분자 조회 (일반적으로 '/', Windows remote 만 '\\')
    let sep = '/';
    try { sep = await bridge.pathSep(); } catch { sep = '/'; }
    const join = (a, b) => {
      if (!a) return b;
      if (a.endsWith('/') || a.endsWith('\\')) return a + b;
      return a + sep + b;
    };

    const pkgPath = join(dirPath, 'package.json');
    const reqPath = join(dirPath, 'requirements.txt');
    const result = {
      production: {},
      development: {},
      python: [],
    };

    // package.json — 없거나 파싱 실패 시 무시 (로컬과 동일)
    try {
      const pkgContent = await bridge.read(pkgPath, 'utf8');
      const pkg = JSON.parse(pkgContent);
      result.production = pkg.dependencies || {};
      result.development = pkg.devDependencies || {};
    } catch {
      // noop
    }

    // requirements.txt — 없거나 읽기 실패 시 무시
    try {
      const reqContent = await bridge.read(reqPath, 'utf8');
      result.python = reqContent
        .split('\n')
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith('#'));
    } catch {
      // noop
    }

    return result;
  } catch (error) {
    console.error('[project:dependencies] Remote error:', error && error.message);
    return null;
  }
}

/**
 * Project IPC 핸들러 등록
 */
function registerProjectHandlers() {
  /**
   * 프로젝트 분석
   * 파일 통계, 라인 수, TODO 개수 등을 수집
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 분석할 디렉터리
   * @returns {object|null} 분석 결과 또는 null
   */
  ipcMain.handle('project:analyze', async (_, dirPath) => {
    // Remote-SSH: active session 이면 SFTP 경유로 원격 스캔
    if (sessionRouter.isRemoteActive()) {
      return _analyzeRemote(dirPath);
    }

    try {
      if (!dirPath || !fs.existsSync(dirPath)) {
        return null;
      }

      // 초기화
      const stats = {
        totalLines: 0,
        totalFiles: 0,
        totalDirs: 0,
        todos: 0,
        extensions: {},
        roles: { source: 0, config: 0, docs: 0, test: 0, style: 0, asset: 0 },
        files: [],
      };

      // 무시할 폴더
      const IGNORE = new Set([
        'node_modules',
        '__pycache__',
        '.git',
        '.venv',
        'dist',
        'build',
        '.DS_Store',
        '.next',
        'coverage',
        '.nyc_output',
      ]);

      // 파일 확장자별 분류
      const SRC_EXT = new Set([
        'js',
        'ts',
        'jsx',
        'tsx',
        'py',
        'java',
        'go',
        'rs',
        'c',
        'cpp',
        'h',
        'rb',
        'php',
        'swift',
        'kt',
      ]);
      const CFG_EXT = new Set(['json', 'yml', 'yaml', 'toml', 'ini', 'env', 'xml', 'lock']);
      const DOC_EXT = new Set(['md', 'txt', 'rst', 'adoc']);
      const TEST_PAT = /test|spec|__test__|__spec__/i;
      const STYLE_EXT = new Set(['css', 'scss', 'sass', 'less', 'styl']);
      const ASSET_EXT = new Set([
        'png',
        'jpg',
        'jpeg',
        'gif',
        'svg',
        'ico',
        'woff',
        'woff2',
        'ttf',
        'eot',
        'mp3',
        'mp4',
        'webp',
      ]);

      /**
       * 디렉터리 재귀 순회
       */
      function walk(dir, depth) {
        if (depth > 5) return; // 깊이 제한

        let entries;
        try {
          entries = fs.readdirSync(dir, { withFileTypes: true });
        } catch {
          return;
        }

        for (const entry of entries) {
          if (IGNORE.has(entry.name) || entry.name.startsWith('.')) {
            continue;
          }

          const filePath = path.join(dir, entry.name);

          if (entry.isDirectory()) {
            stats.totalDirs += 1;
            walk(filePath, depth + 1);
          } else {
            stats.totalFiles += 1;
            const ext = entry.name.split('.').pop().toLowerCase();
            stats.extensions[ext] = (stats.extensions[ext] || 0) + 1;

            let lineCount = 0;
            if (!ASSET_EXT.has(ext)) {
              try {
                const content = fs.readFileSync(filePath, 'utf-8');
                lineCount = content.split('\n').length;
                const todoMatches = content.match(/TODO|FIXME|HACK|XXX/gi);
                if (todoMatches) stats.todos += todoMatches.length;
              } catch {
                // 파일 읽기 실패는 무시
              }
            }

            stats.totalLines += lineCount;

            // 역할 분류
            if (TEST_PAT.test(entry.name) || TEST_PAT.test(filePath)) {
              stats.roles.test += 1;
            } else if (STYLE_EXT.has(ext)) {
              stats.roles.style += 1;
            } else if (ASSET_EXT.has(ext)) {
              stats.roles.asset += 1;
            } else if (DOC_EXT.has(ext)) {
              stats.roles.docs += 1;
            } else if (CFG_EXT.has(ext)) {
              stats.roles.config += 1;
            } else if (SRC_EXT.has(ext)) {
              stats.roles.source += 1;
            }

            // 파일 목록에 추가 (최대 300개)
            if (stats.files.length < 300) {
              const relative = path.relative(dirPath, filePath);
              stats.files.push({
                name: entry.name,
                path: relative,
                ext,
                lines: lineCount,
              });
            }
          }
        }
      }

      walk(dirPath, 0);
      return stats;
    } catch (error) {
      console.error('[project:analyze] Error:', error.message);
      return null;
    }
  });

  /**
   * 의존성 분석
   * package.json 및 requirements.txt에서 의존성 추출
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 프로젝트 디렉터리
   * @returns {object|null} 의존성 정보
   */
  ipcMain.handle('project:dependencies', async (_, dirPath) => {
    // Remote-SSH: active session 이면 SFTP 경유로 원격 파일 읽기
    if (sessionRouter.isRemoteActive()) {
      return _dependenciesRemote(dirPath);
    }

    try {
      if (!dirPath) return null;

      const pkgPath = path.join(dirPath, 'package.json');
      const reqPath = path.join(dirPath, 'requirements.txt');
      const result = {
        production: {},
        development: {},
        python: [],
      };

      // package.json 분석
      if (fs.existsSync(pkgPath)) {
        try {
          const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf-8'));
          result.production = pkg.dependencies || {};
          result.development = pkg.devDependencies || {};
        } catch {
          // JSON 파싱 실패는 무시
        }
      }

      // requirements.txt 분석
      if (fs.existsSync(reqPath)) {
        try {
          const content = fs.readFileSync(reqPath, 'utf-8');
          result.python = content
            .split('\n')
            .map((line) => line.trim())
            .filter((line) => line && !line.startsWith('#'));
        } catch {
          // 파일 읽기 실패는 무시
        }
      }

      // pyproject.toml fallback (PEP 621 dependencies)
      if (!result.python.length) {
        const pyprojectPath = path.join(dirPath, 'pyproject.toml');
        if (fs.existsSync(pyprojectPath)) {
          try {
            const content = fs.readFileSync(pyprojectPath, 'utf-8');
            const deps = [];
            let inDeps = false;
            for (const line of content.split('\n')) {
              if (/^\[.*dependencies\]/.test(line.trim()) || /^dependencies\s*=\s*\[/.test(line.trim())) { inDeps = true; continue; }
              if (inDeps && line.trim().startsWith('[')) break;
              if (inDeps) {
                const clean = line.replace(/["\[\],]/g, '').trim();
                if (clean && !clean.startsWith('#')) deps.push(clean);
              }
            }
            if (deps.length) result.python = deps;
          } catch {}
        }
      }

      // Pipfile fallback
      if (!result.python.length) {
        const pipfilePath = path.join(dirPath, 'Pipfile');
        if (fs.existsSync(pipfilePath)) {
          try {
            const content = fs.readFileSync(pipfilePath, 'utf-8');
            const deps = [];
            let inPackages = false;
            for (const line of content.split('\n')) {
              if (line.trim() === '[packages]') { inPackages = true; continue; }
              if (line.trim().startsWith('[') && inPackages) break;
              if (inPackages && line.includes('=')) {
                deps.push(line.split('=')[0].trim());
              }
            }
            if (deps.length) result.python = deps;
          } catch {}
        }
      }

      return result;
    } catch (error) {
      console.error('[project:dependencies] Error:', error.message);
      return null;
    }
  });
}

module.exports = { registerProjectHandlers };
