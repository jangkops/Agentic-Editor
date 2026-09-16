// git IPC 핸들러가 사용자·모델 입력을 셸 문자열이 아닌 argv 로 실행하는지(인젡션 회귀) 검증.
// session-router 는 가짜로 대체해 execFile 호출 인자를 기록한다.
const calls = [];
jest.mock('../../electron/src/remote/session-router', () => ({
  isRemoteActive: () => false,
  // git:stash-push 는 먼저 `git status --porcelain` 으로 변경 여부를 확인한다 — 변경이 있다고 답한다.
  exec: jest.fn(async (cmd) => ({ stdout: String(cmd).includes('status --porcelain') ? ' M x.txt\n' : '', stderr: '', code: 0 })),
  execFile: jest.fn(async (file, args, opts) => {
    calls.push({ file, args, opts });
    if (args[0] === 'rev-parse' && args.includes('--verify')) return { stdout: '', stderr: '', code: 1 }; // 로컬 브랜치 없음
    if (args[0] === 'shortlog') return { stdout: '   120\tAlice <a@x.io>\n     3\tBob Lee <bob@y.org>\n', stderr: '', code: 0 };
    return { stdout: 'ok\n', stderr: '', code: 0 };
  }),
  shellQuote: (s) => "'" + String(s).replace(/'/g, "'\\''") + "'",
}));

const { ipcMain } = require('electron');
const { registerGitHandlers } = require('../../electron/src/ipc-git-handlers');

function handler(channel) {
  const entry = ipcMain.handle.mock.calls.find(([ch]) => ch === channel);
  if (!entry) throw new Error(`handler not registered: ${channel}`);
  return entry[1];
}

beforeAll(() => { registerGitHandlers(); });
beforeEach(() => { calls.length = 0; });

describe('git IPC handlers use argv, never shell strings', () => {
  test('git:checkout passes a hostile branch name as one literal argument', async () => {
    const res = await handler('git:checkout')(null, '/repo', "main; touch /tmp/pwned");
    expect(res.ok).toBe(false);                                  // REF_RE 가 공백을 거부
    expect(res.error).toBe('invalid_branch');
    expect(calls).toHaveLength(0);
    const res2 = await handler('git:checkout')(null, '/repo', 'origin/feature$(id)');
    expect(res2.ok).toBe(true);
    const co = calls.find((c) => c.args[0] === 'checkout');
    expect(co.file).toBe('git');
    expect(co.args).toEqual(['checkout', '-b', 'feature$(id)', '--track', 'origin/feature$(id)']);
  });

  test('git:show rejects non-hash input without running git', async () => {
    const res = await handler('git:show')(null, '/repo', 'HEAD; rm -rf x');
    expect(res).toBeNull();
    expect(calls).toHaveLength(0);
  });

  test('git:log coerces limit to an integer', async () => {
    await handler('git:log')(null, '/repo', '5; id');
    expect(calls[0].args).toEqual(['log', '--oneline', '--decorate', '--all', '-n', '50']);
    calls.length = 0;
    await handler('git:log')(null, '/repo', 7);
    expect(calls[0].args.slice(-1)).toEqual(['7']);
  });

  test('git:search passes the query after -e as a literal argv element', async () => {
    await handler('git:search')(null, '/repo', '$(id) `whoami` "x"', { caseSensitive: false });
    const grep = calls.find((c) => c.args[0] === 'grep');
    expect(grep.args).toEqual(['grep', '--no-color', '-n', '-I', '-i', '-e', '$(id) `whoami` "x"']);
  });

  test('git:stash-push passes the message as one argument', async () => {
    const res = await handler('git:stash-push')(null, '/repo', 'msg" ; rm -rf /tmp/x ; "');
    expect(res.ok).toBe(true);
    const st = calls.find((c) => c.args[0] === 'stash');
    expect(st.args).toEqual(['stash', 'push', '-m', 'msg" ; rm -rf /tmp/x ; "', '--include-untracked']);
  });

  test('git:clone validates the URL scheme and uses argv with --', async () => {
    const bad = await handler('git:clone')(null, '--upload-pack=touch /tmp/pwned', '', '/tmp/dest');
    expect(bad.ok).toBe(false);
    expect(calls).toHaveLength(0);
    const ok = await handler('git:clone')(null, 'https://github.com/o/r.git', 'main', '/tmp/dest with space');
    expect(ok.ok).toBe(true);
    const clone = calls.find((c) => c.args[0] === 'clone');
    expect(clone.args).toEqual(['clone', '--branch', 'main', '--depth', '1', '--', 'https://github.com/o/r.git', '/tmp/dest with space']);
    expect(clone.opts.env.GIT_TERMINAL_PROMPT).toBe('0');
  });

  test('git:discard-all refuses without an explicit confirm flag and uses argv when confirmed', async () => {
    const refused = await handler('git:discard-all')(null, '/repo');
    expect(refused).toEqual({ ok: false, error: 'confirm_required' });
    expect(calls).toHaveLength(0);
    const ok = await handler('git:discard-all')(null, '/repo', { confirm: true });
    expect(ok).toEqual({ ok: true });
    expect(calls.map((c) => c.args)).toEqual([['checkout', '--', '.'], ['clean', '-fd']]);
    expect(calls.every((c) => c.file === 'git' && c.opts.cwd === '/repo')).toBe(true);
  });

  test('git:contributors parses shortlog into {name,email,commits}', async () => {
    const rows = await handler('git:contributors')(null, '/repo');
    expect(rows).toEqual([
      { commits: 120, name: 'Alice', email: 'a@x.io' },
      { commits: 3, name: 'Bob Lee', email: 'bob@y.org' },
    ]);
  });
});
