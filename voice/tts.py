"""Microsoft Edge neural TTS provider for English and Urdu speech."""

from __future__ import annotations

import asyncio
import io
import logging
import tempfile
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from voice.config import VoiceConfig, load_config
from voice.text_cleaner import split_sentences

LOG = logging.getLogger("jarvis.voice.tts")


def _decode_mp3(source: str | io.BytesIO, sample_rate: int) -> np.ndarray:
    """Decode MP3 data to mono float32 samples for the existing playback queue."""
    import av

    decoded: list[np.ndarray] = []
    with av.open(source) as container:
        if not container.streams.audio:
            return np.zeros(0, dtype=np.float32)
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=sample_rate)
        for frame in container.decode(audio=0):
            for output in resampler.resample(frame):
                decoded.append(output.to_ndarray().reshape(-1).astype(np.float32, copy=False))
        for output in resampler.resample(None):
            decoded.append(output.to_ndarray().reshape(-1).astype(np.float32, copy=False))
    return np.concatenate(decoded) if decoded else np.zeros(0, dtype=np.float32)


class EdgeTTS:
    """Synthesize Edge neural speech into the PCM format used by AudioOutput."""

    def __init__(self, config: VoiceConfig | None = None, *, elevenlabs: ElevenLabsTTS | None = None):
        self.config = config or load_config()
        self.settings = self.config.section("tts")
        voices = self.settings.get("voices", {})
        self.voices = {
            "en": str(voices.get("en", "en-GB-RyanNeural")),
            "ur": str(voices.get("ur", "ur-PK-AsadNeural")),
        }
        self.sample_rate = int(self.settings.get("sample_rate", 24000))
        self._edge_tts = None
        self.elevenlabs = elevenlabs if elevenlabs is not None else ElevenLabsTTS(self.config)

    def load(self) -> None:
        """Load the lightweight client; a missing optional package is non-fatal."""
        if self._edge_tts is not None:
            return
        try:
            import edge_tts

            self._edge_tts = edge_tts
        except Exception as exc:
            LOG.warning("Edge TTS is unavailable: %s", exc)

    def synthesize(self, text: str, lang: str) -> np.ndarray:
        """Return mono float32 samples; failed sentences are logged and skipped."""
        samples: list[np.ndarray] = []
        for sentence in split_sentences(str(text), lang):
            if _contains_arabic_script(sentence):
                try:
                    waveform = self.elevenlabs.synthesize(sentence, "ur")
                except Exception as exc:
                    LOG.warning("ElevenLabs Urdu synthesis failed; trying Edge TTS fallback: %s", exc)
                    waveform = np.zeros(0, dtype=np.float32)
                if waveform.size:
                    samples.append(waveform)
                    continue
                LOG.warning("ElevenLabs Urdu synthesis failed; trying Edge TTS fallback")
                waveform = self._synthesize_edge_sentence(sentence, self.voices["ur"])
                if waveform.size:
                    samples.append(waveform)
                continue

            waveform = self._synthesize_edge_sentence(sentence, self.voices["en"])
            if waveform.size:
                samples.append(waveform)
        if not samples:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(samples).astype(np.float32, copy=False)

    def _synthesize_edge_sentence(self, sentence: str, voice: str) -> np.ndarray:
        temporary_path: str | None = None
        try:
            self.load()
            if self._edge_tts is None:
                return np.zeros(0, dtype=np.float32)
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temporary_file:
                temporary_path = temporary_file.name
            asyncio.run(self._edge_tts.Communicate(sentence, voice).save(temporary_path))
            waveform = self._decode_mp3(temporary_path)
            if waveform.size == 0:
                LOG.warning("Edge TTS returned no audio for a sentence (voice=%s)", voice)
            return waveform
        except Exception as exc:
            LOG.warning("Edge TTS failed for a sentence; skipping it: %s", exc)
            return np.zeros(0, dtype=np.float32)
        finally:
            if temporary_path:
                try:
                    Path(temporary_path).unlink(missing_ok=True)
                except Exception as exc:
                    LOG.warning("Could not remove temporary Edge TTS audio: %s", exc)

    def save_mp3(self, text: str, path: str | Path) -> bool:
        """Save an MP3 without playing it; Urdu prefers ElevenLabs then Edge TTS."""
        sentence = str(text).strip()
        if not sentence:
            return False
        if _contains_arabic_script(sentence):
            elevenlabs_saved = False
            try:
                elevenlabs_saved = self.elevenlabs.save_mp3(sentence, path)
            except Exception as exc:
                LOG.warning("ElevenLabs Urdu save failed; trying Edge TTS fallback: %s", exc)
            if elevenlabs_saved:
                return True
            LOG.warning("ElevenLabs Urdu save failed; trying Edge TTS fallback")
            return self._save_edge_mp3(sentence, path, self.voices["ur"])
        return self._save_edge_mp3(sentence, path, self.voices["en"])

    def _save_edge_mp3(self, sentence: str, path: str | Path, voice: str) -> bool:
        try:
            self.load()
            if self._edge_tts is None:
                return False
            asyncio.run(self._edge_tts.Communicate(sentence, voice).save(str(path)))
            return True
        except Exception as exc:
            LOG.warning("Edge TTS could not save MP3 %s: %s", path, exc)
            return False

    def _voice_for_sentence(self, sentence: str) -> str:
        language = "ur" if _contains_arabic_script(sentence) else "en"
        return self.voices[language]

    def _decode_mp3(self, path: str) -> np.ndarray:
        return _decode_mp3(path, self.sample_rate)

    def warmup(self) -> None:
        """Do not make a network request during service startup."""

    def close(self) -> None:
        self._edge_tts = None
        self.elevenlabs.close()


class ElevenLabsTTS:
    """ElevenLabs Urdu speech provider with the same PCM interface as EdgeTTS."""

    ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech/{}"

    def __init__(
        self,
        config: VoiceConfig | None = None,
        *,
        key_manager: Any = None,
        client: httpx.Client | None = None,
    ):
        self.config = config or load_config()
        self.tts_settings = self.config.section("tts")
        self.settings = self.tts_settings.get("elevenlabs", {})
        self.enabled = bool(self.settings.get("enabled", False))
        self.voice_id = str(self.settings.get("voice_id", "")).strip()
        self.model_id = str(self.settings.get("model_id", "")).strip()
        self.sample_rate = int(self.config.section("tts").get("sample_rate", 24000))
        if key_manager is None:
            try:
                from security_v2.key_manager import SecureKeyManager

                key_manager = SecureKeyManager()
            except Exception as exc:
                LOG.warning("ElevenLabs key manager is unavailable: %s", exc)
        self.key_manager = key_manager
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=float(self.settings.get("timeout_s", 20)))

    def load(self) -> None:
        """No model is loaded locally; the client is ready for requests."""

    def synthesize(self, text: str, lang: str) -> np.ndarray:
        """Return decoded mono PCM; failures are logged and reported as empty audio."""
        waveforms: list[np.ndarray] = []
        for sentence in split_sentences(str(text), lang):
            audio = self._request_audio(sentence)
            if not audio:
                continue
            try:
                waveform = _decode_mp3(io.BytesIO(audio), self.sample_rate)
                if waveform.size:
                    waveforms.append(waveform)
                else:
                    LOG.warning("ElevenLabs returned an empty audio stream; sentence skipped")
            except Exception as exc:
                LOG.warning("Could not decode ElevenLabs audio; sentence skipped: %s", exc)
        return np.concatenate(waveforms).astype(np.float32, copy=False) if waveforms else np.zeros(0, dtype=np.float32)

    def save_mp3(self, text: str, path: str | Path) -> bool:
        """Save response MP3 bytes without playback; return False on any failure."""
        sentence = str(text).strip()
        if not sentence:
            return False
        audio = self._request_audio(sentence)
        if not audio:
            return False
        try:
            Path(path).write_bytes(audio)
            return True
        except Exception as exc:
            LOG.warning("Could not save ElevenLabs MP3 %s: %s", path, exc)
            return False

    def _request_audio(self, sentence: str) -> bytes | None:
        try:
            if not self.enabled:
                LOG.debug("ElevenLabs is disabled in config; skipping")
                return None
            if not self.voice_id or not self.model_id:
                LOG.warning("ElevenLabs voice_id/model_id are missing from tts.elevenlabs configuration")
                return None
            if self.key_manager is None:
                LOG.warning("ElevenLabs is not configured; key manager is unavailable")
                return None
            api_key = self.key_manager.get_key("elevenlabs")
            if not api_key:
                LOG.warning("ElevenLabs is not configured; ELEVENLABS_API_KEY is unavailable")
                return None
            response = self._client.post(
                self.ENDPOINT.format(self.voice_id),
                headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
                json={"text": sentence, "model_id": self.model_id},
            )
            response.raise_for_status()
            audio = bytes(response.content)
            if not audio:
                LOG.warning("ElevenLabs returned an empty response body")
                return None
            return audio
        except Exception as exc:
            LOG.warning("ElevenLabs request failed; sentence will use Edge TTS fallback: %s", exc)
            return None

    def warmup(self) -> None:
        """Do not make a network request during service startup."""

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _contains_arabic_script(sentence: str) -> bool:
    return any(0x0600 <= ord(character) <= 0x06FF for character in sentence)
