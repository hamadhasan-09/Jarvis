"""Experiential Labs OpenAI-compatible provider for JARVIS.

The Experiential Labs gateway exposes an OpenAI-compatible /v1/chat/completions API.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Iterator

import httpx

from .base_provider import (
    BaseProvider,
    ProviderError,
    ProviderResponse,
    ProviderUnavailable,
    sanitize_error,
)


class ExperientialProvider(BaseProvider):
    provider_name = "experiential"

    def __init__(
        self,
        api_key: str | None,
        model: str,
        timeout: float = 18.0,
        base_url: str = "https://api.experientiallabs.ai/v1",
    ):
        super().__init__(model=model, timeout=timeout)
        self._api_key = (api_key or "").strip()
        self.base_url = (base_url or "").strip().rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key and self.base_url)

    def _payload(
        self,
        messages: Iterable[dict[str, str]],
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool = False,
    ) -> dict[str, Any]:
        payload = [dict(item) for item in messages]

        if system_prompt:
            if payload and payload[0].get("role") == "system":
                payload[0]["content"] = system_prompt
            else:
                payload.insert(0, {"role": "system", "content": system_prompt})

        result = {
            "model": self.model,
            "messages": payload,
            "max_tokens": 600 if max_tokens is None else int(max_tokens),
        }

        # Match the proven Experiential Claude request shape. Claude Opus
        # routes reject arbitrary temperature values, so omit temperature.
        # For normal requests, omit stream=false because the gateway's
        # successful non-streaming response does not require that field.
        if stream:
            result["stream"] = True

        if not self.model.startswith("claude-opus"):
            result["temperature"] = 0.65 if temperature is None else float(temperature)

        return result

    @staticmethod
    def _extract_response(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError("experiential", "provider returned no choices")

        message = choices[0].get("message") or {}
        content = message.get("content") or ""

        if isinstance(content, list):
            content = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict)
            )

        text = str(content)
        if not text.strip():
            raise ProviderError("experiential", "provider returned an empty response")

        metadata: dict[str, Any] = {}
        if isinstance(data.get("usage"), dict):
            metadata["usage"] = data["usage"]
        if "cost" in data:
            metadata["cost"] = data["cost"]
        if "provider" in data:
            metadata["upstream_provider"] = data["provider"]
        if "id" in data:
            metadata["request_id"] = data["id"]

        return text, metadata

    def _raise_http_error(self, response: httpx.Response) -> None:
        detail = response.text[:500]
        status = response.status_code
        retryable = status in {408, 409, 429} or status >= 500
        raise ProviderError(
            self.provider_name,
            f"HTTP {status}: {detail}",
            retryable=retryable,
        )

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
            raise ProviderUnavailable(
                self.provider_name,
                "Experiential Labs is not configured",
            )

        request_timeout = max(1.0, float(timeout or self.timeout))
        payload = self._payload(
            messages, system_prompt, temperature, max_tokens, stream=False
        )
        url = f"{self.base_url}/chat/completions"

        try:
            with httpx.Client(timeout=request_timeout) as client:
                response = client.post(
                    url,
                    headers=self._headers,
                    json=payload,
                )

            if response.status_code >= 400:
                self._raise_http_error(response)

            data = response.json()
            text, response_metadata = self._extract_response(data)
            response_metadata["request"] = metadata or {}

            return ProviderResponse(
                text=text,
                provider=self.provider_name,
                model=self.model,
                metadata=response_metadata,
            )
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError(
                self.provider_name,
                f"request timeout after {request_timeout:.1f}s",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                self.provider_name,
                type(exc).__name__ + ": " + sanitize_error(exc),
            ) from exc

    def stream(
        self,
        messages: Iterable[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[str]:
        if not self.is_configured:
            raise ProviderUnavailable(
                self.provider_name,
                "Experiential Labs is not configured",
            )

        request_timeout = max(1.0, float(timeout or self.timeout))
        payload = self._payload(
            messages, system_prompt, temperature, max_tokens, stream=True
        )
        url = f"{self.base_url}/chat/completions"

        try:
            with httpx.Client(timeout=request_timeout) as client:
                with client.stream(
                    "POST",
                    url,
                    headers={**self._headers, "Accept": "text/event-stream"},
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        self._raise_http_error(response)

                    for line in response.iter_lines():
                        if not line:
                            continue

                        if isinstance(line, bytes):
                            line = line.decode("utf-8", errors="replace")

                        if not line.startswith("data:"):
                            continue

                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            return

                        try:
                            data = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue

                        choices = data.get("choices") or []
                        if not choices:
                            continue

                        delta = (choices[0].get("delta") or {}).get("content") or ""
                        if isinstance(delta, str) and delta:
                            yield delta

        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError(
                self.provider_name,
                f"stream timeout after {request_timeout:.1f}s",
            ) from exc
        except Exception as exc:
            raise ProviderError(
                self.provider_name,
                type(exc).__name__ + ": " + sanitize_error(exc),
            ) from exc
