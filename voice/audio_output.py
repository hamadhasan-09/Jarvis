"""Interruptible, single-owner audio playback queue."""

from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time
from typing import Any, Callable

import numpy as np

from voice.config import VoiceConfig, load_config


@dataclass(frozen=True)
class PlaybackItem:
    samples: np.ndarray
    sample_rate: int
    generation: int
    enqueued_at: float
    on_complete: Callable[[bool], None] | None = None


class AudioOutput:
    def __init__(self, config: VoiceConfig | None = None, *, stream_factory: Any = None, reference_sink: Any = None):
        self.config = config or load_config()
        self.settings = self.config.section("audio")
        self.stream_factory = stream_factory
        self.reference_sink = reference_sink
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

    def enqueue(
        self,
        samples: np.ndarray,
        sample_rate: int,
        generation: int,
        *,
        on_complete: Callable[[bool], None] | None = None,
    ) -> bool:
        with self._lock:
            if generation != self._generation or self._closed.is_set():
                self._notify(on_complete, False)
                return False
        values = self._prepare(samples, int(sample_rate))
        import time

        self._queue.put(PlaybackItem(values, int(sample_rate), int(generation), time.perf_counter(), on_complete))
        return True

    def set_reference_sink(self, reference_sink: Any) -> None:
        self.reference_sink = reference_sink

    def interrupt(self) -> int:
        with self._lock:
            self._generation += 1
            stream = self._active_stream
            latency = self._stream_latency(stream)
        if self.reference_sink is not None:
            self.reference_sink.clear_after(time.perf_counter() + latency)
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
        reference_cursor = 0.0
        while True:
            item = self._queue.get()
            completed = False
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
                    reference_cursor = time.perf_counter() + self._stream_latency(stream)
                    with self._lock:
                        self._active_stream = stream
                starts_at = max(time.perf_counter() + self._stream_latency(stream), reference_cursor)
                if self.reference_sink is not None:
                    self.reference_sink.publish(item.samples, item.sample_rate, starts_at)
                reference_cursor = starts_at + item.samples.size / item.sample_rate

                import logging

                logging.getLogger("jarvis.voice.audio").info(
                    "sentence_to_audio_start_ms=%.1f", (time.perf_counter() - item.enqueued_at) * 1000
                )
                stream.write(item.samples.reshape(-1, 1))
                audible_deadline = time.perf_counter() + self._stream_latency(stream)
                while time.perf_counter() < audible_deadline:
                    with self._lock:
                        still_current = item.generation == self._generation
                    if not still_current:
                        break
                    time.sleep(min(0.01, max(0.0, audible_deadline - time.perf_counter())))
                with self._lock:
                    completed = item.generation == self._generation
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
                self._notify(item.on_complete if item is not None else None, completed)
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
                self._notify(item.on_complete if item is not None else None, False)
                self._queue.task_done()

    @staticmethod
    def _stream_latency(stream: Any) -> float:
        try:
            latency = getattr(stream, "latency", 0.0)
            if isinstance(latency, (tuple, list)):
                latency = latency[-1]
            return max(0.0, float(latency or 0.0))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _notify(callback: Callable[[bool], None] | None, completed: bool) -> None:
        if callback is None:
            return
        try:
            callback(bool(completed))
        except Exception:
            import logging

            logging.getLogger("jarvis.voice.audio").exception("Playback completion callback failed")
