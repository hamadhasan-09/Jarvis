"""
Secure API Key Manager
Loads keys from .env file and optionally from OS keychain
"""

import os
from pathlib import Path
from dotenv import load_dotenv


class SecureKeyManager:
    """Manages API keys securely."""

    def __init__(self):
        load_dotenv()
        self._cache = {}

        # Key mapping: service name -> env variable
        self._key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "groq": "GROQ_API_KEY",
        }

    def get_key(self, service):
        """Get an API key for a service."""
        if service in self._cache:
            return self._cache[service]

        # Try OS keychain first
        key = self._try_keychain(service)

        # Fall back to .env
        if not key:
            env_var = self._key_map.get(service, f"{service.upper()}_API_KEY")
            key = os.getenv(env_var, "")

        if key and key.strip():
            self._cache[service] = key.strip()
            return key.strip()

        return None

    def _try_keychain(self, service):
        """Try to get key from OS keychain."""
        try:
            import keyring
            return keyring.get_password("JARVIS", service)
        except Exception:
            return None

    def store_key(self, service, key):
        """Store a key in OS keychain."""
        try:
            import keyring
            keyring.set_password("JARVIS", service, key)
            self._cache[service] = key
            print(f"✅ {service} key stored in OS keychain")
            return True
        except Exception:
            print(f"⚠️ Keychain not available. Add key to .env file instead.")
            return False

    def list_available(self):
        """List which services have keys configured."""
        available = []
        for service in self._key_map:
            if self.get_key(service):
                available.append(service)
        return available

    def setup_wizard(self):
        """Interactive key setup."""
        print("\n🔐 API Key Setup")
        print("=" * 40)
        print("You need at least ONE key.\n")

        import getpass

        for service, env_var in self._key_map.items():
            existing = self.get_key(service)
            if existing:
                print(f"  ✅ {service}: {existing[:8]}...{existing[-4:]}")
                update = input(f"     Update? (y/N): ").strip().lower()
                if update != 'y':
                    continue

            print(f"\n  Get {service} key from:")
            urls = {
                "openai": "https://platform.openai.com/api-keys",
                "groq": "https://console.groq.com/keys (FREE!)",
                "google": "https://aistudio.google.com/apikey",
                "anthropic": "https://console.anthropic.com/"
            }
            print(f"  {urls.get(service, 'provider website')}")

            key = getpass.getpass(f"  Enter {service} key (or Enter to skip): ")
            if key.strip():
                self.store_key(service, key.strip())

        available = self.list_available()
        print(f"\n✅ {len(available)} service(s) configured: {', '.join(available)}")