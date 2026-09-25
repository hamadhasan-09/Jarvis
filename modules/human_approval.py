"""Human approval policy and gateway.

The policy is deliberately explicit:
- LOW: safe, read-only or analytical actions may run automatically.
- MEDIUM: local writes or bounded setup actions require confirmation.
- HIGH/DESTRUCTIVE/EXTERNAL: always require explicit confirmation.
- BLOCKED: credential, payment, and account-takeover actions never run.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path


class RiskLevel:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTERNAL = "external"
    BLOCKED = "blocked"


class ApprovalPolicy:
    """Classify actions independently of any UI implementation."""

    BLOCKED_ACTIONS = {
        "store_password", "save_password", "store_api_key", "save_api_key",
        "save_credit_card", "save_cookies", "auto_login", "transfer_money",
        "change_password",
    }
    LOW_ACTIONS = {
        "read_file", "web_search", "calculate", "summarize", "system_info",
        "analyze", "inspect", "list_files",
    }
    MEDIUM_ACTIONS = {
        "create_file", "write_content", "create_project", "execute_objective",
        "run_local_test",
    }
    HIGH_ACTIONS = {
        "run_command", "execute_code", "delete_file", "deploy", "git_push",
    }
    EXTERNAL_ACTIONS = {
        "send_email", "post_social", "upload", "publish", "invite", "purchase",
    }
    EXTERNAL_WORDS = re.compile(
        r"(?i)\b(send|email|post|publish|upload|deploy|push|invite|purchase|payment|transfer)\b"
    )
    DESTRUCTIVE_WORDS = re.compile(
        r"(?i)\b(delete|remove|wipe|format|drop|destroy|overwrite|kill|shutdown)\b"
    )

    def assess(self, action_type, details=None):
        action = str(action_type or "unknown").strip().lower()
        details = details if isinstance(details, dict) else {}
        text = " ".join(str(value) for value in details.values())
        requested_risk = str(details.get("risk", "")).strip().lower()

        if action in self.BLOCKED_ACTIONS:
            return {
                "level": RiskLevel.BLOCKED,
                "status": "blocked",
                "reason": f"Action '{action}' is permanently blocked by policy.",
            }
        if requested_risk in {RiskLevel.HIGH, RiskLevel.EXTERNAL, RiskLevel.BLOCKED}:
            level = requested_risk
        elif action in self.EXTERNAL_ACTIONS or self.EXTERNAL_WORDS.search(text):
            level = RiskLevel.EXTERNAL
        elif action in self.HIGH_ACTIONS or self.DESTRUCTIVE_WORDS.search(text):
            level = RiskLevel.HIGH
        elif requested_risk == RiskLevel.MEDIUM or action in self.MEDIUM_ACTIONS:
            level = RiskLevel.MEDIUM
        elif action in self.LOW_ACTIONS:
            level = RiskLevel.LOW
        else:
            # Unknown actions are not silently approved.
            level = RiskLevel.MEDIUM

        return {
            "level": level,
            "status": "auto_approved" if level == RiskLevel.LOW else "needs_approval",
            "reason": f"Risk level: {level}.",
        }


class ApprovalGateway:
    """Policy-backed GUI/CLI gateway retained for non-browser engine callers."""

    def __init__(self, gui_root=None, policy=None):
        self.gui_root = gui_root
        self.policy = policy or ApprovalPolicy()
        self.action_log = []
        self.blocked_log = []
        self.log_file = Path("data/logs/approvals.log")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def set_gui_root(self, root):
        self.gui_root = root

    def check_action(self, action_type, details=None):
        assessment = self.policy.assess(action_type, details)
        if assessment["status"] == "blocked":
            self._log_decision(action_type, "blocked", details, assessment)
        return assessment

    def request_approval(self, action_type, details=None, preview=""):
        assessment = self.check_action(action_type, details)
        if assessment["status"] == "blocked":
            return False
        if assessment["status"] == "auto_approved":
            self._log_decision(action_type, "auto_approved", details, assessment)
            return True

        if self.gui_root is not None:
            approved = self._gui_approval(action_type, assessment, preview)
        else:
            approved = self._cli_approval(action_type, assessment, preview)

        self._log_decision(action_type, "approved" if approved else "denied", details, assessment)
        return approved

    def _gui_approval(self, action_type, assessment, preview):
        try:
            from tkinter import messagebox
            result = messagebox.askyesno(
                title="JARVIS Approval Required",
                message=(
                    f"Action: {action_type}\n"
                    f"Risk: {assessment['level'].upper()}\n\n"
                    f"Preview:\n{preview[:400]}\n\nApprove?"
                ),
            )
            return bool(result)
        except Exception:
            return False

    def _cli_approval(self, action_type, assessment, preview):
        if not sys.stdin or not sys.stdin.isatty():
            return False
        try:
            print(
                f"\nAPPROVAL REQUIRED: {action_type} "
                f"[{assessment['level'].upper()}]\n{preview[:300]}"
            )
            return input("Approve? [Y/N]: ").strip().lower() in ("y", "yes")
        except (EOFError, OSError):
            return False

    def _log_decision(self, action_type, decision, details, assessment=None):
        entry = {
            "time": datetime.now().isoformat(),
            "action": action_type,
            "decision": decision,
            "risk": (assessment or {}).get("level", "unknown"),
            "details": str(details)[:200],
        }
        self.action_log.append(entry)
        if decision == "blocked":
            self.blocked_log.append(entry)
        try:
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def get_report(self):
        approved = sum(1 for item in self.action_log if item["decision"] in {"approved", "auto_approved"})
        denied = sum(1 for item in self.action_log if item["decision"] == "denied")
        return f"Approved:{approved} | Denied:{denied} | Blocked:{len(self.blocked_log)}"
