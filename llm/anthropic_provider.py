"""Optional Anthropic provider adapter with lazy dependency loading."""

from __future__ import annotations

from typing import Any, Iterable

from .base_provider import BaseProvider, ProviderError, ProviderResponse, ProviderUnavailable


class AnthropicProvider(BaseProvider):
    provider_name = "anthropic"

    def __init__(self, api_key: str | None, model: str, timeout: float = 18.0):
        super().__init__(model=model, timeout=timeout)
        self._api_key = (api_key or "").strip()
        self.client = None
        if self._api_key:
            try:
                import anthropic

                self.client = anthropic.Anthropic(api_key=self._api_key)
            except Exception:
                self.client = None

    @property
    def is_configured(self) -> bool:
        return self.client is not None and bool(self._api_key)

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
            raise ProviderUnavailable(self.provider_name, "Anthropic is not configured")
        payload = [dict(item) for item in messages if item.get("role") != "system"]
        try:
            response = self.client.messages.create(
                model=self.model,
                system=system_prompt or "You are JARVIS.",
                messages=payload,
                max_tokens=600 if max_tokens is None else int(max_tokens),
                temperature=0.65 if temperature is None else float(temperature),
                timeout=max(1.0, float(timeout or self.timeout)),
            )
            text = ""
            for block in getattr(response, "content", []) or []:
                if getattr(block, "type", "") == "text":
                    text += getattr(block, "text", "") or ""
            if not text.strip():
                raise ProviderError(self.provider_name, "provider returned an empty response")
            return ProviderResponse(text=text, provider=self.provider_name, model=self.model, metadata={"request": metadata or {}})
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.provider_name, type(exc).__name__ + ": " + str(exc)) from exc
