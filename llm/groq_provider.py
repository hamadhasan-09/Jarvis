"""Groq provider adapter used by the live JARVIS route."""

from __future__ import annotations

import threading
from typing import Any, Iterable, Iterator

from .base_provider import BaseProvider, ProviderError, ProviderResponse, ProviderUnavailable


class GroqProvider(BaseProvider):
    provider_name = "groq"

    def __init__(self, api_key: str | None, model: str, timeout: float = 18.0):
        super().__init__(model=model, timeout=timeout)
        self._api_key = (api_key or "").strip()
        self.client = None
        if self._api_key:
            try:
                from groq import Groq

                self.client = Groq(api_key=self._api_key)
            except Exception:
                # The provider remains unavailable; optional provider failures
                # must not prevent the HUD from starting.
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
            raise ProviderUnavailable(self.provider_name, "Groq is not configured")

        payload = [dict(item) for item in messages]
        if system_prompt:
            if payload and payload[0].get("role") == "system":
                payload[0]["content"] = system_prompt
            else:
                payload.insert(0, {"role": "system", "content": system_prompt})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=payload,
                temperature=0.65 if temperature is None else float(temperature),
                max_tokens=600 if max_tokens is None else int(max_tokens),
                timeout=max(1.0, float(timeout or self.timeout)),
            )
            text = ""
            if getattr(response, "choices", None):
                text = getattr(response.choices[0].message, "content", "") or ""
            if not str(text).strip():
                raise ProviderError(self.provider_name, "provider returned an empty response")
            return ProviderResponse(
                text=str(text),
                provider=self.provider_name,
                model=self.model,
                metadata={"request": metadata or {}},
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.provider_name, type(exc).__name__ + ": " + str(exc)) from exc

    def stream(
        self,
        messages: Iterable[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[str]:
        """Yield text deltas and close the active transport when cancelled."""
        if not self.is_configured:
            raise ProviderUnavailable(self.provider_name, "Groq is not configured")
        payload = [dict(item) for item in messages]
        if system_prompt:
            if payload and payload[0].get("role") == "system":
                payload[0]["content"] = system_prompt
            else:
                payload.insert(0, {"role": "system", "content": system_prompt})
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=payload,
                temperature=0.65 if temperature is None else float(temperature),
                max_tokens=600 if max_tokens is None else int(max_tokens),
                timeout=max(1.0, float(timeout or self.timeout)),
                stream=True,
            )
            watcher_stop = threading.Event()
            def close_on_cancel():
                while not watcher_stop.wait(0.025):
                    if cancel_event is not None and cancel_event.is_set():
                        try:
                            stream.close()
                        except Exception:
                            pass
                        return
            watcher = threading.Thread(target=close_on_cancel, name="jarvis-groq-cancel", daemon=True)
            watcher.start()
            try:
                for chunk in stream:
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    delta = ""
                    if getattr(chunk, "choices", None):
                        delta = getattr(chunk.choices[0].delta, "content", "") or ""
                    if delta:
                        yield delta
            finally:
                watcher_stop.set()
                if cancel_event is not None and cancel_event.is_set():
                    try:
                        stream.close()
                    except Exception:
                        pass
        except ProviderError:
            raise
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                return
            raise ProviderError(self.provider_name, type(exc).__name__ + ": " + str(exc)) from exc
