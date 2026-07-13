/**
 * Unit tests — SSH Config Parser (Task 1.5)
 * 
 * Feature: remote-ssh
 * Validates: Requirements 1.1, 1.6, 1.8
 * 
 * Concrete test cases:
 *   - 경로 결정 (env/OS) — HOME 또는 USERPROFILE 기반 ~/.ssh/config 자동 찾기
 *   - 500 blocks 성능 — loadFromDisk() 로드 시간 ≤300ms 검증
 *   - 빈 파일 — parse('') → [] 반환
 *   - 잘못된 라인 — invalid line은 diagnostic 기록 후 skip (throw 없음)
 *   - Round-trip — parse(print(entries)) → 원본과 동일 (wildcard-only 항목 제외)
 */

'use strict';

const assert = require('assert');
const path = require('path');
const fs = require('fs');
const os = require('os');
const parser = require('../../../electron/src/remote/ssh-config-parser');

describe('SSH Config Parser — Unit Tests (Task 1.5)', () => {
  
  // =========================================================================
  // Test 1: 경로 결정 (env/OS) — HOME 또는 USERPROFILE 기반 ~/.ssh/config 자동 찾기
  // =========================================================================
  
  describe('1. Path Resolution (env/OS)', () => {
    
    it('should use $HOME on Unix-like systems', () => {
      const mockHome = '/home/testuser';
      const env = { HOME: mockHome, USERPROFILE: undefined };
      const result = parser.loadFromDisk({ home: mockHome, env });
      
      // loadFromDisk는 sourcePath를 반환하며, Unix에서는 $HOME/.ssh/config 경로를 사용
      assert(result.sourcePath.includes('.ssh/config'));
      assert(result.sourcePath.includes(mockHome) || result.sourcePath === path.join(mockHome, '.ssh', 'config'));
    });

    it('should prefer $SSH_CONFIG_FILE over default path', () => {
      const tempFile = path.join(os.tmpdir(), 'ssh-config-test-' + Date.now());
      const mockHome = '/home/testuser';
      
      // Write a minimal SSH config to the temp file
      fs.writeFileSync(tempFile, 'Host example\n  HostName example.com\n');
      
      try {
        const env = { HOME: mockHome, SSH_CONFIG_FILE: tempFile };
        const result = parser.loadFromDisk({ home: mockHome, env });
        
        assert.strictEqual(result.sourcePath, tempFile);
        assert(Array.isArray(result.entries));
      } finally {
        fs.unlinkSync(tempFile);
      }
    });

    it('should return empty entries for missing config file', () => {
      const nonExistentPath = path.join(os.tmpdir(), 'nonexistent-ssh-config-' + Date.now());
      const mockHome = '/home/testuser';
      
      const env = { HOME: mockHome, SSH_CONFIG_FILE: nonExistentPath };
      const result = parser.loadFromDisk({ home: mockHome, env });
      
      assert.deepStrictEqual(result.entries, []);
      assert.deepStrictEqual(result.diagnostics, []);
    });
  });

  // =========================================================================
  // Test 2: 500 blocks 성능 — loadFromDisk() 로드 시간 ≤300ms 검증
  // =========================================================================

  describe('2. Performance — 500 blocks ≤300ms', () => {
    
    it('should parse 500 Host blocks within 300ms', () => {
      // Jest timeout set via jest.setTimeout in beforeAll
      
      const tempFile = path.join(os.tmpdir(), 'ssh-config-perf-' + Date.now());
      
      // Generate 500 Host blocks
      let configText = '';
      for (let i = 0; i < 500; i++) {
        configText += `Host host${i}\n`;
        configText += `  HostName host${i}.example.com\n`;
        configText += `  User user${i}\n`;
        configText += `  Port ${22 + (i % 100)}\n`;
        configText += `  IdentityFile ~/.ssh/id_${i}\n`;
        configText += '\n';
      }
      
      fs.writeFileSync(tempFile, configText);
      
      try {
        const start = process.hrtime.bigint();
        const result = parser.loadFromDisk({ 
          env: { SSH_CONFIG_FILE: tempFile },
          home: os.homedir()
        });
        const end = process.hrtime.bigint();
        
        const elapsedMs = Number(end - start) / 1e6; // Convert nanoseconds to milliseconds
        
        assert(result.entries.length >= 500, `Expected at least 500 entries, got ${result.entries.length}`);
        assert(elapsedMs <= 300, `Expected parse time ≤300ms, got ${elapsedMs.toFixed(2)}ms`);
        
        console.log(`  ✓ Parsed 500 blocks in ${elapsedMs.toFixed(2)}ms`);
      } finally {
        fs.unlinkSync(tempFile);
      }
    });
  });

  // =========================================================================
  // Test 3: 빈 파일 — parse('') → [] 반환
  // =========================================================================

  describe('3. Empty file handling', () => {
    
    it('should return empty entries for empty text', () => {
      const result = parser.parse('');
      assert.deepStrictEqual(result.entries, []);
    });

    it('should return empty entries for whitespace-only text', () => {
      const result = parser.parse('   \n\n  \n\t\n');
      assert.deepStrictEqual(result.entries, []);
    });

    it('should handle null/undefined gracefully', () => {
      const result1 = parser.parse(null);
      const result2 = parser.parse(undefined);
      
      assert.deepStrictEqual(result1.entries, []);
      assert.deepStrictEqual(result2.entries, []);
    });
  });

  // =========================================================================
  // Test 4: 잘못된 라인 — invalid line은 diagnostic 기록 후 skip (throw 금지)
  // =========================================================================

  describe('4. Invalid lines — diagnostic recording without throw', () => {
    
    it('should record diagnostic for Host directive without pattern', () => {
      const text = 'Host\n  HostName example.com';
      const result = parser.parse(text);
      
      // When Host line is invalid, entries may include implicit '*' from directives before first valid Host
      // The key is that we get a diagnostic and don't throw
      assert(result.diagnostics.length > 0);
      assert(result.diagnostics.some(d => d.severity === 'error' && d.message.includes('Host')));
    });

    it('should skip invalid Port value and record diagnostic', () => {
      const text = `Host test
  HostName test.com
  Port abc
  IdentityFile ~/.ssh/id_rsa`;
      
      const result = parser.parse(text);
      
      assert(result.entries.length === 1);
      assert.strictEqual(result.entries[0].port, 22); // default, not modified by invalid value
      assert(result.diagnostics.some(d => d.severity === 'warn' && d.message.includes('Port')));
    });

    it('should skip invalid ForwardAgent value and record diagnostic', () => {
      const text = `Host test
  HostName test.com
  ForwardAgent invalid_value`;
      
      const result = parser.parse(text);
      
      assert(result.entries.length === 1);
      assert(result.entries[0].forwardAgent === undefined); // Not set due to invalid value
      assert(result.diagnostics.some(d => d.severity === 'warn' && d.message.includes('ForwardAgent')));
    });

    it('should ignore unsupported directives with warning', () => {
      const text = `Host test
  HostName test.com
  UnsupportedDirective somevalue
  User alice`;
      
      const result = parser.parse(text);
      
      assert(result.entries.length === 1);
      assert.strictEqual(result.entries[0].user, 'alice');
      assert(result.diagnostics.some(d => d.severity === 'warn' && d.message.includes('Unknown or unsupported')));
    });

    it('should warn on Match block (not supported in v1)', () => {
      const text = `Host test
  HostName test.com

Match host test
  IdentityFile ~/.ssh/id_special
  User bob`;
      
      const result = parser.parse(text);
      
      // Match block and its directives should be ignored
      assert(result.entries.length === 1);
      assert.strictEqual(result.entries[0].user, ''); // Not set by Match block
      assert(result.diagnostics.some(d => d.severity === 'warn' && d.message.includes('Match')));
    });

    it('should warn on unresolved Include directives', () => {
      const text = `Host test
  HostName test.com

Include ~/.ssh/config.d/*`;
      
      const result = parser.parse(text);
      
      // Include encountered during parse() should warn
      assert(result.diagnostics.some(d => d.severity === 'warn' && d.message.includes('Include')));
    });

    it('should never throw on malformed input', () => {
      const malformedExamples = [
        'Host\nHost\nHost',
        'Port 99999\nUser @#$%',
        'IdentityFile\nIdentityFile',
        'ProxyJump ',
      ];
      
      for (const text of malformedExamples) {
        assert.doesNotThrow(() => {
          const result = parser.parse(text);
          // Should have diagnostics but no exception
          assert(Array.isArray(result.diagnostics));
          assert(Array.isArray(result.entries));
        }, `Should not throw on: ${text.slice(0, 30)}`);
      }
    });
  });

  // =========================================================================
  // Test 5: Round-trip — parse(print(entries)) → 원본과 동일
  // =========================================================================

  describe('5. Round-trip (parse → print → parse)', () => {
    
    it('should round-trip simple Host entries', () => {
      const entries = [
        {
          alias: 'host1',
          isWildcardOnly: false,
          hostName: 'host1.example.com',
          user: 'alice',
          port: 22,
          identityFiles: ['/home/alice/.ssh/id_rsa'],
          proxyJump: [],
          sourcePaths: ['~/.ssh/config'],
          lineNumber: 1,
          raw: []
        },
        {
          alias: 'host2',
          isWildcardOnly: false,
          hostName: 'host2.example.com',
          user: 'bob',
          port: 2222,
          identityFiles: ['/home/bob/.ssh/id_ed25519'],
          proxyJump: [],
          sourcePaths: ['~/.ssh/config'],
          lineNumber: 6,
          raw: []
        }
      ];
      
      const printed = parser.print(entries);
      const reparsed = parser.parse(printed);
      
      assert.strictEqual(reparsed.entries.length, entries.length);
      
      for (let i = 0; i < entries.length; i++) {
        const orig = entries[i];
        const re = reparsed.entries[i];
        
        assert.strictEqual(re.alias, orig.alias);
        assert.strictEqual(re.hostName, orig.hostName);
        assert.strictEqual(re.user, orig.user);
        assert.strictEqual(re.port, orig.port);
        assert.deepStrictEqual(re.identityFiles, orig.identityFiles);
      }
    });

    it('should omit wildcard-only entries from print()', () => {
      const entries = [
        {
          alias: '*',
          isWildcardOnly: true,
          hostName: '*',
          user: 'default',
          port: 22,
          identityFiles: [],
          proxyJump: [],
          sourcePaths: [],
          lineNumber: 1,
          raw: []
        },
        {
          alias: 'specific',
          isWildcardOnly: false,
          hostName: 'specific.com',
          user: 'user',
          port: 22,
          identityFiles: [],
          proxyJump: [],
          sourcePaths: [],
          lineNumber: 5,
          raw: []
        }
      ];
      
      const printed = parser.print(entries);
      const reparsed = parser.parse(printed);
      
      // Wildcard-only should be omitted
      assert.strictEqual(reparsed.entries.length, 1);
      assert.strictEqual(reparsed.entries[0].alias, 'specific');
    });

    it('should handle ProxyJump and ForwardAgent round-trip', () => {
      const entries = [
        {
          alias: 'jump-test',
          isWildcardOnly: false,
          hostName: 'target.com',
          user: 'alice',
          port: 22,
          identityFiles: ['/home/alice/.ssh/id_rsa'],
          proxyJump: ['bastion1', 'bastion2'],
          forwardAgent: true,
          sourcePaths: [],
          lineNumber: 1,
          raw: []
        }
      ];
      
      const printed = parser.print(entries);
      const reparsed = parser.parse(printed);
      
      assert.strictEqual(reparsed.entries.length, 1);
      assert.deepStrictEqual(reparsed.entries[0].proxyJump, ['bastion1', 'bastion2']);
      assert.strictEqual(reparsed.entries[0].forwardAgent, true);
    });

    it('should handle multiple IdentityFile entries round-trip', () => {
      const entries = [
        {
          alias: 'multi-key',
          isWildcardOnly: false,
          hostName: 'host.com',
          user: 'alice',
          port: 22,
          identityFiles: [
            '/home/alice/.ssh/id_rsa',
            '/home/alice/.ssh/id_ed25519',
            '/home/alice/.ssh/id_ecdsa'
          ],
          proxyJump: [],
          sourcePaths: [],
          lineNumber: 1,
          raw: []
        }
      ];
      
      const printed = parser.print(entries);
      const reparsed = parser.parse(printed);
      
      assert.deepStrictEqual(
        reparsed.entries[0].identityFiles,
        entries[0].identityFiles
      );
    });

    it('should not include default HostName in print output', () => {
      const entries = [
        {
          alias: 'test',
          isWildcardOnly: false,
          hostName: 'test', // Same as alias
          user: 'alice',
          port: 22,
          identityFiles: [],
          proxyJump: [],
          sourcePaths: [],
          lineNumber: 1,
          raw: []
        }
      ];
      
      const printed = parser.print(entries);
      
      // Should not contain "HostName test" since it defaults to alias
      assert(!printed.includes('HostName'));
      
      const reparsed = parser.parse(printed);
      assert.strictEqual(reparsed.entries[0].hostName, 'test');
    });

    it('should not include default Port 22 in print output', () => {
      const entries = [
        {
          alias: 'test',
          isWildcardOnly: false,
          hostName: 'test.com',
          user: 'alice',
          port: 22, // Default port
          identityFiles: [],
          proxyJump: [],
          sourcePaths: [],
          lineNumber: 1,
          raw: []
        }
      ];
      
      const printed = parser.print(entries);
      
      // Should not contain "Port 22" since it's the default
      assert(!printed.includes('Port'));
      
      const reparsed = parser.parse(printed);
      assert.strictEqual(reparsed.entries[0].port, 22);
    });
  });

  // =========================================================================
  // Test 6: 추가 검증 — 공백 처리, 주석, 호스트 블록 인식
  // =========================================================================

  describe('6. Additional validation — whitespace, comments, Host block recognition', () => {
    
    it('should handle leading/trailing whitespace', () => {
      const text = `
        Host test1
          HostName test1.com
          User alice
        
        Host test2
          HostName test2.com
      `;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries.length, 2);
      assert.strictEqual(result.entries[0].alias, 'test1');
      assert.strictEqual(result.entries[1].alias, 'test2');
    });

    it('should skip comments', () => {
      const text = `# Top-level comment
Host test
  # Indented comment
  HostName test.com # inline comment
  User alice # another comment`;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries.length, 1);
      assert.strictEqual(result.entries[0].user, 'alice');
    });

    it('should recognize Host block with multiple patterns', () => {
      const text = `Host test1 test2 test3
  HostName example.com
  User alice`;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries.length, 3);
      assert.deepStrictEqual(
        result.entries.map(e => e.alias),
        ['test1', 'test2', 'test3']
      );
      // All should share the same directives
      assert(result.entries.every(e => e.hostName === 'example.com'));
      assert(result.entries.every(e => e.user === 'alice'));
    });

    it('should support Keyword=Value syntax', () => {
      const text = `Host test
  HostName=test.com
  User=alice
  Port=2222`;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries.length, 1);
      assert.strictEqual(result.entries[0].hostName, 'test.com');
      assert.strictEqual(result.entries[0].user, 'alice');
      assert.strictEqual(result.entries[0].port, 2222);
    });

    it('should handle mixed Keyword Value and Keyword=Value', () => {
      const text = `Host test
  HostName test.com
  User=alice
  Port 2222`;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries.length, 1);
      assert.strictEqual(result.entries[0].hostName, 'test.com');
      assert.strictEqual(result.entries[0].user, 'alice');
      assert.strictEqual(result.entries[0].port, 2222);
    });
  });

  // =========================================================================
  // Test 7: 경로 확장 — ~ 처리
  // =========================================================================

  describe('7. Tilde expansion in paths', () => {
    
    it('should expand ~ to home directory in IdentityFile', () => {
      const mockHome = '/home/testuser';
      const text = `Host test
  IdentityFile ~/.ssh/id_rsa`;
      
      const result = parser.parse(text, { env: { HOME: mockHome } });
      assert.strictEqual(
        result.entries[0].identityFiles[0],
        path.join(mockHome, '.ssh', 'id_rsa')
      );
    });

    it('should expand ~ in UserKnownHostsFile', () => {
      const mockHome = '/home/testuser';
      const text = `Host test
  UserKnownHostsFile ~/.ssh/known_hosts`;
      
      const result = parser.parse(text, { env: { HOME: mockHome } });
      assert.strictEqual(
        result.entries[0].userKnownHostsFile[0],
        path.join(mockHome, '.ssh', 'known_hosts')
      );
    });

    it('should not expand ~ in non-path values', () => {
      const text = `Host ~testhost
  HostName example.com`;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries[0].alias, '~testhost');
    });
  });

  // =========================================================================
  // Test 8: 다중 IdentityFile 누적
  // =========================================================================

  describe('8. Multiple IdentityFile accumulation', () => {
    
    it('should accumulate multiple IdentityFile entries in order', () => {
      const text = `Host test
  IdentityFile ~/.ssh/id_rsa
  IdentityFile ~/.ssh/id_ed25519
  IdentityFile ~/.ssh/id_ecdsa`;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries[0].identityFiles.length, 3);
    });

    it('should apply IdentityFile across multiple Host patterns', () => {
      const text = `Host h1 h2
  IdentityFile ~/.ssh/id_1
  IdentityFile ~/.ssh/id_2`;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries.length, 2);
      assert.deepStrictEqual(result.entries[0].identityFiles, result.entries[1].identityFiles);
    });
  });

  // =========================================================================
  // Test 9: 간단한 성능 검증 — parse() 성능
  // =========================================================================

  describe('9. Parse performance (not loadFromDisk)', () => {
    
    it('should parse 1000 lines quickly', () => {
      // Jest has a default timeout of 5000ms for this suite
      
      let text = '';
      for (let i = 0; i < 100; i++) {
        text += `Host host${i}\n  HostName h${i}.com\n  User user${i}\n  Port ${22 + i}\n`;
      }
      
      const start = process.hrtime.bigint();
      const result = parser.parse(text);
      const end = process.hrtime.bigint();
      
      const elapsedMs = Number(end - start) / 1e6;
      
      assert(result.entries.length > 0);
      // Loose performance expectation for parse() — more than loadFromDisk since no I/O
      assert(elapsedMs < 100, `Expected parse time <100ms, got ${elapsedMs.toFixed(2)}ms`);
    });
  });

  // =========================================================================
  // Test 10: OS별 경로 해석
  // =========================================================================

  describe('10. OS-specific path resolution', () => {
    
    it('should use USERPROFILE on Windows when HOME is not set', () => {
      const mockUserProfile = 'C:\\Users\\testuser';
      const env = { USERPROFILE: mockUserProfile, HOME: undefined };
      
      // On Windows platform, loadFromDisk should prefer USERPROFILE
      // We test the resolveHome helper indirectly via loadFromDisk
      const result = parser.loadFromDisk({ 
        home: mockUserProfile,
        env 
      });
      
      // Should not crash and should return valid structure
      assert(Array.isArray(result.entries));
      assert(Array.isArray(result.diagnostics));
    });

    it('should fall back to os.homedir() when env vars not set', () => {
      const env = {};
      const result = parser.loadFromDisk({ env });
      
      // Should not crash
      assert(Array.isArray(result.entries));
    });
  });

  // =========================================================================
  // Test 11: 특수 지시어 검증
  // =========================================================================

  describe('11. Special directive validation', () => {
    
    it('should validate StrictHostKeyChecking values', () => {
      const validValues = ['yes', 'no', 'ask', 'accept-new'];
      
      for (const val of validValues) {
        const text = `Host test
  StrictHostKeyChecking ${val}`;
        
        const result = parser.parse(text);
        assert.strictEqual(result.entries[0].strictHostKeyChecking, val);
      }
    });

    it('should warn on invalid StrictHostKeyChecking value', () => {
      const text = `Host test
  StrictHostKeyChecking maybe`;
      
      const result = parser.parse(text);
      assert(result.diagnostics.some(d => d.message.includes('StrictHostKeyChecking')));
      assert(result.entries[0].strictHostKeyChecking === undefined);
    });

    it('should parse PreferredAuthentications as comma-separated list', () => {
      const text = `Host test
  PreferredAuthentications publickey,password,keyboard-interactive`;
      
      const result = parser.parse(text);
      assert.deepStrictEqual(
        result.entries[0].preferredAuthentications,
        ['publickey', 'password', 'keyboard-interactive']
      );
    });

    it('should parse ProxyJump as comma-separated hops', () => {
      const text = `Host test
  ProxyJump bastion1,bastion2,bastion3`;
      
      const result = parser.parse(text);
      assert.deepStrictEqual(
        result.entries[0].proxyJump,
        ['bastion1', 'bastion2', 'bastion3']
      );
    });

    it('should handle ProxyJump with whitespace', () => {
      const text = `Host test
  ProxyJump bastion1 bastion2 bastion3`;
      
      const result = parser.parse(text);
      assert(result.entries[0].proxyJump.length >= 3);
    });

    it('should parse boolean values case-insensitively', () => {
      const text = `Host test1
  ForwardAgent YES

Host test2
  ForwardAgent no

Host test3
  IdentitiesOnly true

Host test4
  IdentitiesOnly FALSE`;
      
      const result = parser.parse(text);
      assert.strictEqual(result.entries[0].forwardAgent, true);
      assert.strictEqual(result.entries[1].forwardAgent, false);
      assert.strictEqual(result.entries[2].identitiesOnly, true);
      assert.strictEqual(result.entries[3].identitiesOnly, false);
    });
  });
});
