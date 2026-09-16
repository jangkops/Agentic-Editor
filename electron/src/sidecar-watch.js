'use strict';
/**
 * SidecarWatcher — 사이드카 `/health` 를 주기적으로 찔러 **인스턴스 교체(재기동)** 를 감지한다.
 *
 * 왜 필요한가: 자격증명은 메인 프로세스가 `/api/reset-cache` 로 사이드카 메모리에 주입한다.
 * 사이드카가 재기동되면(크래시, `uvicorn --reload`, 원격 세션 전환 시 stop/start) 주입이 사라지는데,
 * 렌더러의 다음 호출을 기다리면 그동안 게이트웨이 호출이 프로파일 폴백으로 흘러간다.
 *
 * 감지 방법: 서버가 기동 때 만든 `boot_id`(uuid) 가 바뀌면 다른 인스턴스다. boot_id 를 주지 않는
 * 구버전 서버는 "응답 없음 → 응답" 전이(recovered)로만 판단한다.
 *
 * 이벤트
 *   'healthy' {base, bootId, recovered, changed}  정상 응답마다(매 폴링)
 *   'down'    {base}                               정상 → 실패로 바뀐 순간 1회
 *
 * 폴링 주기: 정상일 때 intervalMs(기본 5초), 죽어 있을 때 downIntervalMs(기본 1초) — 재기동 직후
 * 새 인스턴스가 살아나는 시점을 빨리 잡기 위해서다. 타이머는 unref 해 앱 종료를 막지 않는다.
 */
const { EventEmitter } = require('events');

class SidecarWatcher extends EventEmitter {
  constructor(opts) {
    super();
    const o = opts || {};
    this._resolveApiBase = typeof o.resolveApiBase === 'function' ? o.resolveApiBase : () => 'http://127.0.0.1:8765';
    this._fetch = typeof o.fetchImpl === 'function' ? o.fetchImpl : (typeof fetch === 'function' ? fetch : null);
    this.intervalMs = o.intervalMs > 0 ? o.intervalMs : 5000;
    this.downIntervalMs = o.downIntervalMs > 0 ? o.downIntervalMs : 1000;
    this.timeoutMs = o.timeoutMs > 0 ? o.timeoutMs : 2000;
    /** @type {boolean|null} null = 아직 확인 안 함 */
    this.up = null;
    /** @type {string|null} 마지막 정상 응답의 boot_id */
    this.lastBootId = null;
    this._timer = null;
    this._running = false;
  }

  start() {
    if (this._running) return;
    this._running = true;
    this._schedule(0);
  }

  stop() {
    this._running = false;
    if (this._timer) { clearTimeout(this._timer); this._timer = null; }
  }

  /** 한 번 확인한다. @returns {Promise<{up: boolean, bootId: string|null}>} */
  async probe() {
    const base = this._resolveApiBase();
    let ok = false;
    let bootId = null;
    try {
      if (!this._fetch) throw new Error('fetch unavailable');
      const res = await this._fetch(`${base}/health`, { signal: AbortSignal.timeout(this.timeoutMs) });
      ok = !!(res && res.ok);
      if (ok) {
        try {
          const j = await res.json();
          if (j && typeof j.boot_id === 'string' && j.boot_id) bootId = j.boot_id;
        } catch (_e) { /* body 없음/JSON 아님 — boot_id 미지원 서버 */ }
      }
    } catch (_e) {
      ok = false;
    }
    if (!ok) {
      const wasUp = this.up === true;
      this.up = false;
      if (wasUp) this.emit('down', { base });
      return { up: false, bootId: null };
    }
    const recovered = this.up === false;
    const changed = !!(this.lastBootId && bootId && this.lastBootId !== bootId);
    this.up = true;
    this.lastBootId = bootId;
    this.emit('healthy', { base, bootId, recovered, changed });
    return { up: true, bootId };
  }

  _schedule(ms) {
    this._timer = setTimeout(async () => {
      this._timer = null;
      try { await this.probe(); } catch (_e) { /* probe 는 스스로 예외를 삼킨다 */ }
      if (this._running) this._schedule(this.up ? this.intervalMs : this.downIntervalMs);
    }, ms);
    if (this._timer && typeof this._timer.unref === 'function') this._timer.unref();
  }
}

module.exports = { SidecarWatcher };
