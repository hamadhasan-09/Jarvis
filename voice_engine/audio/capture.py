"""Microphone capture abstraction; hardware access stays outside orchestration."""

from __future__ import annotations

from collections.abc import Iterator
import threading
import time
from typing import Callable, Any

import numpy as np
import sounddevice as sd


class AudioCapture:
    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        block_frames: int = 320,
        device: int | str | None = None,
        source: Callable[[], np.ndarray] | None = None,
        on_started: Callable[[dict[str, Any]], None] | None = None,
        on_block: Callable[[np.ndarray, int], None] | None = None,
    ):
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.block_frames = int(block_frames)
        self.device = self._normalize_device(device)
        self._source = source
        self.on_started = on_started
        self.on_block = on_block
        self._stop = threading.Event()
        self._active = False
        self._blocks_received = 0
        self._last_rms = 0.0
        self._last_block_at: float | None = None
        self._stream_info: dict[str, Any] = {}

    @staticmethod
    def _normalize_device(device: int | str | None) -> int | str | None:
        if isinstance(device, str):
            value = device.strip()
            if value.lstrip("+-").isdigit():
                return int(value)
            return value or None
        return device

    @property
    def active(self) -> bool:
        return self._active

    @property
    def blocks_received(self) -> int:
        return self._blocks_received

    @property
    def last_rms(self) -> float:
        return self._last_rms

    @property
    def last_block_at(self) -> float | None:
        return self._last_block_at

    @property
    def stream_info(self) -> dict[str, Any]:
        return dict(self._stream_info)

    def stop(self) -> None:
        self._stop.set()

    def reset(self) -> None:
        self._stop.clear()
        self._active = False
        self._blocks_received = 0
        self._last_rms = 0.0
        self._last_block_at = None
        self._stream_info = {}

    def describe_device(self) -> dict[str, Any]:
        """Return the configured/default input device and PortAudio metadata."""
        configured = self.device
        default_device: Any = None
        try:
            defaults = sd.default.device
            if isinstance(defaults, (str, bytes, int, float)):
                default_device = defaults
            else:
                try:
                    default_device = defaults[0]
                except (IndexError, KeyError, TypeError):
                    default_device = defaults
        except Exception:
            pass

        requested = configured if configured is not None else default_device
        result: dict[str, Any] = {
            "configured_device": configured,
            "requested_device": requested,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "block_frames": self.block_frames,
            "backend": "PortAudio/sounddevice",
        }
        try:
            info = sd.query_devices(requested, kind="input")
            result.update(
                {
                    "device_name": str(info.get("name", "<unknown>")),
                    "max_input_channels": int(info.get("max_input_channels", 0)),
                    "default_samplerate": float(info.get("default_samplerate", 0.0)),
                    "hostapi_index": int(info.get("hostapi", -1)),
                }
            )
            hostapi_index = result["hostapi_index"]
            if hostapi_index >= 0:
                hostapi = sd.query_hostapis(hostapi_index)
                result["hostapi_name"] = str(hostapi.get("name", "<unknown>"))

            # PortAudio does not always include the resolved index in the
            # per-device mapping, so find it from the enumerated list.
            for index, candidate in enumerate(sd.query_devices()):
                if str(candidate.get("name", "")) == result["device_name"]:
                    result["device_index"] = index
                    break
        except Exception as exc:
            result["device_error"] = f"{type(exc).__name__}: {exc}"
        return result

    def blocks(self) -> Iterator[np.ndarray]:
        self.reset()
        if self._source is not None:
            info = {
                "configured_device": "<test source>",
                "requested_device": "<test source>",
                "device_name": "<test source>",
                "device_index": None,
                "hostapi_name": "<test source>",
                "sample_rate": self.sample_rate,
                "channels": self.channels,
                "block_frames": self.block_frames,
                "backend": "source",
            }
            self._mark_started(info)
            try:
                while not self._stop.is_set():
                    yield self._observe(self._source())
            finally:
                self._active = False
            return

        info = self.describe_device()
        try:
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="float32",
                blocksize=self.block_frames,
                device=self.device,
            ) as stream:
                stream_info = dict(info)
                stream_info.update(
                    {
                        "sample_rate": float(getattr(stream, "samplerate", self.sample_rate)),
                        "channels": int(getattr(stream, "channels", self.channels)),
                        "block_frames": int(getattr(stream, "blocksize", self.block_frames)),
                        "device": getattr(stream, "device", self.device),
                    }
                )
                self._mark_started(stream_info)
                while not self._stop.is_set():
                    block, _overflowed = stream.read(self.block_frames)
                    yield self._observe(block)
        finally:
            self._active = False

    def _mark_started(self, info: dict[str, Any]) -> None:
        self._stream_info = dict(info)
        self._active = True
        if self.on_started:
            self.on_started(dict(info))

    def _observe(self, block: np.ndarray) -> np.ndarray:
        values = np.asarray(block, dtype=np.float32)
        if values.ndim == 1:
            values = values[:, None]
        self._blocks_received += 1
        flattened = values.reshape(-1)
        self._last_rms = (
            float(np.sqrt(np.mean(np.square(flattened), dtype=np.float64)))
            if flattened.size
            else 0.0
        )
        self._last_block_at = time.monotonic()
        if self.on_block:
            self.on_block(values, self._blocks_received)
        return values
