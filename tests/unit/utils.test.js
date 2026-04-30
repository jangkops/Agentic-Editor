const vm = require('vm');
const fs = require('fs');
const src = fs.readFileSync('src/lib/utils.js', 'utf8');
const ctx = { window: {}, console };
vm.createContext(ctx);
vm.runInContext(src, ctx);
// 브라우저 스타일처럼 전역 (ctx)에 다 올라가 있어야 함
const { esc, fmtNum, fmtElapsed, fmtElapsedMs, fmtMd } = ctx;

const tests = [
  ['esc', esc('<b>&hi</b>'), '&lt;b&gt;&amp;hi</b>'.replace('</b>','&lt;/b&gt;')],
  ['fmtNum K', fmtNum(1234), '1.2K'],
  ['fmtNum M', fmtNum(2500000), '2.5M'],
  ['fmtNum small', fmtNum(5), '5'],
  ['fmtElapsed 0', fmtElapsed(0), '0s'],
  ['fmtElapsed 45', fmtElapsed(45), '45s'],
  ['fmtElapsed 125', fmtElapsed(125), '2m 5s'],
  ['fmtElapsed 3700', fmtElapsed(3700), '1h 1m'],
  ['fmtElapsedMs 0.3s', fmtElapsedMs(300), '0.3s'],
  ['fmtElapsedMs 5s', fmtElapsedMs(5000), '5s'],
  ['fmtMd bold', fmtMd('**hi**').includes('<strong>hi</strong>'), true],
  ['fmtMd code', fmtMd('`code`').includes('<code'), true],
  ['window binding', ctx.window.esc === esc, true],
];
let pass=0, fail=0;
for (const [name, got, want] of tests) {
  if (got === want) { pass++; console.log('  ✓', name); }
  else { fail++; console.log('  ✗', name, '→ got', JSON.stringify(got), 'want', JSON.stringify(want)); }
}
console.log('---');
console.log('Pass:', pass, '/ Fail:', fail);
process.exit(fail ? 1 : 0);
