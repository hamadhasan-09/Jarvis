from __future__ import annotations

from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
LLM = ROOT / "llm"
PROVIDER = LLM / "experiential_provider.py"
MANAGER = LLM / "provider_manager.py"
KEY_MANAGER = ROOT / "security_v2" / "key_manager.py"

RAW = "https://raw.githubusercontent.com/hamadhasan-09/Jarvis/feature/experiential-opus-provider/"

def backup(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".experiential-backup")
    if path.exists() and not backup_path.exists():
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"BACKUP {backup_path}")

def fetch(path: str) -> str:
    with urlopen(RAW + path, timeout=20) as response:
        return response.read().decode("utf-8")

backup(PROVIDER)
PROVIDER.parent.mkdir(parents=True, exist_ok=True)
PROVIDER.write_text(fetch("llm/experiential_provider.py"), encoding="utf-8")
print("INSTALLED llm/experiential_provider.py")

backup(MANAGER)
manager = MANAGER.read_text(encoding="utf-8")

if "from .experiential_provider import ExperientialProvider" not in manager:
    anchor = "from .anthropic_provider import AnthropicProvider\n"
    if anchor not in manager:
        raise SystemExit("ERROR: Could not find Anthropic provider import in local llm/provider_manager.py")
    manager = manager.replace(
        anchor,
        anchor + "from .experiential_provider import ExperientialProvider\n",
        1,
    )

if "JARVIS_ENABLE_EXPERIENTIAL" not in manager:
    anchor = '''        if _env_bool("JARVIS_ENABLE_ANTHROPIC", False):
            model = os.getenv("JARVIS_ANTHROPIC_MODEL", "claude-3-5-haiku-latest").strip()
            provider = AnthropicProvider(self.keys.get_key("anthropic"), model, timeout=self.timeout)
            if provider.is_configured:
                self.providers[("anthropic", model)] = provider
'''
    block = anchor + '''
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
'''
    if anchor not in manager:
        raise SystemExit("ERROR: Could not find Anthropic provider block in local llm/provider_manager.py")
    manager = manager.replace(anchor, block, 1)

MANAGER.write_text(manager, encoding="utf-8")
print("PATCHED local llm/provider_manager.py")

backup(KEY_MANAGER)
key_manager = KEY_MANAGER.read_text(encoding="utf-8")

if '"experiential": "EXPERIENTIAL_API_KEY"' not in key_manager:
    anchor = '            "groq": "GROQ_API_KEY",\n'
    if anchor not in key_manager:
        raise SystemExit("ERROR: Could not find Groq key mapping in local security_v2/key_manager.py")
    key_manager = key_manager.replace(
        anchor,
        anchor + '            "experiential": "EXPERIENTIAL_API_KEY",\n',
        1,
    )

KEY_MANAGER.write_text(key_manager, encoding="utf-8")
print("PATCHED local security_v2/key_manager.py")
print("DONE")
