"""Common provider contract for JARVIS model backends.

Provider implementations deliberately return a small normalized response so the
HUD and orchestrator never need provider-specific code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable
import re


class ProviderError(RuntimeError):
    """A controlled provider failure safe to surface to the orchestrator."""

    def __init__(self, provider: str, message: str, retryable: bool = True):
        self.provider = provider
        self.retryable = retryable
        super().__init__(f"{provider}: {sanitize_error(message)}")


class ProviderUnavailable(ProviderError):
    """Raised when a provider is not configured or cannot be loaded."""

    def __init__(self, provider: str, message: str = "provider is unavailable"):
        super().__init__(provider, message, retryable=False)


@dataclass
class ProviderResponse:
    """Normalized model response returned by every provider."""

    text: str
    provider: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


def sanitize_error(value: Any) -> str:
    """Remove likely credential material from diagnostics."""
    text = str(value or "")
    patterns = [
        r"gsk_[A-Za-z0-9_-]+",
        r"sk-ant-[A-Za-z0-9_-]+",
        r"sk-[A-Za-z0-9_-]+",
        r"AIza[A-Za-z0-9_-]+",
        r"Bearer\s+[A-Za-z0-9._+/-]+=*",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
    return text[:500]


class BaseProvider(ABC):
    """Interface implemented by Groq, Gemini, Anthropic, and future providers."""

    provider_name = "unknown"

    def __init__(self, model: str, timeout: float = 18.0):
        self.model = model
        self.timeout = max(1.0, float(timeout))

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Whether the provider has the dependencies and credentials it needs."""

    @abstractmethod
    def generate(
        self,
        messages: Iterable[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        """Generate one normalized response."""

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model,
            "configured": bool(self.is_configured),
        }
