"""Structured metrics for voice-turn observability."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import time
from typing import Any


@dataclass
class VoiceMetrics:
    capture_latency_ms: float | None = None
    vad_latency_ms: float | None = None
    stt_latency_ms: float | None = None
    llm_first_token_ms: float | None = None
    tts_connection_ms: float | None = None
    tts_first_audio_ms: float | None = None
    playback_latency_ms: float | None = None
    turn_latency_ms: float | None = None
    interrupt_count: int = 0
    stt_provider: str | None = None
    tts_provider: str | None = None
    stt_model: str | None = None
    tts_model: str | None = None
    stale_event_count: int = 0
    underrun_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Stopwatch:
    def __init__(self) -> None:
        self.started = time.perf_counter()

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0
