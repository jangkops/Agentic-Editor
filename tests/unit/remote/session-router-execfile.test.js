// execFile: 로컬은 argv 실행(셸 미경유)이라 메타문자가 명령으로 해석되지 않고,
// 원격 문자열은 shellJoin 으로 요소마다 인용된다.
const router = require('../../../electron/src/remote/session-router');
const { SessionRouter, shellJoin, shellQuote } = router;

describe('SessionRouter.execFile / shellJoin', () => {
  test('shellJoin quotes every element for POSIX sh', () => {
    const cmd = shellJoin('git', ['checkout', "main; touch /tmp/pwned", '$(id)']);
    expect(cmd).toBe(`${shellQuote('git')} ${shellQuote('checkout')} ${shellQuote("main; touch /tmp/pwned")} ${shellQuote('$(id)')}`);
    const outside = cmd.replace(/'\\''/g, '').replace(/'[^']*'/g, '');
    expect(outside).not.toMatch(/[$`;"\\]/);
  });

  test('local execFile passes argv literally (no shell expansion)', async () => {
    const r = new SessionRouter();
    const res = await r.execFile('printf', ['%s', '$(id) `whoami`; echo pwned'], { forceLocal: true });
    expect(res.code).toBe(0);
    expect(res.stdout).toBe('$(id) `whoami`; echo pwned');
  });

  test('local execFile reports non-zero exit in the same {stdout,stderr,code} shape', async () => {
    const r = new SessionRouter();
    const res = await r.execFile('git', ['rev-parse', '--verify', '--quiet', 'refs/heads/definitely-not-a-branch-xyz'], { forceLocal: true });
    expect(res.code).not.toBe(0);
    expect(typeof res.stdout).toBe('string');
    expect(typeof res.stderr).toBe('string');
  });
});
