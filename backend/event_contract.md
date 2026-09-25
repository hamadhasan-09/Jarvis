# JARVIS backend event contract

Ownership boundary:

`ui/index.html` is a presentation/client adapter. `ui/server.py` only translates
Socket.IO events into backend service calls. Risk classification, approval
lifecycle, sandboxing, routing, memory, retrieval, and orchestration belong to
backend modules.

## Approval interface consumed by the server

`backend.approval_service.BrowserApprovalGateway` exposes:

```python
gateway = BrowserApprovalGateway(socketio)
gateway.request_approval(action_type, context, prompt_text) -> bool
gateway.respond(approval_id, result) -> bool
gateway.deny_pending() -> None
```

The gateway applies `modules.human_approval.ApprovalPolicy`:

- `low`: automatically approved only for safe read-only/analysis actions.
- `medium`: explicit confirmation required.
- `high`, `external`, destructive, or unknown: explicit confirmation required.
- blocked credential/payment/account-takeover actions: always denied.
- no response before the bounded timeout: denied.

## Socket.IO events

Backend -> UI `approval_request`:

```json
{
  "id": "opaque-request-id",
  "action_type": "run_command",
  "context": {"risk": "high"},
  "prompt_text": "human-readable preview",
  "risk_level": "high|medium|external",
  "target": "local workspace",
  "reason": "Risk level: high."
}
```

UI -> backend `approval_response`:

```json
{"id": "opaque-request-id", "result": true}
```

Backend -> UI `status` during the lifecycle:

```json
{"state": "waiting_confirmation", "message": "..."}
{"state": "executing|cancelled", "message": "..."}
```

## Minimal server adapter changes

`ui/server.py` now only:

1. imports `BrowserApprovalGateway`,
2. instantiates `engine_gateway = BrowserApprovalGateway(socketio)`,
3. forwards the existing `approval_response` event to `engine_gateway.respond()`, and
4. forwards Socket.IO disconnect to `engine_gateway.deny_pending()` so pending
   actions fail closed immediately.

It does not implement risk policy, pending approval storage, timeouts, or
approval decisions. No HUD redesign or frontend business logic is part of this
backend hardening pass.
