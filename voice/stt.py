"""Groq Whisper transcription with a lazy local faster-whisper fallback."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import math
import re
import time
from typing import Any, Protocol, runtime_checkable
import wave

import numpy as np

from voice.config import VoiceConfig, load_config

LOG = logging.getLogger("jarvis.voice.stt")

_DEVANAGARI = re.compile(r"[\u0900-\u097f]")
_WHISPER_HALLUCINATION_NORMALIZED = {"".join(ch for ch in text.casefold() if ch.isalnum()) for text in (
    "Thank you for watching", "Thanks for watching", "Subscribe", "Please subscribe",
)}


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    lang: str
    confidence: float
    duration_ms: float


@runtime_checkable
class SpeechToText(Protocol):
    """Backend-neutral contract used by the existing local voice service."""

    def load(self) -> None: ...

    def transcribe(
        self, audio: np.ndarray | bytes, *, sample_rate: int = 16000
    ) -> TranscriptResult | None: ...

    def close(self) -> None: ...


class WhisperEngine:
    """Groq Whisper API with a lazy faster-whisper CPU fallback.

    The public class name stays stable for existing Nexus imports. The local
    model is not initialized until the Groq transcription call raises.
    """

    def __init__(self, config: VoiceConfig | None = None, *, model=None, model_factory=None, groq_client=None):
        self.config = config or load_config()
        self.settings = self.config.section("stt")
        self._model = model
        self._model_factory = model_factory
        self._groq_client = groq_client
        self._device = "cpu"
        self.primary_model_name = str(self.settings.get("groq_model", "whisper-large-v3-turbo"))
        self.fallback_model_name = str(self.settings.get("fallback_model", "base.en"))
        self.vocabulary_hint = str(self.settings.get("vocabulary_hint", "")).strip()
        self._model_name = self.fallback_model_name
        self.runtime_reason = "Groq primary; local fallback not loaded"

    def load(self) -> None:
        """Keep startup light; neither call the API nor load local weights."""
        LOG.info(
            "STT configured: provider=groq model=%s; fallback=faster-whisper/%s cpu int8 (lazy)",
            self.primary_model_name,
            self.fallback_model_name,
        )

    def _create_model(self, name: str, device: str, compute: str):
        if self._model_factory:
            return self._model_factory(name, device, compute)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Local STT fallback requires faster-whisper") from exc
        return WhisperModel(name, device=device, compute_type=compute)

    def _load_fallback(self, reason: str = "Groq request failed") -> None:
        if self._model is not None:
            return
        name = self.fallback_model_name
        try:
            self._model = self._create_model(name, "cpu", "int8")
            self._model_name, self._device = name, "cpu"
            self.runtime_reason = reason
            LOG.info(
                "STT fallback loaded: provider=faster-whisper model=%s device=cpu compute_type=int8",
                name,
            )
        except Exception as exc:
            raise RuntimeError(f"Could not load local faster-whisper fallback ({name}): {exc}") from exc

    def transcribe(self, audio: np.ndarray | bytes, *, sample_rate: int = 16000) -> TranscriptResult | None:
        started = time.perf_counter()
        values = self._audio_array(audio)
        if values.size < int(sample_rate * float(self.config.section("vad").get("min_speech_ms", 300)) / 1000):
            return None
        values = self._to_16khz(values, sample_rate)
        try:
            return self._transcribe_groq(values, started)
        except Exception as exc:
            LOG.warning("Groq STT failed (%s); using faster-whisper CPU fallback", type(exc).__name__)
            return self._transcribe_local(values, started)

    def _get_groq_client(self):
        if self._groq_client is None:
            try:
                from groq import Groq
            except ImportError as exc:
                raise RuntimeError("Groq STT requires the groq package") from exc
            self._groq_client = Groq()
        return self._groq_client

    def _transcribe_groq(self, values: np.ndarray, started: float) -> TranscriptResult | None:
        options: dict[str, Any] = {
            "file": ("speech.wav", self._wav_bytes(values)),
            "model": self.primary_model_name,
        }
        language = str(self.settings.get("language", "en")).strip().lower()
        if language and language != "auto":
            options["language"] = language
        if self.vocabulary_hint:
            options["prompt"] = self.vocabulary_hint
        client = self._get_groq_client()
        try:
            response = client.audio.transcriptions.create(**options)
        except Exception:
            if "prompt" not in options:
                raise
            retry_options = dict(options)
            retry_options.pop("prompt")
            LOG.warning("Groq STT request with vocabulary prompt failed; retrying once without prompt")
            response = client.audio.transcriptions.create(**retry_options)
        text = re.sub(r"\s+", " ", str(getattr(response, "text", "") or "")).strip()
        if not text:
            return None
        detected = str(getattr(response, "language", "") or language or "en").lower()
        duration_ms = (time.perf_counter() - started) * 1000.0
        LOG.info(
            "STT provider=groq model=%s language=%s transcription_ms=%.1f",
            self.primary_model_name,
            detected,
            duration_ms,
        )
        return TranscriptResult(text, detected, 0.9, duration_ms)

    @staticmethod
    def _to_16khz(values: np.ndarray, sample_rate: int) -> np.ndarray:
        if sample_rate == 16000:
            return values.astype(np.float32, copy=False)
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        import soxr
        return np.asarray(soxr.resample(values, sample_rate, 16000), dtype=np.float32)

    @staticmethod
    def _wav_bytes(values: np.ndarray) -> bytes:
        pcm = (np.clip(values, -1.0, 1.0) * 32767.0).astype("<i2", copy=False)
        buffer = BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm.tobytes())
        return buffer.getvalue()

    def _transcribe_local(self, values: np.ndarray, started: float) -> TranscriptResult | None:
        self._load_fallback()
        try:
            segments, info = self._run(values, language="en", initial_prompt=self.vocabulary_hint or None)
        except Exception as exc:
            raise RuntimeError(f"Groq and local faster-whisper transcription failed: {exc}") from exc
        result = self._result_from_segments(segments, "en", info, 16000, started)
        duration_ms = (time.perf_counter() - started) * 1000.0
        LOG.info(
            "STT provider=faster-whisper model=%s device=cpu compute_type=int8 language=en transcription_ms=%.1f",
            self.fallback_model_name,
            duration_ms,
        )
        return result

    def warmup(self) -> None:
        """Explicitly warm the local fallback only when requested by an operator."""
        silence = np.zeros(16000, dtype=np.float32)
        self._load_fallback("explicit local fallback warmup")
        self._run(silence, language="en")

    def _run(self, values: np.ndarray, *, language: str | None, initial_prompt: str | None = None):
        options: dict[str, Any] = {
            "language": language,
            "task": "transcribe",
            "beam_size": 1,
            "vad_filter": bool(self.settings.get("vad_filter", True)),
            "condition_on_previous_text": False,
            "temperature": 0.0,
            "no_speech_threshold": float(self.settings.get("no_speech_threshold", 0.6)),
        }
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        segments, info = self._model.transcribe(values, **options)
        return list(segments), info

    @staticmethod
    def _audio_array(audio: np.ndarray | bytes) -> np.ndarray:
        if isinstance(audio, bytes):
            if len(audio) % 2:
                raise ValueError("PCM input must contain complete int16 samples")
            return np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        return np.asarray(audio, dtype=np.float32).reshape(-1)

    @staticmethod
    def _language_scores(info: Any) -> tuple[float, float]:
        scores: dict[str, float] = {}
        raw = getattr(info, "all_language_probs", None)
        if raw:
            try:
                scores = {str(language).lower(): float(probability) for language, probability in raw}
            except (TypeError, ValueError):
                scores = {}
        if scores:
            return scores.get("en", 0.0), scores.get("ur", 0.0) + scores.get("hi", 0.0)
        detected = str(getattr(info, "language", "") or "").lower()
        probability = float(getattr(info, "language_probability", 0.0) or 0.0)
        return (probability, 0.0) if detected == "en" else (0.0, probability) if detected in {"ur", "hi"} else (0.0, 0.0)

    @staticmethod
    def _urdu_prompt() -> str:
        return "اردو زبان میں صاف اور درست اردو رسم الخط استعمال کریں۔"

    def _result_from_segments(self, segments: list[Any], lang: str, info: Any, sample_rate: int, started: float) -> TranscriptResult | None:
        texts: list[str] = []
        logprobs: list[float] = []
        for segment in segments:
            text = str(getattr(segment, "text", "") or "").strip()
            if not text:
                continue
            logprob = getattr(segment, "avg_logprob", None)
            if logprob is not None:
                logprobs.append(float(logprob))
            texts.append(text)
        text = re.sub(r"\s+", " ", " ".join(texts)).strip()
        normalized = "".join(character for character in text.casefold() if character.isalnum())
        if not text or normalized in _WHISPER_HALLUCINATION_NORMALIZED:
            return None
        if logprobs and sum(logprobs) / len(logprobs) < float(self.settings.get("min_avg_logprob", -1.0)):
            return None
        words = re.findall(r"\w+", text.casefold())
        if len(words) >= 8:
            half = len(words) // 2
            if words[:half] == words[half:half * 2]:
                return None
        if any(phrase in normalized for phrase in _WHISPER_HALLUCINATION_NORMALIZED):
            return None
        en_score, ur_score = self._language_scores(info)
        language_score = ur_score if lang == "ur" else en_score
        avg_logprob = sum(logprobs) / len(logprobs) if logprobs else -0.4
        confidence = min(1.0, max(0.0, language_score * math.exp(min(0.0, avg_logprob))))
        return TranscriptResult(text, lang, confidence, (time.perf_counter() - started) * 1000.0)

    def close(self) -> None:
        self._model = None
        self._groq_client = None


# Keep the existing import name stable for the other voice components.
WhisperSTT = WhisperEngine
