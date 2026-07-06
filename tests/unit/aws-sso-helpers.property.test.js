/**
 * Property-based tests for the pure helpers exported by
 * electron/core/aws-sso-manager.js
 *
 * Runner: jest (repo "test:unit": "jest tests/unit/")
 * Library: fast-check
 *
 * These helpers are pure (no file IO / network) so they can be imported and
 * exercised directly. Each property runs >= 100 times.
 */

const fc = require('fast-check');
const {
  mapCredentials,
  sortProfiles,
  buildBedrockUserCandidates,
  buildSsoProfileBlock,
  parseSsoProfileBlock,
} = require('../../electron/core/aws-sso-manager');

const NUM_RUNS = 200; // >= 100 required by the spec

// Safe token charset: alphanumeric only (no '.', '@', '/', whitespace).
const alnum = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
const token = (opts = {}) =>
  fc
    .array(fc.constantFrom(...alnum.split('')), { minLength: 1, maxLength: 12, ...opts })
    .map((chars) => chars.join(''));

// ---------------------------------------------------------------------------
// Feature: app-deployment-readiness, Property 1: 자격증명 매핑 형태 불변
// Validates: Requirements 2.2, 2.4
// For any {accessKeyId, secretAccessKey, sessionToken, region} (including
// missing fields), mapCredentials returns exactly the 4 AWS_* keys and
// preserves each provided value (missing -> '').
// ---------------------------------------------------------------------------
describe('Property 1: 자격증명 매핑 형태 불변', () => {
  test('mapCredentials always yields exactly the 4 AWS_* keys, preserving values', () => {
    fc.assert(
      fc.property(
        fc.record(
          {
            accessKeyId: fc.string(),
            secretAccessKey: fc.string(),
            sessionToken: fc.string(),
            region: fc.string(),
          },
          { requiredKeys: [] }, // any subset of fields may be missing
        ),
        (sdkCreds) => {
          const out = mapCredentials(sdkCreds);

          // Exactly the 4 canonical env-var keys, no more, no less.
          expect(Object.keys(out).sort()).toEqual([
            'AWS_ACCESS_KEY_ID',
            'AWS_DEFAULT_REGION',
            'AWS_SECRET_ACCESS_KEY',
            'AWS_SESSION_TOKEN',
          ]);

          // Provided fields are preserved verbatim; missing fields become ''.
          const expected = (k) => (k in sdkCreds ? sdkCreds[k] : '');

          expect(out.AWS_ACCESS_KEY_ID).toBe(expected('accessKeyId'));
          expect(out.AWS_SECRET_ACCESS_KEY).toBe(expected('secretAccessKey'));
          expect(out.AWS_SESSION_TOKEN).toBe(expected('sessionToken'));
          expect(out.AWS_DEFAULT_REGION).toBe(expected('region'));
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });
});

// ---------------------------------------------------------------------------
// Feature: app-deployment-readiness, Property 3: 프로파일 정렬 불변
// Validates: Requirements 2.5
// For any array of profile names, sortProfiles puts all 'bedrockuser'* first
// and keeps localeCompare order within each group; the result is a permutation
// of the input.
// ---------------------------------------------------------------------------
describe('Property 3: 프로파일 정렬 불변', () => {
  const groupKey = (n) => (n.startsWith('bedrockuser') ? 0 : 1);

  const profileName = fc.oneof(
    token().map((s) => 'bedrockuser' + s), // bedrockuser-prefixed
    fc.string(), // arbitrary (may or may not be prefixed)
  );

  test('bedrockuser* sorted first, localeCompare within group, permutation preserved', () => {
    fc.assert(
      fc.property(fc.array(profileName, { maxLength: 30 }), (names) => {
        const sorted = sortProfiles(names);

        // Input is not mutated.
        // (sortProfiles copies via spread; verify by length + multiset below.)

        // Result is a permutation of the input (same multiset).
        expect([...sorted].sort()).toEqual([...names].sort());

        // Adjacent pairs respect the comparator ordering.
        for (let i = 0; i + 1 < sorted.length; i++) {
          const a = sorted[i];
          const b = sorted[i + 1];
          expect(groupKey(a)).toBeLessThanOrEqual(groupKey(b));
          if (groupKey(a) === groupKey(b)) {
            expect(a.localeCompare(b)).toBeLessThanOrEqual(0);
          }
        }
      }),
      { numRuns: NUM_RUNS },
    );
  });
});

// ---------------------------------------------------------------------------
// Feature: app-deployment-readiness, Property 4: BedrockUser ARN 파싱 불변
// Validates: Requirements 2.3
// For any ARN string, buildBedrockUserCandidates returns a non-empty array of
// strings and never throws. email (first.last@) -> 4-candidate rule;
// non-email identifier -> [identifier].
// ---------------------------------------------------------------------------
describe('Property 4: BedrockUser ARN 파싱 불변', () => {
  test('any string -> non-empty string[] and never throws', () => {
    fc.assert(
      fc.property(fc.string(), (arn) => {
        let out;
        expect(() => {
          out = buildBedrockUserCandidates(arn);
        }).not.toThrow();
        expect(Array.isArray(out)).toBe(true);
        expect(out.length).toBeGreaterThanOrEqual(1);
        out.forEach((c) => expect(typeof c).toBe('string'));
      }),
      { numRuns: NUM_RUNS },
    );
  });

  test('email last-segment (first.last@domain) -> exactly the 4-candidate rule', () => {
    fc.assert(
      fc.property(token(), token(), (first, last) => {
        const arn = `arn:aws:sts::123456789012:assumed-role/RoleName/${first}.${last}@example.com`;
        const out = buildBedrockUserCandidates(arn);
        expect(out).toEqual([
          first.slice(0, 2) + last,
          first.slice(0, 1) + last,
          first.slice(0, 3) + last,
          first + last,
        ]);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  test('non-email last segment -> [identifier]', () => {
    fc.assert(
      fc.property(token(), (id) => {
        // id is alphanumeric only => no '@', no '/'
        const arn = `arn:aws:iam::123456789012:user/${id}`;
        const out = buildBedrockUserCandidates(arn);
        expect(out).toEqual([id]);
      }),
      { numRuns: NUM_RUNS },
    );
  });
});

// ---------------------------------------------------------------------------
// Feature: app-deployment-readiness, Property 5: SSO config 블록 왕복(round-trip)
// Validates: Requirements 4.2
// For any valid {name, startUrl, region, accountId, roleName},
// parseSsoProfileBlock(buildSsoProfileBlock(x), x.name) recovers the input
// values.
//
// Domain: fc-generated strings restricted to a safe ini charset — no newlines,
// no square brackets, no '=' and no leading/trailing whitespace — so the
// generated ini block stays valid and values survive the parser's trim().
// ---------------------------------------------------------------------------
describe('Property 5: SSO config 블록 왕복', () => {
  // Safe ini value charset: alnum + common URL chars, excludes whitespace,
  // newlines, '[', ']', '='.
  const iniChars = (alnum + ':/.-_').split('');
  const iniValue = (min = 1) =>
    fc
      .array(fc.constantFrom(...iniChars), { minLength: min, maxLength: 40 })
      .map((chars) => chars.join(''));

  test('parse(build(x), name) recovers startUrl/region/accountId/roleName', () => {
    fc.assert(
      fc.property(
        fc.record({
          name: iniValue(1),
          startUrl: iniValue(0),
          region: iniValue(0),
          accountId: iniValue(0),
          roleName: iniValue(0),
        }),
        (input) => {
          const block = buildSsoProfileBlock(input);
          const parsed = parseSsoProfileBlock(block, input.name);
          expect(parsed).toEqual({
            startUrl: input.startUrl,
            region: input.region,
            accountId: input.accountId,
            roleName: input.roleName,
          });
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });
});
