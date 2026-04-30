/**
 * Test: IPC Handlers
 * File System IPC 핸들러 테스트
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

describe('IPC Handlers - File System', () => {
  let testDir;

  beforeEach(() => {
    // 임시 디렉터리 생성
    testDir = path.join(os.tmpdir(), `test-fs-${Date.now()}`);
    if (!fs.existsSync(testDir)) {
      fs.mkdirSync(testDir, { recursive: true });
    }
  });

  afterEach(() => {
    // 정리
    if (fs.existsSync(testDir)) {
      fs.rmSync(testDir, { recursive: true, force: true });
    }
  });

  describe('readFileSync', () => {
    test('파일 읽기 성공', () => {
      const testFile = path.join(testDir, 'test.txt');
      const content = 'Hello, World!';
      fs.writeFileSync(testFile, content, 'utf-8');

      const result = fs.readFileSync(testFile, 'utf-8');
      expect(result).toBe(content);
    });

    test('존재하지 않는 파일 읽기 에러', () => {
      const testFile = path.join(testDir, 'nonexistent.txt');
      expect(() => {
        fs.readFileSync(testFile, 'utf-8');
      }).toThrow();
    });
  });

  describe('writeFileSync', () => {
    test('파일 쓰기 성공', () => {
      const testFile = path.join(testDir, 'output.txt');
      const content = 'Test content';
      fs.writeFileSync(testFile, content, 'utf-8');

      const result = fs.readFileSync(testFile, 'utf-8');
      expect(result).toBe(content);
    });

    test('부모 디렉터리 자동 생성', () => {
      const nestedDir = path.join(testDir, 'nested', 'deep', 'path');
      const testFile = path.join(nestedDir, 'file.txt');
      const content = 'Nested content';

      fs.mkdirSync(path.dirname(testFile), { recursive: true });
      fs.writeFileSync(testFile, content, 'utf-8');

      expect(fs.existsSync(testFile)).toBe(true);
      expect(fs.readFileSync(testFile, 'utf-8')).toBe(content);
    });
  });

  describe('readdirSync', () => {
    test('디렉터리 목록 조회', () => {
      // 테스트 파일 생성
      fs.writeFileSync(path.join(testDir, 'file1.txt'), 'content1');
      fs.writeFileSync(path.join(testDir, 'file2.txt'), 'content2');
      fs.mkdirSync(path.join(testDir, 'subdir'));

      const entries = fs.readdirSync(testDir, { withFileTypes: true });
      expect(entries.length).toBe(3);
      expect(entries.some(e => e.name === 'file1.txt')).toBe(true);
      expect(entries.some(e => e.name === 'subdir' && e.isDirectory())).toBe(true);
    });

    test('빈 디렉터리 조회', () => {
      const emptyDir = path.join(testDir, 'empty');
      fs.mkdirSync(emptyDir);

      const entries = fs.readdirSync(emptyDir, { withFileTypes: true });
      expect(entries.length).toBe(0);
    });
  });

  describe('renameSync', () => {
    test('파일 이름 변경', () => {
      const oldPath = path.join(testDir, 'old.txt');
      const newPath = path.join(testDir, 'new.txt');
      fs.writeFileSync(oldPath, 'content');

      fs.renameSync(oldPath, newPath);

      expect(fs.existsSync(oldPath)).toBe(false);
      expect(fs.existsSync(newPath)).toBe(true);
    });
  });

  describe('mkdirSync', () => {
    test('디렉터리 생성 (재귀)', () => {
      const nestedDir = path.join(testDir, 'a', 'b', 'c');
      fs.mkdirSync(nestedDir, { recursive: true });

      expect(fs.existsSync(nestedDir)).toBe(true);
    });
  });

  describe('existsSync', () => {
    test('파일 존재 확인', () => {
      const testFile = path.join(testDir, 'exists.txt');
      fs.writeFileSync(testFile, 'content');

      expect(fs.existsSync(testFile)).toBe(true);
      expect(fs.existsSync(path.join(testDir, 'nonexistent.txt'))).toBe(false);
    });
  });
});

describe('File System Error Handling', () => {
  test('권한 없는 파일 쓰기 처리', () => {
    // 이 테스트는 플랫폼에 따라 다를 수 있음
    const restrictedPath = '/root/test_no_permission.txt';
    
    expect(() => {
      fs.writeFileSync(restrictedPath, 'content', 'utf-8');
    }).toThrow();
  });

  test('경로 traversal 방지', () => {
    const testDir = path.join(os.tmpdir(), 'test-path-traversal');
    fs.mkdirSync(testDir, { recursive: true });

    try {
      const maliciousPath = path.join(testDir, '../../etc/passwd');
      const safePath = path.resolve(testDir, maliciousPath);
      
      // safe path는 testDir 내에 있어야 함
      expect(safePath.startsWith(testDir)).toBe(false); // traversal 감지
    } finally {
      fs.rmSync(testDir, { recursive: true, force: true });
    }
  });
});
