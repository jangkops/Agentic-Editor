/**
 * scripts/build-python.js — materializeSymlinks()
 *
 * 릴리스 CI 의 Windows 러너에서 electron-builder(7-Zip)가 fastembed 모델 캐시의 심볼릭 링크
 * (`snapshots/<rev>/<파일> -> ../../blobs/<sha>`)를 따라가지 못해 패키징이 실패했다(2026-09-17).
 * 빌드 스크립트는 모델을 내려받은 뒤 링크를 실제 파일로 바꾸고 중복 blobs/ 를 지운다.
 * 여기서는 실제 HF 캐시 레이아웃을 임시 디렉터리에 재현해 그 계약을 고정한다.
 *
 * 심볼릭 링크를 만들 수 없는 환경(권한 없는 Windows)에서는 스킵한다.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const { materializeSymlinks, countSymlinks, dirSizeBytes } = require('../../scripts/build-python');

function canSymlink() {
  const probe = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-symlink-probe-'));
  try {
    fs.writeFileSync(path.join(probe, 't'), 'x');
    fs.symlinkSync(path.join(probe, 't'), path.join(probe, 'l'));
    return true;
  } catch (_) {
    return false;
  } finally {
    fs.rmSync(probe, { recursive: true, force: true });
  }
}

const describeIfSymlink = canSymlink() ? describe : describe.skip;

/** 실측한 fastembed/HF 캐시 레이아웃 재현: blobs 실파일 + snapshots 상대 링크 + refs/main + 메타 파일 */
function makeHfCache(cacheRoot, modelName = 'models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q') {
  const model = path.join(cacheRoot, modelName);
  const blobs = path.join(model, 'blobs');
  const snap = path.join(model, 'snapshots', 'faf4aa4225822f3bc6376869cb1164e8e3feedd0');
  const refs = path.join(model, 'refs');
  fs.mkdirSync(blobs, { recursive: true });
  fs.mkdirSync(snap, { recursive: true });
  fs.mkdirSync(refs, { recursive: true });
  fs.mkdirSync(path.join(cacheRoot, '.locks', modelName), { recursive: true });

  const files = {
    'model_optimized.onnx': ['634d0f66c29dc934c8fa72b8a4fe91dd4d420a22f1d82a241058d4316e659a99', 'ONNX-BYTES-0123456789'],
    'config.json': ['5b496dbbbe502a10e2d64525481c6f444d125403', '{"model_type":"bert"}'],
    'tokenizer.json': ['fa685fc160bbdbab64058d4fc91b60e62d207e8dc60b9af5c002c5ab946ded00', '{"version":"1.0"}'],
  };
  for (const [name, [sha, content]] of Object.entries(files)) {
    fs.writeFileSync(path.join(blobs, sha), content);
    // huggingface_hub 가 만드는 것과 같은 상대 링크
    fs.symlinkSync(path.join('..', '..', 'blobs', sha), path.join(snap, name));
  }
  fs.writeFileSync(path.join(snap, 'plain.txt'), 'not-a-link');
  fs.writeFileSync(path.join(refs, 'main'), 'faf4aa4225822f3bc6376869cb1164e8e3feedd0');
  fs.writeFileSync(path.join(model, 'files_metadata.json'), '{}');
  return { model, blobs, snap, refs, files };
}

describeIfSymlink('build-python materializeSymlinks — HF 캐시 심볼릭 링크 실체화', () => {
  let cacheRoot;
  beforeEach(() => {
    cacheRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'ae-materialize-'));
  });
  afterEach(() => {
    fs.rmSync(cacheRoot, { recursive: true, force: true });
  });

  test('snapshots 의 링크가 같은 내용의 실제 파일로 바뀌고, 중복 blobs/ 는 제거된다', () => {
    const { model, blobs, snap, refs, files } = makeHfCache(cacheRoot);
    expect(countSymlinks(model)).toBe(3);

    const n = materializeSymlinks(cacheRoot);

    expect(n).toBe(3);
    expect(countSymlinks(cacheRoot)).toBe(0);
    for (const [name, [, content]] of Object.entries(files)) {
      const p = path.join(snap, name);
      expect(fs.lstatSync(p).isSymbolicLink()).toBe(false);
      expect(fs.lstatSync(p).isFile()).toBe(true);
      expect(fs.readFileSync(p, 'utf8')).toBe(content);
    }
    expect(fs.existsSync(blobs)).toBe(false);
    // 링크가 아니었던 것들은 그대로
    expect(fs.readFileSync(path.join(snap, 'plain.txt'), 'utf8')).toBe('not-a-link');
    expect(fs.readFileSync(path.join(refs, 'main'), 'utf8')).toBe('faf4aa4225822f3bc6376869cb1164e8e3feedd0');
    expect(fs.existsSync(path.join(model, 'files_metadata.json'))).toBe(true);
    expect(fs.existsSync(path.join(cacheRoot, '.locks'))).toBe(true);
    // 임시 파일이 남지 않는다
    expect(fs.readdirSync(snap).some((f) => f.endsWith('.materialize-tmp'))).toBe(false);
  });

  test('멱등: 두 번째 실행은 0을 돌려주고 파일을 바꾸지 않는다', () => {
    const { snap } = makeHfCache(cacheRoot);
    materializeSymlinks(cacheRoot);
    const before = fs.readdirSync(snap).sort().map((f) => [f, fs.readFileSync(path.join(snap, f), 'utf8')]);

    expect(materializeSymlinks(cacheRoot)).toBe(0);

    const after = fs.readdirSync(snap).sort().map((f) => [f, fs.readFileSync(path.join(snap, f), 'utf8')]);
    expect(after).toEqual(before);
  });

  test('디렉터리를 가리키는 링크도 실제 디렉터리(내용 포함)로 바뀐다', () => {
    const { model, snap } = makeHfCache(cacheRoot);
    const realDir = path.join(model, 'blobs', 'dir-blob');
    fs.mkdirSync(realDir);
    fs.writeFileSync(path.join(realDir, 'inner.txt'), 'inner');
    fs.symlinkSync(path.join('..', '..', 'blobs', 'dir-blob'), path.join(snap, 'subdir'));

    const n = materializeSymlinks(cacheRoot);

    expect(n).toBe(4);
    const sub = path.join(snap, 'subdir');
    expect(fs.lstatSync(sub).isSymbolicLink()).toBe(false);
    expect(fs.lstatSync(sub).isDirectory()).toBe(true);
    expect(fs.readFileSync(path.join(sub, 'inner.txt'), 'utf8')).toBe('inner');
    expect(fs.existsSync(path.join(model, 'blobs'))).toBe(false);
  });

  test('대상이 없는 깨진 링크는 제거된다 (패키저도 따라갈 수 없으므로)', () => {
    const { snap } = makeHfCache(cacheRoot);
    fs.symlinkSync(path.join('..', '..', 'blobs', 'does-not-exist'), path.join(snap, 'dangling'));

    const n = materializeSymlinks(cacheRoot);

    expect(n).toBe(4);
    expect(fs.existsSync(path.join(snap, 'dangling'))).toBe(false);
    expect(countSymlinks(cacheRoot)).toBe(0);
  });

  test('blobs/ 는 모델 디렉터리에 링크가 남아 있으면 지우지 않는다 (models-- 밖의 링크는 무관)', () => {
    // models-- 접두어가 없는 디렉터리는 blobs 정리 대상이 아님을 겸해 확인
    const other = path.join(cacheRoot, 'not-a-model');
    fs.mkdirSync(path.join(other, 'blobs'), { recursive: true });
    fs.writeFileSync(path.join(other, 'blobs', 'x'), 'x');
    makeHfCache(cacheRoot);

    materializeSymlinks(cacheRoot);

    expect(fs.existsSync(path.join(other, 'blobs', 'x'))).toBe(true);
  });

  test('실체화 전후 총 바이트가 같다 — blobs 제거로 번들이 두 배가 되지 않음을 크기로 고정', () => {
    const { files } = makeHfCache(cacheRoot);
    const payload = Object.values(files).reduce((s, [, content]) => s + Buffer.byteLength(content), 0);
    const before = dirSizeBytes(cacheRoot);
    // 전: blobs 실파일(payload) + 링크(lstat 크기) + 비링크 파일들
    expect(before).toBeGreaterThanOrEqual(payload);

    materializeSymlinks(cacheRoot);

    const after = dirSizeBytes(cacheRoot);
    // 후: snapshots 실파일(payload) + 비링크 파일들. 링크 바이트가 사라지므로 전보다 작거나 같아야 하고,
    // blobs 가 남아 중복됐다면 payload 만큼 커져 이 부등식이 깨진다.
    expect(after).toBeLessThanOrEqual(before);
    expect(after).toBeLessThan(before + payload);
    expect(after).toBeGreaterThanOrEqual(payload);
  });

  test('존재하지 않는 디렉터리는 0 (모델 다운로드가 실패한 빌드에서도 안전)', () => {
    expect(materializeSymlinks(path.join(cacheRoot, 'nope'))).toBe(0);
  });

  test('require 만으로 빌드가 실행되지 않는다 (main 은 직접 실행일 때만)', () => {
    // 이 테스트 파일이 이미 require 했고 PyInstaller 가 돌지 않았다는 사실 자체가 증거.
    // ai_engine_dist 를 지우거나 만들지 않았는지 부수 효과로 확인.
    const mod = require('../../scripts/build-python');
    expect(typeof mod.materializeSymlinks).toBe('function');
    expect(typeof mod.resolvePython).toBe('function');
  });
});
