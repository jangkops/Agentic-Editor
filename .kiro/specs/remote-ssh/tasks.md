# Implementation Plan — Remote SSH

## Overview

이 구현 계획은 `design.md` 를 13개 Requirement 와 25개 Correctness Property 로 추적 가능한 작업 단위로 분해한다.

**구현 언어**:
- Electron main/renderer 모듈: **JavaScript (CommonJS, ESM 금지)** — `electron/src/remote/`, `src/components/`
- 원격 ai_engine dedup 미들웨어: **Python 3.11+** — `ai_engine/server.py`
- PBT: `fast-check` (JS) + `hypothesis` (Python)

**MVP 경계**:
- v1 **필수** (별표 없음): 단일 호스트 연결, 키 인증, 파일/터미널/API 동작, 자동 프로비저닝, passphrase, ProxyJump, 재연결 복구
- v1 **고급/선택 (`*` 표시)**: 다중 호스트 전환, 2FA, 상세 진단 UI, 성능 튜닝, 일부 통합 테스트

**빌드 순서 원칙**: bottom-up (유틸 → 코어 → IPC 통합 → UI → E2E). 각 phase 끝에 checkpoint.

---

## Tasks

### Phase 1 — 기반 유틸리티 모듈

- [x] 1. SSH Config parser/printer 기반 구현
  - [x] 1.1 HostEntry 타입과 parser 스켈레톤 구현
    - 파일: `electron/src/remote/ssh-config-parser.js`
    - `parse(text, {basePath, env})`, `print(entries)`, `loadFromDisk({env, home})`, `resolveIncludes(text, basePath, depth)` export
    - 지원 지시어: `HostName`, `User`, `Port`, `IdentityFile`, `ProxyJump`, `ProxyCommand`, `ForwardAgent`, `StrictHostKeyChecking`, `UserKnownHostsFile`, `IdentitiesOnly`, `PreferredAuthentications`
    - `~` 확장, case-insensitive 키 처리, 유효하지 않은 라인은 diagnostic 기록 후 skip (throw 금지)
    - _Requirements: 1.1, 1.2, 1.3, 1.6, 1.7_

  - [x] 1.2 Include 재귀 해석 및 깊이 제한
    - 파일: `electron/src/remote/ssh-config-parser.js`
    - 깊이 16 초과 차단, cycle 감지 시 diagnostic 기록
    - 누락 파일은 warn diagnostic, 파싱 계속
    - _Requirements: 1.2, 1.7_

  - [ ]* 1.3 Property test — SSH Config parse/print round-trip
    - 파일: `tests/unit/remote/ssh-config-parser.property.test.js`
    - **Property 1: SSH Config parse/print round-trip**
    - **Validates: Requirements 1.3, 1.4, 1.5, 1.7, 11.6**
    - 검증: `fast-check` 로 임의의 유효 HostEntry 리스트 생성 → `print → parse` round-trip, wildcard-only 항목은 제거 후 비교

  - [ ]* 1.4 Property test — Include 재귀 불변량
    - 파일: `tests/unit/remote/ssh-config-parser.property.test.js`
    - **Property 2: SSH Config Include 재귀 불변량**
    - **Validates: Requirements 1.2, 1.7**
    - 검증: 깊이/사이클/누락 조합 fuzzing, depth>16 미확장·cycle diagnostic·sourcePaths 포함 확인

  - [ ]* 1.5 Unit tests — parser 예제 케이스
    - 파일: `tests/unit/remote/ssh-config-parser.test.js`
    - 구체 케이스: 경로 결정(env/OS), 500 blocks ≤300ms 성능, 빈 파일, 잘못된 라인
    - _Requirements: 1.1, 1.6, 1.8_

- [x] 2. Credential Cache (메모리 전용) 구현
  - [x] 2.1 CredentialCache 클래스 구현
    - 파일: `electron/src/remote/credential-cache.js`
    - `get(alias)`, `set(alias, credential)`, `clear(alias?)` — in-process `Map` 기반
    - `toJSON` 미구현 (구조화 복제 차단), `credential` 는 `{passphrase?, privateKey?, twoFactor?}` 객체
    - process exit / logout / 명시 clear 에서 전체 wipe
    - _Requirements: 10.1, 10.2_

  - [ ]* 2.2 Property test — Credential security invariants
    - 파일: `tests/unit/remote/credential-security.property.test.js`
    - **Property 18: Credential security invariants**
    - **Validates: Requirements 10.1, 10.2, 10.4, 10.5, 10.7**
    - 검증: 임의 입력 시퀀스 후 `userData/` 디스크 write 0회, clear 이후 get=null, ad-hoc JSON 직렬화에 키/패스프레이즈 없음, `PasswordAuthentication=no` 시 prompt 호출 0회

- [x] 3. Log masking helper 구현
  - [x] 3.1 mask() 헬퍼와 로거 진입점
    - 파일: `electron/src/remote/logger.js`
    - `mask(s)` → `s[0] + '****'` (|s| ≥ 2)
    - newline-delimited JSON writer (`userData/logs/remote-ssh.log`)
    - 모든 log sink (console/file/IPC event) 가 이 모듈을 통과하도록 단일 진입점 설계
    - _Requirements: 10.3, 12.2, 12.5_

  - [ ]* 3.2 Property test — Log masking
    - 파일: `tests/unit/remote/log-masking.property.test.js`
    - **Property 19: Log masking**
    - **Validates: Requirements 10.3, 12.5**

- [x] 4. Host Key Store (TOFU) 구현
  - [x] 4.1 HostKeyStore 클래스 및 known_hosts I/O
    - 파일: `electron/src/remote/host-key-store.js`
    - `get(host, port)`, `add(host, port, keyType, keyBase64)`, `verify(host, port, key)` → `{status: 'ok'|'unknown'|'mismatch', fingerprint}`
    - 파일: `app.getPath('userData')/ssh/known_hosts`
    - Unix: 생성 시 `fs.chmod(0o600)`; Windows: ACL 현재 사용자 only
    - SHA256 base64 fingerprint 계산
    - _Requirements: 3.6, 3.7, 10.6, 11.3_

  - [ ]* 4.2 Unit tests — key store 파일 권한 및 TOFU 시나리오
    - 파일: `tests/unit/remote/key-store.test.js`
    - 케이스: 최초 prompt, 불일치 abort, 파일 권한 0600 검증 (Unix)
    - _Requirements: 10.6, 11.3_

- [x] 5. Port Allocator 구현
  - [x] 5.1 allocatePort() 및 범위 스캔
    - 파일: `electron/src/remote/port-allocator.js`
    - 범위 `[18765, 18865]` 스캔, 첫 사용 가능 포트 반환
    - 모두 사용 중이면 `PortExhaustedError` throw
    - `net.createServer().listen(0)` 기반 free-port 검사 (EADDRINUSE 감지)
    - _Requirements: 5.2_

  - [ ]* 5.2 Property test — Port allocator
    - 파일: `tests/unit/remote/port-allocator.property.test.js`
    - **Property 9: Port allocator**
    - **Validates: Requirements 5.2**
    - 검증: 임의 `B ⊆ [18765, 18865]` 가상 점유 집합 → `min([18765, 18865] \ B)` 반환 또는 exhausted

- [x] 6. Windows path normalization 유틸
  - [x] 6.1 normalizeForSshConfigLookup() 구현
    - 파일: `electron/src/remote/path-normalization.js`
    - drive letter 보존, `\` → `/`, 중복 slash 축소
    - _Requirements: 11.2, 11.6_

  - [ ]* 6.2 Property test — Windows path normalization
    - 파일: `tests/unit/remote/path-normalization.property.test.js`
    - **Property 20: Windows path normalization**
    - **Validates: Requirements 11.2**

- [x] 7. Checkpoint — Phase 1 완료 확인
  - Ensure all tests pass, ask the user if questions arise.

---

### Phase 2 — 원격 연결 (ssh2 래퍼 · 상태 머신 · 정책)

- [x] 8. ssh2 Client connect config builder (ProxyJump 체인 포함)
  - [x] 8.1 HostEntry → ssh2 Client config 변환
    - 파일: `electron/src/remote/ssh-client-builder.js`
    - `buildConnectConfig(target, hops, credentialCache, hostKeyStore)` 반환
    - publickey / keyboard-interactive / password 시도 순서 구성 (`PreferredAuthentications` override)
    - `IdentityFile` 로드, 패스프레이즈 필요 시 `credentialCache` 에서 조회, 미스면 prompt 이벤트 발행
    - `ForwardAgent` + `SSH_AUTH_SOCK` 존재 시 agent 위임
    - ProxyJump: nested hop config 체인 (최대 길이 3)
    - OpenSSH (PEM/new), RSA, ECDSA, Ed25519 포맷 지원 — Ed25519 passphrase 는 `sshpk` fallback
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 10.7, 11.1, 11.3_

  - [ ]* 8.2 Property test — ProxyJump 체인 config builder
    - 파일: `tests/unit/remote/ssh-client-builder.property.test.js`
    - **Property 5: ProxyJump 체인 config builder**
    - **Validates: Requirements 3.5**

- [x] 9. Authentication failure window detector (StopPolicy)
  - [x] 9.1 shouldStop() 정책 구현
    - 파일: `electron/src/remote/auth-policy.js`
    - 최근 60초 내 실패 ≥3 시 true
    - 타임스탬프 시퀀스 관리 (중복·비정렬 허용)
    - _Requirements: 3.8_

  - [x] 9.2 effectiveStrictHostKeyChecking() 정책 구현
    - 파일: `electron/src/remote/auth-policy.js`
    - HostEntry 명시 값 우선, 미지정 시 `alias ∈ KeyStore ? 'yes' : 'ask'`
    - _Requirements: 13.1_

  - [ ]* 9.3 Property tests — auth-policy
    - 파일: `tests/unit/remote/auth-policy.property.test.js`
    - **Property 6: Authentication failure window detector** — _Validates: Requirements 3.8_
    - **Property 23: StrictHostKeyChecking default policy** — _Validates: Requirements 13.1_

- [x] 10. RemoteSession 클래스와 상태 머신
  - [x] 10.1 RemoteSession 스켈레톤 + 전이 테이블
    - 파일: `electron/src/remote/remote-session.js`
    - state: `disconnected|connecting|authenticating|provisioning|forwarding|connected|reconnecting|failed`
    - 허용 전이 집합을 상수로 정의 (design.md 의 state machine 다이어그램 그대로)
    - 모든 전이 시 `state` 이벤트 발행 (`{from, to, reason}`)
    - _Requirements: 3.1–3.10, 12.2_

  - [x] 10.2 connect() 흐름 wiring (banner → auth → host key → provision hook)
    - 파일: `electron/src/remote/remote-session.js`
    - ssh2 Client 인스턴스화, `buildConnectConfig` 적용
    - host key 검증: mismatch 시 abort + security-event 로그
    - auth failed → StopPolicy 반영
    - 10초 handshake 목표 (측정은 integration)
    - _Requirements: 3.1, 3.6, 3.7, 3.8, 3.10_

  - [ ]* 10.3 Property test — State machine transition validity
    - 파일: `tests/unit/remote/state-machine.property.test.js`
    - **Property 25: State machine transition validity**
    - **Validates: Requirements §Architecture (state machine consistency)**

  - [ ]* 10.4 Property test — State transition logging parity
    - 파일: `tests/unit/remote/state-log-parity.property.test.js`
    - **Property 21: State transition logging parity**
    - **Validates: Requirements 12.2**

- [x] 11. Keepalive 정책과 백오프
  - [x] 11.1 Keepalive 전송 타이머 및 shouldReconnect()
    - 파일: `electron/src/remote/keepalive-policy.js`
    - ssh2 keepalive ≤30s 간격, 연속 3회 실패 시 reconnect 트리거
    - 결과 시퀀스 카운터는 ok 수신 시 리셋
    - _Requirements: 8.1, 8.2_

  - [x] 11.2 backoffMs(n) 지수 백오프 with cap
    - 파일: `electron/src/remote/backoff.js`
    - `min(2000 * 2^n, 30000)`
    - _Requirements: 8.3, 8.6_

  - [ ]* 11.3 Property test — Keepalive failure detector
    - 파일: `tests/unit/remote/keepalive-policy.property.test.js`
    - **Property 13: Keepalive failure detector**
    - **Validates: Requirements 8.1, 8.2**

  - [ ]* 11.4 Property test — Exponential backoff with cap
    - 파일: `tests/unit/remote/backoff.property.test.js`
    - **Property 14: Exponential backoff with cap**
    - **Validates: Requirements 8.3, 8.6**

- [x] 12. RemoteSessionManager (다중 세션 + 활성 전환)
  - [x] 12.1 Manager 구현 — 최대 4 세션, 활성 1
    - 파일: `electron/src/remote/remote-session-manager.js`
    - `connect(alias)`, `disconnect(alias)` (→ `credentialCache.clear(alias)`), `switchActive(alias)`, `getActive()`, `all()`
    - 비활성 세션의 file watcher 일시정지 훅
    - _Requirements: 9.1, 9.2, 9.4, 9.5, 10.2_

  - [ ]* 12.2 Property test — Session set invariants
    - 파일: `tests/unit/remote/session-manager.property.test.js`
    - **Property 17: Session set invariants**
    - **Validates: Requirements 9.1, 9.2, 9.4**

  - [ ]* 12.3 Unit tests — session-manager 예제
    - 파일: `tests/unit/remote/session-manager.test.js`
    - 케이스: disconnect 시 credential clear, 5번째 connect 거부, local 라우팅 복원
    - _Requirements: 5.4, 5.5, 9.5_

- [x] 13. Checkpoint — Phase 2 완료 확인
  - Ensure all tests pass, ask the user if questions arise.

---

### Phase 3 — 원격 브리지 (파일 · 터미널 · 포트 포워딩)

- [ ] 14. Port Forwarder 구현
  - [ ] 14.1 로컬 `net.createServer` → `session.forwardOut`
    - 파일: `electron/src/remote/port-forwarder.js`
    - `allocatePort()` 로 Local_Forwarded_Port 획득 (재연결 시 동일 포트 유지)
    - 원격 127.0.0.1:8765 로 직통 포워딩
    - `close()` 시 keep-alive 소켓 정리
    - _Requirements: 5.1, 5.2, 5.6_

  - [ ]* 14.2 Unit tests — port-forwarder
    - 파일: `tests/unit/remote/port-forwarder.test.js`
    - 케이스: 포워드 open → /health 200, 종료 시 바인드 해제
    - _Requirements: 5.1_

- [ ] 15. Remote File Bridge (SFTP 기반)
  - [ ] 15.1 SFTP wrapper + 기본 ops (list/read/stat/mkdir/rename)
    - 파일: `electron/src/remote/remote-file-bridge.js`
    - `list`, `read(encoding)`, `readStream`, `stat`, `mkdir({recursive})`, `rename`
    - path separator 헬퍼 `pathSep()` — `uname -s` 결과 기반 (Unix `/`, Windows `\`)
    - 16 MB 초과 시 `LargeFileError` 반환
    - _Requirements: 6.1, 6.2, 6.4, 6.5, 11.6_

  - [ ] 15.2 Atomic write (temp → fsync → rename)
    - 파일: `electron/src/remote/remote-file-bridge.js`
    - `write(remotePath, content)` — `remotePath + '.ae-tmp-' + randHex(6)` 로 쓰기 후 fsync(OpenSSH ext, 미지원 스킵) → rename
    - 실패 시 temp cleanup 보장
    - 에러 코드 (permission/disk-full/io) 를 renderer 로 그대로 전달
    - _Requirements: 6.3, 6.5, 6.6_

  - [ ] 15.3 SFTP polling watcher
    - 파일: `electron/src/remote/remote-file-bridge.js`
    - 활성 디렉터리 500ms, 비활성 2s 폴링 (스냅샷 `(path, mtime, size)` 비교)
    - `remote:event:fs-change` 이벤트 송신 (`created|modified|deleted`)
    - _Requirements: 6.7_

  - [ ]* 15.4 Property test — Remote file atomic write + rollback + round-trip
    - 파일: `tests/unit/remote/remote-file-bridge.property.test.js`
    - **Property 12: Remote file atomic write + rollback + round-trip**
    - **Validates: Requirements 6.3, 6.5, 6.6, 11.6**
    - 실패 주입: `{permission, diskFull, io, rename, fsync}` 부분집합. temp 잔류 없음, 실패 시 원본 해시 불변, 성공 시 round-trip.

- [ ] 16. Remote Terminal Bridge (ssh2 shell + PTY)
  - [ ] 16.1 shell stream create/write/resize/kill
    - 파일: `electron/src/remote/remote-terminal-bridge.js`
    - `create(id, {cols, rows, cwd, shell})` → `ssh2 shell({term: 'xterm-256color', cols, rows})`
    - Remote OS 기반 `shell` 기본값 결정 (Unix `bash`, Windows OpenSSH pwsh)
    - resize → `stream.setWindow(rows, cols)`
    - UTF-8 인코딩 보존
    - _Requirements: 7.1, 7.2, 7.3, 11.4, 11.6_

  - [ ] 16.2 Disconnect 시 스크롤백 보존 + reattach 스텁
    - 파일: `electron/src/remote/remote-terminal-bridge.js`
    - 세션 disconnect → 해당 id `disconnected` 이벤트 발행, xterm 버퍼는 renderer 유지
    - v1: 재부착 시도는 "새 shell + 안내" (실제 PTY 복구는 v2)
    - _Requirements: 7.4, 8.5_

  - [ ] 16.3 stdout 백프레셔 완화 (8KB chunk + throttle)
    - 파일: `electron/src/remote/remote-terminal-bridge.js`
    - 대량 출력 시 8KB chunk 버퍼 + 1ms throttle
    - _Requirements: 7.2_

  - [ ]* 16.4 Unit tests — remote-terminal-bridge
    - 파일: `tests/unit/remote/remote-terminal-bridge.test.js`
    - 케이스: disconnect 이벤트, local-terminal override, UTF-8 왕복
    - _Requirements: 7.4, 7.5, 8.4, 8.5_

- [ ] 17. Checkpoint — Phase 3 완료 확인
  - Ensure all tests pass, ask the user if questions arise.

---

### Phase 4 — ai_engine 프로비저닝

- [ ] 18. Provisioner — probe / python check / upload / supervisor
  - [ ] 18.1 Probe & Python 호환성 검사
    - 파일: `electron/src/remote/provisioner.js`
    - `probe()` — `curl 127.0.0.1:<port>/health` 을 SSH exec 로 실행 (3s timeout)
    - `isPythonCompatible(ver)` — semver 기준 major ≥3, minor ≥11
    - 호환 불가 시 `PythonUnsupportedError` + remediation hint
    - _Requirements: 4.1, 4.2, 4.6_

  - [ ] 18.2 ai_engine 트리 content hash + 업로드 스킵 로직
    - 파일: `electron/src/remote/provisioner.js`
    - `~/.agentic-editor/version` 읽어 `aiEngineContentHash` 비교
    - 해시 일치 → 업로드 스킵, 불일치 → SFTP 업로드 (mtime/mode 보존)
    - 업로드 후 version 매니페스트 갱신
    - _Requirements: 4.3, 4.9_

  - [ ] 18.3 venv 생성 + pip install
    - 파일: `electron/src/remote/provisioner.js`
    - `python3 -m venv ~/.agentic-editor/venv`
    - `pip install -r ~/.agentic-editor/ai_engine/requirements.txt --no-input`
    - `uname -s` 기반 경로 분기 (Linux/macOS vs Windows `%USERPROFILE%`)
    - _Requirements: 4.4, 11.5_

  - [ ] 18.4 Supervisor 스크립트 배포 + 기동
    - 파일: `electron/src/remote/provisioner.js` + 리소스 `electron/src/remote/resources/supervisor.sh`
    - `supervisor.sh` 템플릿 (while loop + sleep 2)
    - PID 파일: `~/.agentic-editor/supervisor.pid`, `~/.agentic-editor/server.pid`
    - 기존 PID 살아있고 /health 200 → 재사용
    - _Requirements: 4.5, 4.8_

  - [ ] 18.5 Manual provisioning mode
    - 파일: `electron/src/remote/provisioner.js`
    - `provisioningMode: 'manual'` 일 때 upload/install 스킵, `/health` 만 확인
    - _Requirements: 4.7_

  - [ ] 18.6 Port occupied by other service 감지
    - 파일: `electron/src/remote/provisioner.js`
    - `/health` 응답 본문에 `{"service":"ai-editor-engine"}` 없으면 `PortOccupiedByOtherService` → `failed`
    - ai_engine `/health` 응답에 `service` 필드 추가 (아래 별도 태스크)
    - _Requirements: 5.2, 12.4_

  - [ ]* 18.7 Property test — ai_engine upload tree equivalence + hash skip
    - 파일: `tests/unit/remote/provisioner.property.test.js`
    - **Property 7: ai_engine upload tree equivalence and content-hash skip**
    - **Validates: Requirements 4.3, 4.9**

  - [ ]* 18.8 Property test — Python version compatibility
    - 파일: `tests/unit/remote/provisioner.property.test.js`
    - **Property 8: Python version compatibility judgement**
    - **Validates: Requirements 4.6**

  - [ ]* 18.9 Unit tests — provisioner 예제 시나리오
    - 파일: `tests/unit/remote/provisioner.test.js`
    - 케이스: /health 200 시 업로드 스킵, 수동 모드, 파이썬 없음 에러 메시지
    - _Requirements: 4.1, 4.2, 4.4, 4.7_

- [ ] 19. ai_engine /health 응답에 service 식별자 추가
  - 파일: `ai_engine/server.py`
  - `/health` 응답에 `{"service": "ai-editor-engine", "version": "<pkg version>"}` 포함
  - 기존 로컬 동작 하위호환 유지
  - _Requirements: 5.2 (판별), 12.4_

- [ ] 20. Checkpoint — Phase 4 완료 확인
  - Ensure all tests pass, ask the user if questions arise.

---

### Phase 5 — IPC 라우팅 통합

- [ ] 21. Session Router + apiBase 헬퍼
  - [ ] 21.1 SessionRouter 구현
    - 파일: `electron/src/remote/session-router.js`
    - `getActive()` / `isRemoteActive()` / `dispatch(opName, args)` — active.state=='connected' && !forceLocal 시 remote bridge, 아니면 로컬
    - `exec(cmd, {cwd})` → remote 시 SSH exec `{stdout, stderr, code}`, 로컬 시 `execSync` wrap (shape 통일)
    - _Requirements: 5.3, 5.4, 6.1, 7.1, 7.5_

  - [ ] 21.2 apiBase() 헬퍼
    - 파일: `electron/src/remote/session-router.js` + `src/lib/utils.js` (renderer 측)
    - connected 이면 `http://127.0.0.1:<localPort>`, 아니면 `http://localhost:8765`
    - _Requirements: 5.3, 5.5_

  - [ ]* 21.3 Property test — apiBase 라우팅 결정
    - 파일: `tests/unit/remote/api-router.property.test.js`
    - **Property 10: apiBase 라우팅 결정**
    - **Validates: Requirements 5.3, 5.5**

  - [ ]* 21.4 Property test — IPC 라우팅 결정
    - 파일: `tests/unit/remote/api-router.property.test.js`
    - **Property 11: IPC 라우팅 결정 (fs/terminal)**
    - **Validates: Requirements 5.4, 6.1, 7.1, 7.5**

- [ ] 22. 기존 IPC 핸들러에 Router 분기 삽입
  - [ ] 22.1 ipc-fs-handlers 원격 라우팅
    - 파일: `electron/src/ipc-fs-handlers.js`
    - 각 핸들러 선두에 `const active = sessionRouter.getActive(); if (active?.isRemote) return bridge.remoteFs[op](...)`
    - path 해석은 Remote 시 `hostEntry.remoteWorkspace` 기준
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [ ] 22.2 ipc-terminal-handlers 원격 라우팅
    - 파일: `electron/src/ipc-terminal-handlers.js`
    - `terminal:create` 에서 active remote && !forceLocal → `RemoteTerminalBridge.create`
    - `data`/`exit` 이벤트 송신 경로 공용 (`terminal:data`, `terminal:exit`)
    - _Requirements: 7.1, 7.5_

  - [ ] 22.3 ipc-git-handlers 원격 라우팅
    - 파일: `electron/src/ipc-git-handlers.js`
    - `execSync(cmd, {cwd})` 호출을 `sessionRouter.exec(cmd, {cwd})` 로 교체
    - 반환 shape: `{stdout, stderr, code}`
    - _Requirements: 6.1_

  - [ ] 22.4 ipc-project-handlers 원격 라우팅
    - 파일: `electron/src/ipc-project-handlers.js`
    - 프로젝트 스캔/파일 조작을 Router 경유로 전환
    - _Requirements: 6.1_

- [ ] 23. src/main.js apiBase 치환 및 라이프사이클 통합
  - [ ] 23.1 fetch URL 을 apiBase() 기반으로 교체
    - 파일: `src/main.js`
    - 모든 `fetch('http://localhost:8765/...')` → `fetch(\`${apiBase()}/...\`)`
    - apiBase 는 `electronAPI.remoteStatus()` 캐시 상태 조회
    - _Requirements: 5.3, 5.5_

  - [ ] 23.2 electron/main.js — Remote Session Manager 부트스트랩 및 ProcessManager 연동
    - 파일: `electron/main.js`
    - `app.whenReady` 에서 `dataStore.loadRemoteHosts()` → `RemoteSessionManager` 인스턴스화
    - 세션 connected 진입 시 `ProcessManager.stopPython()`, 비활성화 시 `startPython()` 재기동
    - IPC 채널 등록: `remote:list-hosts`, `remote:connect`, `remote:disconnect`, `remote:switch-active`, `remote:status`, `remote:respond-auth`, `remote:set-workspace`, `remote:clear-credentials`, `remote:show-log`, `remote:add-ad-hoc-host`, `remote:set-favorite`
    - 모든 핸들러는 **electron/main.js 에만** 등록 (security.md 준수)
    - _Requirements: 5.4, 5.5, 9.5_

  - [ ] 23.3 preload.js 에 electronAPI remote.* 메서드 노출
    - 파일: `electron/preload.js`
    - `contextBridge.exposeInMainWorld` 로 whitelisted 메서드만 (design.md 의 preload 섹션 그대로)
    - 이벤트 구독: `onRemoteState`, `onRemoteAuthRequest`, `onRemoteHostKeyPrompt`, `onRemoteFsChange`
    - _Requirements: 10.4_

- [ ] 24. Checkpoint — Phase 5 완료 확인
  - Ensure all tests pass, ask the user if questions arise.

---

### Phase 6 — UI 컴포넌트 (Web Components)

- [ ] 25. Remote Host Picker Web Component
  - [ ] 25.1 `<remote-host-picker>` 구현
    - 파일: `src/components/remote-host-picker.js`
    - `customElements.define`, no shadow DOM, single file
    - `electronAPI.remoteListHosts()` 로 목록 조회, favorite 섹션 먼저 + 알파벳 정렬
    - 각 엔트리에 alias, resolved hostName, user, state 배지
    - 커맨드 팔레트 진입점 ("Remote: Connect to Host")
    - design token(`variables.css`) 기반 스타일
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6_

  - [ ] 25.2 Ad-hoc host 추가 플로우
    - 파일: `src/components/remote-host-picker.js`
    - 빈 상태 CTA 및 모달 폼 (alias/hostName/user/port/identityFile)
    - `remoteAddAdHocHost()` 호출 → SSH_Config 파일은 변경하지 않음
    - _Requirements: 2.5, 10.5_

  - [ ]* 25.3 Property test — Host picker render contract
    - 파일: `tests/unit/remote/host-picker.property.test.js`
    - **Property 3: Host picker render contract**
    - **Validates: Requirements 2.2, 2.3, 2.6**

  - [ ]* 25.4 Property test — Ad-hoc host addition does not mutate SSH_Config
    - 파일: `tests/unit/remote/remote-hosts-store.property.test.js`
    - **Property 4: Ad-hoc host addition does not mutate SSH_Config**
    - **Validates: Requirements 2.5, 10.5**

  - [ ]* 25.5 Playwright E2E — host picker
    - 파일: `tests/e2e/remote_picker.py`
    - 커맨드 팔레트 오픈 → 호스트 선택 → Status_Bar 상태 변화 확인
    - _Requirements: 2.1, 2.4_

- [ ] 26. Remote Status Bar Web Component
  - [ ] 26.1 `<remote-status-bar>` 구현
    - 파일: `src/components/remote-status-bar.js`
    - `data-state` 속성 기반 CSS 스타일 (connecting=warning, connected=success, reconnecting=warning+pulse, failed=error)
    - `onRemoteState` 이벤트 구독하여 active alias + state 렌더
    - `src/main.js` 상단 바에 마운트
    - _Requirements: 2.7, 12.1_

  - [ ]* 26.2 Unit tests — status bar render
    - 파일: `tests/unit/remote/status-bar.test.js`
    - 케이스: 각 state 별 data 속성과 design token 적용 확인
    - _Requirements: 12.1, 12.3_

- [ ] 27. Auth & Host Key 다이얼로그
  - [ ] 27.1 `<remote-auth-dialog>` 구현 (passphrase / password / 2FA)
    - 파일: `src/components/remote-auth-dialog.js`
    - `onRemoteAuthRequest` 수신 시 kind 에 맞는 입력 필드 렌더
    - 2FA 의 경우 서버 prompt 를 verbatim 표시, echo 플래그 반영
    - 입력값은 `remoteRespondAuth` 즉시 호출 후 DOM 제거 (renderer 메모리 잔류 없음)
    - _Requirements: 3.3, 3.9, 10.1, 10.4_

  - [ ] 27.2 `<remote-host-key-dialog>` 구현 (TOFU confirmation)
    - 파일: `src/components/remote-host-key-dialog.js`
    - SHA256 fingerprint 그룹핑 표시, Accept/Reject 버튼
    - Accept → `remoteRespondAuth` kind=`host-key` payload=`{accept: true}`
    - _Requirements: 3.6, 3.7_

  - [ ]* 27.3 Playwright E2E — auth dialog interaction
    - 파일: `tests/e2e/remote_auth_dialog.py`
    - mock SSH 서버에서 passphrase prompt → 다이얼로그 입력 → 연결 성공
    - _Requirements: 3.3, 3.9_

- [ ] 28. 에러 메시지 / remediation hint / Show Remote Log
  - [ ] 28.1 surfaceError() 공통 빌더
    - 파일: `electron/src/remote/error-surface.js`
    - `{code, category, alias, state, cause, remediationHint}` 정규화
    - category 별 기본 remediationHint 사전
    - _Requirements: 12.4_

  - [ ] 28.2 "Show Remote Log" 커맨드
    - 파일: `electron/main.js` + `src/main.js`
    - `remote:show-log` → read-only editor tab 에서 `userData/logs/remote-ssh.log` 오픈
    - _Requirements: 12.3_

  - [ ]* 28.3 Property test — User-facing error message completeness
    - 파일: `tests/unit/remote/error-surface.property.test.js`
    - **Property 22: User-facing error message completeness**
    - **Validates: Requirements 12.4**

- [ ] 29. Remote hosts preferences 영속화
  - [ ] 29.1 remote-hosts.json CRUD
    - 파일: `electron/core/data-store.js` (확장) + `electron/src/remote/remote-hosts-store.js`
    - `userData/settings/remote-hosts.json` schema: `{schemaVersion, hosts: {alias: {favorite, lastWorkspace, remotePortOverride, provisioningMode, source, adHoc?}}}`
    - `loadHosts()`, `saveHosts(prefs)`, `setFavorite(alias, bool)`, `setWorkspace(alias, path)`, `addAdHoc(...)`
    - ad-hoc 저장 시 identityFile 경로만, 키 내용/패스프레이즈 절대 금지
    - _Requirements: 2.5, 10.5, 13.3_

  - [ ]* 29.2 Property test — Per-host preference persistence round-trip
    - 파일: `tests/unit/remote/remote-hosts-store.property.test.js`
    - **Property 24: Per-host preference persistence round-trip**
    - **Validates: Requirements 13.3**

- [ ] 30. Checkpoint — Phase 6 완료 확인
  - Ensure all tests pass, ask the user if questions arise.

---

### Phase 7 — 연결 복구 및 요청 멱등성

- [ ] 31. Request Queue + ReconnectLoop
  - [ ] 31.1 Request queue (depth 32, FIFO) 구현
    - 파일: `electron/src/remote/request-queue.js`
    - `enqueue(req)` — `|Q|==32` 시 oldest drop + renderer 경고
    - `drain(onSend)` — connected 복귀 시 FIFO replay
    - 큐 대상: `/process`, `/streamprocess` POST
    - _Requirements: 8.7_

  - [ ] 31.2 ReconnectLoop (backoff + 5분 타임아웃)
    - 파일: `electron/src/remote/reconnect-loop.js`
    - `backoffMs(n)` (Property 14) 사용, 포워드 포트 동일 번호 유지
    - 5분 초과 시 `failed` 전이
    - 성공 시 `connected` 복귀 + queue.drain(renderer 에 replay)
    - 터미널은 `disconnected` 표시 + reattach 제안 (v1)
    - _Requirements: 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ]* 31.3 Property test — Replay queue invariants
    - 파일: `tests/unit/remote/request-queue.property.test.js`
    - **Property 15: Replay queue invariants**
    - **Validates: Requirements 8.7**

- [ ] 32. ai_engine dedup middleware (requestid 멱등성)
  - [ ] 32.1 dedup_requestid middleware 추가
    - 파일: `ai_engine/server.py`
    - `@app.middleware("http")` 로 `/process`, `/streamprocess` POST 에 한해 `requestid` 기반 LRU 캐시 (size 512)
    - `/process`: 중복 rid → 캐시된 응답 반환
    - `/streamprocess`: 캐시 금지, "이미 진행 중" 단락 처리
    - 기존 로컬 모드 하위호환
    - _Requirements: 8.8_

  - [ ]* 32.2 Property test — Request idempotency by requestid (Python)
    - 파일: `tests/unit/remote/test_dedup_middleware.py`
    - **Property 16: Request idempotency by requestid**
    - **Validates: Requirements 8.8**
    - `hypothesis` 로 임의 `(rid, body)` 시퀀스 생성, upstream mock 의 요청 수 = unique first-success rid 수 검증

- [ ] 33. Checkpoint — Phase 7 완료 확인
  - Ensure all tests pass, ask the user if questions arise.

---

### Phase 8 — 통합 테스트 환경 및 문서

- [ ] 34. Docker Compose 통합 테스트 환경 구성
  - 파일: `tests/integration/remote/docker-compose.yml` + `tests/integration/remote/keys/` + `tests/integration/remote/setup.sh`
  - sshd + sshd-bastion 컨테이너 (linuxserver/openssh-server)
  - 테스트용 Ed25519 키 쌍 생성 스크립트
  - `tc qdisc ... netem delay 50ms` 설정 훅 (CI 가능 환경에서만)
  - _Requirements: 3.10, 4.5, 4.8, 5.6, 6.2, 6.7, 7.2, 7.3, 8.*, 9.3_

- [ ] 35.* SSH handshake 성능 통합 테스트
  - 파일: `tests/integration/remote/ssh-handshake.integration.test.js`
  - 50ms RTT 환경에서 handshake ≤10s
  - _Requirements: 3.10_

- [ ] 36.* 프로비저닝 E2E 통합 테스트
  - 파일: `tests/integration/remote/provisioning.integration.test.js`
  - 캐시된 wheel 환경에서 probe → /health 200 ≤120s
  - supervisor 재기동 동작 확인
  - _Requirements: 4.5, 4.8_

- [ ] 37.* 파일 브리지 성능 테스트
  - 파일: `tests/integration/remote/file-read-perf.integration.test.js`
  - 1 MB 파일 read ≤500ms, forward 첫 /health ≤2s
  - _Requirements: 5.6, 6.2_

- [ ] 38.* Watcher 지연 통합 테스트
  - 파일: `tests/integration/remote/watcher-latency.integration.test.js`
  - 활성 디렉터리 변경 알림 ≤1s (비활성 별도 측정)
  - _Requirements: 6.7_

- [ ] 39.* 터미널 지연 통합 테스트
  - 파일: `tests/integration/remote/terminal-latency.integration.test.js`
  - keypress → render ≤80ms, resize ≤200ms
  - _Requirements: 7.2, 7.3_

- [ ] 40.* Context switch 통합 테스트
  - 파일: `tests/integration/remote/context-switch.integration.test.js`
  - 활성 세션 전환 ≤500ms
  - _Requirements: 9.3_

- [ ] 41.* Reconnect E2E 통합 테스트
  - 파일: `tests/integration/remote/reconnect.integration.test.js`
  - sshd 컨테이너 재시작 시나리오 → backoff, replay, terminal reattach 안내
  - _Requirements: 8.2, 8.3, 8.4, 8.5, 8.7, 8.8_

- [ ] 42. 사용자 가이드 문서 작성
  - 파일: `docs/REMOTE_SSH.md`
  - 지원 SSH_Config 디렉터브, 수동/자동 프로비저닝 전환, 수동 Python 설치 안내
  - 알려진 한계 (Match 미지원, Windows 원격 자동 설치 미지원, 16MB 파일 상한, ProxyCommand 제한)
  - 트러블슈팅 (포트 범위 고갈, Python 버전 미달, 호스트 키 변경)
  - _Requirements: 13.4_

- [ ] 43. 최종 Checkpoint — 전체 테스트 통과
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- `*` 가 붙은 서브태스크는 선택적(테스트·성능·PBT). MVP 가속 시 생략 가능. Top-level 태스크는 `*` 없음.
- 모든 property test 파일 상단에 주석 태그 포함: `// Feature: remote-ssh, Property N: <title>`.
- 각 태스크는 이전 태스크에 의존하되, Phase 내부 서브태스크는 가능한 한 독립 실행 가능하도록 설계됨.
- 25개 Correctness Property 커버리지: P1–P25 모두 대응 PBT 태스크 존재 (1.3, 1.4, 2.2, 3.2, 4.2, 5.2, 6.2, 8.2, 9.3, 10.3, 10.4, 11.3, 11.4, 12.2, 15.4, 18.7, 18.8, 21.3, 21.4, 25.3, 25.4, 26.2(단위), 28.3, 29.2, 31.3, 32.2).
- 13개 Requirement 커버리지는 각 태스크 `_Requirements:_` 주석 총합으로 확인 가능.
- Phase 별 checkpoint 는 다음 phase 진입 전 blocking.

## Workflow 완료 안내

이 워크플로우는 **설계·계획 산출물** 까지만 다룹니다.
구현은 `tasks.md` 의 각 항목 옆에 있는 **"Start task"** 버튼을 클릭해서 개별 태스크 단위로 실행하시면 됩니다.

- `requirements.md` — 인수 기준 (13 Requirements)
- `design.md` — 아키텍처, 컴포넌트, 25 Correctness Properties
- `tasks.md` — 이 문서, 40+ 실행 가능 태스크 (Phase 1–8)
