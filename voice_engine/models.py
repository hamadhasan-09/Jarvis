"""Provider-neutral models shared by the standalone JARVIS voice engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
import uuid


class VoiceState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    EXECUTING = "EXECUTING"
    ERROR = "ERROR"
    DISCONNECTED = "DISCONNECTED"


class TurnEventKind(str, Enum):
    START = "start"
    END = "end"


@dataclass(frozen=True)
class VoiceTurn:
    session_id: str
    request_id: str
    turn_id: str
    tts_generation_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)

    @classmethod
    def create(cls, session_id: str, *, request_id: str | None = None) -> "VoiceTurn":
        return cls(
            session_id=str(session_id),
            request_id=request_id or f"voice-{uuid.uuid4().hex[:12]}",
            turn_id=f"turn-{uuid.uuid4().hex[:12]}",
        )

    def with_tts_generation(self, generation_id: str) -> "VoiceTurn":
        return VoiceTurn(
            session_id=self.session_id,
            request_id=self.request_id,
            turn_id=self.turn_id,
            tts_generation_id=str(generation_id),
            created_at=self.created_at,
        )


@dataclass(frozen=True)
class AudioChunk:
    pcm: bytes
    sample_rate: int
    channels: int = 1
    generation_id: str | None = None


@dataclass(frozen=True)
class TranscriptSegment:
    text: str
    start_seconds: float | None = None
    end_seconds: float | None = None
    probability: float | None = None


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None
    probability: float | None = None
    segments: tuple[TranscriptSegment, ...] = ()
    provider: str = "unknown"


@dataclass(frozen=True)
class TurnEvent:
    kind: TurnEventKind
    audio: bytes
    sample_rate: int
    channels: int
    started_at: float
    ended_at: float
    reason: str = "silence"
