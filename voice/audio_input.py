"""Native-rate microphone capture with streaming resampling to 16 kHz mono."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import logging
import threading
from typing import Any

import numpy as np

from voice.config import VoiceConfig, load_config

LOG = logging.getLogger("jarvis.voice.audio_input")


@dataclass(frozen=True)
class AudioFrame:
    samples: np.ndarray
    sample_rate: int


class AudioInput:
    def __init__(self, config: VoiceConfig | None = None, *, stream_factory: Any = None):
        self.config = config or load_config()
        self.settings = self.config.section("input")
        self.stream_factory = stream_factory
        self.target_rate = int(self.settings.get("target_sample_rate", 16000))
        self.native_rate = self.target_rate
        self.channels = int(self.settings.get("channels", 1))
        self.gain = float(self.settings.get("gain", 1.0))
        self.frame_samples = int(self.config.section("audio").get("frame_samples", 512))
        self.device = self.settings.get("device", "default")
        self._stop = threading.Event()
        self._resampler = None

    def stop(self) -> None:
        self._stop.set()

    def reset(self) -> None:
        self._stop.clear()

    def _resolve_device_rate(self) -> tuple[Any, int]:
        if self.stream_factory is not None:
            return self.device, int(self.settings.get("test_native_sample_rate", self.target_rate))
        import sounddevice as sd

        device: Any = None if self.device in {None, "", "default"} else self.device
        if isinstance(device, str) and device.lstrip("+-").isdigit():
            device = int(device)
        info = sd.query_devices(device, kind="input")
        rate = int(round(float(info.get("default_samplerate") or self.target_rate)))
        LOG.info("Microphone selected: %s; native_rate=%s; channels=%s", info.get("name", device), rate, self.channels)
        return device, rate

    def frames(self) -> Iterator[AudioFrame]:
        self.reset()
        try:
            import soxr
        except ImportError as exc:
            raise RuntimeError("Audio resampling requires soxr; install the configured voice dependencies") from exc
        device, self.native_rate = self._resolve_device_rate()
        self._resampler = None if self.native_rate == self.target_rate else soxr.ResampleStream(
            self.native_rate, self.target_rate, self.channels, dtype="float32", quality="HQ"
        )
        native_block = max(1, int(round(self.native_rate * self.frame_samples / self.target_rate)))
        factory = self.stream_factory
        if factory is None:
            import sounddevice as sd

            factory = lambda: sd.InputStream(
                samplerate=self.native_rate,
                channels=self.channels,
                dtype="float32",
                blocksize=native_block,
                device=device,
            )
        pending = np.empty(0, dtype=np.float32)
        with factory() as stream:
            LOG.info("Microphone stream opened: rate=%s block=%s", self.native_rate, native_block)
            frames_read = 0
            peak_window = 0.0
            rms_sq_window = 0.0
            while not self._stop.is_set():
                if hasattr(stream, "read"):
                    raw, _overflowed = stream.read(native_block)
                else:
                    raw = stream(native_block)
                values = np.asarray(raw, dtype=np.float32)
                if self.gain != 1.0:
                    values = np.clip(values * self.gain, -1.0, 1.0)
                level_values = values.reshape(-1)
                frames_read += 1
                peak_window = max(peak_window, float(np.max(np.abs(level_values))) if level_values.size else 0.0)
                rms_sq_window += float(np.mean(np.square(level_values, dtype=np.float64))) if level_values.size else 0.0
                if frames_read >= max(1, int(self.native_rate * 5 / native_block)):
                    LOG.info("Microphone level: rms=%.5f peak=%.5f over ~5s", (rms_sq_window / frames_read) ** 0.5, peak_window)
                    frames_read = 0
                    peak_window = rms_sq_window = 0.0
                if values.ndim > 1:
                    values = values.mean(axis=1)
                if self._resampler is not None:
                    values = self._resampler.resample_chunk(values, last=False)
                pending = np.concatenate((pending, np.asarray(values, dtype=np.float32).reshape(-1)))
                while pending.size >= self.frame_samples:
                    frame = pending[: self.frame_samples].copy()
                    pending = pending[self.frame_samples :]
                    yield AudioFrame(frame, self.target_rate)
        if self._resampler is not None:
            final = np.asarray(self._resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True), dtype=np.float32)
            pending = np.concatenate((pending, final.reshape(-1)))
        if pending.size:
            yield AudioFrame(pending.astype(np.float32), self.target_rate)
