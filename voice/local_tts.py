"""Offline Piper adapter recovered from the original Nexus voice implementation."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
import numpy as np

from voice.config import VoiceConfig, load_config
from voice.text_cleaner import split_sentences

LOG = logging.getLogger("jarvis.voice.local_tts")


class LocalTTSError(RuntimeError):
    """A configured local voice is unavailable or synthesis failed."""


class PiperTTS:
    """Offline ONNX TTS using one configured Piper voice per language."""

    def __init__(self, config: VoiceConfig | None = None, *, voice_factory=None):
        self.config = config or load_config()
        self.settings = self.config.section("tts")
        local = self.settings.get("local", {})
        configured = local.get("models", {}) if isinstance(local, dict) else {}
        self.model_paths: dict[str, Path] = {}
        if isinstance(configured, dict):
            for language in ("en", "ur"):
                value = str(configured.get(language, "")).strip()
                if value:
                    path = Path(value).expanduser()
                    if not path.is_absolute():
                        path = self.config.path.parent / path
                    self.model_paths[language] = path.resolve()
        self.sample_rate = int(self.settings.get("sample_rate", 22050))
        self._voices: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self.last_error = ""
        self._voice_factory = voice_factory

    @property
    def name(self) -> str:
        return "piper"

    def load(self) -> None:
        """Validate assets without loading ONNX weights or synthesizing speech."""
        for language in ("en", "ur"):
            if not self.supports_language(language):
                raise LocalTTSError(self.last_error)

    def warmup(self) -> None:
        for language in self.model_paths:
            self._load_voice(language)

    def close(self) -> None:
        self._voices.clear()
        self._errors.clear()

    def supports_language(self, lang: str) -> bool:
        language = _normalize_language(lang)
        model = self.model_paths.get(language)
        config_path = Path(str(model) + ".json") if model else None
        available = bool(model and model.is_file() and config_path and config_path.is_file())
        if not available:
            self.last_error = (
                f"Piper {language} model/config is missing: {model}"
                if model
                else f"No local Piper model is configured for {language}"
            )
        return available

    def capability_report(self) -> dict[str, Any]:
        languages = {}
        for language, model in self.model_paths.items():
            languages[language] = {
                "model": str(model),
                "available": model.is_file() and Path(str(model) + ".json").is_file(),
                "loaded": language in self._voices,
                "diagnostic": self._errors.get(language, ""),
            }
        return {"backend": self.name, "offline": True, "languages": languages}

    def synthesize(self, text: str, lang: str) -> np.ndarray:
        language = _normalize_language(lang)
        if language not in self.model_paths:
            raise LocalTTSError(f"No local Piper model is configured for {language}")
        try:
            voice = self._load_voice(language)
        except Exception as exc:
            detail = self._errors.get(language) or str(exc)
            raise LocalTTSError(f"Piper {language} model failed to load: {detail}") from exc

        samples: list[np.ndarray] = []
        for sentence in split_sentences(str(text), language):
            try:
                for chunk in voice.synthesize(sentence):
                    audio = np.asarray(chunk.audio_float_array, dtype=np.float32).reshape(-1)
                    if audio.size:
                        rate = int(chunk.sample_rate)
                        if rate != self.sample_rate:
                            import soxr
                            audio = np.asarray(soxr.resample(audio, rate, self.sample_rate), dtype=np.float32)
                        samples.append(audio)
            except Exception as exc:
                raise LocalTTSError(f"Piper {language} synthesis failed: {str(exc)}") from exc
        if not samples:
            raise LocalTTSError(f"Piper {language} returned no audio")
        return np.concatenate(samples).astype(np.float32, copy=False)

    def _load_voice(self, language: str) -> Any:
        if language in self._voices:
            return self._voices[language]
        if language in self._errors:
            raise LocalTTSError(self._errors[language])
        model = self.model_paths.get(language)
        config_path = Path(str(model) + ".json") if model else None
        if model is None or not model.is_file() or config_path is None or not config_path.is_file():
            raise LocalTTSError(f"Piper {language} model/config is missing: {model}")
        try:
            if self._voice_factory is None:
                from piper import PiperVoice
                factory = PiperVoice.load
            else:
                factory = self._voice_factory
            voice = factory(str(model), str(config_path), use_cuda=False)
            # Piper's optional diacritizer may download assets; local voices must stay offline.
            voice.use_tashkeel = False
            self._voices[language] = voice
            self._errors.pop(language, None)
            LOG.info("Loaded offline Piper voice lang=%s model=%s sample_rate=%s", language, model.name, self.sample_rate)
            return voice
        except Exception as exc:
            detail = f"{type(exc).__name__}: {str(exc)}"
            self._errors[language] = detail
            raise LocalTTSError(detail) from exc


def _normalize_language(lang: str) -> str:
    value = str(lang or "").strip().lower()
    return "ur" if value.startswith(("ur", "hi", "hindi")) or value == "roman_urdu_or_hinglish" else "en"


def create_tts_backend(config: VoiceConfig | None = None):
    config = config or load_config()
    settings = config.section("tts")
    provider = str(settings.get("provider", "piper")).strip().lower()
    if provider in {"piper", "local_piper"}:
        return PiperTTS(config)
    if provider == "edge_tts":
        if settings.get("online_enabled") is not True:
            raise LocalTTSError("Edge TTS requires explicit tts.online_enabled: true")
        from voice.tts import EdgeTTS
        return EdgeTTS(config)
    raise LocalTTSError(f"Unsupported TTS provider: {provider}")
