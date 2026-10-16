# JARVIS backend event contract

This contract is the boundary between the local browser HUD and backend services. The HUD renders state and sends user intent; backend modules own routing, memory, retrieval, verification, risk policy, approval state, and sandboxing.

## Correlation and scope

- `request_id` is an opaque client-generated or backend-generated identifier for one user request.
- `task_id` identifies the autonomous task associated with a request. For the current single-request engine path it equals `request_id`.
- Approval requests are scoped internally to the originating Socket.IO client session and task. The backend emits them with `to=<client session>`; it never broadcasts an approval request.
- A browser response must include the exact approval `id`, the matching `task_id`, and a JSON boolean. `true`, `false`, and only those booleans are valid; strings such as `"false"` are rejected.
- Expired, duplicate, stale, cross-client, missing-task, or malformed approval responses are denied.
- Protected memory and engine capabilities are granted only to a server-issued HUD session or a local-voice connection authenticated with `JARVIS_LOCAL_VOICE_TOKEN`; event payload roles, session IDs, loopback binding, and CORS are not credentials.

## Client -> backend events

### `user_speech`

```json
{
  "text": "user request",
  "request_id": "opaque-request-id",
  "language_hint": "optional browser hint"
}
```

`text` is redacted before logging, persistence, provider calls, or external retrieval. The backend returns status and response events carrying the same `request_id`.

### `approval_response`

```json
{
  "id": "approval-request-id",
  "task_id": "opaque-task-id",
  "result": true
}
```

The backend validates policy and scope again. The UI is never the authority for risk decisions.

### Other client events

`get_metrics`, `get_memory_stats`, and `forget_memory` are diagnostic/memory operations. Programmatic memory deletion requires an actual JSON boolean confirmation; no truthy string is accepted.

Unauthenticated Socket.IO clients may keep an ordinary conversation, but they do
not receive private memory context, durable memory writes, memory-management
commands, privileged engine dispatch, or detailed memory/system diagnostics.

## Backend -> client events

### `system_status`

Sent on connect with safe diagnostics only:

```json
{
  "status": "ONLINE",
  "model": "groq/model-name",
  "provider": {},
  "memory": {},
  "knowledge": {},
  "web": {},
  "verification_enabled": false
}
```

### `status`

```json
{
  "state": "thinking|retrieving|generating|verifying|executing|waiting_confirmation|approved|completed|cancelled|failed|error",
  "message": "optional safe status",
  "request_id": "opaque-request-id",
  "task_id": "opaque-task-id",
  "task_type": "GENERAL"
}
```

### `approval_request`

Delivered only to the requesting browser session:

```json
{
  "id": "approval-request-id",
  "task_id": "opaque-task-id",
  "action_type": "create_file",
  "context": {"risk": "medium"},
  "prompt_text": "redacted human-readable preview",
  "risk_level": "medium|high|destructive|external",
  "target": "redacted target",
  "reason": "Risk level: medium."
}
```

Low-risk read-only actions may be automatic. Medium, high, destructive, and external actions require explicit confirmation. Permanently blocked credential, payment, cookie, and account-takeover actions can never be approved.

### `jarvis_reply_token`

Streaming text for one request:

```json
{
  "token": "partial text",
  "is_first": true,
  "reset": false,
  "request_id": "opaque-request-id",
  "task_id": "opaque-task-id"
}
```

When a primary provider fails after emitting partial text, the backend sends `reset: true` with an empty token before fallback output. The client must replace its buffer, not append fallback text to the failed provider's partial response.

### `jarvis_reply`

Final response for one request:

```json
{
  "text": "spoken response",
  "request_id": "opaque-request-id",
  "provider": "groq|gemini|anthropic|engine|none",
  "model": "safe model label",
  "fallback_used": false,
  "task_type": "GENERAL",
  "memory_saved": false,
  "retrieval_active": false,
  "web_retrieval_active": false,
  "sources": [],
  "verification": {"performed": false, "passed": null}
}
```

The backend must not claim verification unless the verification call actually ran and returned a valid passing result. A failed autonomous objective is reported as failed and is never converted into a completed state.

## Error and privacy rules

- Event payloads contain safe diagnostics only; credentials, raw secrets, cookies, payment data, and unredacted private text must not be emitted or logged.
- Provider fallback is per request. A failed provider stream is reset before another provider's output begins.
- Disconnecting a browser denies only that browser's pending approvals and cleans up its task scope.
