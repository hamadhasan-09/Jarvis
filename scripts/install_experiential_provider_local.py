from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
LLM = ROOT / "llm"
PROVIDER = LLM / "experiential_provider.py"
MANAGER = LLM / "provider_manager.py"
KEY_MANAGER = ROOT / "security_v2" / "key_manager.py"

def backup(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".experiential-backup")
    if path.exists() and not backup_path.exists():
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"BACKUP {backup_path}")

backup(MANAGER)
manager = MANAGER.read_text(encoding="utf-8")

if "from .experiential_provider import ExperientialProvider" not in manager:
    imports = list(re.finditer(r"^from \.[A-Za-z0-9_]+_provider import .+$", manager, re.MULTILINE))
    if not imports:
        raise SystemExit("ERROR: no local provider imports found in provider_manager.py")
    pos = imports[-1].end()
    manager = manager[:pos] + "\nfrom .experiential_provider import ExperientialProvider" + manager[pos:]

if "JARVIS_ENABLE_EXPERIENTIAL" not in manager:
    marker = re.search(r"^    @property\n    def has_primary\(", manager, re.MULTILINE)
    if not marker:
        raise SystemExit("ERROR: could not find ProviderManager.has_primary marker")
    block = """
        if _env_bool("JARVIS_ENABLE_EXPERIENTIAL", False):
            models = {
                os.getenv("JARVIS_EXPERIENTIAL_MODEL", "claude-opus-5.5").strip(),
                os.getenv("JARVIS_PRIMARY_MODEL", "").strip()
                if os.getenv("JARVIS_PRIMARY_PROVIDER", "").strip().lower() == "experiential"
                else "",
                os.getenv("JARVIS_REASONING_MODEL", "").strip()
                if os.getenv("JARVIS_REASONING_PROVIDER", "").strip().lower() == "experiential"
                else "",
                os.getenv("JARVIS_CODING_MODEL", "").strip()
                if os.getenv("JARVIS_CODING_PROVIDER", "").strip().lower() == "experiential"
                else "",
                os.getenv("JARVIS_VERIFICATION_MODEL", "").strip()
                if os.getenv("JARVIS_VERIFICATION_PROVIDER", "").strip().lower() == "experiential"
                else "",
            }
            base_url = os.getenv(
                "EXPERIENTIAL_BASE_URL",
                "https://api.experientiallabs.ai/v1",
            ).strip()
            key = self.keys.get_key("experiential")
            for model in {item for item in models if item}:
                provider = ExperientialProvider(
                    key,
                    model,
                    timeout=self.timeout,
                    base_url=base_url,
                )
                if provider.is_configured:
                    self.providers[("experiential", model)] = provider

"""
    manager = manager[:marker.start()] + block + manager[marker.start():]

MANAGER.write_text(manager, encoding="utf-8")
print("PATCHED local llm/provider_manager.py")

backup(KEY_MANAGER)
key_manager = KEY_MANAGER.read_text(encoding="utf-8")
if '"experiential": "EXPERIENTIAL_API_KEY"' not in key_manager:
    match = re.search(r'^\s*"groq":\s*"GROQ_API_KEY",\s*$', key_manager, re.MULTILINE)
    if match:
        line_end = match.end()
        key_manager = key_manager[:line_end] + '\n            "experiential": "EXPERIENTIAL_API_KEY",' + key_manager[line_end:]
        KEY_MANAGER.write_text(key_manager, encoding="utf-8")
        print("PATCHED local security_v2/key_manager.py")
    else:
        print("SKIPPED key-manager mapping; generic fallback should use EXPERIENTIAL_API_KEY.")

print("DONE")
