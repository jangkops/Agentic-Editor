'use strict';
const MAX_DEPTH = 32;
class RequestQueue {
  constructor(maxDepth) {
    this._max = Number.isInteger(maxDepth) ? maxDepth : MAX_DEPTH;
    this._q = [];
    this._dropped = [];
  }
  get size() { return this._q.length; }
  enqueue(req) {
    if (!req || !req.id) throw new TypeError('req.id required');
    let dropped = null;
    if (this._q.length >= this._max) {
      dropped = this._q.shift();
      this._dropped.push({ id: dropped.id, droppedAt: Date.now() });
    }
    this._q.push({ id: req.id, method: req.method || 'POST', path: req.path || '/process', body: req.body || null, enqueuedAt: Date.now() });
    return { enqueued: true, dropped: dropped ? { id: dropped.id } : undefined };
  }
  dequeue() { return this._q.shift() || null; }
  drainAll() { const o = this._q.slice(); this._q.length = 0; return o; }
  peek() { return this._q[0] || null; }
  clear() { this._q.length = 0; }
  has(id) { return this._q.some(r => r.id === id); }
}
module.exports = { RequestQueue, MAX_DEPTH };
