# JARVIS UI event adapter contract

`ui/index.html` is the visual shell and `/ui/hud/session.js` is the single frontend composition entry point. The modules under `ui/hud/` own presentation state, voice/TTS interfaces, transport, conversation state, accessibility, and cleanup. The frontend does not make safety, routing, memory, retrieval, or approval-policy decisions.

See `ui/hud/ARCHITECTURE.md` for the responsibility map and lifecycle boundary.

## Server -> browser

### `system_status`

Initial diagnostics: `status`, `model`, `provider`, `memory`, `knowledge`, `web`, and `verification_enabled`.

### `status`

Lifecycle payload:

```json
{"state":"thinking|retrieving|generating|verifying|executing|waiting_confirmation|approved|completed|cancelled|failed|error","message":"optional","request_id":"id","task_id":"id","task_type":"GENERAL"}
```

The browser uses this only to choose the visual state.

### `approval_request`

```json
{"id":"approval-id","task_id":"task-id","action_type":"create_file","context":{},"prompt_text":"preview","risk_level":"medium","target":"target","reason":"reason"}
```

The browser displays the modal and sends a response. Approval is targeted by the backend to the originating Socket.IO client; the browser must not assume a broadcast.

### `jarvis_reply_token`

```json
{"token":"partial text","is_first":true,"reset":false,"request_id":"id","task_id":"id"}
```

Tokens are appended only when their `request_id` matches the active stream. When `reset` is true, the browser clears the current buffer before accepting fallback tokens.

### `jarvis_reply`

The final response includes `text`, `request_id`, `provider`, `model`, `fallback_used`, `task_type`, retrieval flags, `sources`, and `verification`. The browser speaks only the final `text`; it does not infer provider or approval policy.

## Browser -> server

### `user_speech`

```json
{"text":"voice transcript","request_id":"optional-id","language_hint":"optional"}
```

The backend redacts secrets before persistence, provider calls, or external retrieval.

### `approval_response`

```json
{"id":"approval-id","task_id":"task-id","result":true}
```

`result` must be a real JSON boolean. The browser sends `task_id` from the pending request and clears it after one response. Strings such as `"false"` are not valid approvals.

### Diagnostics and memory

`get_metrics`, `get_memory_stats`, and `forget_memory` are diagnostic or explicit memory-management events. Memory deletion confirmation must also be a real boolean.

See `backend/event_contract.md` for backend ownership, risk levels, timeout behavior, reset semantics, and privacy rules.
