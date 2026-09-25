"""Backend approval service for the local browser adapter.

This module owns approval policy application and the pending-request lifecycle.
The UI only consumes the documented ``approval_request`` and
``approval_response`` events; it does not own risk decisions.
"""

from __future__ import annotations

import os
import threading
import uuid

from modules.human_approval import ApprovalPolicy


class BrowserApprovalGateway:
    """Policy-backed gateway used by the autonomous engine."""

    def __init__(self, socketio, policy=None, timeout_seconds=None):
        self.socketio = socketio
        self.policy = policy or ApprovalPolicy()
        configured = timeout_seconds
        if configured is None:
            configured = float(os.getenv("JARVIS_APPROVAL_TIMEOUT", "60"))
        self.timeout_seconds = max(5.0, min(float(configured), 300.0))
        self.pending = {}
        self.lock = threading.RLock()

    def request_approval(self, action_type, context=None, prompt_text="", **kwargs):
        """Return True only for safe automatic actions or explicit approval."""
        context = context if isinstance(context, dict) else {"details": str(context)}
        assessment = self.policy.assess(action_type, context)
        risk_level = assessment["level"]

        if assessment["status"] == "blocked":
            self.log(f"Approval blocked by policy: {action_type} [{risk_level}]")
            return False
        if assessment["status"] == "auto_approved":
            self.log(f"Low-risk action auto-approved: {action_type}")
            return True

        approval_id = uuid.uuid4().hex
        event = threading.Event()
        with self.lock:
            self.pending[approval_id] = {"event": event, "result": False}

        payload = {
            "id": approval_id,
            "action_type": action_type,
            "context": context,
            "prompt_text": str(prompt_text or "")[:1200],
            "risk_level": risk_level,
            "target": kwargs.get("target", context.get("target", "local workspace")),
            "reason": assessment["reason"],
        }
        self.socketio.emit(
            "status",
            {"state": "waiting_confirmation", "message": f"Action approval required: {action_type}"},
        )
        self.socketio.emit("approval_request", payload)
        self.log(f"Approval requested: {action_type} [{risk_level}]")

        completed = event.wait(self.timeout_seconds)
        with self.lock:
            record = self.pending.pop(approval_id, {"result": False})
        approved = bool(completed and record.get("result"))
        self.socketio.emit(
            "status",
            {
                "state": "executing" if approved else "cancelled",
                "message": f"Approval {'granted' if approved else 'denied'} for {action_type}.",
            },
        )
        return approved

    def respond(self, approval_id, result):
        """Resolve one pending request. Unknown/expired ids fail closed."""
        with self.lock:
            record = self.pending.get(str(approval_id))
            if not record:
                return False
            record["result"] = bool(result)
            record["event"].set()
            return True

    def deny_pending(self):
        """Deny all pending actions during shutdown or disconnect handling."""
        with self.lock:
            for record in self.pending.values():
                record["result"] = False
                record["event"].set()

    def log(self, message):
        print(message)
        self.socketio.emit("status", {"state": "executing", "message": str(message)})
