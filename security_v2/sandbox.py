"""
Command Sandbox

The single execution boundary used by autonomous tools.  It deliberately uses
argument-vector execution (shell=False), a bounded workspace, command
allowlisting, and lightweight Python AST checks before running generated code.
This is a safety boundary, not a replacement for a full OS/container sandbox.
"""

from __future__ import annotations

import ast
import getpass
import ntpath
import os
import shlex
from datetime import datetime
import subprocess
import sys
from pathlib import Path


class CommandSandbox:
    """Allowlist-based command execution inside one workspace."""

    ALLOWED = {
        "ls", "dir", "pwd", "cd", "cat", "type", "head", "tail",
        "echo", "date", "time", "whoami", "df", "free", "uptime",
        "uname", "ping", "nslookup", "python", "python3", "node",
        "git", "npm", "pip", "curl", "wget",
    }

    FORBIDDEN_CHARS = (";", "&&", "||", "|", "`", "$(", "${", ">", "<")
    BLOCKED_PYTHON_IMPORTS = {
        "subprocess", "socket", "ctypes", "winreg", "shutil",
        "multiprocessing", "pty", "telnetlib",
    }
    BLOCKED_PYTHON_CALLS = {"eval", "exec", "compile", "__import__", "breakpoint"}
    BLOCKED_OS_CALLS = {"system", "popen", "spawnl", "spawnlp", "spawnv", "spawnvp"}

    def __init__(self, workspace_dir: str | os.PathLike[str] | None = None):
        base = Path(workspace_dir) if workspace_dir else Path("workspace")
        self.workspace = base.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.log: list[dict[str, str]] = []

    def _within_workspace(self, candidate: str | os.PathLike[str]) -> bool:
        try:
            Path(candidate).resolve().relative_to(self.workspace)
            return True
        except ValueError:
            return False

    @staticmethod
    def _looks_like_absolute_or_escape(value: str) -> bool:
        token = value.strip().strip("'\"")
        if "=" in token and token.startswith("-"):
            token = token.split("=", 1)[1]
        return (
            os.path.isabs(token)
            or ntpath.isabs(token)
            or token in {"..", "~"}
            or token.startswith(("../", "..\\", "~/", "~\\"))
        )

    def validate(self, command: str) -> tuple[bool, str]:
        """Validate a single command before it reaches subprocess."""
        if not command or len(command) > 500:
            return False, "Invalid command"

        for marker in self.FORBIDDEN_CHARS:
            if marker in command:
                return False, f"Forbidden shell operator: {marker}"

        try:
            parts = shlex.split(command, posix=True)
        except ValueError:
            return False, "Invalid command syntax"
        if not parts:
            return False, "Empty command"

        base = os.path.basename(parts[0]).lower().replace(".exe", "")
        if base not in self.ALLOWED:
            return False, f"'{base}' not allowed. Allowed: {', '.join(sorted(self.ALLOWED))}"

        # Do not allow an LLM to smuggle an inline interpreter program through
        # the terminal path; run_python() handles generated code separately.
        if base in {"python", "python3"} and "-c" in parts:
            return False, "Inline Python (-c) is not allowed; use the approved Python tool"

        for token in parts[1:]:
            if self._looks_like_absolute_or_escape(token):
                return False, "Path escapes the sandbox workspace"

        return True, "OK"

    def _builtin_path(self, token: str | None) -> Path | None:
        """Resolve a relative read target while enforcing the workspace boundary."""
        if not token:
            return self.workspace
        candidate = (self.workspace / token).resolve()
        return candidate if self._within_workspace(candidate) else None

    def _execute_builtin(self, parts: list[str]) -> dict[str, object] | None:
        """Run safe shell built-ins without delegating to a command shell."""
        base = os.path.basename(parts[0]).lower().replace(".exe", "")
        if base == "echo":
            return {"success": True, "output": " ".join(parts[1:]) + "\n", "error": "", "returncode": 0}
        if base in {"pwd", "cd"}:
            return {"success": True, "output": str(self.workspace) + "\n", "error": "", "returncode": 0}
        if base in {"date", "time"}:
            value = datetime.now().strftime("%Y-%m-%d" if base == "date" else "%H:%M:%S")
            return {"success": True, "output": value + "\n", "error": "", "returncode": 0}
        if base == "whoami":
            return {"success": True, "output": getpass.getuser() + "\n", "error": "", "returncode": 0}
        if base in {"ls", "dir"}:
            target = self._builtin_path(parts[1] if len(parts) > 1 else None)
            if target is None:
                return {"success": False, "output": "", "error": "Path escapes the sandbox workspace", "returncode": 1}
            if not target.exists():
                return {"success": False, "output": "", "error": "Path does not exist", "returncode": 1}
            if target.is_file():
                listing = target.name
            else:
                listing = "\n".join(sorted(item.name for item in target.iterdir()))
            return {"success": True, "output": listing + ("\n" if listing else ""), "error": "", "returncode": 0}
        if base in {"cat", "type", "head", "tail"}:
            if len(parts) != 2:
                return {"success": False, "output": "", "error": f"{base} requires one workspace-relative file", "returncode": 1}
            target = self._builtin_path(parts[1])
            if target is None or not target.is_file():
                return {"success": False, "output": "", "error": "File is outside the sandbox or does not exist", "returncode": 1}
            text = target.read_text(encoding="utf-8", errors="replace")
            if base == "head":
                text = "\n".join(text.splitlines()[:10]) + ("\n" if text else "")
            elif base == "tail":
                text = "\n".join(text.splitlines()[-10:]) + ("\n" if text else "")
            return {"success": True, "output": text[:5000], "error": "", "returncode": 0}
        return None

    def execute(self, command: str, timeout: int = 30) -> dict[str, object]:
        """Execute one validated argument-vector command with shell disabled."""
        valid, reason = self.validate(command)
        if not valid:
            self.log.append({"cmd": command[:100], "status": "BLOCKED", "reason": reason})
            return {"success": False, "error": f"Blocked: {reason}"}

        try:
            parts = shlex.split(command, posix=True)
            builtin_result = self._execute_builtin(parts)
            if builtin_result is not None:
                self.log.append({
                    "cmd": command[:100],
                    "status": "OK" if builtin_result["success"] else "FAIL",
                    "execution": "internal_builtin",
                })
                return builtin_result

            result = subprocess.run(
                parts,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(self.workspace),
                shell=False,
            )
            output = result.stdout[:5000] if result.stdout else ""
            error = result.stderr[:2000] if result.stderr else ""
            self.log.append({
                "cmd": command[:100],
                "status": "OK" if result.returncode == 0 else "FAIL",
            })
            return {
                "success": result.returncode == 0,
                "output": output,
                "error": error,
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            self.log.append({"cmd": command[:100], "status": "TIMEOUT"})
            return {"success": False, "error": f"Timed out after {timeout}s"}
        except Exception as exc:
            self.log.append({"cmd": command[:100], "status": "ERROR"})
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    def validate_python(self, code: str) -> tuple[bool, str]:
        """Reject common process/network escape primitives before execution."""
        if not code or len(code) > 20000:
            return False, "Python payload is empty or too large"
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            return False, f"Python syntax error: {exc.msg}"

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
                blocked = set(names) & self.BLOCKED_PYTHON_IMPORTS
                if blocked:
                    return False, f"Blocked Python import: {sorted(blocked)[0]}"
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".", 1)[0]
                if root in self.BLOCKED_PYTHON_IMPORTS:
                    return False, f"Blocked Python import: {root}"
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in self.BLOCKED_PYTHON_CALLS:
                    return False, f"Blocked Python call: {node.func.id}"
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr in self.BLOCKED_OS_CALLS
                ):
                    return False, f"Blocked os call: os.{node.func.attr}"

        return True, "OK"

    def execute_python_file(self, script_path: str | os.PathLike[str], timeout: int = 30) -> dict[str, object]:
        """Run an approved script located inside the sandbox workspace."""
        path = Path(script_path).resolve()
        if not self._within_workspace(path) or not path.is_file():
            return {"success": False, "error": "Python script is outside the sandbox workspace"}

        try:
            result = subprocess.run(
                [sys.executable, "-I", path.name],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(self.workspace),
                shell=False,
            )
            return {
                "success": result.returncode == 0,
                "output": (result.stdout or "")[:5000],
                "error": (result.stderr or "")[:2000],
                "returncode": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timed out after {timeout}s"}
        except Exception as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}
