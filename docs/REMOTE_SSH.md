# Remote SSH 사용자 가이드

## 개요

AI 에디터의 Remote SSH 기능은 로컬 Electron 앱에서 원격 호스트에 SSH로 접속하여 `ai_engine/server.py`를 자동 프로비저닝하고, 해당 서버의 HTTP 포트를 로컬로 포워딩하여 기존 `localhost:8765` API 계약을 그대로 재사용합니다.

VS Code Remote-SSH와 유사한 워크플로우를 제공하며, 다음 세 가지 주요 사용 사례를 지원합니다:

1. **GPU-bound agentic work** — 원격 GPU EC2 인스턴스에서 에이전트 워크플로우를 실행하면서 로컬 UI는 반응성을 유지
2. **Restricted-network Bedrock access** — VPC 또는 jump host에서만 Bedrock Gateway에 접근 가능한 엔터프라이즈 환경
3. **Portable distribution** — 워크스테이션에 `ssh` 클라이언트와 네트워크 접근 가능한 호스트만 있으면 동작

SSH 연결 라이브러리로 `ssh2` (Node.js pure JavaScript)를 사용하므로 OS 네이티브 `ssh` 바이너리에 의존하지 않습니다.

---

## 지원 SSH Config 디렉터브

`~/.ssh/config` (또는 `$SSH_CONFIG_FILE` 환경변수로 지정한 경로)에서 다음 디렉터브를 파싱합니다:

### 기본 연결 디렉터브
| 디렉터브 | 설명 |
|----------|------|
| `HostName` | 실제 접속할 호스트명 또는 IP |
| `User` | SSH 사용자명 |
| `Port` | SSH 포트 (기본 22) |
| `IdentityFile` | 개인키 파일 경로 (다중 지정 가능) |

### 프록시 디렉터브
| 디렉터브 | 설명 |
|----------|------|
| `ProxyJump` | 중간 hop 호스트 체인 (최대 3 hop) |
| `ProxyCommand` | OS ssh 바이너리 터널 fallback 사용 |

### 인증 및 보안 디렉터브
| 디렉터브 | 설명 |
|----------|------|
| `ForwardAgent` | SSH agent forwarding 활성화 |
| `StrictHostKeyChecking` | 호스트 키 검증 정책 (`yes`/`no`/`ask`) |
| `UserKnownHostsFile` | known_hosts 파일 경로 |
| `IdentitiesOnly` | 지정된 키만 사용 |
| `PreferredAuthentications` | 인증 방법 우선순위 |

### Include 디렉터브
- `Include` 디렉터브를 재귀적으로 해석 (깊이 16 제한)
- 순환 참조 감지 시 diagnostic 기록 후 확장 중단
- 누락된 Include 파일은 경고 기록 후 파싱 계속

### 와일드카드 Host 패턴
- `*`, `?` 등 와일드카드 패턴을 포함한 Host 블록 지원
- 순수 와일드카드 전용 엔트리(`*`)는 호스트 목록에서 제외

### 미지원 디렉터브
- `Match` 블록 (Host 패턴만 지원)
- `CanonicalizeHostname`
- `Include` 와일드카드 경로

---

## 연결 방법

### 1. Command Palette에서 호스트 선택

Command Palette → **"Remote: Connect to Host"** 를 실행합니다.

### 2. 호스트 선택 또는 Ad-hoc 호스트 추가

- SSH Config에서 파싱된 호스트 목록이 표시됩니다 (즐겨찾기 우선, 알파벳 정렬)
- 각 항목에 alias, resolved HostName, User, 현재 연결 상태가 표시됩니다
- **Ad-hoc 호스트 추가**: SSH Config 파일을 수정하지 않고 에디터 내에서 호스트를 추가할 수 있습니다
  - 저장 항목: alias, hostName, user, port, identityFile 경로만 (키 내용/패스프레이즈는 저장하지 않음)

### 3. 인증

인증 시도 순서 (기본값):
1. `publickey` — IdentityFile 또는 SSH agent
2. `keyboard-interactive` — 2FA 포함
3. `password`

`PreferredAuthentications` 디렉터브로 순서를 override할 수 있습니다.

- **키 인증**: OpenSSH (PEM/new), RSA, ECDSA, Ed25519 포맷 지원
- **패스프레이즈**: 암호화된 키의 경우 다이얼로그에서 입력, 프로세스 메모리에만 캐시
- **SSH Agent**: `ForwardAgent yes` + `SSH_AUTH_SOCK` 존재 시 agent 위임
- **2FA**: 서버 challenge를 그대로 표시, 사용자 응답 전달
- **인증 실패 보호**: 60초 내 3회 연속 실패 시 자동 재시도 중단

### 4. 자동 프로비저닝

연결 성공 후 원격 호스트에 ai_engine을 자동 설치합니다:

1. Python 3.11+ 감지
2. `~/.agentic-editor/venv` 에 venv 생성
3. `pip install -r requirements.txt` 실행
4. supervisor 스크립트 배포 및 기동
5. content hash 기반 업로드 스킵 (변경 없으면 재배포 안 함)

### 5. 포트 포워딩 → 원격 ai_engine 연결

- 원격 ai_engine의 포트(기본 8765)를 로컬 포트(18765–18865 범위)로 SSH 터널링
- 기존 `localhost:8765` 호출이 자동으로 로컬 포워딩 포트로 라우팅됨
- 원격 연결 중에는 로컬 ai_engine 프로세스가 자동 중지됨

---

## 프로비저닝 모드

### 자동 모드 (기본)

기본 프로비저닝 흐름:

1. `/health` 엔드포인트로 기존 ai_engine 존재 여부 확인 (3초 timeout)
2. 응답 없으면 자동 설치 시작:
   - `python3 --version` 으로 Python 3.11+ 확인
   - `~/.agentic-editor/venv` 에 가상환경 생성
   - `pip install -r requirements.txt --no-input` 으로 의존성 설치
   - `supervisor.sh` (while loop + sleep 2) 배포 및 기동
3. content hash 기반 업로드 스킵:
   - `~/.agentic-editor/version` 파일에 `aiEngineContentHash` 기록
   - 로컬 ai_engine 트리의 SHA-256 해시와 비교
   - 해시 일치 시 업로드 생략 (재배포 불필요)

### 수동 모드

per-host 설정에서 `provisioningMode: 'manual'` 지정 시:

- ai_engine 업로드/설치를 완전히 스킵
- 사용자가 직접 원격 호스트에서 ai_engine 설치 및 실행
- 에디터는 `/health` 엔드포인트 확인만 수행
- 응답에 `{"service": "ai-editor-engine"}` 이 포함되어야 정상 인식

설정 방법: `userData/settings/remote-hosts.json` 에서 해당 호스트의 `provisioningMode`를 `"manual"`로 지정

---

## 수동 Python 설치 안내

원격 호스트에 Python 3.11 이상이 설치되어 있어야 합니다.

```bash
# Debian/Ubuntu
sudo apt install python3.11 python3.11-venv

# macOS
brew install python@3.11

# Fedora/RHEL
sudo dnf install python3.11

# Windows
winget install Python.Python.3.11
```

수동 모드에서 ai_engine을 직접 실행하는 방법:

```bash
source ~/.agentic-editor/venv/bin/activate
cd ~/.agentic-editor/ai_engine
python -m uvicorn server:app --host 127.0.0.1 --port 8765
```

---

## 알려진 한계

| 항목 | 설명 |
|------|------|
| `Match` 디렉터브 | 미지원 (Host 패턴만 지원) |
| Windows 원격 호스트 | 자동 프로비저닝 미지원 (수동 모드 사용) |
| 파일 크기 상한 | 16 MB (SFTP read cap) |
| ProxyCommand | OS ssh 바이너리 터널 fallback 사용 (`ssh -W` 템플릿만 허용) |
| ProxyJump | 최대 3 hop |
| 동시 세션 | 최대 4개 Remote Session |
| 터미널 재부착 | 재연결 시 새 shell로 reattach (기존 PTY 복구는 v2) |
| File watcher | SFTP polling 기반 (inotify 에이전트 미사용) |

---

## 트러블슈팅

### 포트 범위 고갈

- **증상**: `PortExhaustedError` — 로컬 포워딩 포트를 할당할 수 없음
- **원인**: 18765–18865 범위의 모든 포트가 점유됨
- **해결**:
  ```bash
  lsof -i:18765-18865
  ```
  점유 프로세스를 확인 후 불필요한 프로세스 종료

### Python 버전 미달

- **증상**: `PythonUnsupportedError` — "Remote Python X.Y is below the required 3.11"
- **원인**: 원격 호스트의 Python이 3.11 미만이거나 설치되지 않음
- **해결**: Python 3.11+ 설치 (위 [수동 Python 설치 안내](#수동-python-설치-안내) 참조)

### 호스트 키 변경

- **증상**: "Host key changed!" 경고, 연결 즉시 중단
- **원인**: 원격 서버의 호스트 키가 이전에 저장된 키와 다름 (서버 재설치 등)
- **해결**:
  1. 서버 재설치가 확인된 경우: `userData/ssh/known_hosts` 에서 해당 항목 삭제 후 재연결
  2. 예상치 못한 변경인 경우: 중간자 공격 가능성 확인

### 연결 끊김 후 재연결

자동 재연결 동작:

- **재연결 budget**: 최대 5분
- **지수 백오프**: 2s → 4s → 8s → 16s → 30s (cap)
- **재연결 중 요청 처리**: 큐에 보관 (최대 32개, FIFO)
  - 큐 초과 시 가장 오래된 요청 drop
  - 재연결 성공 시 FIFO 순서로 replay
  - `requestid` 기반 중복 제거로 멱등성 보장
- **터미널**: 스크롤백 보존, 재연결 후 새 shell로 reattach 제안
- **5분 초과 시**: `failed` 상태로 전이, 사용자에게 재시도/연결 해제 선택 제공

### 포트 점유 충돌

- **증상**: `PortOccupiedByOtherServiceError`
- **원인**: 원격 포트(기본 8765)에 다른 서비스가 이미 실행 중
- **해결**: 원격 호스트에서 충돌 서비스 중지, 또는 per-host 설정에서 `remotePortOverride`로 포트 변경

### Show Remote Log

- **단축키**: `Cmd+Shift+L` (또는 Command Palette → "Show Remote Log")
- 원격 SSH 로그 파일(`userData/logs/remote-ssh.log`)을 읽기 전용 탭으로 열기
- 로그 형식: newline-delimited JSON (timestamp, host alias, event, state 전이 정보 포함)

---

## 키보드 단축키

| 단축키 | 동작 |
|--------|------|
| `Cmd+Shift+L` | Show Remote Log |
| Command Palette | Remote: Connect to Host |

---

## Connection State (연결 상태)

Status Bar에 현재 연결 상태가 표시됩니다:

| 상태 | 설명 | 표시 색상 |
|------|------|-----------|
| `disconnected` | 연결 없음 | — |
| `connecting` | TCP + SSH 배너 교환 중 | warning (노란색) |
| `authenticating` | 인증 진행 중 | warning |
| `provisioning` | ai_engine 설치/확인 중 | warning |
| `forwarding` | 포트 포워딩 설정 중 | warning |
| `connected` | 정상 연결됨 | success (초록색) |
| `reconnecting` | 재연결 시도 중 | warning + pulse 애니메이션 |
| `failed` | 연결 실패 | error (빨간색) |

---

## 파일 구조

```
electron/src/remote/           — SSH 연결, 브리지, 프로비저닝 모듈
  ssh-config-parser.js         — SSH Config 파싱/출력
  remote-session.js            — 상태 머신 + SSH 연결 관리
  remote-session-manager.js    — 다중 세션 관리 (최대 4)
  remote-file-bridge.js        — SFTP 기반 파일 작업
  remote-terminal-bridge.js    — 원격 PTY 터미널
  provisioner.js               — ai_engine 자동 설치
  port-forwarder.js            — 로컬 포트 포워딩
  port-allocator.js            — 포트 범위 스캔
  session-router.js            — IPC 라우팅 분기
  credential-cache.js          — 메모리 전용 자격증명 캐시
  host-key-store.js            — TOFU 호스트 키 저장소
  keepalive-policy.js          — Keepalive 감시
  backoff.js                   — 지수 백오프 계산
  reconnect-loop.js            — 재연결 루프
  request-queue.js             — 재연결 중 요청 큐 (depth 32)
  logger.js                    — 로그 마스킹 + 파일 기록
  auth-policy.js               — 인증 실패 정책
  error-surface.js             — 에러 메시지 정규화
  path-normalization.js        — Windows 경로 정규화
  resources/supervisor.sh      — 원격 supervisor 스크립트

src/components/                — UI Web Components
  remote-host-picker.js        — 호스트 선택 다이얼로그
  remote-status-bar.js         — 연결 상태 표시
  remote-auth-dialog.js        — 인증 다이얼로그 (passphrase/password/2FA)
  remote-host-key-dialog.js    — 호스트 키 확인 다이얼로그

ai_engine/server.py            — /health service 식별자, requestid dedup middleware

userData/settings/             — 사용자 설정
  remote-hosts.json            — 호스트별 설정 (favorite, workspace, provisioningMode)

userData/logs/                 — 로그
  remote-ssh.log               — 연결 상태 전이, 에러 등 구조화 로그

userData/ssh/                  — SSH 관련
  known_hosts                  — 호스트 키 저장소 (TOFU, 권한 0600)
```

---

## 보안 참고사항

- SSH 패스프레이즈, 복호화된 개인키, 2FA 응답은 **Electron 메인 프로세스 메모리에만** 존재
- 디스크에 자격증명을 저장하지 않음 (프로세스 종료 시 자동 삭제)
- Ad-hoc 호스트 저장 시 identityFile **경로**만 기록 (키 내용 미저장)
- `known_hosts` 파일은 Unix에서 `0600` 권한으로 생성
- 모든 로그에서 패스프레이즈/API token은 첫 글자 + `****` 로 마스킹
