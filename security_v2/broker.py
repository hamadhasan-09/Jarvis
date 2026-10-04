"""Central policy, credential-access, and audit broker for Jarvis actions."""

from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from security_v2.secret_detector import SecretDetector


LOG = logging.getLogger(__name__)


class SecurityBroker:
    """Apply the permission policy before sensitive operations.

    ``get_credential`` returns the raw secret only to its caller. Callers MUST
    NOT place that value in LLM-visible text, logs, or conversation memory.
    Network-layer credential substitution is intentionally not implemented.
    """

    CAPABILITIES = frozenset({
        "fs.read",
        "fs.write",
        "fs.delete",
        "fs.execute",
        "shell.exec",
        "python.exec",
        "net.fetch",
        "net.send",
        "credential.read",
        "system.always_human",
    })

    DEFAULT_POLICY = {
        "fs.read": "auto_allow",
        "fs.write": "ask_every_time",
        "fs.delete": "ask_every_time",
        "fs.execute": "ask_every_time",
        "shell.exec": "ask_every_time",
        "python.exec": "ask_every_time",
        "net.fetch": "auto_allow",
        "net.send": "ask_every_time",
        "credential.read": "auto_allow",
        "system.always_human": "deny_always",
    }
    VALID_RULES = frozenset({
        "auto_allow",
        "ask_once_per_session",
        "ask_every_time",
        "deny_always",
    })

    def __init__(self, gateway, key_manager, policy_path=None, audit_log_path="data/logs/permission_audit.jsonl"):
        self.gateway = gateway
        self.key_manager = key_manager
        self.policy_path = Path(policy_path) if policy_path else Path(__file__).with_name("permission_policy.yaml")
        self.audit_log_path = Path(audit_log_path)
        self.secret_detector = SecretDetector()
        self._lock = threading.RLock()
        self._audit_lock = threading.Lock()
        self._local = threading.local()
        self._session_decisions: dict[str, bool] = {}
        self._known_secrets: set[str] = set()
        self.policy = self._load_policy()

    def _load_policy(self) -> dict[str, str]:
        try:
            with self.policy_path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
            configured = data.get("capabilities", data) if isinstance(data, dict) else {}
            if not isinstance(configured, dict):
                raise ValueError("policy must be a capability-to-rule mapping")
            policy = dict(self.DEFAULT_POLICY)
            for capability, rule in configured.items():
                capability = str(capability)
                rule = str(rule).strip().lower()
                if rule not in self.VALID_RULES:
                    LOG.warning("Invalid permission rule for %s; using ask_every_time", capability)
                    rule = "ask_every_time"
                policy[capability] = rule
            return policy
        except FileNotFoundError:
            LOG.warning("Permission policy file is missing at %s; using hardcoded safe defaults", self.policy_path)
        except Exception as exc:
            LOG.warning("Permission policy could not be loaded (%s); using hardcoded safe defaults", type(exc).__name__)
        return dict(self.DEFAULT_POLICY)

    def authorize(self, capability: str, action_type: str, context: dict, preview: str) -> dict:
        """Return an audited allow/deny decision, failing closed on gateway errors."""
        capability = str(capability or "unknown")
        action_type = str(action_type or "unknown")
        context = dict(context) if isinstance(context, dict) else {"details": str(context)}
        job_id = getattr(self._local, "job_id", None)
        if job_id:
            context["job_id"] = job_id
        unknown = capability not in self.CAPABILITIES or capability not in self.policy
        if unknown:
            LOG.warning("Permission capability %s is not recognized or mapped; requiring approval", self._redact(capability)[:200])
            rule = "ask_every_time"
        else:
            rule = self.policy[capability]

        reused = False
        reason = ""
        if rule == "deny_always":
            allowed = False
            reason = "Denied by security policy."
        elif rule == "auto_allow":
            allowed = True
            reason = "Allowed by security policy."
        elif rule == "ask_once_per_session":
            with self._lock:
                if capability in self._session_decisions:
                    allowed = self._session_decisions[capability]
                    reused = True
                    reason = "Reused this session's approval." if allowed else "Reused this session's denial."
                else:
                    allowed, reason = self._request_approval(action_type, context, preview)
                    self._session_decisions[capability] = allowed
        else:
            allowed, reason = self._request_approval(action_type, context, preview)

        self._append_audit(
            capability=capability,
            action_type=action_type,
            rule_applied=rule,
            allowed=allowed,
            reused=reused,
            context=context,
            preview=preview,
        )
        return {"allowed": allowed, "reason": reason, "rule_applied": rule}

    @contextmanager
    def scoped_job(self, job_id: str):
        """Attach a durable job ID to broker audit contexts in this thread."""
        previous = getattr(self._local, "job_id", None)
        self._local.job_id = str(job_id)
        try:
            yield self
        finally:
            self._local.job_id = previous

    def _request_approval(self, action_type: str, context: dict, preview: str) -> tuple[bool, str]:
        if self.gateway is None:
            return False, "No approval gateway is connected."
        try:
            decision = self.gateway.request_approval(action_type, context, preview)
            if type(decision) is not bool:
                return False, "Approval gateway returned an invalid decision."
            return decision, "Approved by the user." if decision else "Denied by the user."
        except Exception as exc:
            LOG.warning("Approval request failed (%s)", type(exc).__name__)
            return False, f"Approval request failed: {type(exc).__name__}."

    def _append_audit(
        self,
        *,
        capability: str,
        action_type: str,
        rule_applied: str,
        allowed: bool,
        reused: bool,
        context: dict,
        preview: str,
    ) -> None:
        context_summary = json.dumps(context, ensure_ascii=False, default=str)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "capability": self._redact(capability)[:200],
            "action_type": self._redact(action_type)[:200],
            "rule_applied": rule_applied,
            "decision": "allowed" if allowed else "denied",
            "reused": bool(reused),
            "context_summary": self._redact(context_summary)[:200],
            "preview_summary": self._redact(str(preview or ""))[:200],
        }
        if isinstance(context, dict) and context.get("job_id"):
            entry["job_id"] = self._redact(str(context["job_id"]))[:200]
        with self._audit_lock:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _redact(self, value: str) -> str:
        redacted = self.secret_detector.redact(str(value or ""))
        with self._lock:
            for secret in self._known_secrets:
                if secret:
                    redacted = redacted.replace(secret, "[REDACTED_CREDENTIAL]")
        return redacted

    def get_credential(self, service: str, purpose: str = "") -> str | None:
        """Return a key only to trusted code; never expose it to prompts, logs, or memory.

        The caller-supplied service and purpose are deliberately omitted from the
        audit payload so a mistakenly supplied secret cannot be copied there.
        """
        decision = self.authorize(
            "credential.read",
            "get_credential",
            {"source": "provider credential lookup"},
            preview="Read provider credential",
        )
        if not decision["allowed"]:
            return None
        credential = self.key_manager.get_key(service)
        if isinstance(credential, str) and credential:
            with self._lock:
                self._known_secrets.add(credential)
        return credential
