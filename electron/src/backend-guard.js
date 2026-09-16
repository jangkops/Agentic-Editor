'use strict';
/**
 * 사이드카 포트(127.0.0.1:8765) 점유자 판정.
 *
 * 예전 main.js 는 8765 가 응답하면 "우리 백엔드가 이미 떠 있다"고 간주하고 시작을 건너뛰었다(HEAD /health).
 * 다른 프로세스가 그 포트를 쓰고 있어도 같은 판정이라 앱은 백엔드 없이 조용히 동작 불능이 됐다(원장 #29).
 * 이제 GET /health 본문의 `service == "ai-editor-engine"` 으로 세 가지를 구분한다:
 *   'ours'    — 우리 사이드카가 이미 실행 중 → 시작 생략
 *   'foreign' — 다른 프로세스가 포트 점유 → 시작 불가, 사용자에게 알림
 *   'down'    — 아무도 없음 → 사이드카 시작
 */
const http = require('http');

const SIDECAR_SERVICE = 'ai-editor-engine';

/** /health 응답(status, body)을 판정한다. */
function classifyHealth(statusCode, body) {
  if (statusCode !== 200) return 'foreign';
  try {
    const j = JSON.parse(String(body || ''));
    return j && j.service === SIDECAR_SERVICE ? 'ours' : 'foreign';
  } catch (_e) {
    return 'foreign';
  }
}

/**
 * 포트를 확인해 'ours' | 'foreign' | 'down' 을 resolve 한다(절대 reject 하지 않음).
 * @param {{host?: string, port?: number, timeoutMs?: number, get?: Function}} [opts] get 은 테스트 주입점(기본 http.get)
 */
function probeBackend(opts) {
  const o = opts || {};
  const host = o.host || '127.0.0.1';
  const port = o.port || 8765;
  const timeoutMs = o.timeoutMs || 2000;
  const get = typeof o.get === 'function' ? o.get : http.get;
  return new Promise((resolve) => {
    let done = false;
    const finish = (v) => { if (!done) { done = true; resolve(v); } };
    try {
      const req = get({ host, port, path: '/health', timeout: timeoutMs }, (res) => {
        let body = '';
        try { res.setEncoding('utf8'); } catch (_e) { /* fake res */ }
        res.on('data', (d) => { if (body.length < 4096) body += String(d); });
        res.on('end', () => finish(classifyHealth(res.statusCode, body)));
        res.on('error', () => finish('down'));
      });
      req.on('error', () => finish('down'));
      req.on('timeout', () => { try { req.destroy(); } catch (_e) { /* ignore */ } finish('down'); });
    } catch (_e) {
      finish('down');
    }
  });
}

module.exports = { SIDECAR_SERVICE, classifyHealth, probeBackend };
