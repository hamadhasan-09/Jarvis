"""Voice session correlation and lifecycle state."""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import uuid

from voice_engine.models import VoiceState, VoiceTurn


@dataclass
class VoiceSession:
    session_id: str
    state: VoiceState = VoiceState.IDLE
    active_turn: VoiceTurn | None = None
    active_tts_generation: str | None = None
    interrupt_count: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def begin_turn(self, *, request_id: str | None = None) -> VoiceTurn:
        with self._lock:
            turn = VoiceTurn.create(self.session_id, request_id=request_id)
            self.active_turn = turn
            self.state = VoiceState.THINKING
            return turn

    def begin_tts(self, turn: VoiceTurn) -> VoiceTurn:
        with self._lock:
            if self.active_turn is None or self.active_turn.turn_id != turn.turn_id:
                raise ValueError("Cannot start TTS for an inactive turn")
            generation = f"tts-{uuid.uuid4().hex[:12]}"
            self.active_tts_generation = generation
            self.active_turn = turn.with_tts_generation(generation)
            self.state = VoiceState.SPEAKING
            return self.active_turn

    def is_current(self, *, turn_id: str, generation_id: str | None = None) -> bool:
        with self._lock:
            if not self.active_turn or self.active_turn.turn_id != turn_id:
                return False
            return generation_id is None or generation_id == self.active_tts_generation

    def interrupt(self) -> str | None:
        with self._lock:
            old_generation = self.active_tts_generation
            self.interrupt_count += 1
            self.active_tts_generation = None
            # Drop the active turn as well: a reply that arrives after an
            # interruption must not be allowed to start stale TTS.
            self.active_turn = None
            self.state = VoiceState.INTERRUPTED
            return old_generation

    def set_state(self, state: VoiceState) -> None:
        with self._lock:
            self.state = state

    def finish_turn(self, turn_id: str) -> bool:
        with self._lock:
            if not self.active_turn or self.active_turn.turn_id != turn_id:
                return False
            self.active_turn = None
            self.active_tts_generation = None
            self.state = VoiceState.LISTENING
            return True
