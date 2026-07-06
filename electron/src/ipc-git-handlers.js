/**
 * Git IPC Handlers
 * 책임: Git 로그, 커밋, 브랜치, 상태, 검색 등
 *
 * Remote-SSH 통합 (Task 22.3):
 *   모든 `execSync(cmd, {cwd})` 호출을 `sessionRouter.exec(cmd, {cwd})` 로
 *   교체했다. Router 는 활성 RemoteSession 이 있을 때 SSH 를 통해 원격 호스트
 *   에서 명령을 실행하고, 없을 때는 로컬 `child_process.execSync` 로 위임한다.
 *   두 경로 모두 `{stdout, stderr, code}` 형태로 반환한다. 기존 코드는
 *   `execSync` 가 비-영 종료 시 throw 하는 패턴에 의존했으므로, 동일한 흐름을
 *   유지하기 위해 `run()` 헬퍼가 `code !== 0` 일 때 `Error` 를 던진다
 *   (기존 catch 블록이 그대로 동작하도록).
 */

const { ipcMain } = require('electron');
const sessionRouter = require('./remote/session-router');

/**
 * sessionRouter.exec 래퍼: 비-영 종료 시 기존 execSync 처럼 throw 하여
 * 핸들러의 try/catch 흐름을 보존한다.
 *
 * @param {string} cmd
 * @param {{cwd?:string, timeout?:number}} [opts]
 * @returns {Promise<string>} stdout (utf-8)
 */
async function run(cmd, opts) {
  const result = await sessionRouter.exec(cmd, opts || {});
  if (result.code !== 0) {
    const err = new Error(result.stderr || result.stdout || `exit ${result.code}`);
    err.code = result.code;
    err.status = result.code;
    err.stdout = result.stdout;
    err.stderr = result.stderr;
    throw err;
  }
  return result.stdout;
}

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
      const output = await run(cmd, {
        cwd: dirPath,
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
      const info = await run(showCmd, {
        cwd: dirPath,
        timeout: 10000,
      });

      const parts = info.split('---STAT---');
      const lines = parts[0].split('\n');

      // diff 조회
      let diff = '';
      try {
        // `2>/dev/null` 제거 — exec가 stderr를 분리 캡처하므로 Windows(cmd.exe)에서
        // 깨지는 Unix 리다이렉트가 불필요하다. `||` 폴백은 cmd.exe·sh 모두 지원.
        diff = await run(`git diff ${hash}~1 ${hash} || git show ${hash} --format=""`, {
          cwd: dirPath,
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
      if (!dirPath || !query) return [];
      const ci = options?.caseSensitive ? '' : '-i';
      const q = String(query).replace(/"/g, '\\"');
      // `git grep`은 크로스플랫폼(Windows용 Git 포함)이라 Unix 전용 grep/head/파이프/
      // `2>/dev/null` 없이 동작한다. 원격(SSH linux)에서도 동일하게 실행된다.
      // 출력 형식은 "path:line:text". 매치가 없으면 git grep이 비-영으로 종료 →
      // run()이 throw → 아래 catch에서 [] 반환(정상 흐름).
      // (참고: git 워크트리 밖 폴더는 검색되지 않는다 — .gitignore/바이너리 자동 제외 이점.)
      const cmd = `git grep --no-color -n -I ${ci} -e "${q}"`.replace(/\s+/g, ' ').trim();
      const result = await run(cmd, { cwd: dirPath, timeout: 15000 });

      const byFile = new Map();
      for (const line of result.split('\n')) {
        if (!line) continue;
        const m = line.match(/^(.+?):(\d+):(.*)$/); // path:line:text
        if (!m) continue;
        const file = m[1];
        if (!byFile.has(file)) {
          if (byFile.size >= 30) continue; // 최대 30개 파일
          byFile.set(file, []);
        }
        const arr = byFile.get(file);
        if (arr.length < 10) arr.push({ line: +m[2], text: m[3].trim() }); // 파일당 최대 10
      }

      return Array.from(byFile.entries()).map(([file, matches]) => ({ file, matches }));
    } catch (_error) {
      // 매치 없음(git grep exit 1)·비-git 폴더 포함 → 조용히 빈 결과
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
        current = (await run('git rev-parse --abbrev-ref HEAD', {
          cwd: dirPath,
          timeout: 5000,
        })).trim();
      } catch {
        // 브랜치가 없을 수 있음 (detached HEAD)
      }

      const localRaw = await run('git branch --format="%(refname:short)"', {
        cwd: dirPath,
        timeout: 5000,
      });
      const local = localRaw.split('\n').map((s) => s.trim()).filter(Boolean);

      let remote = [];
      try {
        const remoteRaw = await run('git branch -r --format="%(refname:short)"', {
          cwd: dirPath,
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

      const output = await run('git status --porcelain', {
        cwd: dirPath,
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
          await run(`git rev-parse --verify --quiet "refs/heads/${localName}"`, {
            cwd: dirPath,
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

      const output = await run(`${checkoutCmd} 2>&1`, {
        cwd: dirPath,
        timeout: 15000,
      });

      // 체크아웃 후 현재 브랜치 재조회
      let current = null;
      try {
        current = (await run('git rev-parse --abbrev-ref HEAD', {
          cwd: dirPath,
          timeout: 5000,
        })).trim();
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
      const status = (await run('git status --porcelain', { cwd: dirPath })).trim();
      if (!status) return { ok: true, skipped: true, message: 'nothing to stash' };
      const output = (await run(`git stash push -m "${msg}" --include-untracked 2>&1`, {
        cwd: dirPath, timeout: 10000,
      })).trim();
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
      const list = (await run('git stash list', { cwd: dirPath })).trim();
      if (!list) return { ok: false, error: 'stash가 비어있습니다' };
      const output = (await run('git stash pop 2>&1', {
        cwd: dirPath, timeout: 10000,
      })).trim();
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
      const output = (await run('git stash list --format="%gd|%s|%ci"', {
        cwd: dirPath, timeout: 5000,
      })).trim();
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
      await run('git checkout -- . 2>&1', { cwd: dirPath, timeout: 10000 });
      await run('git clean -fd 2>&1', { cwd: dirPath, timeout: 10000 });
      return { ok: true };
    } catch (error) {
      const msg = String(error.stdout || error.stderr || error.message || error);
      return { ok: false, error: msg };
    }
  });

  /**
   * 저장소 clone (GitHub 가져오기).
   *
   * 기존 구현은 터미널에 `git clone`을 문자열로 흘려보내고 대상 폴더에 파일이
   * 생기면 "성공"으로 오판했다. git clone은 실패해도 대상 디렉터리와 .git을 먼저
   * 만들기 때문에 인증 실패에도 "성공"으로 표시되는 버그가 있었다. 이 핸들러는
   * clone을 직접 실행하고 종료코드/stderr로 성패를 정확히 판정한다.
   *
   * 비대화식 강제(무한 대기 방지):
   *   - GIT_TERMINAL_PROMPT=0 : HTTPS 자격증명 프롬프트에서 멈추지 않고 즉시 실패
   *   - GIT_SSH_COMMAND (BatchMode=yes) : SSH 키/호스트키 프롬프트에서 멈추지 않음
   *     (StrictHostKeyChecking=accept-new 로 최초 호스트키는 자동 수용, 이후 검증)
   *
   * private 저장소 지원(token):
   *   token이 주어지고 URL이 https(github.com 등)면
   *   `https://x-access-token:<token>@host/...` 형태로 인증 URL을 만들어 clone한다.
   *   토큰은 어디에도 저장하지 않고 이 호출에서 1회만 사용하며, 반환하는
   *   output/error 문자열에서 토큰을 마스킹하여 로그/화면 노출을 막는다.
   *
   * @param {string} url    저장소 URL (https 또는 git@ SSH)
   * @param {string} branch 브랜치명 (빈 문자열이면 원격 기본 브랜치)
   * @param {string} dest   clone 대상 절대경로
   * @param {string} [token] private 저장소용 access token (선택, 저장 안 함)
   * @returns {{ok:boolean, dest?:string, error?:string}}
   */
  ipcMain.handle('git:clone', async (_, url, branch, dest, token) => {
    // 반환/로그 직전 토큰을 마스킹. 인증 URL(x-access-token:TOKEN@)과 토큰 원문
    // 모두를 가려 stderr에 자격증명이 새지 않게 한다.
    const rawToken = String(token || '').trim();
    const maskSecrets = (s) => {
      let out = String(s || '');
      // https://user:pass@host → https://***@host (자격증명 부분 마스킹)
      out = out.replace(/(https?:\/\/)[^/@\s]+@/gi, '$1***@');
      // 토큰 원문이 남아있으면 제거
      if (rawToken) out = out.split(rawToken).join('***');
      return out;
    };

    try {
      if (!url || !dest) return { ok: false, error: 'url과 dest가 필요합니다' };

      // 입력 인용 — 셸 인젝션 방지(큰따옴표 감싸고 위험문자 제거).
      const safeUrl = String(url).trim().replace(/["`$\\]/g, '');
      const safeDest = String(dest).replace(/"/g, '\\"');
      const safeBranch = String(branch || '').trim().replace(/[^\w.\-/]/g, '');
      // 토큰은 URL basic-auth로만 사용 — 셸/URL을 깨뜨리는 문자를 제거해 인젝션 방지.
      const safeToken = rawToken.replace(/[^\w.\-~+/=]/g, '');

      // 대상 디렉터리가 이미 존재하고 비어있지 않으면 git이 실패한다. 사전에 명확히 안내.
      // (존재 여부 확인은 fs로 직접 — 로컬 기준. 원격 세션이면 git 에러 메시지로 표면화됨)
      try {
        const fs = require('fs');
        if (fs.existsSync(dest) && fs.readdirSync(dest).length > 0) {
          return { ok: false, error: `대상 폴더가 이미 존재하며 비어있지 않습니다:\n${dest}\n(기존 폴더를 지우거나 다른 위치를 사용하세요)` };
        }
      } catch (_) { /* fs 확인 실패는 무시하고 git에 위임 */ }

      // token이 있고 https URL이면 인증 URL로 변환. (git@ SSH URL은 토큰과 무관하므로 그대로 둠)
      let cloneUrl = safeUrl;
      if (safeToken && /^https:\/\//i.test(safeUrl)) {
        // 기존에 자격증명이 박혀 있으면 제거 후 재주입.
        const bare = safeUrl.replace(/^https:\/\/[^/@]+@/i, 'https://');
        cloneUrl = bare.replace(/^https:\/\//i, `https://x-access-token:${safeToken}@`);
      }

      const branchArg = safeBranch ? `--branch "${safeBranch}"` : '';
      // 비대화식 강제 환경변수는 셸 프리픽스(`VAR=val cmd`)로 넣지 않는다 — 그 문법은
      // POSIX 셸 전용이라 Windows cmd.exe에서 실행 자체가 실패한다. run()→exec의
      // `env` 옵션으로 셸 밖에서 주입해 win/mac/원격 모두에서 동일하게 동작하게 한다.
      const cloneEnv = {
        GIT_TERMINAL_PROMPT: '0',
        GIT_SSH_COMMAND: 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10',
      };
      // `--` 로 옵션/URL 경계를 명확히 하여 URL이 옵션으로 오인되지 않게 한다.
      // (`2>&1` 제거 — exec가 stdout/stderr를 분리 캡처하므로 리다이렉트 불필요.
      //  실패 시 run()이 stderr로 throw → 아래 catch에서 정확한 사유를 반환한다.)
      const cmd = `git clone ${branchArg} --depth 1 -- "${cloneUrl}" "${safeDest}"`;

      // clone은 네트워크/인증이 걸리므로 넉넉히(120초). 비대화식이라 실패 시 빨리 끝난다.
      const output = await run(cmd, { timeout: 120000, env: cloneEnv });
      return { ok: true, dest, output: maskSecrets(String(output || '').trim()) };
    } catch (error) {
      const msg = maskSecrets(String(error.stdout || error.stderr || error.message || error).trim());
      console.error('[git:clone] Error:', msg);
      return { ok: false, error: msg };
    }
  });
}

module.exports = { registerGitHandlers };
