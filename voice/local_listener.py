"""Local Windows microphone -> speech recognition -> JARVIS Socket.IO bridge.

This process owns the microphone so the HUD remains a presentation layer.
SpeechRecognition's Google recognizer is used as the initial STT backend because
it requires no extra model download. The transport is the existing Socket.IO
"user_speech" contract.
"""

from __future__ import annotations

import io
import os
import threading
import time
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
        silence_seconds: float = 0.85,
        min_phrase_seconds: float = 0.35,
        threshold_multiplier: float = 3.0,
        language: str = "en-US",
    ):
        self.url = f"http://{host}:{port}"
        self.sample_rate = sample_rate
        self.block_size = max(1, int(sample_rate * block_seconds))
        self.max_phrase_blocks = max(1, int(max_phrase_seconds / block_seconds))
        self.silence_blocks = max(1, int(silence_seconds / block_seconds))
        self.min_phrase_blocks = max(1, int(min_phrase_seconds / block_seconds))
        self.threshold_multiplier = max(1.5, float(threshold_multiplier))
        self.language = language
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.socket = socketio.Client(reconnection=True, logger=False, engineio_logger=False)
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = False
        self.input_device = os.getenv("JARVIS_VOICE_DEVICE", "").strip() or None
        self.ambient_rms = 0.005
        self.threshold = 0.015
        self.suppress_until = 0.0

        @self.socket.event
        def connect():
            print("[LOCAL VOICE] Socket.IO connected")

        @self.socket.event
        def disconnect():
            print("[LOCAL VOICE] Socket.IO disconnected")

        @self.socket.on("jarvis_reply")
        def on_reply(data):
            text = str((data or {}).get("text", ""))
            # Give browser TTS time to finish before reopening the capture window.
            duration = max(1.5, min(8.0, len(text.split()) * 0.28))
            self.suppress_until = time.monotonic() + duration

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

    def _connect_socket(self) -> None:
        if self.socket.connected:
            return
        try:
            self.socket.connect(self.url, wait=False)
        except Exception:
            pass

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
                if time.monotonic() < self.suppress_until:
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
        speech_started = False
        silence_count = 0

        for index in range(self.max_phrase_blocks):
            block = self._record(self.block_size)
            level = self._rms(block)
            is_speech = level >= self.threshold

            if not speech_started:
                if is_speech:
                    speech_started = True
                    blocks.append(block)
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
            return None

        return np.concatenate(blocks)

    def _recognize(self, audio: np.ndarray) -> str:
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16).tobytes()

        audio_data = sr.AudioData(
            pcm,
            self.sample_rate,
            2,
        )

        try:
            return self.recognizer.recognize_google(
                audio_data,
                language=self.language,
            ).strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            print(f"[LOCAL VOICE] STT service unavailable: {exc}")
            time.sleep(1.0)
            return ""

    def _emit(self, text: str) -> None:
        self._connect_socket()
        if not self.socket.connected:
            return

        request_id = f"local-{uuid.uuid4().hex[:12]}"
        language_hint = self._language_hint(text)

        print(f"User: {text}")
        self.socket.emit(
            "user_speech",
            {
                "text": text,
                "request_id": request_id,
                "language_hint": language_hint,
            },
        )

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
