"""Voice activity detector providers with a common local interface."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class VADSignal:
    is_speech: bool
    probability: float | None = None
    level: float | None = None
    threshold: float | None = None


class VADDetector(Protocol):
    sample_rate: int

    def detect(self, block: np.ndarray) -> VADSignal: ...

    def reset(self) -> None: ...


class AdaptiveEnergyVAD:
    """Local fallback VAD with a slowly adapting noise floor.

    This is deliberately isolated behind the VAD interface so a Silero or
    WebRTC implementation can replace it without changing turn detection.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        multiplier: float = 2.0,
        minimum_threshold: float = 0.012,
        attack: float = 0.05,
        release: float = 0.01,
        warmup_blocks: int = 5,
    ):
        self.sample_rate = int(sample_rate)
        self.multiplier = max(1.1, float(multiplier))
        self.minimum_threshold = max(0.0001, float(minimum_threshold))
        self.attack = min(1.0, max(0.001, float(attack)))
        self.release = min(1.0, max(0.001, float(release)))
        self.warmup_blocks = max(0, int(warmup_blocks))
        self.noise_floor: float | None = None
        self._warmup_remaining = self.warmup_blocks

    @staticmethod
    def rms(block: np.ndarray) -> float:
        values = np.asarray(block, dtype=np.float32).reshape(-1)
        if values.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(values), dtype=np.float64)))

    @property
    def threshold(self) -> float:
        return max(
            self.minimum_threshold,
            (self.noise_floor if self.noise_floor is not None else self.minimum_threshold) * self.multiplier,
        )

    def detect(self, block: np.ndarray) -> VADSignal:
        level = self.rms(block)
        threshold = self.threshold
        is_speech = level >= threshold
        if self.noise_floor is None:
            self.noise_floor = max(self.minimum_threshold / self.multiplier, level)
        elif not is_speech:
            self.noise_floor = (1.0 - self.release) * self.noise_floor + self.release * level
        elif level < self.noise_floor:
            self.noise_floor = (1.0 - self.attack) * self.noise_floor + self.attack * level
        probability = min(1.0, max(0.0, level / max(threshold, 1e-9)))
        if self._warmup_remaining > 0:
            self._warmup_remaining -= 1
            return VADSignal(
                is_speech=False,
                probability=probability,
                level=level,
                threshold=threshold,
            )
        return VADSignal(
            is_speech=is_speech,
            probability=probability,
            level=level,
            threshold=threshold,
        )

    def reset(self) -> None:
        self.noise_floor = None
        self._warmup_remaining = self.warmup_blocks


class WebRTCVADDetector:
    """Optional WebRTC VAD adapter; dependency is loaded only when selected."""

    def __init__(self, *, sample_rate: int = 16000, aggressiveness: int = 2):
        if sample_rate not in {8000, 16000, 32000, 48000}:
            raise ValueError("WebRTC VAD requires 8, 16, 32, or 48 kHz")
        try:
            import webrtcvad  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "JARVIS_VAD_PROVIDER=webrtc requires the optional webrtcvad package"
            ) from exc
        self.sample_rate = sample_rate
        self._vad = webrtcvad.Vad(max(0, min(3, int(aggressiveness))))

    def detect(self, block: np.ndarray) -> VADSignal:
        values = np.clip(np.asarray(block, dtype=np.float32).reshape(-1), -1.0, 1.0)
        pcm = (values * 32767.0).astype(np.int16).tobytes()
        frame_bytes = int(self.sample_rate * 0.02) * 2
        frames = [pcm[i : i + frame_bytes] for i in range(0, len(pcm), frame_bytes)]
        frames = [frame for frame in frames if len(frame) == frame_bytes]
        if not frames:
            return VADSignal(False, 0.0, 0.0, None)
        decisions = [self._vad.is_speech(frame, self.sample_rate) for frame in frames]
        return VADSignal(
            is_speech=any(decisions),
            probability=float(sum(decisions) / len(decisions)),
            level=None,
            threshold=None,
        )

    def reset(self) -> None:
        return


def create_vad_from_env(*, sample_rate: int = 16000) -> VADDetector:
    provider = os.getenv("JARVIS_VAD_PROVIDER", "energy").strip().lower()
    if provider in {"webrtc", "webrtcvad"}:
        return WebRTCVADDetector(
            sample_rate=sample_rate,
            aggressiveness=int(os.getenv("JARVIS_VAD_AGGRESSIVENESS", "2")),
        )
    return AdaptiveEnergyVAD(
        sample_rate=sample_rate,
        multiplier=float(os.getenv("JARVIS_VAD_MULTIPLIER", "2.0")),
        minimum_threshold=float(os.getenv("JARVIS_VAD_MIN_THRESHOLD", "0.012")),
    )
