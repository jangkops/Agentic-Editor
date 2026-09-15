// 회귀 테스트 — /bridge/search_files 가 모델 입력을 큰따옴표 escape 만으로 grep 문자열에
// 넣어 `$(...)`·백틱·`\` 가 원격 셸에 그대로 도달하던 결함. shellQuote 인용과 -e/-- 를 확인한다.
const { handleRequest } = require('../../../electron/src/remote/bridge-server');
const { shellQuote } = require('../../../electron/src/remote/session-router');

function fakeRouter(captured) {
  return {
    getActive: () => ({ state: 'connected', alias: 'test' }),
    isRemoteActive: () => true,
    exec: async (cmd) => { captured.cmd = cmd; return { stdout: '', stderr: '', code: 0 }; },
  };
}

describe('bridge /bridge/search_files shell quoting', () => {
  test('quotes query, path and file_pattern with POSIX single quotes', async () => {
    const captured = {};
    const query = "$(id) `whoami` \"x\" 'y' \\ z -v";
    const p = "/tmp/dir with space'q";
    const res = await handleRequest('/bridge/search_files', { query, path: p, file_pattern: '*.js' }, fakeRouter(captured), {});
    expect(res.ok).toBe(true);
    const cmd = captured.cmd;
    expect(cmd.startsWith('grep -rn ')).toBe(true);
    expect(cmd).toContain(`-e ${shellQuote(query)}`);
    expect(cmd).toContain(`-- ${shellQuote(p)}`);
    expect(cmd).toContain(`--include=${shellQuote('*.js')}`);
    // 인용 구간을 걷어낸 나머지에 셸 메타문자가 남아 있으면 안 된다.
    const outside = cmd.replace(/'\\''/g, '').replace(/'[^']*'/g, '');
    expect(outside).not.toMatch(/[$`"\\]/);
  });

  test('refuses when no remote session is active', async () => {
    const router = { getActive: () => null, isRemoteActive: () => false, exec: async () => { throw new Error('must not run'); } };
    const res = await handleRequest('/bridge/search_files', { query: 'x' }, router, {});
    expect(res.ok).toBe(false);
  });
});
