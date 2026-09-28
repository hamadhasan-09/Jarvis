"""Turn detection with VAD, pre-roll, post-roll, and bounded turn duration."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
import time

import numpy as np

from voice_engine.models import TurnEvent, TurnEventKind
from voice_engine.vad.detector import VADDetector, VADSignal


class TurnDetector:
    def __init__(
        self,
        vad: VADDetector,
        *,
        sample_rate: int,
        silence_timeout_seconds: float = 0.8,
        minimum_speech_seconds: float = 0.25,
        maximum_turn_seconds: float = 15.0,
        pre_roll_seconds: float = 0.35,
        post_roll_seconds: float = 0.15,
        on_speech_start: Callable[[VADSignal, float], None] | None = None,
        on_speech_end: Callable[[TurnEvent], None] | None = None,
    ):
        self.vad = vad
        self.sample_rate = int(sample_rate)
        self.silence_timeout_seconds = max(0.05, float(silence_timeout_seconds))
        self.minimum_speech_seconds = max(0.0, float(minimum_speech_seconds))
        self.maximum_turn_seconds = max(self.minimum_speech_seconds, float(maximum_turn_seconds))
        self.pre_roll_seconds = max(0.0, float(pre_roll_seconds))
        self.post_roll_seconds = max(0.0, float(post_roll_seconds))
        self.on_speech_start = on_speech_start
        self.on_speech_end = on_speech_end
        self._pre_roll: deque[np.ndarray] = deque()
        self._active: list[np.ndarray] = []
        self._speech_started_at: float | None = None
        self._speech_samples = 0
        self._silence_seconds = 0.0
        self._clock_seconds = 0.0

    @property
    def active(self) -> bool:
        return self._speech_started_at is not None

    def reset(self) -> None:
        self._pre_roll.clear()
        self._active.clear()
        self._speech_started_at = None
        self._speech_samples = 0
        self._silence_seconds = 0.0
        self._clock_seconds = 0.0
        self.vad.reset()

    def feed(self, block: np.ndarray) -> list[TurnEvent]:
        values = np.asarray(block, dtype=np.float32)
        flat = values.reshape(-1)
        if flat.size == 0:
            return []
        duration = flat.size / self.sample_rate
        self._clock_seconds += duration
        signal = self.vad.detect(values)
        events: list[TurnEvent] = []

        if not self.active:
            self._pre_roll.append(flat.copy())
            self._trim_pre_roll()
            if signal.is_speech:
                self._speech_started_at = time.monotonic() - duration
                self._active = list(self._pre_roll)
                self._speech_samples = sum(item.size for item in self._active)
                self._silence_seconds = 0.0
                if self.on_speech_start:
                    self.on_speech_start(signal, self._speech_started_at)
            return events

        self._active.append(flat.copy())
        self._speech_samples += flat.size
        if signal.is_speech:
            self._silence_seconds = 0.0
        else:
            self._silence_seconds += duration

        speech_duration = self._speech_samples / self.sample_rate
        silence_required = max(self.silence_timeout_seconds, self.post_roll_seconds)
        if (
            speech_duration >= self.minimum_speech_seconds
            and (
                self._silence_seconds >= silence_required
                or speech_duration >= self.maximum_turn_seconds
            )
        ):
            reason = "max_duration" if speech_duration >= self.maximum_turn_seconds else "silence"
            events.append(self._finish_and_notify(reason))
        return events

    def flush(self, reason: str = "flush") -> TurnEvent | None:
        if not self.active:
            return None
        if self._speech_samples / self.sample_rate < self.minimum_speech_seconds:
            self.reset()
            return None
        return self._finish_and_notify(reason)

    def _trim_pre_roll(self) -> None:
        max_samples = int(self.pre_roll_seconds * self.sample_rate)
        while sum(item.size for item in self._pre_roll) > max_samples and len(self._pre_roll) > 1:
            self._pre_roll.popleft()

    def _finish_and_notify(self, reason: str) -> TurnEvent:
        event = self._finish(reason)
        if self.on_speech_end:
            self.on_speech_end(event)
        return event

    def _finish(self, reason: str) -> TurnEvent:
        started_at = self._speech_started_at if self._speech_started_at is not None else time.monotonic()
        samples = np.concatenate(self._active).astype(np.float32, copy=False)
        audio = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()
        event = TurnEvent(
            kind=TurnEventKind.END,
            audio=audio,
            sample_rate=self.sample_rate,
            channels=1,
            started_at=started_at,
            ended_at=time.monotonic(),
            reason=reason,
        )
        self.reset()
        return event
