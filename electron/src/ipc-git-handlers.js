/**
 * Git IPC Handlers
 * 책임: Git 로그, 커밋, 브랜치, 상태, 검색 등
 */

const { ipcMain } = require('electron');
const { execSync } = require('child_process');

/**
 * Git IPC 핸들러 등록
 */
function registerGitHandlers() {
  /**
   * Git 로그 조회
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 저장소 디렉터리
   * @param {number} limit - 표시할 커밋 개수 (기본 50)
   * @returns {Array} 커밋 정보 배열
   */
  ipcMain.handle('git:log', async (_, dirPath, limit = 50) => {
    try {
      const cmd = `git log --oneline --decorate --all -n ${limit}`;
      const output = execSync(cmd, {
        cwd: dirPath,
        encoding: 'utf-8',
        timeout: 10000,
      });

      return output
        .split('\n')
        .filter(Boolean)
        .map((line) => {
          const match = line.match(/^([a-f0-9]+)\s+(?:\(([^)]+)\)\s+)?(.+)$/);
          if (!match) {
            return { hash: '', refs: '', message: line };
          }
          return { hash: match[1], refs: match[2] || '', message: match[3] };
        });
    } catch (error) {
      console.error('[git:log] Error:', error.message);
      return [];
    }
  });

  /**
   * 특정 커밋 상세 정보
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 저장소 디렉터리
   * @param {string} hash - 커밋 해시
   * @returns {object|null} 커밋 정보 (hash, author, email, date, subject, body, stat, diff)
   */
  ipcMain.handle('git:show', async (_, dirPath, hash) => {
    try {
      const showCmd = `git show --stat --format="%H%n%an%n%ae%n%ai%n%s%n%b%n---STAT---" ${hash}`;
      const info = execSync(showCmd, {
        cwd: dirPath,
        encoding: 'utf-8',
        timeout: 10000,
      });

      const parts = info.split('---STAT---');
      const lines = parts[0].split('\n');

      // diff 조회
      let diff = '';
      try {
        diff = execSync(`git diff ${hash}~1 ${hash} 2>/dev/null || git show ${hash} --format=""`, {
          cwd: dirPath,
          encoding: 'utf-8',
          timeout: 10000,
        });
      } catch {
        // diff 없을 수 있음 (첫 커밋 등)
      }

      return {
        hash: lines[0] || '',
        author: lines[1] || '',
        email: lines[2] || '',
        date: lines[3] || '',
        subject: lines[4] || '',
        body: lines.slice(5).join('\n').trim(),
        stat: (parts[1] || '').trim(),
        diff,
      };
    } catch (error) {
      console.error('[git:show] Error:', error.message);
      return null;
    }
  });

  /**
   * 텍스트 검색 (grep)
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 저장소 디렉터리
   * @param {string} query - 검색 쿼리
   * @param {object} options - {caseSensitive: boolean}
   * @returns {Array} [{file, matches: [{line, text}]}]
   */
  ipcMain.handle('git:search', async (_, dirPath, query, options) => {
    try {
      const caseSensitiveFlag = options?.caseSensitive ? '' : '-i';
      const flags = `${caseSensitiveFlag} -n --include="*"`;
      const cmd = `grep -r ${flags} --color=never -l "${query.replace(/"/g, '\\"')}" . 2>/dev/null | head -50`;

      const result = execSync(cmd, {
        cwd: dirPath,
        encoding: 'utf-8',
        timeout: 15000,
      });

      const files = result.split('\n').filter(Boolean);
      const matches = [];

      for (const file of files.slice(0, 30)) {
        try {
          const grepCmd = `grep -n ${
            options?.caseSensitive ? '' : '-i'
          } --color=never "${query.replace(/"/g, '\\"')}" "${file}" 2>/dev/null | head -10`;
          const grepLines = execSync(grepCmd, {
            cwd: dirPath,
            encoding: 'utf-8',
            timeout: 5000,
          });

          const fileMatches = grepLines
            .split('\n')
            .filter(Boolean)
            .map((line) => {
              const match = line.match(/^(\d+):(.*)$/);
              return match ? { line: +match[1], text: match[2].trim() } : null;
            })
            .filter(Boolean);

          if (fileMatches.length > 0) {
            matches.push({
              file: file.replace(/^\.\//, ''),
              matches: fileMatches,
            });
          }
        } catch {
          // 개별 파일 검색 실패는 무시
        }
      }

      return matches;
    } catch (error) {
      console.error('[git:search] Error:', error.message);
      return [];
    }
  });

  /**
   * 브랜치 목록 조회
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 저장소 디렉터리
   * @returns {object} {current, local, remote, error?}
   */
  ipcMain.handle('git:branches', async (_, dirPath) => {
    try {
      if (!dirPath) {
        return { current: null, local: [], remote: [], error: 'no_dir' };
      }

      let current = null;
      try {
        current = execSync('git rev-parse --abbrev-ref HEAD', {
          cwd: dirPath,
          encoding: 'utf-8',
          timeout: 5000,
        }).trim();
      } catch {
        // 브랜치가 없을 수 있음 (detached HEAD)
      }

      const localRaw = execSync('git branch --format="%(refname:short)"', {
        cwd: dirPath,
        encoding: 'utf-8',
        timeout: 5000,
      });
      const local = localRaw.split('\n').map((s) => s.trim()).filter(Boolean);

      let remote = [];
      try {
        const remoteRaw = execSync('git branch -r --format="%(refname:short)"', {
          cwd: dirPath,
          encoding: 'utf-8',
          timeout: 5000,
        });
        remote = remoteRaw
          .split('\n')
          .map((s) => s.trim())
          .filter((s) => s && !s.includes('HEAD'));
      } catch {
        // 원격 브랜치 없음
      }

      return { current, local, remote };
    } catch (error) {
      console.error('[git:branches] Error:', error.message);
      return { current: null, local: [], remote: [], error: error.message };
    }
  });

  /**
   * Git 저장소 상태 조회
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 저장소 디렉터리
   * @returns {object} {clean, dirtyCount, entries, error?}
   */
  ipcMain.handle('git:status', async (_, dirPath) => {
    try {
      if (!dirPath) {
        return { clean: false, error: 'no_dir' };
      }

      const output = execSync('git status --porcelain', {
        cwd: dirPath,
        encoding: 'utf-8',
        timeout: 5000,
      });

      const lines = output.split('\n').filter(Boolean);
      return {
        clean: lines.length === 0,
        dirtyCount: lines.length,
        entries: lines.slice(0, 50),
      };
    } catch (error) {
      console.error('[git:status] Error:', error.message);
      return { clean: false, error: error.message };
    }
  });

  /**
   * 브랜치 체크아웃
   * @param {IpcMainInvokeEvent} _ - IPC 이벤트
   * @param {string} dirPath - 저장소 디렉터리
   * @param {string} branch - 브랜치명 또는 원격 브랜치 (origin/xxx)
   * @param {object} options - 옵션 (선택사항)
   * @returns {object} {ok, output?, current?, error?}
   */
  ipcMain.handle('git:checkout', async (_, dirPath, branch, options) => {
    try {
      if (!dirPath || !branch) {
        return { ok: false, error: 'invalid_args' };
      }

      // 원격 브랜치(origin/xxx)인 경우 로컬 tracking 브랜치로 변환
      const isRemote = branch.includes('/') && !branch.startsWith('refs/heads/');
      let checkoutCmd;

      if (isRemote) {
        const localName = branch.replace(/^[^/]+\//, '');

        // 로컬에 이미 같은 이름의 브랜치가 있는지 확인
        let hasLocal = false;
        try {
          execSync(`git rev-parse --verify --quiet "refs/heads/${localName}"`, {
            cwd: dirPath,
            encoding: 'utf-8',
            timeout: 3000,
          });
          hasLocal = true;
        } catch {
          // 없음
        }

        if (hasLocal) {
          checkoutCmd = `git checkout ${localName}`;
        } else {
          checkoutCmd = `git checkout -b ${localName} --track ${branch}`;
        }
      } else {
        checkoutCmd = `git checkout ${branch}`;
      }

      const output = execSync(`${checkoutCmd} 2>&1`, {
        cwd: dirPath,
        encoding: 'utf-8',
        timeout: 15000,
      });

      // 체크아웃 후 현재 브랜치 재조회
      let current = null;
      try {
        current = execSync('git rev-parse --abbrev-ref HEAD', {
          cwd: dirPath,
          encoding: 'utf-8',
          timeout: 5000,
        }).trim();
      } catch {
        // 현재 브랜치 조회 실패
      }

      return { ok: true, output, current };
    } catch (error) {
      const msg = String(error.stdout || error.stderr || error.message || error);
      console.error('[git:checkout] Error:', msg);
      return { ok: false, error: msg };
    }
  });

  // ===== Checkpoint/Restore (Git Stash 기반) =====

  ipcMain.handle('git:stash-push', async (_, dirPath, message) => {
    try {
      if (!dirPath) return { ok: false, error: 'dirPath required' };
      const msg = message || `checkpoint-${Date.now()}`;
      // 변경사항이 없으면 stash 불필요
      const status = execSync('git status --porcelain', { cwd: dirPath, encoding: 'utf-8' }).trim();
      if (!status) return { ok: true, skipped: true, message: 'nothing to stash' };
      const output = execSync(`git stash push -m "${msg}" --include-untracked 2>&1`, {
        cwd: dirPath, encoding: 'utf-8', timeout: 10000,
      }).trim();
      return { ok: true, output, message: msg };
    } catch (error) {
      const msg = String(error.stdout || error.stderr || error.message || error);
      console.error('[git:stash-push] Error:', msg);
      return { ok: false, error: msg };
    }
  });

  ipcMain.handle('git:stash-pop', async (_, dirPath) => {
    try {
      if (!dirPath) return { ok: false, error: 'dirPath required' };
      // stash가 비어있는지 확인
      const list = execSync('git stash list', { cwd: dirPath, encoding: 'utf-8' }).trim();
      if (!list) return { ok: false, error: 'stash가 비어있습니다' };
      const output = execSync('git stash pop 2>&1', {
        cwd: dirPath, encoding: 'utf-8', timeout: 10000,
      }).trim();
      return { ok: true, output };
    } catch (error) {
      const msg = String(error.stdout || error.stderr || error.message || error);
      console.error('[git:stash-pop] Error:', msg);
      return { ok: false, error: msg };
    }
  });

  ipcMain.handle('git:stash-list', async (_, dirPath) => {
    try {
      if (!dirPath) return { ok: true, stashes: [] };
      const output = execSync('git stash list --format="%gd|%s|%ci"', {
        cwd: dirPath, encoding: 'utf-8', timeout: 5000,
      }).trim();
      if (!output) return { ok: true, stashes: [] };
      const stashes = output.split('\n').map(line => {
        const [ref, message, date] = line.split('|');
        return { ref: ref.trim(), message: message.trim(), date: date.trim() };
      });
      return { ok: true, stashes };
    } catch (error) {
      return { ok: true, stashes: [] };
    }
  });

  ipcMain.handle('git:discard-all', async (_, dirPath) => {
    try {
      if (!dirPath) return { ok: false, error: 'dirPath required' };
      execSync('git checkout -- . 2>&1', { cwd: dirPath, encoding: 'utf-8', timeout: 10000 });
      execSync('git clean -fd 2>&1', { cwd: dirPath, encoding: 'utf-8', timeout: 10000 });
      return { ok: true };
    } catch (error) {
      const msg = String(error.stdout || error.stderr || error.message || error);
      return { ok: false, error: msg };
    }
  });
}

module.exports = { registerGitHandlers };
