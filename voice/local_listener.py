"""Local Windows microphone -> speech recognition -> JARVIS Socket.IO bridge.

This process owns the microphone so the HUD remains a presentation layer.
Groq Whisper is preferred when its API key is configured; SpeechRecognition's
Google recognizer remains a fallback. The transport is the existing Socket.IO
"user_speech" contract.
"""

from __future__ import annotations

import base64
from collections import deque
import io
import os
import subprocess
import threading
import time
import traceback
import uuid
import wave
from typing import Optional

import numpy as np
import sounddevice as sd
import speech_recognition as sr
import socketio


class LocalVoiceListener:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        sample_rate: int = 16000,
        block_seconds: float = 0.2,
        max_phrase_seconds: float = 12.0,
        silence_seconds: float = 1.15,
        min_phrase_seconds: float = 0.35,
        threshold_multiplier: float = 1.5,
        language: str = "en-US",
        session_id: str | None = None,
    ):
        self.url = f"http://{host}:{port}"
        self.session_id = str(session_id or os.getenv("JARVIS_SESSION_ID", "primary-user"))
        self.local_token = os.getenv("JARVIS_LOCAL_VOICE_TOKEN", "").strip()
        self.sample_rate = sample_rate
        self.block_size = max(1, int(sample_rate * block_seconds))
        self.max_phrase_blocks = max(1, int(max_phrase_seconds / block_seconds))
        self.silence_blocks = max(1, int(silence_seconds / block_seconds))
        self.min_phrase_blocks = max(1, int(min_phrase_seconds / block_seconds))
        self.pre_roll_blocks = max(1, int(0.4 / block_seconds))
        self.threshold_multiplier = max(1.5, float(threshold_multiplier))
        self.language = language
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.socket = socketio.Client(reconnection=True, logger=False, engineio_logger=False)
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = False
        self.groq_client = None
        self.stt_model = os.getenv("JARVIS_STT_MODEL", "whisper-large-v3-turbo").strip() or "whisper-large-v3-turbo"
        self.stt_fallback = os.getenv("JARVIS_STT_FALLBACK", "none").strip().lower()
        self.stt_timeout = max(3.0, min(float(os.getenv("JARVIS_STT_TIMEOUT", "8")), 30.0))
        self.request_started: dict[str, float] = {}
        self._init_groq_stt()
        self.input_device = os.getenv("JARVIS_VOICE_DEVICE", "").strip() or None
        self.ambient_rms = 0.005
        self.threshold = 0.015
        self.suppress_until = 0.0
        self._tts_lock = threading.Lock()
        self._tts_state_lock = threading.Lock()
        self._tts_pending = 0
        self._tts_active = threading.Event()

        @self.socket.event
        def connect():
            print(f"[LOCAL VOICE] Socket.IO connected (sid={self.socket.sid})", flush=True)

        @self.socket.event
        def disconnect():
            print("[LOCAL VOICE] Socket.IO disconnected", flush=True)

        @self.socket.on("jarvis_reply")
        def on_reply(data):
            payload = data if isinstance(data, dict) else {}
            request_id = str(payload.get("request_id", "")).strip() or "<missing>"
            text = str(payload.get("text", "")).strip()
            print(f"[LOCAL VOICE] Received jarvis_reply: {request_id}", flush=True)
            started_at = self.request_started.pop(request_id, None)
            if started_at is not None:
                print(
                    f"[LOCAL VOICE] Reply latency: {time.monotonic() - started_at:.2f}s "
                    f"({request_id})",
                    flush=True,
                )
            print(f"[LOCAL VOICE] TTS text length: {len(text)} ({request_id})", flush=True)
            if text:
                with self._tts_state_lock:
                    self._tts_pending += 1
                    self._tts_active.set()
                threading.Thread(
                    target=self._speak_guarded,
                    args=(text, request_id),
                    name="jarvis-local-tts",
                    daemon=True,
                ).start()
            # Keep the microphone closed while JARVIS is speaking so its
            # Windows TTS output is not immediately transcribed as user input.
            duration = max(1.5, min(8.0, len(text.split()) * 0.28))
            self.suppress_until = max(self.suppress_until, time.monotonic() + duration)

    def _init_groq_stt(self) -> None:
        """Use the existing Groq credential for robust multilingual transcription."""
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            if self.stt_fallback == "google":
                print("[LOCAL VOICE] STT provider: Google (Groq key not configured)", flush=True)
            else:
                print("[LOCAL VOICE] No STT provider configured; set GROQ_API_KEY or opt into Google fallback", flush=True)
            return
        try:
            from groq import Groq

            self.groq_client = Groq(api_key=api_key, timeout=self.stt_timeout)
            fallback = "Google fallback enabled" if self.stt_fallback == "google" else "no slow fallback"
            print(
                f"[LOCAL VOICE] STT provider: Groq {self.stt_model}; "
                f"timeout={self.stt_timeout:g}s; {fallback}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"[LOCAL VOICE] Groq STT unavailable: {type(exc).__name__}; "
                f"Google fallback {'enabled' if self.stt_fallback == 'google' else 'disabled'}",
                flush=True,
            )

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(
            target=self._run,
            name="jarvis-local-voice",
            daemon=True,
        )
        self.thread.start()
        print("[LOCAL VOICE] Windows voice input enabled")

    def stop(self) -> None:
        self.running = False
        try:
            self.socket.disconnect()
        except Exception:
            pass

    def _speak_guarded(self, text: str, request_id: str = "<direct>") -> None:
        print(f"[LOCAL VOICE] Starting Windows TTS: {request_id}", flush=True)
        success = False
        try:
            with self._tts_lock:
                success = bool(self._speak(text, request_id=request_id))
        except Exception:
            traceback.print_exc()
        finally:
            with self._tts_state_lock:
                self._tts_pending = max(0, self._tts_pending - 1)
                if self._tts_pending == 0:
                    self._tts_active.clear()
            print(
                f"[LOCAL VOICE] Windows TTS completed: {request_id} "
                f"success={success}",
                flush=True,
            )

    def _speak(self, text: str, request_id: str = "<direct>") -> bool:
        """Speak a final JARVIS reply through Windows system TTS."""
        # Pass reply text as UTF-8 data rather than interpolating it into a
        # PowerShell string. This preserves punctuation, quotes, and newlines
        # without creating a command-injection surface.
        payload = base64.b64encode(str(text).encode("utf-8")).decode("ascii")
        script = (
            "$ErrorActionPreference = 'Stop';"
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "try {"
            "$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
            + payload
            + "'));"
            "$s.Speak($text)"
            "} finally { $s.Dispose() }"
        )
        encoded_command = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-Sta",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded_command,
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            print(f"[LOCAL VOICE] Windows TTS return code: {result.returncode} ({request_id})", flush=True)
            if result.stderr:
                print(f"[LOCAL VOICE] Windows TTS stderr ({request_id}):", flush=True)
                print(result.stderr, end="", flush=True)
            if result.stdout:
                print(f"[LOCAL VOICE] Windows TTS stdout ({request_id}):", flush=True)
                print(result.stdout, end="", flush=True)
            if result.returncode != 0:
                return False
            return True
        except Exception:
            traceback.print_exc()
            return False

    def _connect_socket(self) -> None:
        if self.socket.connected:
            return
        print(f"[LOCAL VOICE] Connecting Socket.IO: {self.url}", flush=True)
        try:
            auth = {"role": "local_voice", "session_id": self.session_id}
            if self.local_token:
                auth["token"] = self.local_token
            self.socket.connect(
                self.url,
                auth=auth,
                wait=True,
                wait_timeout=10,
            )
        except Exception:
            traceback.print_exc()

    def _run(self) -> None:
        self._connect_socket()
        try:
            self._calibrate()
        except Exception as exc:
            print(f"[LOCAL VOICE] Calibration failed: {type(exc).__name__}: {exc}")
            self.ambient_rms = 0.005
            self.threshold = 0.02

        while self.running:
            try:
                self._connect_socket()
                if self._tts_active.is_set() or time.monotonic() < self.suppress_until:
                    time.sleep(0.15)
                    continue

                phrase = self._capture_phrase()
                if phrase is None:
                    continue

                text = self._recognize(phrase)
                if text:
                    self._emit(text)
            except Exception as exc:
                print(f"[LOCAL VOICE] Runtime error: {type(exc).__name__}: {exc}")
                time.sleep(0.5)

    def _record(self, frames: int) -> np.ndarray:
        data = sd.rec(
            frames,
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            device=self.input_device,
        )
        sd.wait()
        return np.asarray(data[:, 0], dtype=np.float32)

    @staticmethod
    def _rms(data: np.ndarray) -> float:
        if data.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(data), dtype=np.float64)))

    def _calibrate(self) -> None:
        print("[LOCAL VOICE] Calibrating microphone...")
        samples = []
        for _ in range(5):
            block = self._record(self.block_size)
            samples.append(self._rms(block))
        baseline = float(np.median(samples)) if samples else 0.005
        self.ambient_rms = max(0.002, baseline)
        self.threshold = max(0.01, self.ambient_rms * self.threshold_multiplier)
        print(
            f"[LOCAL VOICE] Ambient RMS={self.ambient_rms:.5f}, "
            f"speech threshold={self.threshold:.5f}"
        )

    def _capture_phrase(self) -> Optional[np.ndarray]:
        blocks: list[np.ndarray] = []
        pre_roll: deque[np.ndarray] = deque(maxlen=self.pre_roll_blocks)
        speech_started = False
        silence_count = 0
        peak_level = 0.0

        for _ in range(self.max_phrase_blocks):
            block = self._record(self.block_size)
            level = self._rms(block)
            peak_level = max(peak_level, level)
            is_speech = level >= self.threshold

            if not speech_started:
                pre_roll.append(block)
                if is_speech:
                    speech_started = True
                    blocks.extend(pre_roll)
                    silence_count = 0
                continue

            blocks.append(block)

            if is_speech:
                silence_count = 0
            else:
                silence_count += 1

            if silence_count >= self.silence_blocks:
                break

        if not speech_started or len(blocks) < self.min_phrase_blocks:
            print(
                f"[LOCAL VOICE] No phrase detected: peak RMS={peak_level:.5f}, "
                f"threshold={self.threshold:.5f}",
                flush=True,
            )
            return None

        print(
            f"[LOCAL VOICE] Phrase captured: peak RMS={peak_level:.5f}, "
            f"blocks={len(blocks)}",
            flush=True,
        )
        return np.concatenate(blocks)

    def _recognize(self, audio: np.ndarray) -> str:
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16).tobytes()

        audio_data = sr.AudioData(
            pcm,
            self.sample_rate,
            2,
        )

        started_at = time.monotonic()
        print(
            f"[LOCAL VOICE] Sending captured audio to STT: samples={audio.size}, "
            f"language={self.language}",
            flush=True,
        )
        if self.groq_client is not None:
            try:
                transcript = self._recognize_groq(audio_data)
                if transcript:
                    print(
                        f"[LOCAL VOICE] Groq STT completed in {time.monotonic() - started_at:.2f}s",
                        flush=True,
                    )
                    return transcript
                print("[LOCAL VOICE] Groq STT returned no reliable speech", flush=True)
                return self._recognize_google(audio_data) if self.stt_fallback == "google" else ""
            except Exception as exc:
                print(f"[LOCAL VOICE] Groq STT failed after {time.monotonic() - started_at:.2f}s: {type(exc).__name__}", flush=True)
                return self._recognize_google(audio_data) if self.stt_fallback == "google" else ""
        if self.stt_fallback == "google":
            return self._recognize_google(audio_data)
        print("[LOCAL VOICE] No STT provider available; set JARVIS_STT_FALLBACK=google to opt in", flush=True)
        return ""

    def _recognize_google(self, audio_data: sr.AudioData) -> str:
        """Run the legacy recognizer only when explicitly enabled, with a timeout."""
        self.recognizer.operation_timeout = max(2.0, min(self.stt_timeout, 8.0))
        started_at = time.monotonic()
        try:
            text = self.recognizer.recognize_google(audio_data, language=self.language).strip()
            print(f"[LOCAL VOICE] Google STT completed in {time.monotonic() - started_at:.2f}s", flush=True)
            return text
        except sr.UnknownValueError:
            print("[LOCAL VOICE] STT could not understand the captured audio", flush=True)
            return ""
        except sr.RequestError as exc:
            print(f"[LOCAL VOICE] STT service unavailable: {exc}", flush=True)
            return ""
        except Exception as exc:
            print(f"[LOCAL VOICE] Google STT failed: {type(exc).__name__}", flush=True)
            return ""

    def _recognize_groq(self, audio_data: sr.AudioData) -> str:
        """Transcribe captured audio through Groq Whisper without storing it."""
        # Auto detection is a better fit for English/Urdu/Hindi code-switching.
        # Keep a separate override so the legacy Google fallback can retain its
        # existing locale setting (for example, en-US).
        language = os.getenv("JARVIS_GROQ_STT_LANGUAGE", "auto").strip().lower()
        language = language.split("-", 1)[0].split("_", 1)[0]
        options = {
            "file": ("speech.wav", audio_data.get_wav_data()),
            "model": self.stt_model,
            "response_format": "verbose_json",
            "temperature": 0.0,
            "prompt": "JARVIS voice assistant. The speaker may use English, Urdu, Hindi, or Hinglish.",
        }
        if language and language not in {"auto", "automatic"}:
            options["language"] = language
        response = self.groq_client.audio.transcriptions.create(**options)
        text = str(getattr(response, "text", "") or "").strip()
        segments = getattr(response, "segments", None) or []
        if text and segments:
            try:
                quality = []
                for segment in segments:
                    if isinstance(segment, dict):
                        no_speech = segment.get("no_speech_prob")
                        logprob = segment.get("avg_logprob")
                    else:
                        no_speech = getattr(segment, "no_speech_prob", None)
                        logprob = getattr(segment, "avg_logprob", None)
                    if no_speech is not None and logprob is not None:
                        quality.append((float(no_speech), float(logprob)))
                if quality:
                    max_no_speech = max(item[0] for item in quality)
                    mean_logprob = sum(item[1] for item in quality) / len(quality)
                    print(
                        f"[LOCAL VOICE] STT quality: no_speech={max_no_speech:.2f}, "
                        f"avg_logprob={mean_logprob:.2f}",
                        flush=True,
                    )
                    if max_no_speech >= 0.80 and mean_logprob <= -0.70:
                        return ""
            except (TypeError, ValueError):
                pass
        return text

    def _emit(self, text: str) -> None:
        self._connect_socket()
        if not self.socket.connected:
            return

        request_id = f"local-{uuid.uuid4().hex[:12]}"
        language_hint = self._language_hint(text)
        self.request_started[request_id] = time.monotonic()
        while len(self.request_started) > 20:
            self.request_started.pop(next(iter(self.request_started)))

        print(f"User: {text}")
        print(f"[LOCAL VOICE] Sending user_speech: {request_id}", flush=True)
        self.socket.emit(
            "user_speech",
            {
                "text": text,
                "request_id": request_id,
                "language_hint": language_hint,
                "source": "local_voice",
            },
        )
        print(f"[LOCAL VOICE] Request sent; awaiting JARVIS reply ({request_id})", flush=True)

    @staticmethod
    def _language_hint(text: str) -> str:
        value = str(text or "")
        if any("\u0600" <= ch <= "\u06ff" for ch in value):
            return "urdu_script"
        if any("\u0900" <= ch <= "\u097f" for ch in value):
            return "hindi_script"
        lowered = value.lower()
        roman_markers = (
            "mujhe", "muje", "aap", "apko", "tumhe", "meri", "mere",
            "yaad", "yad", "bhool", "bhul", "kya", "kia", "hai", "nahi",
            "nhi", "karo", "krdo", "acha", "accha",
        )
        if any(token in lowered.split() for token in roman_markers):
            return "roman_urdu_or_hinglish"
        return "english_or_mixed"
