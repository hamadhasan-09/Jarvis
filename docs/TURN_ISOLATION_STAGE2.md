# Stage 2: turn and cancellation isolation

## Identity and ownership

Persistent memory continues to use the single-user `SESSION_ID`. Storage formats,
the memory manager, pairing, the security broker, native lifecycle and voice
engines are unchanged.

Each accepted Socket.IO connection receives a new server-generated control scope.
Request serialization and the backend turn registry use that scope, never the
persistent memory principal or a supplied `session_id`. Request controls are
indexed by `(SID, request_id)`, so identical wire request IDs on different tabs
cannot overwrite or cancel each other. Disconnect removes only that SID's
controls and connection state. Reconnection creates a fresh scope.

Each request also has a server-minted `control_turn_id`. Browser wire turn IDs
use this value. Configured-token voice keeps its supplied wire `turn_id` and
registered wire session for compatibility, but those fields do not enter the
backend control registry. The backend never adopts a client-provided turn into
another session's registry. Browser turn/source/role/session claims in speech or
cancel payloads do not establish ownership.

All request work still passes Stage 1 capability checks. Registration happens
after prompt/rate checks and before waiting for the per-connection coordinator,
so a same-client new request can signal cancellation to its own previous work.
It does not signal other connections. Queued requests superseded again before
execution are discarded. Finalization uses the canonical control turn, so an
older worker cannot end its successor's turn, including when voice repeats a
wire turn ID.

Cancellation and playback look up controls using the actual requesting SID and
validate the current connection scope. Supplied turn IDs, when present, must
match that owned record. Playback cannot finish a cancelled request. Existing
server-buffer prefix validation and shared-principal memory persistence remain
in place. The current browser HUD can still omit a turn ID on cancel/playback.

Request IDs are one-shot within a connection, including completed IDs. This
prevents a replay or late completion from targeting a replacement record. The
connection retains at most 10,000 seen IDs; clients reaching that limit must
reconnect. Normal clients already generate fresh request IDs.

## Legacy failure investigation

Before changing server isolation or authenticating the old fixtures, the legacy
tests were loaded against synthetic services instead of importing production
`ui.server`. The result reproduced OmniRush's report: **8 passed, 10 failed**.

| Failing legacy case | Confirmed cause | Migration |
| --- | --- | --- |
| Local reply routing | Anonymous GET treated as browser authentication; voice omitted token | Genuine pairing and configured-token voice |
| Local speech correlation | Tokenless voice denied before routing | Configured-token voice; correlation assertions preserved |
| Local approval ownership | Tokenless voice could not receive an approval | Configured-token voice; wrong-SID/task assertions preserved |
| Active browser cancel, empty heard prefix | Anonymous browser never started generation | Paired browser; empty-prefix assertion preserved |
| Active browser cancel, valid prefix | Anonymous browser never started generation | Paired browser; exact spoken-prefix assertion preserved |
| Active browser cancel, fabricated prefix | Anonymous browser never started generation | Paired browser; fabricated-text rejection preserved |
| Finished reply, spoken prefix only | Hardcoded SID had no authenticated connection | Actual paired SID and scoped record |
| In-progress server-buffer prefix | Hardcoded voice SID had no authenticated connection | Actual token-authenticated SID and scoped record |
| Cancel then next normal persistence | Hardcoded SID had no authenticated connection | Actual paired SID and scoped records |
| Normal playback persistence | Hardcoded SID had no authenticated connection | Actual paired SID and scoped record |

The eight initial passes consisted of five independent mocked voice/frontend
tests and three denial/no-write cases. Those three cases also used unauthenticated
hardcoded SIDs and could pass without reaching prefix validation; their fixtures
now use real authenticated connection registration. No tests were skipped and
behavioral expectations were not weakened.

The migrated legacy suite passed **18/18**. An intermediate combined run found
two Stage 1 fixture failures: they indexed an already-removed control using the
old global key. The fixtures now retain the record reference to verify writes
are disabled and additionally assert the scoped key is removed after disconnect.
These were fixture-layout changes, not a reason to retain stale controls.

## Verification — October 16, 2026

Final combined result: **152 passed in 18.47s; zero failures/errors**.

* Stage 1 authentication/routing, registry, reused startup/shutdown and selected
  approval/security-policy regressions: **114 passed**.
* Legacy `test_voice_routing.py` and `test_voice_cancellation.py`: **18 passed**.
* New `test_turn_isolation.py`: **20 passed**.

Coverage includes guests A/B against trusted turns; independent browsers and
two tabs under one credential; identical request IDs; browser/browser,
browser/voice, voice/browser and voice/voice concurrent requests; legitimate
same-client interruption; repeated voice wire turn IDs; forged session/source/
role/turn/cancellation IDs; replay and reconnection; disconnect/revocation/expiry
during generation and awaiting playback; stable memory identity; private reply
delivery and existing approval ownership.

The test fixture compiles the current server handlers with synthetic memory,
providers, jobs and services. It uses real turn-registry functions and the new
Stage 2 tests use the real request coordinator. SQLite connections are blocked
in that fixture. Legacy audio/service tests use injected mock audio streams and
service dependencies; no real audio device or model is activated. Shutdown
regressions use real HTTP against ephemeral synthetic-service listeners.

The project venv supplies application libraries. Its missing pytest runner is
borrowed from the installed system package directory without installing or
changing dependencies. Temporary test directories are created under `.cache`
with unique names and removed afterward. Production `ui.server` is not imported,
and no actual user memory or real automation is exercised.

Syntax checks passed for all six changed Python files. Before/after hashes
matched for 77 protected files/function snapshots, including pairing, the
security broker, memory storage, core contracts, voice engines, native sources,
icons, shutdown/startup functions and Stage 1 private-event routing. Targeted
Git whitespace checks passed. Temporary editing and verification scripts were
removed after validation; unrelated working-tree changes were preserved.

## Compatibility and remaining limits

Configured-token local voice retains request/turn/session reply correlation.
The native voice client's own turn registry is not mutated by browser requests.
Guests remain presentation-only and require no new permissions. Stage 1 pairing,
revocation and sanitized public voice state retain their existing rules.

Revoking a credential shared by several tabs still disconnects **all** those
tabs; that is intentional authentication policy, not cross-tab cancellation.
Authorized owner browsers continue receiving local-voice presentation according
to Stage 1 routing policy; Stage 2 does not create separate memory users.

Cancellation remains cooperative. Already-started external operations cannot be
rolled back or forcibly stopped by setting a request event. A provider that
ignores cancellation can delay the next request on its own connection until the
existing coordinator timeout, without locking other connections. Existing
coordinator locks are retained for process lifetime to avoid replacing a live
lock; rapid authenticated reconnects can therefore accumulate lock entries.

**UNVERIFIED:** production launch, real microphone/STT/TTS/providers, native GUI
end-to-end operation and any trusted Qt owner-pairing bootstrap. Qt pairing
remains unavailable in desktop-safe mode. This validation does not complete the
overall security milestone.
