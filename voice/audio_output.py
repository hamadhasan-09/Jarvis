"""Interruptible, single-owner audio playback queue."""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
from typing import Any

import numpy as np

from voice.config import VoiceConfig, load_config


@dataclass(frozen=True)
class PlaybackItem:
    samples: np.ndarray
    sample_rate: int
    generation: int
    enqueued_at: float


class AudioOutput:
    def __init__(self, config: VoiceConfig | None = None, *, stream_factory: Any = None):
        self.config = config or load_config()
        self.settings = self.config.section("audio")
        self.stream_factory = stream_factory
        self._queue: queue.Queue[PlaybackItem | None] = queue.Queue(
            maxsize=int(self.settings.get("playback_queue_size", 32))
        )
        self._generation = 0
        self._lock = threading.RLock()
        self._active_stream = None
        self._thread: threading.Thread | None = None
        self._closed = threading.Event()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._closed.clear()
        self._thread = threading.Thread(target=self._playback_loop, name="jarvis-playback", daemon=True)
        self._thread.start()

    def enqueue(self, samples: np.ndarray, sample_rate: int, generation: int) -> bool:
        with self._lock:
            if generation != self._generation or self._closed.is_set():
                return False
        values = self._prepare(samples, int(sample_rate))
        import time

        self._queue.put(PlaybackItem(values, int(sample_rate), int(generation), time.perf_counter()))
        return True

    def interrupt(self) -> int:
        with self._lock:
            self._generation += 1
            stream = self._active_stream
        self._drain()
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                try:
                    stream.stop()
                except Exception:
                    pass
        return self._generation

    def wait_empty(self, timeout: float | None = None) -> bool:
        if timeout is None:
            self._queue.join()
            return True
        done = threading.Event()
        waiter = threading.Thread(target=lambda: (self._queue.join(), done.set()), daemon=True)
        waiter.start()
        return done.wait(timeout)

    def close(self) -> None:
        self._closed.set()
        self.interrupt()
        self._queue.put(None)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def _prepare(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        values = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
        if not values.size:
            return values
        peak_target = 10 ** (float(self.settings.get("target_peak_dbfs", -1.0)) / 20.0)
        peak = float(np.max(np.abs(values)))
        if peak > 1e-8:
            values *= min(peak_target / peak, 20.0)
        fade_samples = int(int(self.settings.get("fade_ms", 5)) * 0.001 * sample_rate)
        fade_samples = min(fade_samples, values.size // 2)
        if fade_samples > 0:
            ramp = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
            values[:fade_samples] *= ramp
            values[-fade_samples:] *= ramp[::-1]
        return np.clip(values, -1.0, 1.0)

    def _make_stream(self, sample_rate: int):
        if self.stream_factory:
            return self.stream_factory(sample_rate)
        import sounddevice as sd

        return sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=0)

    def _playback_loop(self) -> None:
        stream = None
        stream_rate = None
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    break
                with self._lock:
                    current = self._generation
                if item.generation != current or not item.samples.size:
                    continue
                if stream is None or stream_rate != item.sample_rate:
                    if stream is not None:
                        stream.__exit__(None, None, None)
                    stream = self._make_stream(item.sample_rate)
                    stream.__enter__()
                    stream_rate = item.sample_rate
                    with self._lock:
                        self._active_stream = stream
                import time

                import logging

                logging.getLogger("jarvis.voice.audio").info(
                    "sentence_to_audio_start_ms=%.1f", (time.perf_counter() - item.enqueued_at) * 1000
                )
                stream.write(item.samples.reshape(-1, 1))
            except Exception:
                import logging

                logging.getLogger("jarvis.voice.audio").exception("Playback worker recovered from an audio error")
                if stream is not None:
                    try:
                        stream.__exit__(None, None, None)
                    except Exception:
                        pass
                    stream = None
                    stream_rate = None
                    with self._lock:
                        self._active_stream = None
            finally:
                self._queue.task_done()
        if stream is not None:
            try:
                stream.__exit__(None, None, None)
            except Exception:
                pass

    def _drain(self) -> None:
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._queue.task_done()
