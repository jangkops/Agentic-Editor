// src/lib/utils.js 는 브라우저 전역(window.*)에 함수를 올리는 classic script 라
// vm 컨텍스트에 로드해 검증한다. 예전 파일은 자체 루프 + process.exit 로 끝나
// Jest 워커가 죽고 suite 가 실패로 집계됐다 — describe/test 형식으로 전환.
const vm = require('vm');
const fs = require('fs');
const path = require('path');

const src = fs.readFileSync(path.join(__dirname, '..', '..', 'src', 'lib', 'utils.js'), 'utf8');
const ctx = { window: {}, console };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const { esc, fmtNum, fmtElapsed, fmtElapsedMs, fmtMd } = ctx;

describe('src/lib/utils.js', () => {
  test.each([
    ['esc', () => esc('<b>&hi</b>'), '&lt;b&gt;&amp;hi&lt;/b&gt;'],
    ['fmtNum K', () => fmtNum(1234), '1.2K'],
    ['fmtNum M', () => fmtNum(2500000), '2.5M'],
    ['fmtNum small', () => fmtNum(5), '5'],
    ['fmtElapsed 0', () => fmtElapsed(0), '0s'],
    ['fmtElapsed 45', () => fmtElapsed(45), '45s'],
    ['fmtElapsed 125', () => fmtElapsed(125), '2m 5s'],
    ['fmtElapsed 3700', () => fmtElapsed(3700), '1h 1m'],
    ['fmtElapsedMs 0.3s', () => fmtElapsedMs(300), '0.3s'],
    ['fmtElapsedMs 5s', () => fmtElapsedMs(5000), '5s'],
  ])('%s', (_name, fn, want) => {
    expect(fn()).toBe(want);
  });

  test('fmtMd renders bold and inline code', () => {
    expect(fmtMd('**hi**')).toContain('<strong>hi</strong>');
    expect(fmtMd('`code`')).toContain('<code');
  });

  test('functions are exposed on window', () => {
    expect(ctx.window.esc).toBe(esc);
  });
});
