const { exec } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');

// AWS SDK for JavaScript v3 (task 3 — CLI shell-out 대체, 계약 보존)
// 모두 순수 JS 패키지이므로 electron 없이도 require 가능하다.
const {
  SSOOIDCClient,
  RegisterClientCommand,
  StartDeviceAuthorizationCommand,
  CreateTokenCommand,
} = require('@aws-sdk/client-sso-oidc');
const { SSOClient, GetRoleCredentialsCommand } = require('@aws-sdk/client-sso');
const { STSClient, GetCallerIdentityCommand, AssumeRoleCommand } = require('@aws-sdk/client-sts');
const { fromSSO } = require('@aws-sdk/credential-providers');

// ---------------------------------------------------------------------------
// Pure helper functions (task 2.1)
// 파일 IO·네트워크·예외와 무관한 순수 함수. fast-check 프로퍼티 테스트가 직접
// import 하여 검증한다. 기존 IPC 계약·반환 형태는 불변이며, 아래 헬퍼는 task 3에서
// login/getCredentials/getBedrockUsername 재구현이 사용할 로직 조각을 분리한 것이다.
// ---------------------------------------------------------------------------

/**
 * SDK가 반환하는 자격증명 객체를 렌더러/백엔드가 기대하는 env-var 키 형태로 매핑한다.
 * getCredentials의 반환 계약(env-var 키)을 보존한다. 값은 그대로 보존하며,
 * 누락된 필드는 빈 문자열로 채워 항상 정확히 4개 키를 가진 객체를 반환한다.
 * 예외를 던지지 않는다.
 *
 * @param {{accessKeyId?, secretAccessKey?, sessionToken?, region?}} sdkCreds
 * @returns {{AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_DEFAULT_REGION}}
 */
function mapCredentials(sdkCreds) {
  const c = (sdkCreds && typeof sdkCreds === 'object') ? sdkCreds : {};
  const keep = (v) => (v !== undefined && v !== null ? v : '');
  return {
    AWS_ACCESS_KEY_ID: keep(c.accessKeyId),
    AWS_SECRET_ACCESS_KEY: keep(c.secretAccessKey),
    AWS_SESSION_TOKEN: keep(c.sessionToken),
    AWS_DEFAULT_REGION: keep(c.region),
  };
}

/**
 * 프로파일 이름 배열을 정렬한다. bedrockuser 접두 프로파일을 상단에 두고,
 * 같은 그룹 내에서는 localeCompare(사전순)를 유지한다. 입력을 변형하지 않고
 * 정렬된 새 배열을 반환한다(listProfiles의 기존 인라인 정렬과 동일한 동작).
 *
 * @param {string[]} names
 * @returns {string[]}
 */
function sortProfiles(names) {
  return [...names].sort((a, b) => {
    const aB = a.startsWith('bedrockuser') ? 0 : 1;
    const bB = b.startsWith('bedrockuser') ? 0 : 1;
    return aB - bB || a.localeCompare(b);
  });
}

/**
 * STS caller ARN에서 BedrockUser assume-role 후보 이름 목록을 생성한다.
 * - 이메일(first.last@...) → [first[:2]+last, first[:1]+last, first[:3]+last, first+last]
 * - 이메일이 아니면 → [식별자]
 * - 이름 분해 불가 시 → 정의된 폴백([firstToken])
 * 항상 문자열 배열을 반환하며 예외를 던지지 않는다.
 *
 * @param {string} arnOrEmail STS caller ARN 또는 이메일/식별자
 * @returns {string[]}
 */
function bedrockUsernameCandidates(arnOrEmail) {
  const safeArn = typeof arnOrEmail === 'string' ? arnOrEmail : '';
  const emailOrId = safeArn.split('/').pop() || '';
  // 비이메일 식별자 → 원본 식별자 그대로
  if (!emailOrId.includes('@')) {
    return [emailOrId];
  }
  const namePart = emailOrId.split('@')[0]; // 예: changgeun.jang
  const names = namePart.split('.');
  // 이름 분해 불가(단일 토큰) → 정의된 폴백
  if (names.length < 2) {
    return [names[0] || ''];
  }
  const first = names[0];
  const last = names[names.length - 1];
  return [
    first.slice(0, 2) + last,
    first.slice(0, 1) + last,
    first.slice(0, 3) + last,
    first + last,
  ];
}

// ---------------------------------------------------------------------------
// 조직 기본 SSO 프리셋 (task: 최종 사용자 zero-config 온보딩)
// 사내 배포 대상자가 아무 값도 입력하지 않고 "로그인" 버튼만 눌러 사용할 수 있도록,
// 확정된 조직 기본 SSO 프리셋을 하드코딩한다. ViewOnlyAccess는 로그인·신원확인 용도이며
// 실제 게이트웨이 권한은 로그인 후 BedrockUser-{name} role assume로 별도 획득한다.
// 값은 AE_SSO_* 환경변수로 배포 시 override 가능(다른 조직/관리자 설정).
// ---------------------------------------------------------------------------

/**
 * 확정된 조직 기본 SSO 프리셋. resolveDefaultSsoPreset의 하드코딩 폴백 값.
 * buildSsoProfileBlock({name, startUrl, region, accountId, roleName})와 호환되는 형태.
 * @type {{name: string, startUrl: string, region: string, accountId: string, roleName: string}}
 */
const DEFAULT_SSO_PRESET = Object.freeze({
  name: 'bedrock-gw',
  startUrl: 'https://d-906617189d.awsapps.com/start',
  region: 'us-east-1',
  accountId: '107650139384',
  roleName: 'ViewOnlyAccess', // 일반 사용자 기본값 (관리자만 AdministratorAccess)
});

/**
 * 프리셋 각 필드를 override 하는 환경변수 키.
 * @type {{name: string, startUrl: string, region: string, accountId: string, roleName: string}}
 */
const SSO_PRESET_ENV_KEYS = Object.freeze({
  name: 'AE_SSO_PROFILE_NAME',
  startUrl: 'AE_SSO_START_URL',
  region: 'AE_SSO_REGION',
  accountId: 'AE_SSO_ACCOUNT_ID',
  roleName: 'AE_SSO_ROLE_NAME',
});

/**
 * 조직 기본 SSO 프리셋을 해석한다(순수 함수).
 * 환경변수 override(AE_SSO_*)가 있으면 개별 반영하고, 없으면 하드코딩 기본값을 사용한다.
 * env 인자를 명시적으로 받아(기본 process.env) 테스트 가능하게 한다.
 *
 * - 예외를 던지지 않는다.
 * - 항상 정확히 5개 문자열 키({name, startUrl, region, accountId, roleName})를 반환한다.
 * - 반환 형태는 buildSsoProfileBlock과 호환되어 그대로 넘겨 쓸 수 있다.
 * - override 값은 문자열이고 trim 후 비어있지 않을 때만 적용한다(빈 값·비문자열 무시 → 기본값).
 *
 * @param {Record<string, any>} [env=process.env] 환경변수 맵
 * @returns {{name: string, startUrl: string, region: string, accountId: string, roleName: string}}
 */
function resolveDefaultSsoPreset(env = process.env) {
  const source = (env && typeof env === 'object') ? env : {};
  const preset = {};
  for (const field of Object.keys(SSO_PRESET_ENV_KEYS)) {
    const envKey = SSO_PRESET_ENV_KEYS[field];
    const raw = source[envKey];
    // override는 문자열이면서 trim 후 비어있지 않을 때만 적용. 그 외에는 하드코딩 기본값.
    if (typeof raw === 'string' && raw.trim() !== '') {
      preset[field] = raw.trim();
    } else {
      preset[field] = DEFAULT_SSO_PRESET[field];
    }
  }
  return preset;
}

/**
 * 온보딩 입력으로 ~/.aws/config에 append/생성할 SSO 프로파일 ini 블록 문자열을 만든다.
 * SSO 메타데이터만 포함하며 aws_access_key_id/aws_secret_access_key를 절대 포함하지 않는다.
 *
 * @param {{name, startUrl, region, accountId, roleName}} input
 * @returns {string} ini 블록 문자열
 */
function buildSsoProfileBlock({ name, startUrl, region, accountId, roleName }) {
  return [
    `[profile ${name}]`,
    `sso_start_url = ${startUrl}`,
    `sso_region = ${region}`,
    `sso_account_id = ${accountId}`,
    `sso_role_name = ${roleName}`,
    `region = ${region}`,
    '',
  ].join('\n');
}

/**
 * buildSsoProfileBlock가 생성한 형식의 ini 텍스트에서 지정한 프로파일의 SSO 구성을
 * 파싱한다. buildSsoProfileBlock와 왕복(round-trip) 관계를 만족한다. 예외를 던지지 않으며,
 * 항목이 없으면 빈 문자열을 채운 객체를 반환한다.
 *
 * @param {string} iniText
 * @param {string} name
 * @returns {{startUrl, region, accountId, roleName}}
 */
function parseSsoProfileBlock(iniText, name) {
  const result = { startUrl: '', region: '', accountId: '', roleName: '' };
  if (typeof iniText !== 'string') return result;
  const header = `[profile ${name}]`;
  let inSection = false;
  for (const rawLine of iniText.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line.startsWith('[') && line.endsWith(']')) {
      inSection = line === header;
      continue;
    }
    if (!inSection) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    switch (key) {
      case 'sso_start_url':
        result.startUrl = value;
        break;
      case 'sso_region':
        result.region = value;
        break;
      case 'sso_account_id':
        result.accountId = value;
        break;
      case 'sso_role_name':
        result.roleName = value;
        break;
      default:
        break;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// 내부 유틸 (파일 IO / 브라우저 오픈 / CLI 탐지) — task 3
// electron은 login의 브라우저 오픈에서만 lazy-require 한다. 단위 테스트나 electron
// 부재 환경에서도 이 모듈은 require 가능하다.
// ---------------------------------------------------------------------------

// 브라우저 오픈 훅. 테스트/호출측이 주입할 수 있으며, 미주입 시 electron shell을
// lazy-require 하고, 그마저 없으면 콘솔에 URL을 출력하는 no-op로 폴백한다.
let _externalOpener = null;

/** verificationUriComplete를 여는 opener를 주입한다(테스트/커스텀 용도, 선택적). */
function setExternalOpener(fn) {
  _externalOpener = typeof fn === 'function' ? fn : null;
}

function openExternal(url) {
  if (typeof _externalOpener === 'function') {
    try { return _externalOpener(url); } catch (_) { /* 폴백으로 진행 */ }
  }
  try {
    // eslint-disable-next-line global-require
    const { shell } = require('electron');
    if (shell && typeof shell.openExternal === 'function') {
      return shell.openExternal(url);
    }
  } catch (_) {
    // electron 부재(단위 테스트 등) — 콘솔 폴백
  }
  console.log(`[aws-sso] 브라우저에서 다음 URL을 열어 로그인하세요: ${url}`);
  return undefined;
}

function _sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** ~/.aws/config 내용을 읽는다. /fsx/home/{user} 우선, 그다음 홈, AWS_CONFIG_FILE 순. */
function readAwsConfigContent() {
  const username = os.userInfo().username;
  const candidates = [
    `/fsx/home/${username}/.aws/config`,
    path.join(os.homedir(), '.aws', 'config'),
    process.env.AWS_CONFIG_FILE || '',
  ].filter(Boolean);
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) return fs.readFileSync(p, 'utf-8');
    } catch (_) {
      // 다음 후보 시도
    }
  }
  return '';
}

/**
 * SSO 프로파일 블록을 append/생성할 대상 ~/.aws/config 경로를 결정한다.
 * readAwsConfigContent와 동일한 우선순위(첫 존재 후보)를 따르므로, 기록 후
 * listProfiles가 새 프로파일을 즉시 인식한다. 존재 후보가 없으면 ~/.aws/config로 폴백.
 *
 * @returns {string}
 */
function resolveWritableConfigPath() {
  const username = os.userInfo().username;
  const candidates = [
    `/fsx/home/${username}/.aws/config`,
    path.join(os.homedir(), '.aws', 'config'),
    process.env.AWS_CONFIG_FILE || '',
  ].filter(Boolean);
  for (const p of candidates) {
    try {
      if (fs.existsSync(p)) return p;
    } catch (_) {
      // 다음 후보 시도
    }
  }
  return path.join(os.homedir(), '.aws', 'config');
}

/** 일반 ini 파서 — { headerText: { key: value } } 맵을 반환한다. */
function parseIniSections(content) {
  const sections = {};
  let current = null;
  for (const raw of String(content || '').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#') || line.startsWith(';')) continue;
    if (line.startsWith('[') && line.endsWith(']')) {
      current = line.slice(1, -1).trim();
      if (!sections[current]) sections[current] = {};
      continue;
    }
    if (!current) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    sections[current][line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return sections;
}

/**
 * ~/.aws/config에서 프로파일의 SSO 구성을 해석한다. sso_session 간접 참조를
 * best-effort로 지원한다. 항상 객체를 반환하며 예외를 던지지 않는다.
 *
 * @param {string} profileName
 * @returns {{startUrl, region, accountId, roleName}}
 */
function resolveSsoConfig(profileName) {
  const out = { startUrl: '', region: '', accountId: '', roleName: '' };
  const sections = parseIniSections(readAwsConfigContent());
  // [default]는 'profile ' 접두가 없다. 그 외는 'profile <name>'.
  const header = profileName === 'default' ? 'default' : `profile ${profileName}`;
  const prof = sections[header] || {};
  out.startUrl = prof.sso_start_url || '';
  out.region = prof.sso_region || '';
  out.accountId = prof.sso_account_id || '';
  out.roleName = prof.sso_role_name || '';
  // sso_session 간접 참조 (best-effort): start_url/region을 세션 블록에서 보완
  if ((!out.startUrl || !out.region) && prof.sso_session) {
    const sess = sections[`sso-session ${prof.sso_session}`] || {};
    out.startUrl = out.startUrl || sess.sso_start_url || '';
    out.region = out.region || sess.sso_region || '';
  }
  // sso_region이 없으면 일반 region으로 폴백
  if (!out.region) out.region = prof.region || '';
  return out;
}

/**
 * SSO 토큰을 AWS CLI 호환 형식으로 ~/.aws/sso/cache/<sha1(startUrl)>.json에 기록한다.
 * 이로써 기존 sso:get-expiry 핸들러와 Python boto3 SSO 해석이 동일 캐시를 재사용한다.
 */
function writeSsoTokenCache({ startUrl, region, accessToken, expiresIn, clientId, clientSecret, registrationExpiresAt }) {
  const cacheDir = path.join(os.homedir(), '.aws', 'sso', 'cache');
  fs.mkdirSync(cacheDir, { recursive: true });
  const hash = crypto.createHash('sha1').update(String(startUrl)).digest('hex');
  // AWS CLI는 밀리초 없는 UTC ISO8601(...Z)를 사용한다. boto3 호환을 위해 ms를 제거.
  const toCliIso = (d) => new Date(d).toISOString().replace(/\.\d{3}Z$/, 'Z');
  const payload = {
    startUrl,
    region,
    accessToken,
    expiresAt: toCliIso(Date.now() + (Number(expiresIn) || 0) * 1000),
    clientId,
    clientSecret,
  };
  if (registrationExpiresAt !== undefined && registrationExpiresAt !== null) {
    // SDK는 clientSecretExpiresAt을 epoch seconds(number) 또는 Date로 반환할 수 있다.
    const asDate = typeof registrationExpiresAt === 'number'
      ? new Date(registrationExpiresAt * 1000)
      : new Date(registrationExpiresAt);
    if (!Number.isNaN(asDate.getTime())) {
      payload.registrationExpiresAt = toCliIso(asDate);
    }
  }
  fs.writeFileSync(path.join(cacheDir, `${hash}.json`), JSON.stringify(payload, null, 2), 'utf-8');
}

/** `aws` 실행파일이 PATH에 있는지 탐지한다(which/where). 예외 없이 boolean resolve. */
function hasAwsCli() {
  return new Promise((resolve) => {
    const cmd = process.platform === 'win32' ? 'where aws' : 'which aws';
    exec(cmd, { timeout: 5000 }, (err, stdout) => {
      resolve(!err && !!String(stdout || '').trim());
    });
  });
}

/**
 * CreateToken을 폴링한다. interval 준수, AuthorizationPendingException 대기,
 * SlowDownException 백오프, ExpiredToken/AccessDenied는 실패로 throw.
 */
async function pollForToken(oidc, { clientId, clientSecret, deviceCode, interval, expiresIn }) {
  let intervalMs = (Number(interval) || 5) * 1000;
  const deadline = Date.now() + (Number(expiresIn) || 600) * 1000;
  while (Date.now() < deadline) {
    await _sleep(intervalMs);
    try {
      const resp = await oidc.send(new CreateTokenCommand({
        clientId,
        clientSecret,
        grantType: 'urn:ietf:params:oauth:grant-type:device_code',
        deviceCode,
      }));
      return resp; // { accessToken, expiresIn, ... }
    } catch (e) {
      const name = (e && e.name) || '';
      if (name === 'AuthorizationPendingException') {
        continue; // 사용자 승인 대기 — 계속 폴링
      }
      if (name === 'SlowDownException') {
        intervalMs += 5000; // 백오프 후 계속
        continue;
      }
      // ExpiredTokenException / AccessDeniedException / 기타 → 실패
      throw e;
    }
  }
  throw new Error('device-code 토큰 폴링 시간이 초과되었습니다');
}

class AwsSsoManager {
  listProfiles() {
    const content = readAwsConfigContent();
    if (!content) return [];

    const profiles = [];
    const regex = /\[profile\s+(.+?)\]/g;
    let match;
    while ((match = regex.exec(content)) !== null) {
      profiles.push(match[1]);
    }
    if (content.includes('[default]')) profiles.unshift('default');

    // bedrockuser-* assume role 프로파일을 상단에 정렬 (순수 헬퍼로 분리 — 동작 동일)
    return sortProfiles(profiles);
  }

  /**
   * SSO 로그인 (AWS SDK for JS v3 device-code 흐름).
   * SDK 경로 실패 시, aws 실행파일이 있을 때에 한해 기존 CLI shell-out으로 폴백한다.
   * 반환 계약: { success: true, profile } | { success: false, error } (불변).
   */
  async login(profileName) {
    try {
      return await this._loginViaSdk(profileName);
    } catch (sdkErr) {
      // CLI 폴백은 aws 실행파일이 실제로 존재할 때만 (R2.1: CLI 강한 의존 제거)
      const cliAvailable = await hasAwsCli();
      if (cliAvailable) {
        return this._loginViaCli(profileName);
      }
      return {
        success: false,
        error: (sdkErr && sdkErr.message) ? sdkErr.message : String(sdkErr),
      };
    }
  }

  /** SDK v3 device-code 로그인 본체. 실패 시 throw(상위 login이 폴백/오류 처리). */
  async _loginViaSdk(profileName) {
    const cfg = resolveSsoConfig(profileName);
    if (!cfg.startUrl || !cfg.region || !cfg.accountId || !cfg.roleName) {
      throw new Error(`프로파일 '${profileName}'의 SSO 구성(sso_start_url/sso_region/sso_account_id/sso_role_name)을 ~/.aws/config에서 찾을 수 없습니다`);
    }

    const oidc = new SSOOIDCClient({ region: cfg.region });
    const reg = await oidc.send(new RegisterClientCommand({
      clientName: 'agentic-editor',
      clientType: 'public',
    }));
    const auth = await oidc.send(new StartDeviceAuthorizationCommand({
      clientId: reg.clientId,
      clientSecret: reg.clientSecret,
      startUrl: cfg.startUrl,
    }));

    // 브라우저로 device-code 승인 페이지 오픈 (electron shell 또는 주입된 opener)
    openExternal(auth.verificationUriComplete || auth.verificationUri);

    const token = await pollForToken(oidc, {
      clientId: reg.clientId,
      clientSecret: reg.clientSecret,
      deviceCode: auth.deviceCode,
      interval: auth.interval,
      expiresIn: auth.expiresIn,
    });

    // AWS CLI 호환 형식으로 토큰 캐시 기록 (sso:get-expiry / boto3 SSO 재사용 보존)
    writeSsoTokenCache({
      startUrl: cfg.startUrl,
      region: cfg.region,
      accessToken: token.accessToken,
      expiresIn: token.expiresIn,
      clientId: reg.clientId,
      clientSecret: reg.clientSecret,
      registrationExpiresAt: reg.clientSecretExpiresAt,
    });

    // 자격증명 검증 — GetRoleCredentials 성공 시에만 로그인 성공으로 간주
    const sso = new SSOClient({ region: cfg.region });
    await sso.send(new GetRoleCredentialsCommand({
      accessToken: token.accessToken,
      accountId: cfg.accountId,
      roleName: cfg.roleName,
    }));

    return { success: true, profile: profileName };
  }

  /** 기존 AWS CLI shell-out 로그인 (SDK 실패 + CLI 존재 시에만 사용되는 폴백). */
  _loginViaCli(profileName) {
    return new Promise((resolve) => {
      exec(`aws sso login --profile ${profileName}`, { timeout: 120000 }, (err, _stdout, stderr) => {
        const verify = (onFailMsg) => {
          exec(
            `aws configure export-credentials --profile ${profileName} --format env-no-export`,
            { timeout: 10000 },
            (e, out) => {
              if (!e && String(out || '').includes('AWS_ACCESS_KEY_ID')) {
                resolve({ success: true, profile: profileName });
              } else {
                resolve({ success: false, error: onFailMsg });
              }
            },
          );
        };
        if (err) {
          // SSO 로그인 실패 — 이미 유효한 세션이 있는지 확인
          verify(stderr || err.message);
        } else {
          // SSO 로그인 성공 — 자격증명 검증
          verify('SSO 로그인 후 자격증명 획득 실패');
        }
      });
    });
  }

  /**
   * 자격증명 획득 (SDK v3 fromSSO). env-var 키 형태로 매핑해 반환하고 실패 시 null.
   * 반환 계약: {AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN, AWS_DEFAULT_REGION} | null.
   */
  async getCredentials(profileName) {
    try {
      const provider = fromSSO({ profile: profileName });
      const creds = await provider();
      if (!creds || !creds.accessKeyId) {
        throw new Error('fromSSO가 자격증명을 반환하지 않았습니다');
      }
      // fromSSO 자격증명에는 region이 없으므로 config에서 보완
      const cfg = resolveSsoConfig(profileName);
      return mapCredentials({
        accessKeyId: creds.accessKeyId,
        secretAccessKey: creds.secretAccessKey,
        sessionToken: creds.sessionToken,
        region: cfg.region,
      });
    } catch (_) {
      // SDK 경로 실패 — aws 실행파일이 있을 때에 한해 CLI 폴백 (login과 동일 패턴, R2.1).
      // CLI가 없으면 폴백 없이 null. 반환 계약({...} | null) 불변.
      const cliAvailable = await hasAwsCli();
      if (cliAvailable) {
        return this._getCredentialsViaCli(profileName);
      }
      return null;
    }
  }

  /**
   * 기존 AWS CLI shell-out 자격증명 획득 (SDK 실패 + CLI 존재 시에만 사용되는 폴백).
   * `aws configure export-credentials --format env-no-export` 출력을 파싱해 env-var 키
   * 형태로 매핑한다. 실패 시 null. 반환 계약({...} | null) 불변.
   */
  _getCredentialsViaCli(profileName) {
    return new Promise((resolve) => {
      exec(
        `aws configure export-credentials --profile ${profileName} --format env-no-export`,
        { timeout: 10000 },
        (err, stdout) => {
          if (err || !String(stdout || '').includes('AWS_ACCESS_KEY_ID')) {
            resolve(null);
            return;
          }
          const env = {};
          for (const line of String(stdout).split(/\r?\n/)) {
            const eq = line.indexOf('=');
            if (eq === -1) continue;
            env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
          }
          const cfg = resolveSsoConfig(profileName);
          resolve(mapCredentials({
            accessKeyId: env.AWS_ACCESS_KEY_ID,
            secretAccessKey: env.AWS_SECRET_ACCESS_KEY,
            sessionToken: env.AWS_SESSION_TOKEN,
            region: env.AWS_DEFAULT_REGION || cfg.region,
          }));
        },
      );
    });
  }

  /**
   * SSO identity에서 BedrockUser 이름 추출 (SDK v3 STS).
   * GetCallerIdentity → 후보 생성(순수 헬퍼) → AssumeRole 프로브. 반환 계약: string (불변).
   */
  async getBedrockUsername(profileName) {
    try {
      const cfg = resolveSsoConfig(profileName);
      const region = cfg.region || 'us-west-2';
      const credentials = fromSSO({ profile: profileName });
      const sts = new STSClient({ region, credentials });

      const ident = await sts.send(new GetCallerIdentityCommand({}));
      const arn = ident.Arn || '';
      // 후보 생성 규칙은 순수 헬퍼 그대로 사용 (실행 수단만 SDK로 교체 — 동작 보존)
      const candidates = bedrockUsernameCandidates(arn);
      // 비이메일/단일 토큰 → 후보 1개, assume-role 없이 즉시 반환 (기존 동작 보존)
      if (candidates.length === 1) return candidates[0];

      const account = ident.Account;
      for (const candidate of candidates) {
        try {
          await sts.send(new AssumeRoleCommand({
            RoleArn: `arn:aws:iam::${account}:role/BedrockUser-${candidate}`,
            RoleSessionName: 'probe',
            DurationSeconds: 900,
          }));
          return candidate; // 첫 성공 후보 (assume-role로 검증됨)
        } catch (_) {
          // 다음 후보 시도
        }
      }
      // 어떤 후보도 실제 assume-role에 성공하지 못함 → 틀린 추측을 저장하지 않는다.
      // 빈 문자열을 반환하면 UI가 사용자에게 BedrockUser 이름을 1회 입력받는다(배포 안전).
      // (틀린 이름을 조용히 저장하면 다른 사용자 계정/DynamoDB에 잘못 묶일 위험)
      return '';
    } catch (_) {
      return '';
    }
  }

  /**
   * 온보딩 입력으로 ~/.aws/config에 SSO 프로파일 블록을 append/생성한다 (spec §6.1, R4.2/4.5/4.6).
   *
   * - buildSsoProfileBlock로 secret-free ini 블록을 생성한다.
   *   aws_access_key_id/aws_secret_access_key는 절대 기록하지 않는다(R4.6).
   * - 대상 파일은 resolveWritableConfigPath로 결정(/fsx/home/{user} 우선순위 존중, listProfiles와 일치).
   * - 동일 프로파일명이 이미 존재하면 중복 생성하지 않고 명확한 결과를 반환한다.
   * - 쓰기 권한 오류 시 실패 사유와 수동 구성용 ini 블록(secret-free)을 manualHint로 반환한다(R4.5).
   *
   * 반환 계약:
   *   성공     → { success: true, profile }
   *   중복     → { success: false, duplicate: true, profile, error }
   *   입력누락 → { success: false, error }
   *   쓰기오류 → { success: false, error, manualHint }
   *
   * @param {{name, startUrl, region, accountId, roleName}} input
   * @returns {{success: boolean, profile?: string, duplicate?: boolean, error?: string, manualHint?: string}}
   */
  writeSsoProfile(input) {
    const { name, startUrl, region, accountId, roleName } = input || {};

    // 1) 필수 입력 검증 — 통과 전까지 config를 건드리지 않는다.
    const missing = [];
    if (!name || !String(name).trim()) missing.push('name');
    if (!startUrl || !String(startUrl).trim()) missing.push('startUrl');
    if (!region || !String(region).trim()) missing.push('region');
    if (!accountId || !String(accountId).trim()) missing.push('accountId');
    if (!roleName || !String(roleName).trim()) missing.push('roleName');
    if (missing.length > 0) {
      return { success: false, error: `필수 입력이 누락되었습니다: ${missing.join(', ')}` };
    }

    const profileName = String(name).trim();
    // secret-free 블록 — aws_access_key_id/secret 미포함 (R4.6). manualHint로도 재사용.
    const block = buildSsoProfileBlock({
      name: profileName,
      startUrl: String(startUrl).trim(),
      region: String(region).trim(),
      accountId: String(accountId).trim(),
      roleName: String(roleName).trim(),
    });
    const configPath = resolveWritableConfigPath();

    // 2) 중복 프로파일명 검사 — 대상 파일(listProfiles가 읽는 파일)에서 확인.
    try {
      if (fs.existsSync(configPath)) {
        const existing = fs.readFileSync(configPath, 'utf-8');
        const sections = parseIniSections(existing);
        const dupHeader = profileName === 'default' ? 'default' : `profile ${profileName}`;
        if (sections[dupHeader]) {
          return {
            success: false,
            duplicate: true,
            profile: profileName,
            error: `프로파일 '${profileName}'이(가) 이미 ~/.aws/config에 존재합니다`,
          };
        }
      }
    } catch (_) {
      // 읽기 실패는 아래 쓰기 단계에서 권한 오류로 표면화된다.
    }

    // 3) append/생성 — 디렉터리 없으면 생성, 기존 내용 끝 개행 보정.
    try {
      fs.mkdirSync(path.dirname(configPath), { recursive: true });
      let prefix = '';
      if (fs.existsSync(configPath)) {
        const current = fs.readFileSync(configPath, 'utf-8');
        if (current.length > 0 && !current.endsWith('\n')) prefix = '\n';
      }
      fs.appendFileSync(configPath, prefix + block, 'utf-8');
      return { success: true, profile: profileName };
    } catch (error) {
      // 쓰기 권한 오류 등 — 수동 구성용 secret-free 블록을 manualHint로 제공(R4.5).
      return {
        success: false,
        error: `~/.aws/config 쓰기에 실패했습니다: ${(error && error.message) || String(error)}`,
        manualHint: block,
      };
    }
  }
}

module.exports = {
  AwsSsoManager,
  mapCredentials,
  sortProfiles,
  buildBedrockUserCandidates: bedrockUsernameCandidates,
  buildSsoProfileBlock,
  parseSsoProfileBlock,
  setExternalOpener,
  // zero-config 온보딩 — 조직 기본 SSO 프리셋
  DEFAULT_SSO_PRESET,
  SSO_PRESET_ENV_KEYS,
  resolveDefaultSsoPreset,
};
