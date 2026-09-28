"""Silero VAD adapter for 512-sample, 16 kHz mono frames."""

from __future__ import annotations

import numpy as np

from voice.config import VoiceConfig, load_config


class SileroVAD:
    def __init__(self, config: VoiceConfig | None = None, *, model=None, torch_module=None):
        self.config = config or load_config()
        self.threshold = float(self.config.section("vad").get("threshold", 0.5))
        self._model = model
        self._torch = torch_module

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from silero_vad import load_silero_vad
        except ImportError as exc:
            raise RuntimeError("Silero VAD requires silero-vad and torch") from exc
        self._torch = torch
        self._model = load_silero_vad()
        self._model.eval()

    def probability(self, frame: np.ndarray) -> float:
        self.load()
        values = np.asarray(frame, dtype=np.float32).reshape(-1)
        if values.size != 512:
            raise ValueError(f"Silero VAD expects 512 samples per frame, got {values.size}")
        with self._torch.no_grad():
            score = self._model(self._torch.from_numpy(values), 16000)
        return float(score.item())

    def is_speech(self, frame: np.ndarray) -> bool:
        return self.probability(frame) > self.threshold

    def reset(self) -> None:
        if self._model is not None and hasattr(self._model, "reset_states"):
            self._model.reset_states()
