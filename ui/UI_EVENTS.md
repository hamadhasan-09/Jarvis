# JARVIS UI Event Integration Documentation

This document outlines the Socket.IO events that the UI consumes and emits. The Application/UI layer acts entirely as a presentation and state-tracking layer. It relies on the backend to enforce safety, sandboxing, and execution.

## Events Consumed by UI (Server -> Client)

### `approval_request`
Sent by the backend when an autonomous action requires human confirmation.
**Payload:**
```json
{
  "id": "uuid-string",
  "action_type": "str (e.g. 'File Edit', 'Shell Command')",
  "context": "str (Brief context or reason)",
  "prompt_text": "str (Detailed explanation of what JARVIS wants to do)",
  "risk_level": "str ('high' | 'medium' | 'low')",
  "target": "str (Target file, path, or command)"
}
```
**UI Behavior:** Changes state to `waiting_confirmation`, displays the authorization modal.

### `status`
Sent by the backend to transition the UI visual state.
**Payload:**
```json
{
  "state": "str ('thinking' | 'retrieving' | 'searching' | 'verifying' | 'executing' | 'waiting_confirmation' | 'completed' | 'cancelled' | 'failed' | 'error')",
  "message": "str (Optional status text)",
  "request_id": "str (Optional correlation ID)",
  "task_type": "str"
}
```
**UI Behavior:** Maps the state to visual animations on the HUD core and updates status labels.

### `jarvis_reply_token`
Sent during a streaming response.
**Payload:**
```json
{
  "token": "str",
  "is_first": "bool"
}
```
**UI Behavior:** Appends the token to the live speech display.

### `jarvis_reply`
Sent when a response is fully completed.
**Payload:**
```json
{
  "text": "str (Full spoken text)",
  "memory_saved": "bool",
  "request_id": "str",
  "provider": "str",
  "model": "str",
  "fallback_used": "bool",
  "task_type": "str",
  "retrieval_active": "bool",
  "web_retrieval_active": "bool",
  "sources": "list",
  "verification": "dict"
}
```
**UI Behavior:** Synthesizes the text to voice, resets the core to `idle`, and updates history details.

### `system_status`
Sent upon initial connection.
**Payload:**
```json
{
  "status": "str",
  "model": "str",
  "memory": "dict",
  "knowledge": "dict",
  "web": "dict",
  "verification_enabled": "bool"
}
```
**UI Behavior:** Populates diagnostic overlays.

### `metrics` / `memory_stats` / `memory_action`
System and memory usage data.
**UI Behavior:** Updates the runtime data overlay.

---

## Events Emitted by UI (Client -> Server)

### `approval_response`
Sent in response to an `approval_request`.
**Payload:**
```json
{
  "id": "uuid-string (Must match request ID)",
  "result": "bool (true for Approve, false for Deny/Cancel)"
}
```

### `user_speech`
Sent when the frontend speech recognition completes a command.
**Payload:**
```json
{
  "text": "str",
  "request_id": "str",
  "language_hint": "str"
}
```

### `get_metrics` / `get_memory_stats` / `forget_memory`
Diagnostic and memory-management requests.
