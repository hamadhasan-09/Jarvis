"""Human Approval Gateway - GUI Aware (works with pythonw)"""
import sys
from datetime import datetime
from pathlib import Path
import json


class ApprovalGateway:
    AUTO_APPROVED = ["read_file", "create_file", "web_search", "calculate",
                     "summarize", "write_content", "system_info", "analyze"]
    ALWAYS_APPROVE = ["send_email", "post_social", "upload", "deploy",
                      "delete_file", "run_command", "execute_code", "git_push"]
    BLOCKED = ["store_password", "save_credit_card", "save_cookies", "auto_login",
               "transfer_money", "change_password"]

    def __init__(self, gui_root=None):
        self.gui_root = gui_root
        self.action_log = []
        self.blocked_log = []
        self.log_file = Path("data/logs/approvals.log")
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def set_gui_root(self, root):
        self.gui_root = root

    def check_action(self, action_type, details=None):
        if action_type in self.BLOCKED:
            self._log_decision(action_type, "blocked", details)
            return {"status": "blocked", "reason": f"JARVIS cannot {action_type}"}
        if action_type in self.AUTO_APPROVED:
            return {"status": "auto_approved"}
        return {"status": "needs_approval"}

    def request_approval(self, action_type, details, preview):
        check = self.check_action(action_type, details)
        if check["status"] == "blocked":
            return False
        if check["status"] == "auto_approved":
            return True

        if self.gui_root is not None:
            approved = self._gui_approval(action_type, preview)
        else:
            approved = self._cli_approval(action_type, preview)

        self._log_decision(action_type, "approved" if approved else "denied", details)
        return approved

    def _gui_approval(self, action_type, preview):
        try:
            from tkinter import messagebox
            result = messagebox.askyesno(
                title="JARVIS Approval Required",
                message=f"Action: {action_type}\n\nPreview:\n{preview[:400]}\n\nApprove?"
            )
            return bool(result)
        except Exception:
            return False

    def _cli_approval(self, action_type, preview):
        if not sys.stdin or not sys.stdin.isatty():
            return False
        try:
            print(f"\n⚠️ APPROVAL: {action_type}\n{preview[:200]}")
            return input("Approve? [Y/N]: ").strip().lower() in ('y', 'yes')
        except (EOFError, OSError):
            return False

    def _log_decision(self, action_type, decision, details):
        entry = {"time": datetime.now().isoformat(), "action": action_type,
                 "decision": decision, "details": str(details)[:200]}
        self.action_log.append(entry)
        if decision == "blocked":
            self.blocked_log.append(entry)
        try:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def get_report(self):
        approved = sum(1 for a in self.action_log if a["decision"] == "approved")
        denied = sum(1 for a in self.action_log if a["decision"] == "denied")
        return f"Approved:{approved} | Denied:{denied} | Blocked:{len(self.blocked_log)}"