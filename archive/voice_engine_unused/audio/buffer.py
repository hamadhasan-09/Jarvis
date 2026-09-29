"""Bounded, generation-aware PCM buffer for realtime playback."""

from __future__ import annotations

from collections import deque
import threading
import time
from typing import Callable

from voice_engine.models import AudioChunk


class PCMBuffer:
    def __init__(self, *, max_bytes: int = 1_920_000, on_underrun: Callable[[], None] | None = None):
        self.max_bytes = max(1024, int(max_bytes))
        self._chunks: deque[AudioChunk] = deque()
        self._bytes = 0
        self._generation_id: str | None = None
        self._stopped = False
        self._producer_done = False
        self._closed = False
        self._condition = threading.Condition()
        self._on_underrun = on_underrun

    @property
    def generation_id(self) -> str | None:
        with self._condition:
            return self._generation_id

    @property
    def buffered_bytes(self) -> int:
        with self._condition:
            return self._bytes

    def start(self, generation_id: str) -> None:
        with self._condition:
            self._generation_id = str(generation_id)
            self._chunks.clear()
            self._bytes = 0
            self._stopped = False
            self._producer_done = False
            self._condition.notify_all()

    def append(self, chunk: AudioChunk) -> bool:
        raw = bytes(chunk.pcm)
        if not raw:
            return True
        with self._condition:
            if self._closed or self._stopped or self._producer_done:
                return False
            if chunk.generation_id is not None and chunk.generation_id != self._generation_id:
                return False
            if self._generation_id is None:
                self._generation_id = chunk.generation_id
            if len(raw) > self.max_bytes or self._bytes + len(raw) > self.max_bytes:
                return False
            self._chunks.append(
                AudioChunk(raw, chunk.sample_rate, chunk.channels, self._generation_id)
            )
            self._bytes += len(raw)
            self._condition.notify()
            return True

    def read(self, timeout: float = 0.25) -> AudioChunk | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while not self._chunks and not self._stopped and not self._producer_done and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if self._on_underrun and self._generation_id is not None:
                        self._on_underrun()
                    return None
                self._condition.wait(remaining)
            if not self._chunks:
                return None
            chunk = self._chunks.popleft()
            self._bytes -= len(chunk.pcm)
            return chunk

    def complete(self, generation_id: str | None = None) -> bool:
        with self._condition:
            if generation_id is not None and generation_id != self._generation_id:
                return False
            self._producer_done = True
            self._condition.notify_all()
            return True

    @property
    def producer_done(self) -> bool:
        with self._condition:
            return self._producer_done

    def flush(self, generation_id: str | None = None) -> int:
        with self._condition:
            if generation_id is not None and generation_id != self._generation_id:
                return 0
            removed = self._bytes
            self._chunks.clear()
            self._bytes = 0
            self._condition.notify_all()
            return removed

    def stop(self, generation_id: str | None = None) -> bool:
        with self._condition:
            if generation_id is not None and generation_id != self._generation_id:
                return False
            self._stopped = True
            self._producer_done = True
            self._chunks.clear()
            self._bytes = 0
            self._condition.notify_all()
            return True

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._stopped = True
            self._producer_done = True
            self._chunks.clear()
            self._bytes = 0
            self._condition.notify_all()
