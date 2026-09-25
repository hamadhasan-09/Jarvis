"""
Autonomous tool adapter.

The LLM may still propose fenced bash/python blocks because that is the
existing SOP contract, but every block now passes through CommandSandbox and
an approval gateway.  This module never invokes a shell directly.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from security_v2.sandbox import CommandSandbox


class Toolchain:
    """Parse model tool blocks and route them through the security boundary."""

    def __init__(self, workspace_dir=".", approval_gateway=None):
        # Keep generated work in a dedicated child of the project workspace.
        # Passing an existing .../workspace path remains backwards-compatible.
        requested = Path(workspace_dir).resolve()
        self.workspace_dir = requested if requested.name.lower() == "workspace" else requested / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.sandbox = CommandSandbox(self.workspace_dir)
        self.approval_gateway = approval_gateway

    def _approve(self, action_type, details, preview):
        """Fail closed when no explicit approval gateway is available."""
        if self.approval_gateway is None:
            return False, "No approval gateway is connected"
        try:
            approved = bool(self.approval_gateway.request_approval(action_type, details, preview))
            return approved, "Approved" if approved else "Denied by approval gateway"
        except Exception as exc:
            return False, f"Approval failed: {type(exc).__name__}: {exc}"

    @staticmethod
    def _format_result(label, result):
        output = str(result.get("output", "") or "").strip()
        error = str(result.get("error", "") or "").strip()
        status = "Success" if result.get("success") else "Failed"
        return f"{label}\n{status}.\nStdout:\n{output}\nStderr:\n{error}".strip()

    def run_bash(self, command, timeout=30):
        approved, reason = self._approve(
            "run_command",
            {"command": str(command)[:300], "risk": "high"},
            f"Run sandboxed command:\n{str(command)[:500]}",
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
            f"Run sandboxed Python code ({len(code)} bytes):\n{code[:500]}",
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
            results.append(f"Bash Command: {command}\nResult: {self.run_bash(command)}")
        for code in python_blocks:
            results.append(f"Python Script Proposed.\nResult: {self.run_python(code)}")
        return results
