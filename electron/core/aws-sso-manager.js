const { exec } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');

class AwsSsoManager {
  listProfiles() {
    // /fsx/home/username/.aws/config 우선, 그 다음 ~/.aws/config
    const username = os.userInfo().username;
    const candidates = [
      `/fsx/home/${username}/.aws/config`,
      path.join(os.homedir(), '.aws', 'config'),
      process.env.AWS_CONFIG_FILE || '',
    ].filter(Boolean);

    let content = '';
    for (const p of candidates) {
      if (fs.existsSync(p)) {
        content = fs.readFileSync(p, 'utf-8');
        break;
      }
    }
    if (!content) return [];

    const profiles = [];
    const regex = /\[profile\s+(.+?)\]/g;
    let match;
    while ((match = regex.exec(content)) !== null) {
      profiles.push(match[1]);
    }
    if (content.includes('[default]')) profiles.unshift('default');

    // bedrockuser-* assume role 프로파일을 상단에 정렬
    profiles.sort((a, b) => {
      const aB = a.startsWith('bedrockuser') ? 0 : 1;
      const bB = b.startsWith('bedrockuser') ? 0 : 1;
      return aB - bB || a.localeCompare(b);
    });

    return profiles;
  }

  // assume role 기반 로그인 — bedrockuser-username 패턴
  login(profileName) {
    return new Promise((resolve) => {
      // 먼저 assume role로 자격증명 유효성 확인
      exec(`aws sts get-caller-identity --profile ${profileName}`, { timeout: 15000 }, (err, stdout) => {
        if (!err && stdout.includes('Account')) {
          resolve({ success: true, profile: profileName });
          return;
        }
        // SSO 로그인 시도
        exec(`aws sso login --profile ${profileName}`, { timeout: 120000 }, (err2, stdout2, stderr2) => {
          if (err2) {
            resolve({ success: false, error: stderr2 || err2.message });
          } else {
            resolve({ success: true, profile: profileName });
          }
        });
      });
    });
  }

  getCredentials(profileName) {
    return new Promise((resolve) => {
      exec(
        `aws configure export-credentials --profile ${profileName} --format env-no-export`,
        { timeout: 10000 },
        (err, stdout) => {
          if (err) { resolve(null); return; }
          const creds = {};
          for (const line of stdout.trim().split('\n')) {
            const [key, ...rest] = line.split('=');
            if (key && rest.length) creds[key.trim()] = rest.join('=').trim();
          }
          resolve(creds);
        }
      );
    });
  }

  // SSO identity에서 BedrockUser 이름 추출
  getBedrockUsername(profileName) {
    return new Promise((resolve) => {
      // Step 1: caller identity에서 이메일 추출
      exec(`aws sts get-caller-identity --profile ${profileName} --output json`, { timeout: 10000 }, (err, stdout) => {
        if (err) { resolve(''); return; }
        try {
          const data = JSON.parse(stdout);
          const arn = data.Arn || '';
          const emailOrId = arn.split('/').pop() || '';
          if (!emailOrId.includes('@')) { resolve(emailOrId); return; }
          const namePart = emailOrId.split('@')[0]; // changgeun.jang
          const names = namePart.split('.');
          if (names.length < 2) { resolve(names[0]); return; }
          const first = names[0], last = names[names.length - 1];
          const account = data.Account;
          // Step 2: BedrockUser-{prefix}{last} 패턴 시도
          const candidates = [first.slice(0,2)+last, first.slice(0,1)+last, first.slice(0,3)+last, first+last];
          let found = false;
          let tried = 0;
          for (const candidate of candidates) {
            exec(`aws sts assume-role --role-arn arn:aws:iam::${account}:role/BedrockUser-${candidate} --role-session-name probe --duration-seconds 900 --profile ${profileName} --output json`, { timeout: 8000 }, (e2, s2) => {
              tried++;
              if (!found && !e2) { found = true; resolve(candidate); }
              else if (tried >= candidates.length && !found) { resolve(first.slice(0,2)+last); } // fallback
            });
          }
        } catch { resolve(''); }
      });
    });
  }
}

module.exports = { AwsSsoManager };
