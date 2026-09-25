from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
PM = ROOT / "llm" / "provider_manager.py"
PLANNER = ROOT / "engine" / "planner.py"
EXECUTOR = ROOT / "engine" / "executor.py"

def backup(path: Path) -> None:
    if not path.exists():
        return
    b = path.with_suffix(path.suffix + ".token-budget-backup")
    if not b.exists():
        b.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print("BACKUP", b)

backup(PM)
s = PM.read_text(encoding="utf-8")

# Import token budget using the stable stdlib import section.
if "from .token_budget import budget_for" not in s:
    anchor = "import os\n"
    if anchor not in s:
        raise SystemExit("ERROR: provider_manager.py has no 'import os' anchor")
    s = s.replace(anchor, anchor + "from .token_budget import budget_for\n", 1)

# Patch generate().
g = re.search(r"(?ms)^    def generate\(.*?(?=^    def stream_generate\()", s)
if not g:
    raise SystemExit("ERROR: generate() not found")
generate = g.group(0)

generate = generate.replace("max_tokens: int = 600,", "max_tokens: int | None = None,", 1)

if "resolved_max_tokens =" not in generate:
    marker = re.search(r"(?m)^        candidates = \[$", generate)
    if not marker:
        raise SystemExit("ERROR: generate() candidates marker not found")
    line = '        resolved_max_tokens = int(max_tokens) if max_tokens is not None else budget_for(getattr(route, "task_type", "GENERAL"))\n'
    generate = generate[:marker.start()] + line + generate[marker.start():]

generate = generate.replace("max_tokens=max_tokens,", "max_tokens=resolved_max_tokens,", 1)
s = s[:g.start()] + generate + s[g.end():]

# Patch stream_generate().
st = re.search(r"(?ms)^    def stream_generate\(.*?(?=^    @staticmethod\n    def _call_with_deadline)", s)
if not st:
    raise SystemExit("ERROR: stream_generate() not found")
stream = st.group(0)

stream = stream.replace("max_tokens: int = 600,", "max_tokens: int | None = None,", 1)

if "resolved_max_tokens =" not in stream:
    marker = re.search(r"(?m)^        candidates = \[$", stream)
    if not marker:
        raise SystemExit("ERROR: stream_generate() candidates marker not found")
    line = '        resolved_max_tokens = int(max_tokens) if max_tokens is not None else budget_for(getattr(route, "task_type", "GENERAL"))\n'
    stream = stream[:marker.start()] + line + stream[marker.start():]

stream = stream.replace("max_tokens=max_tokens,", "max_tokens=resolved_max_tokens,")

s = s[:st.start()] + stream + s[st.end():]
PM.write_text(s, encoding="utf-8")
print("PATCHED llm/provider_manager.py")

for path, old, new in [
    (PLANNER, "                temperature=0.3\n", "                temperature=0.3,\n                max_tokens=None\n"),
    (EXECUTOR, "                temperature=0.4,\n                max_tokens=2000\n", "                temperature=0.4,\n                max_tokens=None\n"),
]:
    backup(path)
    value = path.read_text(encoding="utf-8")
    if old in value:
        path.write_text(value.replace(old, new, 1), encoding="utf-8")
        print("PATCHED", path)
    else:
        print("UNCHANGED", path)

print("DONE")
