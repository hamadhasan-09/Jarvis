"""The single execution boundary used by autonomous tools.

This is intentionally a narrow policy boundary, not a claim of OS/container
isolation. LLM output cannot select an arbitrary executable, shell, network
client, filesystem path, or process primitive.
"""

from __future__ import annotations

import ast
import getpass
import ntpath
import os
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any


class CommandSandbox:
    """Run only approved internal commands inside one workspace."""

    # These commands are implemented internally below. No arbitrary executable
    # is resolved from PATH, so Node/Python aliases/curl/wget/git/npm/pip and
    # equivalent process or network escapes are not available through `execute`.
    ALLOWED = {
        "ls", "dir", "pwd", "cd", "cat", "type", "head", "tail", "echo",
        "date", "time", "whoami",
    }
    FORBIDDEN_CHARS = (";", "&&", "||", "|", "`", "$(", "${", ">", "<")
    SAFE_PYTHON_CALLS = {"print", "len", "str", "int", "float", "bool", "sorted"}
    SAFE_PYTHON_NAMES = SAFE_PYTHON_CALLS | {"True", "False", "None"}

    def __init__(self, workspace_dir: str | os.PathLike[str] | None = None):
        base = Path(workspace_dir) if workspace_dir else Path("workspace")
        self.workspace = base.resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.log: list[dict[str, str]] = []

    def _within_workspace(self, candidate: str | os.PathLike[str]) -> bool:
        try:
            Path(candidate).resolve().relative_to(self.workspace)
            return True
        except (ValueError, OSError):
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
        """Validate a single internal command; never interpret shell syntax."""
        if not command or len(command) > 500:
            return False, "Invalid command"
        for marker in self.FORBIDDEN_CHARS:
            if marker in command:
                return False, f"Forbidden shell operator: {marker}"
        try:
            parts = shlex.split(str(command), posix=True)
        except ValueError:
            return False, "Invalid command syntax"
        if not parts:
            return False, "Empty command"

        base = os.path.basename(parts[0]).lower().replace(".exe", "")
        if base not in self.ALLOWED:
            return False, f"'{base}' is not an approved internal command"
        for token in parts[1:]:
            if self._looks_like_absolute_or_escape(token):
                return False, "Path escapes the sandbox workspace"
        if base in {"pwd", "whoami", "date", "time"} and len(parts) != 1:
            return False, f"{base} does not accept arguments"
        if base in {"cd"} and len(parts) > 2:
            return False, "cd accepts at most one workspace-relative path"
        if base in {"cat", "type", "head", "tail"} and len(parts) != 2:
            return False, f"{base} requires one workspace-relative file"
        if base in {"ls", "dir"} and len(parts) > 2:
            return False, f"{base} accepts at most one workspace-relative path"
        return True, "OK"

    def _builtin_path(self, token: str | None) -> Path | None:
        if not token:
            return self.workspace
        candidate = (self.workspace / token).resolve()
        return candidate if self._within_workspace(candidate) else None

    def _execute_builtin(self, parts: list[str]) -> dict[str, object] | None:
        base = os.path.basename(parts[0]).lower().replace(".exe", "")
        if base == "echo":
            return {"success": True, "output": " ".join(parts[1:]) + "\n", "error": "", "returncode": 0}
        if base in {"pwd", "cd"}:
            if base == "cd" and len(parts) == 2:
                target = self._builtin_path(parts[1])
                if target is None or not target.is_dir():
                    return {"success": False, "output": "", "error": "Workspace directory does not exist", "returncode": 1}
            return {"success": True, "output": str(self.workspace) + "\n", "error": "", "returncode": 0}
        if base in {"date", "time"}:
            value = datetime.now().strftime("%Y-%m-%d" if base == "date" else "%H:%M:%S")
            return {"success": True, "output": value + "\n", "error": "", "returncode": 0}
        if base == "whoami":
            return {"success": True, "output": getpass.getuser() + "\n", "error": "", "returncode": 0}
        if base in {"ls", "dir"}:
            target = self._builtin_path(parts[1] if len(parts) > 1 else None)
            if target is None or not target.exists():
                return {"success": False, "output": "", "error": "Path does not exist inside workspace", "returncode": 1}
            listing = target.name if target.is_file() else "\n".join(sorted(item.name for item in target.iterdir()))
            return {"success": True, "output": listing + ("\n" if listing else ""), "error": "", "returncode": 0}
        if base in {"cat", "type", "head", "tail"}:
            target = self._builtin_path(parts[1])
            if target is None or not target.is_file():
                return {"success": False, "output": "", "error": "File is outside the workspace or does not exist", "returncode": 1}
            text = target.read_text(encoding="utf-8", errors="replace")
            if base == "head":
                text = "\n".join(text.splitlines()[:10]) + ("\n" if text else "")
            elif base == "tail":
                text = "\n".join(text.splitlines()[-10:]) + ("\n" if text else "")
            return {"success": True, "output": text[:5000], "error": "", "returncode": 0}
        return None

    def execute(self, command: str, timeout: int = 30) -> dict[str, object]:
        """Execute only the internal allowlisted command set."""
        valid, reason = self.validate(command)
        if not valid:
            self.log.append({"cmd": str(command)[:100], "status": "BLOCKED", "reason": reason})
            return {"success": False, "error": f"Blocked: {reason}"}
        try:
            parts = shlex.split(str(command), posix=True)
            result = self._execute_builtin(parts)
            if result is None:
                return {"success": False, "error": "No internal implementation for command"}
            self.log.append({"cmd": str(command)[:100], "status": "OK" if result["success"] else "FAIL", "execution": "internal_builtin"})
            return result
        except Exception as exc:
            self.log.append({"cmd": str(command)[:100], "status": "ERROR"})
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    def validate_python(self, code: str) -> tuple[bool, str]:
        """Allow only pure, small expressions with no imports or attributes.

        This permits a tiny formatting/calculation helper but not filesystem,
        network, subprocess, dynamic import, or arbitrary process execution.
        """
        if not code or len(code) > 20000:
            return False, "Python payload is empty or too large"
        try:
            tree = ast.parse(str(code), mode="exec")
        except SyntaxError as exc:
            return False, f"Python syntax error: {exc.msg}"
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom, ast.Attribute, ast.Lambda, ast.FunctionDef, ast.ClassDef,
                                 ast.AsyncFunctionDef, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith,
                                 ast.Try, ast.Raise, ast.Delete, ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom,
                                 ast.Await, ast.If, ast.IfExp, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                return False, f"Blocked Python construct: {type(node).__name__}"
            if isinstance(node, ast.Name) and node.id not in self.SAFE_PYTHON_NAMES:
                return False, f"Blocked Python name: {node.id}"
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in self.SAFE_PYTHON_CALLS:
                    return False, "Only approved pure Python calls are allowed"
            if isinstance(node, ast.Assign):
                if any(not isinstance(target, ast.Name) for target in node.targets):
                    return False, "Only local scalar assignment is allowed"
        return True, "OK"

    def execute_python_file(self, script_path: str | os.PathLike[str], timeout: int = 30) -> dict[str, object]:
        """Refuse arbitrary generated scripts; pure Python is not executed here.

        The autonomous tool path should use explicit file/tool APIs instead of
        turning model-generated code into a general interpreter process.
        """
        path = Path(script_path).resolve()
        if not self._within_workspace(path) or not path.is_file():
            return {"success": False, "error": "Python script is outside the sandbox workspace"}
        return {"success": False, "error": "Generated Python execution is disabled by the sandbox policy"}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "allowed_commands": sorted(self.ALLOWED),
            "python_execution": "disabled_for_generated_code",
            "network_access": "disabled",
        }
