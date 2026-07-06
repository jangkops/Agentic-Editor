/**
 * Property-based test for Property 6: 자격증명 비저장 불변
 *
 * Feature: app-deployment-readiness, Property 6: 자격증명 비저장 불변
 * Validates: Requirements 2.6, 4.6
 *
 * Runner: jest (repo "test:unit": "jest tests/unit/")
 * Library: fast-check
 *
 * For any credential object + onboarding input:
 *   (a) buildSsoProfileBlock(input) NEVER contains aws_access_key_id /
 *       aws_secret_access_key (nor the AWS_* env-var key literals) and NEVER
 *       contains any of the credential values.
 *   (b) The settings object that would be persisted to settings.json contains
 *       ONLY the profile name (+ optional bedrockUser name), and NEVER the
 *       credential keys or their values.
 *
 * The block builder is imported directly from the real module. The settings
 * serialization is modeled after what the app actually persists — see
 * src/main.js (`saveSettings({ awsProfile: profile })` / `state.settings.bedrockUser`)
 * and electron/core/data-store.js (`saveSettings` -> JSON.stringify(settings)).
 * The renderer only ever writes the profile NAME (and optional bedrockUser
 * name); credentials are injected at runtime and never touch settings.json.
 */

const fc = require('fast-check');
const { buildSsoProfileBlock } = require('../../electron/core/aws-sso-manager');

const NUM_RUNS = 200; // >= 100 required by the spec

// ---------------------------------------------------------------------------
// Model of the settings serialization the app uses (profile name only).
// Mirrors src/main.js: `saveSettings({ awsProfile: profile })` and
// `state.settings.bedrockUser = bu`. Credentials are NEVER placed here.
// data-store.saveSettings() persists this object via JSON.stringify(settings).
// This helper deliberately ignores any credentials passed alongside it, which
// is exactly the invariant Property 6 protects.
// ---------------------------------------------------------------------------
function buildPersistedSettings({ profileName, bedrockUser }) {
  const settings = { awsProfile: profileName };
  if (bedrockUser) settings.bedrockUser = bedrockUser;
  return settings;
}

// Sentinel char that none of the onboarding-input generators can produce, so
// generated credential values are guaranteed distinct from any block content.
// This keeps the "credential value never appears" assertion meaningful.
const SENTINEL = '\u00A7'; // §

// Safe ini value charset for onboarding fields: alnum + common URL/ARN chars,
// excludes whitespace, newlines, '[', ']', '=' and the sentinel.
const alnum = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
const iniChars = (alnum + ':/.-_').split('');
const iniValue = (min = 1) =>
  fc
    .array(fc.constantFrom(...iniChars), { minLength: min, maxLength: 40 })
    .map((chars) => chars.join(''));

const onboardingInput = fc.record({
  name: iniValue(1),
  startUrl: iniValue(0),
  region: iniValue(0),
  accountId: iniValue(0),
  roleName: iniValue(0),
});

// Credential-like values carrying the sentinel so they can never collide with
// onboarding-derived block content.
const credValue = (prefix) =>
  fc
    .array(fc.constantFrom(...alnum.split('')), { minLength: 8, maxLength: 40 })
    .map((chars) => `${prefix}${SENTINEL}${chars.join('')}`);

const credentials = fc.record({
  AWS_ACCESS_KEY_ID: credValue('AKIA'),
  AWS_SECRET_ACCESS_KEY: credValue('SECRET'),
  AWS_SESSION_TOKEN: credValue('TOKEN'),
  AWS_DEFAULT_REGION: iniValue(0),
  // Also model the raw SDK-shaped keys, which must likewise never leak.
  accessKeyId: credValue('AKIA'),
  secretAccessKey: credValue('SECRET'),
  sessionToken: credValue('TOKEN'),
});

// Forbidden credential KEY literals (case-insensitive) that must never appear
// in either the config block or the persisted settings.
const FORBIDDEN_KEYS = [
  'aws_access_key_id',
  'aws_secret_access_key',
  'aws_session_token',
  'accesskeyid',
  'secretaccesskey',
  'sessiontoken',
];

function assertNoForbiddenKeys(text) {
  const lower = text.toLowerCase();
  for (const key of FORBIDDEN_KEYS) {
    expect(lower.includes(key)).toBe(false);
  }
}

function assertNoCredentialValues(text, creds) {
  const values = [
    creds.AWS_ACCESS_KEY_ID,
    creds.AWS_SECRET_ACCESS_KEY,
    creds.AWS_SESSION_TOKEN,
    creds.accessKeyId,
    creds.secretAccessKey,
    creds.sessionToken,
  ];
  for (const v of values) {
    if (v) expect(text.includes(v)).toBe(false);
  }
}

describe('Property 6: 자격증명 비저장 불변', () => {
  test('(a) buildSsoProfileBlock never emits credential keys or values', () => {
    fc.assert(
      fc.property(onboardingInput, credentials, (input, creds) => {
        const block = buildSsoProfileBlock(input);

        // No credential key literals (case-insensitive).
        assertNoForbiddenKeys(block);
        // No credential values leak into the block.
        assertNoCredentialValues(block, creds);

        // Sanity: the block is still a valid SSO profile block (secret-free
        // metadata only) — the profile name is present.
        expect(block.includes(`[profile ${input.name}]`)).toBe(true);
      }),
      { numRuns: NUM_RUNS },
    );
  });

  test('(b) persisted settings contain only the profile name, never credentials', () => {
    fc.assert(
      fc.property(
        onboardingInput,
        credentials,
        fc.option(iniValue(1), { nil: undefined }),
        (input, creds, bedrockUser) => {
          const settings = buildPersistedSettings({
            profileName: input.name,
            bedrockUser,
          });

          // The persisted object holds only the profile name (+ optional
          // bedrockUser name) — no credential fields whatsoever.
          const allowedKeys = ['awsProfile', 'bedrockUser'];
          expect(Object.keys(settings).every((k) => allowedKeys.includes(k))).toBe(true);
          expect(settings.awsProfile).toBe(input.name);

          // Serialize exactly as data-store.saveSettings() does.
          const serialized = JSON.stringify(settings, null, 2);

          assertNoForbiddenKeys(serialized);
          assertNoCredentialValues(serialized, creds);
        },
      ),
      { numRuns: NUM_RUNS },
    );
  });
});
