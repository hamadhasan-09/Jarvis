"""Autonomous tool adapter.

The LLM may propose fenced blocks because that is the existing SOP contract,
but every block is validated by the single sandbox and approval policy. There
is no direct shell or arbitrary interpreter execution here.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from core.memory_manager import SecretDetector
from security_v2.sandbox import CommandSandbox


class Toolchain:
    """Parse model tool blocks and route them through the security boundary."""

    def __init__(self, workspace_dir=".", approval_gateway=None):
        requested = Path(workspace_dir).resolve()
        self.workspace_dir = requested if requested.name.lower() == "workspace" else requested / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox = CommandSandbox(self.workspace_dir)
        self.approval_gateway = approval_gateway
        self.secret_detector = SecretDetector()

    def _safe_preview(self, value, limit=500):
        return self.secret_detector.redact(str(value or ""))[:limit]

    def _approve(self, action_type, details, preview):
        """Fail closed when no gateway or a non-boolean decision is returned."""
        if self.approval_gateway is None:
            return False, "No approval gateway is connected"
        try:
            approved = self.approval_gateway.request_approval(
                action_type,
                {key: self._safe_preview(value, 300) if key not in {"bytes"} else value for key, value in details.items()},
                self._safe_preview(preview),
            )
            if type(approved) is not bool:
                return False, "Malformed approval decision"
            return approved, "Approved" if approved else "Denied by approval gateway"
        except Exception as exc:
            return False, f"Approval failed: {type(exc).__name__}"

    def _format_result(self, label, result):
        output = self._safe_preview(result.get("output", ""), 5000).strip()
        error = self._safe_preview(result.get("error", ""), 2000).strip()
        status = "Success" if result.get("success") else "Failed"
        return f"{label}\n{status}.\nStdout:\n{output}\nStderr:\n{error}".strip()

    def run_bash(self, command, timeout=30):
        clean_command = self._safe_preview(command, 300)
        approved, reason = self._approve(
            "run_command",
            {"command": clean_command, "risk": "high"},
            f"Run sandboxed internal command:\n{clean_command}",
        )
        if not approved:
            return f"Blocked before execution: {reason}"
        return self._format_result("Sandboxed command", self.sandbox.execute(str(command), timeout=timeout))

    def run_python(self, code, timeout=30):
        code = str(code)
        valid, reason = self.sandbox.validate_python(code)
        if not valid:
            return f"Blocked before execution: {reason}"

        approved, approval_reason = self._approve(
            "execute_code",
            {"bytes": len(code), "risk": "high"},
            f"Generated pure Python is not executable under the current policy:\n{code[:500]}",
        )
        if not approved:
            return f"Blocked before execution: {approval_reason}"

        script_path = self.workspace_dir / f".jarvis_tool_{uuid.uuid4().hex}.py"
        try:
            script_path.write_text(code, encoding="utf-8")
            result = self.sandbox.execute_python_file(script_path, timeout=timeout)
            return self._format_result("Sandboxed Python", result)
        finally:
            try:
                script_path.unlink(missing_ok=True)
            except Exception:
                pass

    def evaluate_tools(self, llm_response):
        """Parse fenced blocks while retaining the existing SOP interface."""
        results = []
        text = str(llm_response or "")
        bash_blocks = re.findall(r"```(?:bash|sh|shell)\s*\r?\n(.*?)\r?\n```", text, re.IGNORECASE | re.DOTALL)
        python_blocks = re.findall(r"```python\s*\r?\n(.*?)\r?\n```", text, re.IGNORECASE | re.DOTALL)
        for command in bash_blocks:
            results.append(f"Bash Command Proposed: {self._safe_preview(command)}\nResult: {self.run_bash(command)}")
        for code in python_blocks:
            results.append(f"Python Script Proposed ({len(code)} bytes).\nResult: {self.run_python(code)}")
        return results
