// 렌더러(src/**)는 AWS 자격증명 값을 다루지 않아야 한다. 메인 프로세스가 사이드카에 직접 주입한다.
// (XSS 로 렌더러가 장악돼도 access key / secret / session token 이 노출되지 않게 하는 구조적 불변식.)
const fs = require('fs');
const path = require('path');

function walk(dir, out) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === 'vendor') continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.name.endsWith('.js') || e.name.endsWith('.html')) out.push(p);
  }
  return out;
}

test('no renderer source references AWS credential fields', () => {
  const files = walk(path.join(__dirname, '..', '..', 'src'), []);
  const forbidden = /AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|secretAccessKey|sessionToken|_cachedCreds/;
  const offenders = files.filter((f) => forbidden.test(fs.readFileSync(f, 'utf8'))).map((f) => path.relative(process.cwd(), f));
  expect(offenders).toEqual([]);
});
