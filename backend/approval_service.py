"""Session-scoped approval lifecycle for the autonomous engine.

The browser is only a confirmation surface. This service owns policy, pending
state, request/task/client correlation, timeouts, and fail-closed behavior.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading
import time
import uuid

from core.memory_manager import SecretDetector
from modules.human_approval import ApprovalPolicy


class BrowserApprovalGateway:
    """Policy-backed gateway used by the autonomous engine."""

    def __init__(self, socketio, policy=None, timeout_seconds=None):
        self.socketio = socketio
        self.policy = policy or ApprovalPolicy()
        self.secret_detector = SecretDetector()
        configured = timeout_seconds
        if configured is None:
            configured = float(os.getenv("JARVIS_APPROVAL_TIMEOUT", "60"))
        self.timeout_seconds = max(5.0, min(float(configured), 300.0))
        self.pending: dict[str, dict[str, object]] = {}
        self.lock = threading.RLock()
        self._local = threading.local()
        # Optional server-owned capability validator; existing non-browser
        # adapters keep their policy behavior when no validator is installed.
        self.client_authorizer = None

    def _client_authorized(self, client_id):
        if self.client_authorizer is None:
            return True
        try:
            return bool(self.client_authorizer(client_id))
        except Exception:
            return False

    @contextmanager
    def scoped(
        self,
        client_id: str | None = None,
        task_id: str | None = None,
        *,
        on_approval_state=None,
    ):
        previous = getattr(self._local, "scope", None)
        self._local.scope = {
            "client_id": client_id,
            "task_id": task_id,
            "on_approval_state": on_approval_state,
        }
        try:
            yield self
        finally:
            self._local.scope = previous

    def _scope(self, context, kwargs):
        active = getattr(self._local, "scope", None) or {}
        context = context if isinstance(context, dict) else {}
        client_id = kwargs.get("client_id") or context.get("client_id") or active.get("client_id")
        task_id = kwargs.get("task_id") or context.get("task_id") or active.get("task_id")
        return (str(client_id) if client_id else "", str(task_id) if task_id else "")

    def request_approval(self, action_type, context=None, prompt_text="", **kwargs):
        """Return True only for safe automatic actions or explicit scoped approval."""
        context = context if isinstance(context, dict) else {"details": str(context)}
        assessment = self.policy.assess(action_type, context)
        risk_level = assessment["level"]
        client_id, task_id = self._scope(context, kwargs)
        if client_id and not self._client_authorized(client_id):
            return False

        if assessment["status"] == "blocked":
            self.log(f"Approval blocked by policy: {action_type} [{risk_level}]")
            return False
        if assessment["status"] == "auto_approved":
            self.log(f"Low-risk action auto-approved: {action_type}")
            return True
        # Confirmation cannot be safely delivered without a browser owner.
        if not client_id:
            self.log(f"Approval denied: no client scope for {action_type}")
            return False

        approval_id = uuid.uuid4().hex
        event = threading.Event()
        expires_at = time.monotonic() + self.timeout_seconds
        with self.lock:
            self.pending[approval_id] = {
                "event": event,
                "result": False,
                "client_id": client_id,
                "task_id": task_id,
                "risk_level": risk_level,
                "expires_at": expires_at,
                "action_type": str(action_type),
            }

        payload = {
            "id": approval_id,
            "task_id": task_id,
            "action_type": str(action_type),
            "context": _safe_context(context, self.secret_detector),
            "prompt_text": self.secret_detector.redact(str(prompt_text or ""))[:1200],
            "risk_level": risk_level,
            "target": self.secret_detector.redact(str(kwargs.get("target", context.get("target", "local workspace"))))[:300],
            "reason": assessment["reason"],
        }
        scope = getattr(self._local, "scope", None) or {}
        state_callback = scope.get("on_approval_state")
        if state_callback:
            try:
                state_callback("awaiting_approval")
            except Exception as exc:
                with self.lock:
                    self.pending.pop(approval_id, None)
                self.log(f"Approval state could not be persisted for {action_type}: {type(exc).__name__}")
                return False
        self._emit("status", {"state": "waiting_confirmation", "message": f"Action approval required: {action_type}", "request_id": approval_id, "task_id": task_id}, client_id)
        self._emit("approval_request", payload, client_id)
        if not self._client_authorized(client_id):
            self.deny_pending(client_id=client_id)
        self.log(f"Approval requested: {action_type} [{risk_level}]")

        state_callback_failed = False
        try:
            completed = event.wait(self.timeout_seconds)
        finally:
            if state_callback:
                try:
                    state_callback("running")
                except Exception as exc:
                    state_callback_failed = True
                    self.log(f"Approval state could not be restored for {action_type}: {type(exc).__name__}")
        with self.lock:
            record = self.pending.pop(approval_id, None)
        approved = bool(
            completed
            and record
            and time.monotonic() <= float(record.get("expires_at", 0))
            and record.get("result") is True
            and record.get("client_id") == client_id
            and not state_callback_failed
            and self._client_authorized(client_id)
        )
        self._emit(
            "status",
            {
                "state": "executing" if approved else "cancelled",
                "message": f"Approval {'granted' if approved else 'denied'} for {action_type}.",
                "request_id": approval_id,
                "task_id": task_id,
            },
            client_id,
        )
        return approved

    def respond(self, approval_id, result, client_id: str | None = None, task_id: str | None = None):
        """Resolve one pending request; malformed/stale/cross-client responses fail."""
        if not self._client_authorized(client_id):
            if client_id:
                self.deny_pending(client_id=client_id)
            return False
        if type(result) is not bool:
            return False
        request_key = str(approval_id or "")
        with self.lock:
            record = self.pending.get(request_key)
            if not record:
                return False
            expected_client = str(record.get("client_id") or "")
            expected_task = str(record.get("task_id") or "")
            if not client_id or str(client_id) != expected_client:
                return False
            if str(task_id or "") != expected_task:
                return False
            if time.monotonic() > float(record.get("expires_at", 0)):
                record["result"] = False
                record["event"].set()
                return False
            # A blocked action never gets a pending record, and an already
            # resolved record cannot be changed because the event is one-shot.
            if record.get("event").is_set():
                return False
            record["result"] = result
            record["event"].set()
            return True

    def deny_pending(self, client_id: str | None = None, task_id: str | None = None):
        """Deny matching pending actions during disconnect/shutdown."""
        with self.lock:
            for record in self.pending.values():
                if client_id is not None and str(record.get("client_id")) != str(client_id):
                    continue
                if task_id is not None and str(record.get("task_id")) != str(task_id):
                    continue
                record["result"] = False
                record["event"].set()

    def _emit(self, name, payload, client_id):
        # Flask-SocketIO supports `to=`; tests and alternate adapters may expose
        # a smaller emit signature. Never silently fall back to a broadcast.
        if not client_id:
            return False
        if name == "approval_request" and not self._client_authorized(client_id):
            return False
        try:
            self.socketio.emit(name, payload, to=client_id)
            return True
        except TypeError:
            try:
                self.socketio.emit(name, payload, room=client_id)
                return True
            except TypeError:
                return False

    def log(self, message):
        text = str(message)[:500]
        print(text)
        scope = getattr(self._local, "scope", None) or {}
        client_id = scope.get("client_id")
        self._emit("status", {"state": "executing", "message": text, "task_id": scope.get("task_id")}, client_id)


def _safe_context(context: dict, detector: SecretDetector | None = None) -> dict:
    allowed = {"risk", "target", "step", "objective", "action", "bytes"}
    detector = detector or SecretDetector()
    return {str(key): detector.redact(str(value))[:300] for key, value in context.items() if key in allowed}
