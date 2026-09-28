"""Optional openWakeWord adapter; it is loaded only when enabled."""

from __future__ import annotations

import numpy as np

from voice.config import VoiceConfig, load_config


class WakeWord:
    def __init__(self, config: VoiceConfig | None = None, *, model=None):
        self.config = config or load_config()
        self.settings = self.config.section("wakeword")
        self._model = model

    @property
    def enabled(self) -> bool:
        return bool(self.settings.get("enabled", False))

    def load(self) -> None:
        if not self.enabled or self._model is not None:
            return
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            raise RuntimeError("Wake-word mode requires openWakeWord") from exc
        self._model = Model(wakeword_models=[str(self.settings.get("model", "hey_jarvis"))])

    def detected(self, frame: np.ndarray) -> bool:
        if not self.enabled:
            return True
        self.load()
        pcm = (np.clip(np.asarray(frame, dtype=np.float32).reshape(-1), -1.0, 1.0) * 32767).astype(np.int16)
        scores = self._model.predict(pcm)
        threshold = float(self.settings.get("threshold", 0.5))
        return any(float(score) >= threshold for score in scores.values())

    def reset(self) -> None:
        if self._model is not None and hasattr(self._model, "reset"):
            self._model.reset()
