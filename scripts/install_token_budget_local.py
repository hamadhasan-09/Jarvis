from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
FILES = [
    ROOT / "llm" / "task_router.py",
    ROOT / "llm" / "provider_manager.py",
    ROOT / "engine" / "executor.py",
    ROOT / "engine" / "planner.py",
    ROOT / "llm" / "token_budget.py",
]

def backup(path: Path) -> None:
    if not path.exists():
        return
    backup_path = path.with_suffix(path.suffix + ".token-budget-backup")
    if not backup_path.exists():
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print("BACKUP", backup_path)

def git_show(path: str) -> str:
    cmd = ["git", "show", f"origin/feature/experiential-opus-provider:{path}"]
    return subprocess.check_output(cmd, cwd=ROOT, text=True, encoding="utf-8")

# 1. Install the standalone budget module from the branch.
budget_path = ROOT / "llm" / "token_budget.py"
backup(budget_path)
budget_path.write_text(git_show("llm/token_budget.py"), encoding="utf-8")
print("INSTALLED llm/token_budget.py")

# 2. Route decisions carry task type without breaking existing positional callers.
router_path = ROOT / "llm" / "task_router.py"
backup(router_path)
router = router_path.read_text(encoding="utf-8")
if "task_type: str = GENERAL" not in router:
    marker = '    reason: str = ""\n'
    if marker not in router:
        raise SystemExit("ERROR: could not find RouteDecision reason field")
    router = router.replace(marker, marker + "    task_type: str = GENERAL\n", 1)

if "task_type=task.task_type" not in router:
    marker = "            reason=reason,\n"
    if marker not in router:
        raise SystemExit("ERROR: could not find RouteDecision return block")
    router = router.replace(marker, marker + "            task_type=task.task_type,\n", 1)
router_path.write_text(router, encoding="utf-8")
print("PATCHED llm/task_router.py")

# 3. Provider manager resolves a budget when callers do not explicitly provide one.
pm_path = ROOT / "llm" / "provider_manager.py"
backup(pm_path)
pm = pm_path.read_text(encoding="utf-8")

if "from .token_budget import budget_for" not in pm:
    anchor = "from .groq_provider import GroqProvider\n"
    if anchor not in pm:
        raise SystemExit("ERROR: could not find Groq provider import")
    pm = pm.replace(anchor, anchor + "from .token_budget import budget_for\n", 1)

# Generate signature + resolver.
if "resolved_max_tokens = int(max_tokens)" not in pm:
    pm = re.sub(
        r"(def generate\([\s\S]*?temperature: float = 0\.65,\n)        max_tokens: int = 600,",
        lambda m: m.group(1) + "        max_tokens: int | None = None,",
        pm,
        count=1,
    )
    marker = '        """Try the selected provider, then the explicitly configured fallback."""\n'
    if marker not in pm:
        raise SystemExit("ERROR: generate docstring marker not found")
    pm = pm.replace(
        marker,
        marker + '        resolved_max_tokens = int(max_tokens) if max_tokens is not None else budget_for(getattr(route, "task_type", "GENERAL"))\n',
        1,
    )

# First provider.generate call in generate().
pm = pm.replace(
    "                        max_tokens=max_tokens,\n                        timeout=call_timeout,",
    "                        max_tokens=resolved_max_tokens,\n                        timeout=call_timeout,",
    1,
)

# Stream signature + resolver.
if "resolved_max_tokens = int(max_tokens) if max_tokens is not None else budget_for" not in pm.split("def stream_generate",1)[1]:
    tail = pm.split("def stream_generate",1)
    head, rest = tail[0], tail[1]
    rest = rest.replace("        max_tokens: int = 600,", "        max_tokens: int | None = None,", 1)
    marker2 = "        call_timeout = max(1.0, float(timeout or self.timeout))\n"
    if marker2 not in rest:
        raise SystemExit("ERROR: stream timeout marker not found")
    rest = rest.replace(
        marker2,
        marker2 + '        resolved_max_tokens = int(max_tokens) if max_tokens is not None else budget_for(getattr(route, "task_type", "GENERAL"))\n',
        1,
    )
    rest = rest.replace("max_tokens=max_tokens,", "max_tokens=resolved_max_tokens,", 2)
    pm = head + "def stream_generate" + rest

pm_path.write_text(pm, encoding="utf-8")
print("PATCHED llm/provider_manager.py")

# 4. Stop planner/executor from imposing their own lower ceiling.
for path, old in [
    (ROOT / "engine" / "planner.py", "                temperature=0.3\n"),
    (ROOT / "engine" / "executor.py", "                temperature=0.4,\n                max_tokens=2000\n"),
]:
    backup(path)
    value = path.read_text(encoding="utf-8")
    if path.name == "planner.py":
        value = value.replace(old, "                temperature=0.3,\n                max_tokens=None\n", 1)
    else:
        value = value.replace(old, "                temperature=0.4,\n                max_tokens=None\n", 1)
    path.write_text(value, encoding="utf-8")
    print("PATCHED", path)

print("DONE")
