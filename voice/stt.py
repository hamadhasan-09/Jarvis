"""Local-first, offline-only Faster-Whisper with explicitly opted-in Groq STT."""

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
    """Preserve the existing STT contract while selecting one configured provider."""

    def __init__(self, config: VoiceConfig | None = None, *, model=None, model_factory=None, groq_client=None):
        self.config = config or load_config()
        self.settings = self.config.section("stt")
        self._model = model
        self._model_factory = model_factory
        self._groq_client = groq_client
        self.provider = str(self.settings.get("provider", "faster-whisper")).strip().lower()
        if self.provider not in {"faster-whisper", "groq"}:
            raise ValueError(f"Unsupported STT provider: {self.provider}")
        if self.provider == "groq" and self.settings.get("online_enabled") is not True:
            raise ValueError("Groq STT requires explicit stt.online_enabled: true")
        self._device = str(self.settings.get("device", "cpu"))
        self.compute_type = str(self.settings.get("compute_type", "int8"))
        self._model_name = str(self.settings.get("model", "large-v3"))
        self.primary_model_name = str(self.settings.get("groq_model", "whisper-large-v3-turbo"))
        self.vocabulary_hint = str(self.settings.get("vocabulary_hint", "")).strip()
        self.runtime_reason = f"Configured {self.provider}; local weights loaded only on demand"

    def load(self) -> None:
        """No model loading, download, or cloud request during ordinary startup."""
        LOG.info("STT configured: provider=%s model=%s device=%s compute_type=%s (lazy)",
                 self.provider, self._model_name if self.provider == "faster-whisper" else self.primary_model_name,
                 self._device, self.compute_type)

    def _create_model(self, name: str, device: str, compute: str):
        if self._model_factory:
            return self._model_factory(name, device, compute)
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError("Local STT requires faster-whisper") from exc
        from pathlib import Path
        from faster_whisper.utils import download_model
        # Resolve from disk/cache only before construction: WhisperModel otherwise
        # fetches a default tokenizer when tokenizer.json is absent, even with
        # local_files_only=True. Reject incomplete local models instead.
        model_path = Path(name)
        if not model_path.is_dir():
            model_path = Path(download_model(name, local_files_only=True))
        for required in ("model.bin", "config.json", "tokenizer.json"):
            if not (model_path / required).is_file():
                raise FileNotFoundError(f"Local Whisper model is incomplete: {model_path / required}")
        return WhisperModel(str(model_path), device=device, compute_type=compute, local_files_only=True)

    def _load_local(self) -> None:
        if self._model is not None:
            return
        name = str(self.settings.get("model_path") or self._model_name)
        if self.settings.get("model_path"):
            from pathlib import Path
            path = Path(name).expanduser()
            name = str(path if path.is_absolute() else self.config.path.parent / path)
        try:
            self._model = self._create_model(name, self._device, self.compute_type)
        except Exception as exc:
            raise RuntimeError(
                f"Local Faster-Whisper {self._model_name} unavailable ({type(exc).__name__}). "
                "Install the configured CTranslate2 model in the local cache or set stt.model_path; "
                "downloads and model substitution are disabled."
            ) from exc

    def _language(self) -> str | None:
        value = str(self.settings.get("language") or "auto").strip().lower()
        return None if value == "auto" else value

    def transcribe(self, audio: np.ndarray | bytes, *, sample_rate: int = 16000) -> TranscriptResult | None:
        started = time.perf_counter()
        values = self._audio_array(audio)
        if values.size < int(sample_rate * float(self.config.section("vad").get("min_speech_ms", 300)) / 1000):
            return None
        values = self._to_16khz(values, sample_rate)
        if self.provider == "groq":
            return self._transcribe_groq(values, started)
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
        language = self._language()
        if language:
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
        self._load_local()
        try:
            segments, info = self._run(values, language=self._language(), initial_prompt=self.vocabulary_hint or None)
        except Exception as exc:
            raise RuntimeError(f"Local faster-whisper transcription failed: {type(exc).__name__}") from exc
        language = self._language() or str(getattr(info, "language", "") or "und").lower()
        result = self._result_from_segments(segments, language, info, 16000, started)
        LOG.info("STT provider=faster-whisper model=%s device=%s compute_type=%s language=%s transcription_ms=%.1f",
                 self._model_name, self._device, self.compute_type, language, (time.perf_counter()-started)*1000)
        return result

    def warmup(self) -> None:
        """Only explicitly requested local warmup loads weights; never call a cloud API."""
        if self.provider == "faster-whisper":
            self._load_local()
            self._run(np.zeros(16000, dtype=np.float32), language=self._language())

    def _run(self, values: np.ndarray, *, language: str | None, initial_prompt: str | None = None):
        options: dict[str, Any] = {
            "language": language,
            "task": "transcribe",
            "beam_size": int(self.settings.get("beam_size", 1)),
            "vad_filter": bool(self.settings.get("vad_filter", True)),
            "condition_on_previous_text": bool(self.settings.get("condition_on_previous_text", False)),
            "temperature": self.settings.get("temperatures", [0.0]),
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
