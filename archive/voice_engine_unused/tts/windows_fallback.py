"""Windows System.Speech TTS fallback with safe text transport and PCM output."""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
import tempfile
import wave

from voice_engine.audio.format import PCMFormatError, validate_pcm
from voice_engine.models import AudioChunk
from voice_engine.profiles.voice_profile import VoiceProfile
from voice_engine.tts.base import TTSProvider, TTSProviderError


class WindowsFallbackTTS(TTSProvider):
    name = "windows_system_speech"
    model_name = "System.Speech"

    def __init__(self, *, profile: VoiceProfile | None = None, chunk_bytes: int = 48_000):
        super().__init__(profile=profile)
        self.chunk_bytes = max(2, int(chunk_bytes))
        self._process: asyncio.subprocess.Process | None = None
        self._generation_id: str | None = None
        self._cancelled: set[str] = set()

    async def connect(self) -> None:
        if os.name != "nt":
            raise TTSProviderError("Windows System.Speech fallback requires Windows")

    def _script(self, text_b64: str, path_b64: str) -> str:
        voice = self.profile.voice or ""
        voice_b64 = base64.b64encode(voice.encode("utf-8")).decode("ascii")
        rate = None
        if self.profile.speech_rate is not None:
            rate = max(-10, min(10, int(round(float(self.profile.speech_rate)))))
        return (
            "$ErrorActionPreference = 'Stop';"
            "Add-Type -AssemblyName System.Speech;"
            "$text=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            + text_b64
            + "'));"
            "$path=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            + path_b64
            + "'));"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            + (f"$s.Rate={rate};" if rate is not None else "")
            + "try {"
            + "$s.SetOutputToWaveFile($path);"
            + ("$voice=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + voice_b64 + "'));"
               "$s.SelectVoice($voice);" if voice else "")
            + "$s.Speak($text)"
            "} finally { $s.Dispose() }"
        )

    async def synthesize_stream(
        self, text: str, *, generation_id: str
    ):
        await self.connect()
        generation_id = str(generation_id)
        self._generation_id = generation_id
        self._cancelled.discard(generation_id)
        fd, raw_path = tempfile.mkstemp(prefix="jarvis-voice-", suffix=".wav")
        os.close(fd)
        path = Path(raw_path)
        try:
            text_b64 = base64.b64encode(str(text).encode("utf-8")).decode("ascii")
            path_b64 = base64.b64encode(str(path).encode("utf-8")).decode("ascii")
            encoded = base64.b64encode(
                self._script(text_b64, path_b64).encode("utf-16le")
            ).decode("ascii")
            self._process = await asyncio.create_subprocess_exec(
                "powershell.exe",
                "-Sta",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await self._process.communicate()
            return_code = self._process.returncode
            self._process = None
            if generation_id in self._cancelled:
                return
            if return_code != 0:
                detail = stderr.decode("utf-8", errors="replace").strip()[:1000]
                raise TTSProviderError(f"System.Speech failed ({return_code}): {detail}")
            if not path.exists() or path.stat().st_size == 0:
                raise TTSProviderError("System.Speech returned no WAV audio")
            with wave.open(str(path), "rb") as wav:
                sample_rate = wav.getframerate()
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                if sample_width != 2:
                    raise PCMFormatError(f"System.Speech returned {sample_width * 8}-bit WAV")
                pcm = wav.readframes(wav.getnframes())
            pcm = validate_pcm(
                pcm,
                sample_rate=sample_rate,
                channels=channels,
                expected_channels=1,
            )
            for offset in range(0, len(pcm), self.chunk_bytes):
                if generation_id in self._cancelled or generation_id != self._generation_id:
                    return
                chunk = pcm[offset : offset + self.chunk_bytes]
                if chunk:
                    yield AudioChunk(chunk, sample_rate, channels, generation_id)
        except asyncio.CancelledError:
            await self.cancel(generation_id)
            raise
        finally:
            self._process = None
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            if self._generation_id == generation_id:
                self._generation_id = None

    async def cancel(self, generation_id: str | None = None) -> None:
        if generation_id is not None and self._generation_id not in {None, str(generation_id)}:
            return
        active = str(generation_id or self._generation_id or "")
        if active:
            self._cancelled.add(active)
        process = self._process
        if process and process.returncode is None:
            process.kill()
            await process.wait()
            self._process = None

    async def close(self) -> None:
        await self.cancel()


WindowsSystemSpeechTTS = WindowsFallbackTTS
