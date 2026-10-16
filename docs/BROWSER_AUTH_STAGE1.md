# Browser authentication: Stage 1

Integrated from the extracted `nexus_stage1_auth_foundation` in
`C:\Users\Hammad\Desktop\Nexus_stage1`, after reviewing its `INTEGRATION_NOTES.md`.
No ZIP remained in that folder; the supplied Python implementation and tests
were present. No GitHub source was fetched or used to replace local files.

## Trust boundary and endpoints

Anonymous `GET /` only serves the presentation page. Anonymous Socket.IO clients
have no conversation, memory, engine or diagnostics capabilities. They cannot
submit speech to a model. The configured-token `local_voice` authentication path
is retained; a voice connection without a configured token is presentation-only.

Owner tokens are generated only by `PairingRegistry.issue_owner_pairing_token()`
inside the server process. There is **no HTTP token-generation endpoint**.
Tokens contain 256 random bits, expire after 90 seconds, and can be redeemed once.
Issuing another owner token invalidates the previous unused token. The registry
stores only pairing-token hashes and maintains opaque credential IDs with
principal, issuance, expiry, revocation and process-epoch records under a lock.
Browser credentials expire after eight hours and are lost on server restart.
`revoke_all()` rotates the epoch and invalidates active credentials and unused
tokens. It is an in-process owner API, not a public HTTP endpoint.

All authentication endpoints require a loopback server bind, a loopback peer,
a loopback Host on the configured port, and a matching Origin when present.
Forwarded headers do not establish trust. Remote privileged pairing is disabled.
Mutating requests also require `X-Nexus-Browser-Auth: 1` and JSON where applicable.
This header provides CSRF protection; it is not an owner credential.

* `POST /auth/pair`, JSON `{"token":"..."}`: exchange a trusted owner
  token for an HttpOnly, SameSite=Strict session cookie; 401 for unknown, expired
  or replayed tokens. Successful replacement revokes the previous credential.
* `GET /auth/status`: 200 with `authenticated: true` for a live credential;
  otherwise 401. It does not issue credentials or return secrets.
* `POST /auth/revoke`: revoke the caller's credential and clear its cookie;
  401 if not authenticated. This revokes all sockets sharing that credential.

The previous local `/auth/browser/pair`, `/auth/browser/status` and
`/auth/browser/revoke` paths remain aliases using identical validation. The
credential ID is stored only in the signed cookie, never returned in JSON.

The cookie name is `nexus_owner_session_v1`; the session field is
`nexus_owner_credential_v1`. Neither the old Flask `session` cookie nor the old
`nexus_browser_capability` field grants access. A signed new cookie alone is also
insufficient: its credential must exist in this process's registry. Local HTTP
uses HttpOnly/SameSite cookies without `Secure`; remote transport is unsupported.
Authentication responses are not cacheable.

## Legitimate owner pairing in legacy browser mode

This stage supplies a manual bootstrap, not a new HUD pairing interface:

1. From the owner's trusted, interactive terminal, explicitly start the legacy
   server with `venv\Scripts\python.exe ui\server.py --pair-browser`.
   Existing legacy startup behavior still applies; this option does not disable
   voice or other legacy services. Do not use it to perform safety-isolated tests.
2. The process displays one short-lived token directly in that terminal. Pairing
   token output is refused when input/output is redirected, the server bind is
   non-loopback, or desktop-safe mode is enabled. Do not share or record the token.
3. Open the server's local page. In that page's browser developer console, run:

   ```javascript
   await fetch('/auth/pair', {
     method: 'POST',
     headers: {'Content-Type': 'application/json', 'X-Nexus-Browser-Auth': '1'},
     body: JSON.stringify({token: prompt('Owner token from the trusted terminal')})
   }).then(r => r.json())
   ```

4. After `authenticated: true`, reload the page. Existing sockets do not gain
   capabilities in place; a new handshake validates the newly issued cookie.

The token is entered through a prompt rather than pasted as a command literal,
and is never placed in a URL. There is no automatic pairing through GET,
local-network origin, a claimed role, launch identity or shutdown secret.

## Current Qt desktop-safe mode

The current Qt launcher has no trusted browser pairing bootstrap or owner pairing
surface. This stage does not add one or modify native lifecycle code. Therefore
browser clients in that runtime stay presentation-only unless a future trusted
native integration explicitly calls the in-process registry and securely
delivers the one-time token. `--pair-browser` is deliberately unavailable there.
There is no environment-variable, HTTP or log-based fallback that elevates an
anonymous browser. A separate Python process cannot mint a valid token for an
already-running registry.

A later native bootstrap should use a distinct credential and owner-restricted
IPC, or an explicit trusted pairing confirmation. Do not reuse the public launch
ID, configured voice token or shutdown secret as browser credentials.

## Connected sockets, approvals and limits

Request-scoped local-voice status, replies, tokens and other routed events are
private. Their origin must be authorized to receive them; browser presentation
recipients require live conversation and memory-read capabilities. Each recipient
is checked immediately before emission, including after the recipient list was
selected. Approval events remain targeted to the owner SID and additionally
require engine-start capability; they are not mirrored to other browsers.

The deliberately public `voice_state` event contains **only** `state`, limited to
`idle`, `listening`, `transcribing`, `thinking`, `speaking`, `error` or `stopped`.
Unknown state values become `idle`. Initial connection snapshots, the real voice
service callback and routed voice-state updates all use this projection. Request
IDs, session IDs, turn IDs, error details and free text are never public state.

Capability checks revalidate browser credentials against the registry. A reverse
credential-ID-to-SID index supports active invalidation. Expiry also runs on
daemon timers, including idle sockets, without starting the job scheduler in
desktop-safe mode. Revocation/expiry clears
capabilities, denies pending approvals, clears pending forget confirmations,
disables further request memory persistence, cancels owned request controls and
disconnects matching sockets. Approval admission, responses and the final approval
decision also recheck authorization. Existing policy, SID/task correlation and
termination behavior remain in place.

Already executed operations cannot be rolled back. An already-started provider
request may finish or incur charges before cooperative cancellation takes effect.
This is not an OS-process isolation boundary against software running as the owner.
Connection-scoped turn and cancellation isolation is documented in
[TURN_ISOLATION_STAGE2.md](TURN_ISOLATION_STAGE2.md). Presentation-only guests
are rejected before entering request-control processing.
Already emitted or queued events cannot be recalled after revocation. Paired
owner browsers still share authorized local-voice presentation; this is not
separate persistent-memory isolation between trusted clients.

## Configured-token local voice

Normal legacy startup loads the project `.env` and then resolves
`JARVIS_LOCAL_VOICE_TOKEN` from the process environment, defaulting to an empty
string. Existing environment values take precedence over `.env`. The server's
`initialize_local_voice()` passes that exact configured value into
`VoiceBrainBridge(..., token=VOICE_LOCAL_TOKEN)`. `VoiceBrainBridge.connect()`
sends it as the Socket.IO authentication `token` alongside `role: local_voice`.
The backend checks the configured token before granting capabilities. A standalone
bridge must receive the same value explicitly through its `token` constructor
argument; the bridge does not independently load `.env`.

The example configuration leaves this token empty, and `start.bat` enables voice
without generating one. Therefore a normal installation needs a nonempty token
in the server's environment or project `.env` for authenticated voice requests:

1. Generate a private random token in the owner's trusted console, for example
   `venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Set `JARVIS_LOCAL_VOICE_TOKEN` to that value in the owner-controlled environment
   or `.env`. Keep it private; do not put it in URLs, source, shared logs or chat.
3. Restart the legacy server so the backend and its normal bridge receive the
   same value. Independently launched bridges also need that explicit token.

An empty/unset token leaves voice sockets connected but unprivileged: speech
requests are rejected before model routing, memory or engine execution. With a
configured token, missing or incorrect credentials are rejected at connection.
Loopback access and a claimed role never replace token authentication. No token
is generated or privileges granted automatically to bypass this requirement.
Desktop-safe mode still suppresses voice startup regardless of this setting.
The owner's actual `.env` and production token were not inspected or changed.

## Isolated verification

`tests/test_browser_auth.py` and `tests/test_socketio_authorization.py` use the
opt-in `isolated_auth_server` fixture. It compiles current server handler definitions
and decorators against a fresh Flask/Socket.IO application, real auth registry and
approval gateway, with synthetic memory/providers/engine/jobs. It never imports
`ui.server` or constructs production services. Fake clocks test expiration
deterministically; a separate bounded timer test checks idle expiration delivery.
These tests do not substitute for a future native bootstrap integration test.

### Supplied foundation integration verification, October 16, 2026

* **PASS: 87 focused tests**, `87 passed in 9.11s`, with zero remaining failures
  or errors. This includes the supplied 13 registry tests, HTTP/Socket.IO tests,
  existing approval regressions and six existing permission-policy regressions.
* The existing startup/shutdown regression bodies were called with the synthetic
  server fixture rather than their original production-import fixture. Both
  single-request and simultaneous-request shutdown checks used real HTTP against
  an ephemeral test listener, confirming the complete 202 JSON preceded shutdown.
* The final run used `venv\Scripts\python.exe` and the project's installed Flask
  and Flask-SocketIO packages. Since pytest is absent from the project venv, its
  installed system package directory was appended to supply the runner. No
  dependencies were installed or changed.
* An initial run had 81 passes and six setup errors because pytest could not
  access its default Windows temporary directory. Rerunning with a fresh,
  workspace-contained `--basetemp` resolved all six. Generated policy/audit test
  files were removed afterward; existing cache contents were preserved.
* The supplied dependency-free suite also passed independently with site packages
  disabled: `venv\Scripts\python.exe -B -S -m unittest discover -s tests -p
  test_browser_auth_registry.py -v` (13 tests, OK; overlaps the 87-test run).
* Covered anonymous GET and speech rejection; owner pairing; atomic concurrent
  redemption/replay rejection; token and credential expiration; idle expiry
  notification; revocation of active/shared sockets and pending approvals;
  revocation after a positive approval response; legacy/unknown-cookie rejection;
  remote peer, non-loopback bind, Host and Origin rejection; CSRF protection;
  explicit console bootstrap exchange; configured voice-token preservation;
  authenticated browser/voice request routing through mock providers; browser
  engine admission through an inert orchestrator; stale cached-capability denial;
  callback-failure isolation and malformed-token rejection. Guests do not receive
  provider/knowledge diagnostics in their initial status event.
* No production backend, microphone, model call, real automation or user memory
  was exercised. Database connections fail immediately in the opt-in fixture.
* Syntax checks passed for all six changed Python files; targeted whitespace
  checks passed. Before/after hashes matched for 39 protected files/function
  snapshots, including native sources, icons, voice files, memory storage code,
  the existing approval service, and shutdown/startup functions.
* **UNVERIFIED:** full application boot and native/browser end-to-end pairing.
  Qt owner bootstrap is deliberately not implemented in this stage.

### Final routing and voice corrections verification

* **PASS: 114 focused tests**, `114 passed in 12.65s`, zero failures/errors.
  The 27 new tests cover private status/reply/token/unknown-event and approval
  routing, exclusion of guests/tokenless/unrelated voice clients, delivery-time
  revalidation after revocation or expiry, global-state payload sanitization,
  initial connection sanitization, and the real normal-startup constructor/token
  handoff into `VoiceBrainBridge.connect()` using a mocked network client.
* The existing authentication, registry, memory-handler, configured-token voice,
  pending-approval, startup/shutdown and selected security-policy regressions
  passed in the same run. Shutdown tests use real HTTP with an ephemeral
  synthetic-service listener; the production backend was never launched.
* The `forget_memory` denial now correctly identifies both paired browsers and
  authenticated local voice as valid memory-management clients.
* The project venv supplied Flask/Socket.IO; the installed system pytest package
  supplied only the test runner as above. Tests used an isolated workspace
  temporary directory, which was removed afterward. No dependencies changed.
* **UNVERIFIED:** live microphone/STT/TTS and production configuration. No voice
  engine, real provider, actual user memory or real automation was exercised.
