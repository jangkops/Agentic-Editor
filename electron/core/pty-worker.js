/**
 * PTY Worker — 별도 Node.js 프로세스에서 node-pty 실행.
 * Electron의 Node ABI 불일치 문제를 우회.
 * 
 * 부모 프로세스와 IPC로 통신:
 * - { type: 'create', id, shell, cwd } → PTY 생성
 * - { type: 'write', id, data } → PTY에 데이터 전송
 * - { type: 'kill', id } → PTY 종료
 * - { type: 'data', id, data } → PTY에서 데이터 수신 (부모로 전송)
 * - { type: 'exit', id, code } → PTY 종료 (부모로 전송)
 */
const pty = require('node-pty');
const terminals = new Map();

process.on('message', (msg) => {
  if (msg.type === 'create') {
    try {
      const shell = msg.shell || process.env.SHELL || '/bin/bash';
      const term = pty.spawn(shell, [], {
        name: 'xterm-256color',
        cols: msg.cols || 120,
        rows: msg.rows || 30,
        cwd: msg.cwd || process.env.HOME || process.cwd(),
        env: { ...process.env, TERM: 'xterm-256color' },
      });
      terminals.set(msg.id, term);
      term.onData((data) => {
        process.send({ type: 'data', id: msg.id, data });
      });
      term.onExit(({ exitCode }) => {
        terminals.delete(msg.id);
        process.send({ type: 'exit', id: msg.id, code: exitCode });
      });
      process.send({ type: 'created', id: msg.id, success: true });
    } catch (e) {
      process.send({ type: 'created', id: msg.id, success: false, error: e.message });
    }
  } else if (msg.type === 'write') {
    const term = terminals.get(msg.id);
    if (term) term.write(msg.data);
  } else if (msg.type === 'kill') {
    const term = terminals.get(msg.id);
    if (term) { term.kill(); terminals.delete(msg.id); }
  } else if (msg.type === 'resize') {
    const term = terminals.get(msg.id);
    if (term) term.resize(msg.cols, msg.rows);
  }
});

process.send({ type: 'ready' });
