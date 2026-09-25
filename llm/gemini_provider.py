"""Optional Google Gemini provider adapter.

The import is lazy so a missing Gemini package or key never breaks Groq-only
startup.
"""

from __future__ import annotations

from typing import Any, Iterable

from .base_provider import BaseProvider, ProviderError, ProviderResponse, ProviderUnavailable


class GeminiProvider(BaseProvider):
    provider_name = "gemini"

    def __init__(self, api_key: str | None, model: str, timeout: float = 18.0):
        super().__init__(model=model, timeout=timeout)
        self._api_key = (api_key or "").strip()
        self._genai = None
        if self._api_key:
            try:
                import google.generativeai as genai

                genai.configure(api_key=self._api_key)
                self._genai = genai
            except Exception:
                self._genai = None

    @property
    def is_configured(self) -> bool:
        return self._genai is not None and bool(self._api_key)

    @staticmethod
    def _prompt(messages: Iterable[dict[str, str]], system_prompt: str | None) -> str:
        lines = []
        if system_prompt:
            lines.append("SYSTEM:\n" + system_prompt)
        for item in messages:
            lines.append(f"{item.get('role', 'user').upper()}:\n{item.get('content', '')}")
        return "\n\n".join(lines)

    def generate(
        self,
        messages: Iterable[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        if not self.is_configured:
            raise ProviderUnavailable(self.provider_name, "Gemini is not configured")
        try:
            model = self._genai.GenerativeModel(self.model)
            generation_config = {
                "temperature": 0.65 if temperature is None else float(temperature),
                "max_output_tokens": 600 if max_tokens is None else int(max_tokens),
            }
            response = model.generate_content(
                self._prompt(messages, system_prompt),
                generation_config=generation_config,
                request_options={"timeout": max(1.0, float(timeout or self.timeout))},
            )
            text = getattr(response, "text", "") or ""
            if not text.strip():
                raise ProviderError(self.provider_name, "provider returned an empty response")
            return ProviderResponse(text=text, provider=self.provider_name, model=self.model, metadata={"request": metadata or {}})
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.provider_name, type(exc).__name__ + ": " + str(exc)) from exc
