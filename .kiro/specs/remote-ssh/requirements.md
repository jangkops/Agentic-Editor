# Requirements Document

## Introduction

This feature adds VS Code Remote-SSH-equivalent capability to the Agentic Editor. A
local Electron app (macOS, Windows, Linux) SHALL connect to a remote host over SSH,
provision or reuse the FastAPI-based AI engine (`ai_engine/server.py`) on that host,
forward its HTTP port back to the local machine, and transparently route file
operations, terminal sessions, and Bedrock Gateway calls to the remote host.

The design must serve three primary use cases:

1. **GPU-bound agentic work** - offloading long-running agent workflows to a remote
   GPU-backed EC2 instance while the UI stays responsive on the laptop.
2. **Restricted-network Bedrock access** - enterprise environments where Bedrock
   Gateway is only reachable from a VPC or jump host.
3. **Portable distribution** - the feature is intended for redistribution to
   arbitrary users, so it MUST NOT depend on workstation-specific tooling beyond a
   working `ssh` client and a network-reachable host.

The feature preserves the existing architecture: the same FastAPI contract that runs
locally on `localhost:8765` runs on the remote host and is exposed to the Electron
process through an SSH local port forward (`-L`).

## Glossary

- **Local_Editor**: The Electron application running on the user's workstation.
- **Remote_Host**: A server reachable over SSH that runs `ai_engine/server.py`.
- **SSH_Config**: The OpenSSH configuration file located at `~/.ssh/config` by
  default, or the path specified by the `SSH_CONFIG_FILE` environment variable.
- **Host_Entry**: A single `Host` block parsed out of SSH_Config, including
  resolved directives such as `HostName`, `User`, `Port`, `IdentityFile`,
  `ProxyJump`, `ProxyCommand`, `ForwardAgent`, and `StrictHostKeyChecking`.
- **SSH_Config_Parser**: The module that converts SSH_Config text into a list of
  Host_Entry records.
- **SSH_Config_Printer**: The module that serializes a list of Host_Entry records
  back into SSH_Config-equivalent text.
- **Remote_Session**: An active SSH connection to a single Remote_Host, including
  its forwarded ports, provisioning state, and authentication material.
- **Remote_AI_Engine**: The instance of `ai_engine/server.py` running on a
  Remote_Host, listening on a remote TCP port (default 8765).
- **Local_Forwarded_Port**: A local TCP port on the workstation that is
  SSH-forwarded (`-L`) to the Remote_AI_Engine's listening port.
- **Host_Key_Store**: The Local_Editor's record of trusted Remote_Host public
  keys, persisted at `userData/ssh/known_hosts`.
- **Credential_Cache**: An in-memory store for SSH key passphrases and one-time
  auth responses (2FA codes) scoped to the lifetime of the Electron process.
- **Connection_State**: One of `disconnected`, `connecting`, `authenticating`,
  `provisioning`, `forwarding`, `connected`, `reconnecting`, `failed`.
- **Status_Bar**: The Local_Editor UI element that surfaces Connection_State for
  the active Remote_Session.
- **Remote_File_Bridge**: The module that translates local file-system IPC calls
  (`electron/src/ipc-fs-handlers.js` equivalents) into remote operations executed
  on the Remote_Host.
- **Remote_Terminal_Bridge**: The module that pipes node-pty terminal I/O to a
  `bash`/`pwsh` session on the Remote_Host over SSH.

## Requirements

### Requirement 1: SSH Config Discovery and Parsing

**User Story:** As a developer who already manages SSH shortcuts in
`~/.ssh/config`, I want the Local_Editor to read that file, so that I can pick
remote hosts without re-entering connection details.

#### Acceptance Criteria

1. WHEN the Local_Editor launches, THE SSH_Config_Parser SHALL locate the
   SSH_Config file at `$SSH_CONFIG_FILE` if set, otherwise at `~/.ssh/config` on
   Unix-like systems and `%USERPROFILE%\.ssh\config` on Windows.
2. WHEN SSH_Config is present, THE SSH_Config_Parser SHALL parse every `Host`
   block and produce a Host_Entry for each, resolving `Include` directives
   recursively up to a depth of 16.
3. THE SSH_Config_Parser SHALL support the directives `HostName`, `User`,
   `Port`, `IdentityFile`, `ProxyJump`, `ProxyCommand`, `ForwardAgent`,
   `StrictHostKeyChecking`, `UserKnownHostsFile`, `IdentitiesOnly`, and
   `PreferredAuthentications`.
4. THE SSH_Config_Printer SHALL serialize a list of Host_Entry records back to
   SSH_Config-equivalent text that omits wildcard-only entries.
5. FOR ALL Host_Entry lists produced by SSH_Config_Parser, applying
   SSH_Config_Printer and then SSH_Config_Parser SHALL produce a Host_Entry list
   semantically equal to the original (round-trip property).
6. IF SSH_Config is missing, THEN THE SSH_Config_Parser SHALL return an empty
   Host_Entry list without raising an error.
7. IF SSH_Config contains a syntactically invalid line, THEN THE
   SSH_Config_Parser SHALL skip that line, record a parse diagnostic with line
   number and file path, and continue parsing subsequent lines.
8. THE SSH_Config_Parser SHALL complete parsing of an SSH_Config file of up to
   500 Host blocks within 300 ms on reference hardware (Apple M1 / Intel i5
   10th gen).

### Requirement 2: Remote Host Selection UI

**User Story:** As a user switching between local and remote workspaces, I want a
host picker inside the Local_Editor, so that I can start a Remote_Session in two
clicks.

#### Acceptance Criteria

1. THE Local_Editor SHALL expose a "Remote" command in the command palette and a
   persistent indicator in the Status_Bar.
2. WHEN the user opens the Remote command, THE Local_Editor SHALL display a list
   of Host_Entry records sorted alphabetically by host alias.
3. THE Local_Editor SHALL display for each Host_Entry: alias, resolved
   `HostName`, `User`, and Connection_State if a Remote_Session already exists.
4. WHEN the user selects a Host_Entry with Connection_State `disconnected`, THE
   Local_Editor SHALL begin Remote_Session establishment per Requirement 3.
5. THE Local_Editor SHALL allow the user to add an ad-hoc host (alias, hostname,
   user, port, identity file) without editing SSH_Config on disk.
6. WHERE the user marks a Host_Entry as a favorite, THE Local_Editor SHALL
   display the favorite list above the full list.
7. THE Status_Bar SHALL render the active host alias and current
   Connection_State using the design tokens defined in `src/styles/variables.css`.

### Requirement 3: SSH Connection Establishment

**User Story:** As an engineer with an OpenSSH-compatible server, I want the
Local_Editor to connect to it using my existing keys, so that I do not need a
separate authentication workflow.

#### Acceptance Criteria

1. WHEN a Remote_Session begins, THE Local_Editor SHALL attempt authentication
   in the order `publickey`, `keyboard-interactive`, `password`, unless the
   Host_Entry's `PreferredAuthentications` overrides this.
2. WHEN an `IdentityFile` value is present, THE Local_Editor SHALL attempt to
   load the referenced private key from disk.
3. IF a private key is passphrase-protected, THEN THE Local_Editor SHALL prompt
   the user for the passphrase and store the decrypted key material in the
   Credential_Cache for the duration of the Electron process only.
4. WHERE `ForwardAgent yes` is set for a Host_Entry and the user's
   `SSH_AUTH_SOCK` is reachable, THE Local_Editor SHALL delegate signing to the
   SSH agent instead of loading key material directly.
5. WHERE `ProxyJump` is set for a Host_Entry, THE Local_Editor SHALL establish
   transitive connections through every listed jump host before connecting to
   the target Remote_Host.
6. WHEN the Remote_Host's public key is not present in the Host_Key_Store, THE
   Local_Editor SHALL display the host fingerprint and require explicit user
   confirmation before adding it to the Host_Key_Store.
7. IF the Remote_Host's public key differs from the entry already stored in the
   Host_Key_Store, THEN THE Local_Editor SHALL abort the connection, display a
   host-key-mismatch warning, and log a security event.
8. IF authentication fails three consecutive times for the same Host_Entry
   within one minute, THEN THE Local_Editor SHALL stop retrying automatically
   and surface the failure to the user.
9. WHERE the Host_Entry triggers a keyboard-interactive 2FA prompt, THE
   Local_Editor SHALL display each challenge from the server verbatim and send
   the user's response back.
10. THE Local_Editor SHALL complete a successful SSH handshake to a reachable
    Remote_Host within 10 seconds over a 50 ms RTT link.

### Requirement 4: Remote AI Engine Provisioning

**User Story:** As a user deploying the editor to a fresh EC2 instance, I want
`ai_engine/server.py` to be installed and started on the Remote_Host, so that the
same Bedrock Gateway contract is available remotely.

#### Acceptance Criteria

1. WHEN a Remote_Session reaches the `provisioning` Connection_State, THE
   Local_Editor SHALL probe the Remote_Host for an existing Remote_AI_Engine by
   issuing an HTTP `GET /health` on the remote listening port.
2. IF no healthy Remote_AI_Engine responds within 3 seconds, THEN THE
   Local_Editor SHALL execute the provisioning workflow defined in
   Requirement 4 clauses 3 through 7.
3. THE Local_Editor SHALL upload the current `ai_engine/` directory and
   `ai_engine/requirements.txt` to `~/.agentic-editor/ai_engine` on the
   Remote_Host via `sftp` or equivalent, preserving file modes.
4. THE Local_Editor SHALL create a virtual environment at
   `~/.agentic-editor/venv` on the Remote_Host using the Remote_Host's
   `python3` (version 3.11 or newer) and install
   `ai_engine/requirements.txt` into it.
5. THE Local_Editor SHALL start the Remote_AI_Engine under a supervisor process
   that restarts the server if it exits unexpectedly.
6. IF the Remote_Host lacks a compatible Python interpreter, THEN THE
   Local_Editor SHALL display a provisioning-failed error that names the
   missing prerequisite and links to the manual-install documentation.
7. WHERE the user selects "Manual provisioning" for a Host_Entry, THE
   Local_Editor SHALL skip automatic upload and install and instead verify that
   a user-installed Remote_AI_Engine is reachable on the configured port.
8. THE Local_Editor SHALL complete provisioning on a host with pre-installed
   Python 3.11 and a cached pip wheel index within 120 seconds.
9. THE Local_Editor SHALL record the provisioning script version in
   `~/.agentic-editor/version` on the Remote_Host and skip re-upload when the
   local and remote versions match.

### Requirement 5: Port Forwarding and API Routing

**User Story:** As a user who expects the Bedrock Gateway integration to work
unchanged, I want the Local_Editor to forward the remote API port locally, so
that every existing `localhost:8765` call keeps working.

#### Acceptance Criteria

1. WHEN the Remote_AI_Engine is healthy, THE Local_Editor SHALL open an SSH
   local port forward from a Local_Forwarded_Port to the Remote_AI_Engine's
   listening port (default `8765`).
2. IF the preferred Local_Forwarded_Port is already bound on the workstation,
   THEN THE Local_Editor SHALL select the next available port in the range
   `18765` to `18865` and update the routing configuration.
3. THE Local_Editor SHALL route every in-process HTTP call that currently
   targets `localhost:8765` to the Local_Forwarded_Port while a Remote_Session
   is in state `connected`.
4. WHILE a Remote_Session is in state `connected`, THE Local_Editor SHALL NOT
   launch or communicate with a local `ai_engine/server.py` instance.
5. WHEN the Remote_Session transitions from `connected` to any terminal state,
   THE Local_Editor SHALL restore the default local routing to `localhost:8765`.
6. THE Local_Editor SHALL complete the first successful HTTP `GET /health`
   through the Local_Forwarded_Port within 2 seconds of the forward being
   established.

### Requirement 6: Remote File System Bridge

**User Story:** As a user editing code that lives on the Remote_Host, I want the
Local_Editor's explorer, Monaco tabs, and file IPC to operate on the remote
filesystem, so that I can work as if the files were local.

#### Acceptance Criteria

1. WHILE a Remote_Session is in state `connected` and a remote workspace root is
   set, THE Remote_File_Bridge SHALL service every `ipc-fs-handlers` call
   (list, read, write, stat, watch) against the Remote_Host.
2. WHEN the user opens a file through the file explorer, THE Remote_File_Bridge
   SHALL stream file contents from the Remote_Host and surface them to Monaco
   within 500 ms for files up to 1 MB on a 50 ms RTT link.
3. WHEN the user saves a file, THE Remote_File_Bridge SHALL write the buffer to
   the Remote_Host atomically (write to temp file, fsync, rename) and return
   the new file stat to the renderer.
4. THE Remote_File_Bridge SHALL display remote paths in the UI using the
   Remote_Host's native path separator (`/` for Unix, `\` for Windows).
5. IF a remote write fails due to permission, disk full, or I/O error, THEN THE
   Remote_File_Bridge SHALL return the underlying error code to the renderer
   and leave the remote file unchanged.
6. FOR ALL file contents written through the Remote_File_Bridge, reading the
   file back immediately SHALL return the byte sequence that was written
   (round-trip property, excluding intentional line-ending normalization).
7. WHERE the user enables a file watcher on a remote directory, THE
   Remote_File_Bridge SHALL deliver change notifications to the renderer with
   latency no greater than 1 second under normal network conditions.

### Requirement 7: Remote Terminal Integration

**User Story:** As a user running build or Git commands in the integrated
terminal, I want the terminal to attach to the Remote_Host when a Remote_Session
is active, so that my shell matches the code I am editing.

#### Acceptance Criteria

1. WHILE a Remote_Session is in state `connected`, THE Remote_Terminal_Bridge
   SHALL spawn new terminals as PTY sessions on the Remote_Host rather than on
   the workstation.
2. THE Remote_Terminal_Bridge SHALL pipe stdin, stdout, and stderr between the
   local node-pty consumer and the remote PTY with end-to-end latency under
   80 ms on a 50 ms RTT link.
3. WHEN the terminal is resized in the renderer, THE Remote_Terminal_Bridge
   SHALL send the new rows and columns to the remote PTY within 200 ms.
4. WHEN a Remote_Session disconnects, THE Remote_Terminal_Bridge SHALL mark
   every associated terminal as `disconnected`, preserve its scrollback, and
   offer to reattach after reconnection.
5. WHERE the user explicitly opens a "Local Terminal", THE Remote_Terminal_Bridge
   SHALL spawn the PTY on the workstation regardless of Remote_Session state.

### Requirement 8: Connection Monitoring and Recovery

**User Story:** As a user working over a flaky network, I want the Local_Editor
to detect disconnects and recover gracefully, so that I do not lose work in
progress.

#### Acceptance Criteria

1. WHILE a Remote_Session is in state `connected`, THE Local_Editor SHALL send
   an SSH keepalive at most every 30 seconds.
2. IF three consecutive keepalives fail, THEN THE Local_Editor SHALL transition
   the Remote_Session to `reconnecting` and begin recovery.
3. WHILE in state `reconnecting`, THE Local_Editor SHALL attempt to
   re-establish the SSH connection with exponential backoff of 2, 4, 8, 16, 30
   seconds, capped at 30 seconds between attempts.
4. WHEN reconnection succeeds, THE Local_Editor SHALL re-open the port forward
   and resume API routing without restarting the Electron process.
5. WHEN reconnection succeeds, THE Local_Editor SHALL re-attach existing
   terminals by binding to the remote supervisor's persisted PTY sessions when
   available, otherwise mark those terminals `disconnected`.
6. IF reconnection does not succeed within 5 minutes, THEN THE Local_Editor
   SHALL transition the Remote_Session to `failed` and prompt the user to
   retry or disconnect.
7. WHILE in state `reconnecting`, THE Local_Editor SHALL queue outbound
   `/process` and `/streamprocess` requests, up to a queue depth of 32, and
   replay them once the session returns to `connected`.
8. FOR ALL queued requests replayed after reconnection, replaying the same
   request sequence twice SHALL NOT produce duplicate Bedrock Gateway calls
   beyond the first successful response (idempotency via request UUID
   deduplication on the Remote_AI_Engine).

### Requirement 9: Multi-Host Context Switching

**User Story:** As a power user juggling a dev host and a GPU host, I want to
keep multiple Remote_Sessions alive and switch between them, so that I do not
pay the handshake cost repeatedly.

#### Acceptance Criteria

1. THE Local_Editor SHALL support at most four concurrent Remote_Sessions.
2. THE Local_Editor SHALL designate exactly one Remote_Session as the active
   session at any time and route file, terminal, and API traffic to it.
3. WHEN the user selects a different Remote_Session from the host picker, THE
   Local_Editor SHALL complete the context switch within 500 ms.
4. WHILE a Remote_Session is inactive, THE Local_Editor SHALL keep its SSH
   connection and port forward alive but suspend file watchers.
5. WHEN the user invokes "Disconnect", THE Local_Editor SHALL tear down the
   selected Remote_Session, close its forwards, and flush its Credential_Cache
   entries.

### Requirement 10: Credential and Key Security

**User Story:** As a security-conscious administrator distributing this editor,
I want key material and passphrases to stay in memory only, so that the app
cannot leak secrets to disk.

#### Acceptance Criteria

1. THE Local_Editor SHALL NOT persist SSH passphrases, decrypted private keys,
   or 2FA responses to any file under `app.getPath('userData')` or any other
   location on the workstation.
2. THE Credential_Cache SHALL reside entirely in the Electron main process
   memory and SHALL be cleared on process exit, user logout, and explicit
   "Clear cached credentials" command.
3. THE Local_Editor SHALL mask SSH passphrases in every log sink, showing at
   most the first character followed by `****`.
4. THE Local_Editor SHALL NOT transmit SSH keys, passphrases, or decrypted
   credentials to any service other than the SSH daemon of the Remote_Host or
   a configured ProxyJump.
5. IF the user stores an ad-hoc host, THEN THE Local_Editor SHALL persist only
   alias, hostname, user, port, and identity-file path, never the private key
   contents or passphrase.
6. THE Host_Key_Store SHALL use `0600` file permissions on Unix-like systems
   and the Windows ACL equivalent restricting access to the current user.
7. WHERE the `PasswordAuthentication` option is disabled for a Host_Entry, THE
   Local_Editor SHALL NOT prompt the user for a password under any flow.

### Requirement 11: Cross-Platform Compatibility

**User Story:** As a teammate on Windows, I want to use the same Remote_Session
feature as my macOS and Linux colleagues, so that the team can share a single
workflow.

#### Acceptance Criteria

1. THE Local_Editor SHALL implement SSH connectivity through a bundled library
   that does not depend on a platform-specific `ssh` binary on PATH.
2. WHEN packaged for Windows, THE Local_Editor SHALL normalize path separators
   for the SSH_Config lookup, Host_Key_Store, and IdentityFile resolution.
3. THE Local_Editor SHALL read SSH keys in the formats OpenSSH (PEM and
   OpenSSH-new), RSA, ECDSA, and Ed25519.
4. THE Local_Editor SHALL support Remote_Hosts running Linux, macOS, and
   Windows (OpenSSH for Windows, with `bash` or `pwsh` as the remote shell).
5. WHERE the Remote_Host is Windows, THE Remote_AI_Engine SHALL be provisioned
   into `%USERPROFILE%\.agentic-editor` with PowerShell-compatible paths.
6. THE Local_Editor SHALL preserve UTF-8 encoding end-to-end for terminal and
   file data across every supported workstation-Remote_Host pair.

### Requirement 12: Status, Diagnostics, and Observability

**User Story:** As a user troubleshooting a failed Remote_Session, I want clear
state indicators and diagnostic logs, so that I can resolve issues without
guessing.

#### Acceptance Criteria

1. THE Status_Bar SHALL render Connection_State using distinct styles for
   `connecting`, `connected`, `reconnecting`, and `failed`, sourced from the
   design tokens in `src/styles/variables.css`.
2. WHEN a Remote_Session transitions state, THE Local_Editor SHALL append a
   structured log entry to `userData/logs/remote-ssh.log` containing
   timestamp, host alias, from-state, to-state, and reason.
3. THE Local_Editor SHALL expose a "Show Remote Log" command that opens the
   remote-ssh log in a read-only editor tab.
4. WHEN an error surfaces to the user, THE Local_Editor SHALL include the host
   alias, Connection_State at the time of failure, and a short remediation
   hint.
5. THE Local_Editor SHALL mask any SSH passphrase or API token appearing in a
   diagnostic message using the first-four-character-plus-`****` convention
   already used for `apitoken` logging.

### Requirement 13: Settings and Distribution Defaults

**User Story:** As a user who received this editor from a colleague, I want
Remote_Session behavior to follow sensible defaults, so that I can start
without tweaking configuration.

#### Acceptance Criteria

1. THE Local_Editor SHALL default `StrictHostKeyChecking` to `ask` for new
   hosts and `yes` for hosts already in the Host_Key_Store.
2. THE Local_Editor SHALL default the remote working directory to the user's
   home directory on the Remote_Host until the user selects a workspace.
3. THE Local_Editor SHALL persist per-host preferences (favorite, last used
   workspace, remote port override, manual-vs-auto provisioning) in
   `userData/settings/remote-hosts.json`.
4. THE Local_Editor SHALL document every supported SSH_Config directive and
   every override in `docs/REMOTE_SSH.md`.
5. THE Local_Editor SHALL NOT require any AWS credentials or Bedrock Gateway
   configuration beyond what is already specified for local operation.
