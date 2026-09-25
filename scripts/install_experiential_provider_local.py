from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LLM = ROOT / "llm"
PROVIDER = LLM / "experiential_provider.py"
MANAGER = LLM / "provider_manager.py"
KEY_MANAGER = ROOT / "security_v2" / "key_manager.py"

PROVIDER_CODE = "\"\"\"Experiential Labs OpenAI-compatible provider for JARVIS.\n\nThe Experiential Labs gateway exposes an OpenAI-compatible /v1/chat/completions API.\n\"\"\"\n\nfrom __future__ import annotations\n\nimport json\nfrom typing import Any, Iterable, Iterator\n\nimport httpx\n\nfrom .base_provider import (\n    BaseProvider,\n    ProviderError,\n    ProviderResponse,\n    ProviderUnavailable,\n    sanitize_error,\n)\n\n\nclass ExperientialProvider(BaseProvider):\n    provider_name = \"experiential\"\n\n    def __init__(\n        self,\n        api_key: str | None,\n        model: str,\n        timeout: float = 18.0,\n        base_url: str = \"https://api.experientiallabs.ai/v1\",\n    ):\n        super().__init__(model=model, timeout=timeout)\n        self._api_key = (api_key or \"\").strip()\n        self.base_url = (base_url or \"\").strip().rstrip(\"/\")\n        self._headers = {\n            \"Authorization\": f\"Bearer {self._api_key}\",\n            \"Content-Type\": \"application/json\",\n            \"Accept\": \"application/json\",\n        }\n\n    @property\n    def is_configured(self) -> bool:\n        return bool(self._api_key and self.base_url)\n\n    def _payload(\n        self,\n        messages: Iterable[dict[str, str]],\n        system_prompt: str | None,\n        temperature: float | None,\n        max_tokens: int | None,\n        stream: bool = False,\n    ) -> dict[str, Any]:\n        payload = [dict(item) for item in messages]\n\n        if system_prompt:\n            if payload and payload[0].get(\"role\") == \"system\":\n                payload[0][\"content\"] = system_prompt\n            else:\n                payload.insert(0, {\"role\": \"system\", \"content\": system_prompt})\n\n        return {\n            \"model\": self.model,\n            \"messages\": payload,\n            \"temperature\": 0.65 if temperature is None else float(temperature),\n            \"max_tokens\": 600 if max_tokens is None else int(max_tokens),\n            \"stream\": bool(stream),\n        }\n\n    @staticmethod\n    def _extract_response(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:\n        choices = data.get(\"choices\") or []\n        if not choices:\n            raise ProviderError(\"experiential\", \"provider returned no choices\")\n\n        message = choices[0].get(\"message\") or {}\n        content = message.get(\"content\") or \"\"\n\n        if isinstance(content, list):\n            content = \"\".join(\n                item.get(\"text\", \"\")\n                for item in content\n                if isinstance(item, dict)\n            )\n\n        text = str(content)\n        if not text.strip():\n            raise ProviderError(\"experiential\", \"provider returned an empty response\")\n\n        metadata: dict[str, Any] = {}\n        if isinstance(data.get(\"usage\"), dict):\n            metadata[\"usage\"] = data[\"usage\"]\n        if \"cost\" in data:\n            metadata[\"cost\"] = data[\"cost\"]\n        if \"provider\" in data:\n            metadata[\"upstream_provider\"] = data[\"provider\"]\n        if \"id\" in data:\n            metadata[\"request_id\"] = data[\"id\"]\n\n        return text, metadata\n\n    def _raise_http_error(self, response: httpx.Response) -> None:\n        detail = response.text[:500]\n        status = response.status_code\n        retryable = status in {408, 409, 429} or status >= 500\n        raise ProviderError(\n            self.provider_name,\n            f\"HTTP {status}: {detail}\",\n            retryable=retryable,\n        )\n\n    def generate(\n        self,\n        messages: Iterable[dict[str, str]],\n        system_prompt: str | None = None,\n        temperature: float | None = None,\n        max_tokens: int | None = None,\n        timeout: float | None = None,\n        metadata: dict[str, Any] | None = None,\n    ) -> ProviderResponse:\n        if not self.is_configured:\n            raise ProviderUnavailable(\n                self.provider_name,\n                \"Experiential Labs is not configured\",\n            )\n\n        request_timeout = max(1.0, float(timeout or self.timeout))\n        payload = self._payload(\n            messages, system_prompt, temperature, max_tokens, stream=False\n        )\n        url = f\"{self.base_url}/chat/completions\"\n\n        try:\n            with httpx.Client(timeout=request_timeout) as client:\n                response = client.post(\n                    url,\n                    headers=self._headers,\n                    json=payload,\n                )\n\n            if response.status_code >= 400:\n                self._raise_http_error(response)\n\n            data = response.json()\n            text, response_metadata = self._extract_response(data)\n            response_metadata[\"request\"] = metadata or {}\n\n            return ProviderResponse(\n                text=text,\n                provider=self.provider_name,\n                model=self.model,\n                metadata=response_metadata,\n            )\n        except ProviderError:\n            raise\n        except httpx.TimeoutException as exc:\n            raise ProviderError(\n                self.provider_name,\n                f\"request timeout after {request_timeout:.1f}s\",\n            ) from exc\n        except Exception as exc:\n            raise ProviderError(\n                self.provider_name,\n                type(exc).__name__ + \": \" + sanitize_error(exc),\n            ) from exc\n\n    def stream(\n        self,\n        messages: Iterable[dict[str, str]],\n        system_prompt: str | None = None,\n        temperature: float | None = None,\n        max_tokens: int | None = None,\n        timeout: float | None = None,\n        metadata: dict[str, Any] | None = None,\n    ) -> Iterator[str]:\n        if not self.is_configured:\n            raise ProviderUnavailable(\n                self.provider_name,\n                \"Experiential Labs is not configured\",\n            )\n\n        request_timeout = max(1.0, float(timeout or self.timeout))\n        payload = self._payload(\n            messages, system_prompt, temperature, max_tokens, stream=True\n        )\n        url = f\"{self.base_url}/chat/completions\"\n\n        try:\n            with httpx.Client(timeout=request_timeout) as client:\n                with client.stream(\n                    \"POST\",\n                    url,\n                    headers={**self._headers, \"Accept\": \"text/event-stream\"},\n                    json=payload,\n                ) as response:\n                    if response.status_code >= 400:\n                        self._raise_http_error(response)\n\n                    for line in response.iter_lines():\n                        if not line:\n                            continue\n\n                        if isinstance(line, bytes):\n                            line = line.decode(\"utf-8\", errors=\"replace\")\n\n                        if not line.startswith(\"data:\"):\n                            continue\n\n                        chunk = line[5:].strip()\n                        if chunk == \"[DONE]\":\n                            return\n\n                        try:\n                            data = json.loads(chunk)\n                        except json.JSONDecodeError:\n                            continue\n\n                        choices = data.get(\"choices\") or []\n                        if not choices:\n                            continue\n\n                        delta = (choices[0].get(\"delta\") or {}).get(\"content\") or \"\"\n                        if isinstance(delta, str) and delta:\n                            yield delta\n\n        except ProviderError:\n            raise\n        except httpx.TimeoutException as exc:\n            raise ProviderError(\n                self.provider_name,\n                f\"stream timeout after {request_timeout:.1f}s\",\n            ) from exc\n        except Exception as exc:\n            raise ProviderError(\n                self.provider_name,\n                type(exc).__name__ + \": \" + sanitize_error(exc),\n            ) from exc\n"

def backup(path: Path) -> None:
    backup_path = path.with_suffix(path.suffix + ".experiential-backup")
    if path.exists() and not backup_path.exists():
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"BACKUP {backup_path}")

backup(PROVIDER)
PROVIDER.parent.mkdir(parents=True, exist_ok=True)
PROVIDER.write_text(PROVIDER_CODE, encoding="utf-8")
print("INSTALLED llm/experiential_provider.py")

backup(MANAGER)
manager = MANAGER.read_text(encoding="utf-8")

if "from .experiential_provider import ExperientialProvider" not in manager:
    anchor = "from .anthropic_provider import AnthropicProvider\\n"
    if anchor not in manager:
        raise SystemExit("ERROR: local provider_manager.py layout is different; no changes made to it.")
    manager = manager.replace(
        anchor,
        anchor + "from .experiential_provider import ExperientialProvider\\n",
        1,
    )

if "JARVIS_ENABLE_EXPERIENTIAL" not in manager:
    anchor = '''        if _env_bool("JARVIS_ENABLE_ANTHROPIC", False):
            model = os.getenv("JARVIS_ANTHROPIC_MODEL", "claude-3-5-haiku-latest").strip()
            provider = AnthropicProvider(self.keys.get_key("anthropic"), model, timeout=self.timeout)
            if provider.is_configured:
                self.providers[("anthropic", model)] = provider
'''
    block = anchor + '''        if _env_bool("JARVIS_ENABLE_EXPERIENTIAL", False):
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
        raise SystemExit("ERROR: local provider_manager.py layout is different; no changes made to it.")
    manager = manager.replace(anchor, block, 1)

MANAGER.write_text(manager, encoding="utf-8")
print("PATCHED local llm/provider_manager.py")

backup(KEY_MANAGER)
key_manager = KEY_MANAGER.read_text(encoding="utf-8")
if '"experiential": "EXPERIENTIAL_API_KEY"' not in key_manager:
    anchor = '            "groq": "GROQ_API_KEY",\\n'
    if anchor in key_manager:
        key_manager = key_manager.replace(
            anchor,
            anchor + '            "experiential": "EXPERIENTIAL_API_KEY",\\n',
            1,
        )
        KEY_MANAGER.write_text(key_manager, encoding="utf-8")
        print("PATCHED local security_v2/key_manager.py")
    else:
        print("SKIPPED key-manager mapping: local manager already supports generic EXPERIENTIAL_API_KEY fallback.")
else:
    print("Key-manager mapping already present.")

print("DONE")
