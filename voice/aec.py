"""WebRTC AEC3 adapter and timestamped playback-reference buffer."""

from __future__ import annotations

from collections import deque
import logging
import threading
import time
from typing import Any

import numpy as np

LOG = logging.getLogger("jarvis.voice.aec")


class PlaybackReference:
    """Keep recent speaker PCM on a monotonic 16 kHz timeline for AEC."""

    def __init__(self, sample_rate: int = 16000, history_seconds: float = 4.0):
        self.sample_rate = int(sample_rate)
        self.history_seconds = max(1.0, float(history_seconds))
        self._segments: deque[tuple[float, np.ndarray]] = deque()
        self._lock = threading.RLock()

    def publish(self, samples: np.ndarray, sample_rate: int, starts_at: float) -> None:
        values = np.asarray(samples, dtype=np.float32).reshape(-1)
        if not values.size:
            return
        if int(sample_rate) != self.sample_rate:
            import soxr

            values = np.asarray(soxr.resample(values, int(sample_rate), self.sample_rate), dtype=np.float32)
        segment = (float(starts_at), values.copy())
        with self._lock:
            self._segments.append(segment)
            cutoff = time.perf_counter() - self.history_seconds
            while self._segments and self._segments[0][0] + self._segments[0][1].size / self.sample_rate < cutoff:
                self._segments.popleft()

    def read(self, ends_at: float, sample_count: int) -> np.ndarray:
        """Return the speaker reference for the capture frame's time interval."""
        count = max(0, int(sample_count))
        result = np.zeros(count, dtype=np.float32)
        if not count:
            return result
        end = float(ends_at)
        start = end - count / self.sample_rate
        with self._lock:
            segments = tuple(self._segments)
        for segment_start, samples in segments:
            segment_end = segment_start + samples.size / self.sample_rate
            overlap_start = max(start, segment_start)
            overlap_end = min(end, segment_end)
            if overlap_end <= overlap_start:
                continue
            out_start = max(0, int(round((overlap_start - start) * self.sample_rate)))
            src_start = max(0, int(round((overlap_start - segment_start) * self.sample_rate)))
            length = min(
                count - out_start,
                samples.size - src_start,
                int(round((overlap_end - overlap_start) * self.sample_rate)),
            )
            if length > 0:
                result[out_start : out_start + length] += samples[src_start : src_start + length]
        return np.clip(result, -1.0, 1.0)

    def clear_after(self, cutoff: float) -> None:
        """Drop samples scheduled after an aborted playback stream's tail."""
        cutoff = float(cutoff)
        with self._lock:
            retained: deque[tuple[float, np.ndarray]] = deque()
            for start, samples in self._segments:
                duration = cutoff - start
                keep = min(samples.size, max(0, int(round(duration * self.sample_rate))))
                if keep:
                    retained.append((start, samples[:keep]))
            self._segments = retained

    def clear(self) -> None:
        with self._lock:
            self._segments.clear()


class WebRTCAEC:
    """Use the WebRTC Audio Processing Module's AEC3 implementation."""

    def __init__(self, reference: PlaybackReference, *, stream_delay_ms: int = 60, processor_factory: Any = None):
        self.reference = reference
        self.stream_delay_ms = max(0, int(stream_delay_ms))
        self.processor_factory = processor_factory
        self._processor: Any = None

    def load(self) -> None:
        if self._processor is not None:
            return
        factory = self.processor_factory
        if factory is None:
            try:
                from pywebrtc_audio import AudioProcessor
            except ImportError as exc:
                raise RuntimeError("Barge-in requires the optional pywebrtc-audio AEC package") from exc
            factory = AudioProcessor
        self._processor = factory(
            sample_rate=self.reference.sample_rate,
            num_channels=1,
            echo_cancellation=True,
            noise_suppression=False,
            auto_gain_control=False,
            stream_delay_ms=self.stream_delay_ms,
        )
        LOG.info("WebRTC AEC3 initialized: rate=%s delay_hint_ms=%s", self.reference.sample_rate, self.stream_delay_ms)

    def process(self, microphone: np.ndarray, captured_at: float) -> np.ndarray:
        if self._processor is None:
            raise RuntimeError("AEC was not initialized")
        near = np.asarray(microphone, dtype=np.float32).reshape(-1)
        far = self.reference.read(captured_at, near.size)
        clean = self._processor.process(near, far)
        return np.asarray(clean, dtype=np.float32).reshape(-1)

    def reset(self) -> None:
        if self._processor is not None:
            self._processor.reset()
