"""
Command Sandbox
Allowlist-based command execution
"""

import shlex
import subprocess
import platform
import os
from pathlib import Path


class CommandSandbox:
    """Safe command execution using allowlist."""

    ALLOWED = {
        'ls', 'dir', 'pwd', 'cd', 'cat', 'type', 'head', 'tail',
        'echo', 'date', 'time', 'whoami', 'df', 'free', 'uptime',
        'uname', 'ping', 'nslookup', 'python', 'python3', 'node',
        'git', 'npm', 'pip', 'curl', 'wget',
    }

    FORBIDDEN_CHARS = [';', '&&', '||', '|', '`', '$(', '${', '>', '<']

    def __init__(self):
        self.workspace = Path("workspace").resolve()
        self.workspace.mkdir(exist_ok=True)
        self.log = []

    def validate(self, command):
        """Check if command is safe."""
        if not command or len(command) > 500:
            return False, "Invalid command"

        for char in self.FORBIDDEN_CHARS:
            if char in command:
                return False, f"Forbidden character: {char}"

        try:
            parts = shlex.split(command)
        except ValueError:
            return False, "Invalid syntax"

        base = os.path.basename(parts[0]).lower().replace('.exe', '')
        if base not in self.ALLOWED:
            return False, f"'{base}' not allowed. Allowed: {', '.join(sorted(self.ALLOWED))}"

        return True, "OK"

    def execute(self, command, timeout=30):
        """Execute a validated command."""
        valid, reason = self.validate(command)
        if not valid:
            self.log.append({"cmd": command[:100], "status": "BLOCKED", "reason": reason})
            return {"success": False, "error": f"🚫 {reason}"}

        try:
            parts = shlex.split(command)
            result = subprocess.run(
                parts, capture_output=True, text=True,
                timeout=timeout, cwd=str(self.workspace), shell=False
            )

            output = result.stdout[:5000] if result.stdout else ""
            error = result.stderr[:1000] if result.stderr else ""

            self.log.append({"cmd": command[:100], "status": "OK" if result.returncode == 0 else "FAIL"})

            return {
                "success": result.returncode == 0,
                "output": output,
                "error": error
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}