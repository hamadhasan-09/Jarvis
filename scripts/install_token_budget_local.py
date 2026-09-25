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

def patch_provider_manager() -> None:
    backup(PM)
    s = PM.read_text(encoding="utf-8")
    if "from .token_budget import budget_for" not in s:
        m = re.search(r"^from \\.[A-Za-z0-9_]+_provider import .+$", s, re.MULTILINE)
        if not m:
            raise SystemExit("ERROR: provider_manager.py has no provider import section")
        s = s[:m.end()] + "\nfrom .token_budget import budget_for" + s[m.end():]

    g = re.search(r"(?ms)^    def generate\(.*?(?=^    def stream_generate\()", s)
    if not g:
        raise SystemExit("ERROR: generate() block not found")
    generate = g.group(0)

    generate = generate.replace(
        "max_tokens: int = 600,",
        "max_tokens: int | None = None,",
        1,
    )
    if "resolved_max_tokens =" not in generate:
        marker = re.search(r"(?m)^        candidates = \[$", generate)
        if not marker:
            raise SystemExit("ERROR: generate() candidate list not found")
        insert = (
            "        resolved_max_tokens = int(max_tokens) if max_tokens is not None "
            'else budget_for(getattr(route, "task_type", "GENERAL"))\n'
        )
        generate = generate[:marker.start()] + insert + generate[marker.start():]
    if "max_tokens=resolved_max_tokens" not in generate:
        generate = generate.replace("max_tokens=max_tokens,", "max_tokens=resolved_max_tokens,", 1)

    s = s[:g.start()] + generate + s[g.end():]

    st = re.search(r"(?ms)^    def stream_generate\(.*?(?=^    @staticmethod\n    def _call_with_deadline)", s)
    if not st:
        raise SystemExit("ERROR: stream_generate() block not found")
    stream = st.group(0)

    stream = stream.replace(
        "max_tokens: int = 600,",
        "max_tokens: int | None = None,",
        1,
    )
    if "resolved_max_tokens =" not in stream:
        marker = re.search(r"(?m)^        candidates = \[$", stream)
        if not marker:
            raise SystemExit("ERROR: stream candidate list not found")
        insert = (
            "        resolved_max_tokens = int(max_tokens) if max_tokens is not None "
            'else budget_for(getattr(route, "task_type", "GENERAL"))\n'
        )
        stream = stream[:marker.start()] + insert + stream[marker.start():]
    stream = stream.replace("max_tokens=max_tokens,", "max_tokens=resolved_max_tokens,")

    s = s[:st.start()] + stream + s[st.end():]
    PM.write_text(s, encoding="utf-8")
    print("PATCHED llm/provider_manager.py")

def patch_simple(path: Path, replacements: list[tuple[str, str]]) -> None:
    backup(path)
    s = path.read_text(encoding="utf-8")
    changed = False
    for old, new in replacements:
        if old in s and new not in s:
            s = s.replace(old, new, 1)
            changed = True
    if changed:
        path.write_text(s, encoding="utf-8")
        print("PATCHED", path)
    else:
        print("UNCHANGED", path)

patch_provider_manager()
patch_simple(
    PLANNER,
    [("                temperature=0.3\n", "                temperature=0.3,\n                max_tokens=None\n")],
)
patch_simple(
    EXECUTOR,
    [("                temperature=0.4,\n                max_tokens=2000\n", "                temperature=0.4,\n                max_tokens=None\n")],
)
print("DONE")
