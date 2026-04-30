/**
 * Project Analysis IPC Handlers
 * 책임: 프로젝트 분석 (파일 통계), 의존성 분석
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

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
        if (depth > 10) return; // 깊이 제한

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

      return result;
    } catch (error) {
      console.error('[project:dependencies] Error:', error.message);
      return null;
    }
  });
}

module.exports = { registerProjectHandlers };
