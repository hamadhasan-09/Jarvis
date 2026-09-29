"""Dedicated PCM playback worker with injectable output streams for tests."""

from __future__ import annotations

import threading
from typing import Callable, Protocol

import sounddevice as sd

from voice_engine.audio.buffer import PCMBuffer
from voice_engine.models import AudioChunk


class OutputStream(Protocol):
    def __enter__(self): ...
    def __exit__(self, exc_type, exc, tb): ...
    def write(self, data: bytes) -> None: ...


class PCMPlaybackWorker:
    def __init__(
        self,
        *,
        buffer: PCMBuffer | None = None,
        stream_factory: Callable[[AudioChunk], OutputStream] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_underrun: Callable[[], None] | None = None,
    ):
        self.on_error = on_error
        self.on_underrun = on_underrun
        self.buffer = buffer or PCMBuffer(on_underrun=on_underrun)
        self._stream_factory = stream_factory or self._default_stream
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._generation_id: str | None = None
        self._speaking = False

    @property
    def generation_id(self) -> str | None:
        with self._lock:
            return self._generation_id

    @property
    def speaking(self) -> bool:
        with self._lock:
            return self._speaking

    def start(self, generation_id: str) -> None:
        self.stop()
        with self._lock:
            self._generation_id = str(generation_id)
            self._speaking = True
        self._stop_event.clear()
        self.buffer.start(str(generation_id))
        self._thread = threading.Thread(
            target=self._run,
            name="jarvis-voice-playback",
            daemon=True,
        )
        self._thread.start()

    def enqueue(self, chunk: AudioChunk) -> bool:
        with self._lock:
            if not self._speaking or chunk.generation_id != self._generation_id:
                return False
        return self.buffer.append(chunk)

    def complete(self, generation_id: str | None = None) -> bool:
        with self._lock:
            if generation_id is not None and generation_id != self._generation_id:
                return False
        return self.buffer.complete(generation_id)

    def cancel(self, generation_id: str | None = None) -> bool:
        with self._lock:
            if generation_id is not None and generation_id != self._generation_id:
                return False
            self._speaking = False
            active = self._generation_id
        self._stop_event.set()
        self.buffer.stop(active)
        return True

    def stop(self) -> None:
        self.cancel()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None

    def close(self) -> None:
        self.stop()
        self.buffer.close()

    def _default_stream(self, first_chunk: AudioChunk) -> OutputStream:
        return sd.RawOutputStream(
            samplerate=first_chunk.sample_rate,
            channels=first_chunk.channels,
            dtype="int16",
            blocksize=0,
        )

    def _run(self) -> None:
        stream = None
        try:
            while not self._stop_event.is_set():
                chunk = self.buffer.read(timeout=0.25)
                if chunk is None:
                    if self.buffer.producer_done or not self.speaking:
                        break
                    continue
                with self._lock:
                    if chunk.generation_id != self._generation_id or not self._speaking:
                        continue
                if stream is None:
                    stream = self._stream_factory(chunk)
                    stream.__enter__()
                stream.write(chunk.pcm)
        except Exception as exc:  # controlled worker failure; caller decides fallback
            with self._lock:
                self._speaking = False
            if self.on_error:
                self.on_error(exc)
        finally:
            if stream is not None:
                try:
                    stream.__exit__(None, None, None)
                except Exception:
                    pass
            with self._lock:
                self._speaking = False
