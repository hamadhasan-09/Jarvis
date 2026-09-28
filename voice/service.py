"""Threaded local voice service with bounded queues and recoverable stages."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
import logging
import queue
import threading
import time
from typing import Any

import numpy as np

from voice.audio_input import AudioInput
from voice.audio_output import AudioOutput
from voice.config import VoiceConfig, load_config
from voice.logging_config import configure_voice_logging
from voice.stt import SpeechToText, TranscriptResult, WhisperEngine
from voice.text_cleaner import split_sentences
from voice.tts import IndicParlerTTS
from voice.vad import SileroVAD
from voice.wakeword import WakeWord

LOG = logging.getLogger("jarvis.voice")


class VoiceState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class _Turn:
    samples: np.ndarray
    started_at: float
    ended_at: float


@dataclass(frozen=True)
class _SpeechJob:
    text: str
    lang: str
    generation: int
    end_of_stream: bool = False


class VoiceService:
    """Own mic, STT, TTS, and playback while exposing a small brain callback API."""

    def __init__(
        self,
        config: VoiceConfig | None = None,
        *,
        audio_input: Any = None,
        vad: Any = None,
        wakeword: Any = None,
        stt: SpeechToText | None = None,
        tts: Any = None,
        audio_output: Any = None,
    ):
        self.config = config or load_config()
        self.audio_input = audio_input or AudioInput(self.config)
        self.vad = vad or SileroVAD(self.config)
        self.wakeword = wakeword or WakeWord(self.config)
        self.stt = stt if stt is not None else WhisperEngine(self.config)
        # The STT-first milestone does not require a separate speech generator.
        self._tts_enabled = tts is not None or bool(self.config.section("tts").get("enabled", True))
        self.tts = tts if tts is not None else (IndicParlerTTS(self.config) if self._tts_enabled else None)
        self.audio_output = audio_output or AudioOutput(self.config)
        self.state = VoiceState.IDLE
        self._state_lock = threading.RLock()
        self._callbacks: list[Callable[[str, str], Any]] = []
        self._running = threading.Event()
        self._ptt_active = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._stt_thread: threading.Thread | None = None
        self._tts_thread: threading.Thread | None = None
        self._stt_queue: queue.Queue[_Turn | None] = queue.Queue(
            maxsize=int(self.config.section("audio").get("input_queue_size", 128))
        )
        self._tts_queue: queue.Queue[_SpeechJob | None] = queue.Queue(
            maxsize=int(self.config.section("audio").get("playback_queue_size", 32))
        )
        self._speaking = threading.Event()
        self._last_language = "en"
        self._turn_number = 0

    def on_transcript(self, callback: Callable[[str, str], Any]) -> None:
        if not callable(callback):
            raise TypeError("transcript callback must be callable")
        self._callbacks.append(callback)

    def start(self) -> None:
        if self._running.is_set():
            return
        configure_voice_logging(self.config)
        LOG.info("Loading voice models once for this session")
        providers = [("VAD", self.vad), ("wake word", self.wakeword), ("STT", self.stt)]
        if self.tts is not None:
            providers.append(("TTS", self.tts))
        for name, provider in providers:
            loader = getattr(provider, "load", None)
            if callable(loader):
                try:
                    loader()
                except Exception:
                    if name == "wake word" and not self.wakeword.enabled:
                        continue
                    raise RuntimeError(f"Could not load {name}: see the preceding error")
        stt_warmup = getattr(self.stt, "warmup", None)
        if callable(stt_warmup):
            stt_warmup()
        if self.tts is not None:
            warmup = getattr(self.tts, "warmup", None)
            if callable(warmup):
                warmup()
            self.audio_output.start()
        self._running.set()
        self._set_state(VoiceState.LISTENING)
        self._stt_thread = threading.Thread(target=self._stt_loop, name="jarvis-stt", daemon=True)
        if self.tts is not None:
            self._tts_thread = threading.Thread(target=self._tts_loop, name="jarvis-tts", daemon=True)
        self._capture_thread = threading.Thread(target=self._capture_loop, name="jarvis-capture", daemon=True)
        self._stt_thread.start()
        if self._tts_thread is not None:
            self._tts_thread.start()
        self._capture_thread.start()
        LOG.info("Voice service started; input mode=%s", self.config.section("input").get("mode"))

    def stop(self) -> None:
        self._running.clear()
        self.audio_input.stop()
        self.audio_output.close()
        for target in (self._stt_queue, self._tts_queue):
            try:
                target.put_nowait(None)
            except queue.Full:
                pass
        for thread in (self._capture_thread, self._stt_thread, self._tts_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=3)
        for provider in (self.stt, self.tts):
            if provider is None:
                continue
            closer = getattr(provider, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    LOG.exception("Voice provider shutdown failed")
        self._set_state(VoiceState.STOPPED)

    def speak(self, text: str, lang: str, interrupt: bool = False) -> None:
        if not self._tts_enabled:
            return
        if interrupt:
            generation = self.interrupt()
        else:
            generation = self.audio_output.generation
        self._enqueue_speech(text, lang, generation)

    def speak_stream(self, sentence_iterator: Iterable[str], lang: str) -> None:
        if not self._tts_enabled:
            for _sentence in sentence_iterator:
                pass
            return
        generation = self.audio_output.generation

        def feed() -> None:
            current_generation = generation
            try:
                for sentence in sentence_iterator:
                    if not self._running.is_set():
                        break
                    if isinstance(sentence, dict) and sentence.get("reset"):
                        current_generation = self.interrupt()
                        continue
                    self._enqueue_speech(str(sentence), lang, current_generation, add_end=False)
            except Exception:
                LOG.exception("Reply sentence stream failed")
            finally:
                self._queue_end(current_generation)

        threading.Thread(target=feed, name="jarvis-speech-stream", daemon=True).start()

    def interrupt(self) -> int:
        generation = self.audio_output.interrupt()
        while True:
            try:
                item = self._tts_queue.get_nowait()
            except queue.Empty:
                break
            else:
                self._tts_queue.task_done()
        self._speaking.clear()
        self._set_state(VoiceState.LISTENING)
        return generation

    @property
    def current_state(self) -> VoiceState:
        with self._state_lock:
            return self.state

    def set_push_to_talk(self, active: bool) -> None:
        if active:
            self._ptt_active.set()
        else:
            self._ptt_active.clear()

    def _set_state(self, state: VoiceState) -> None:
        with self._state_lock:
            self.state = state

    def _capture_loop(self) -> None:
        settings = self.config.section("vad")
        audio_settings = self.config.section("audio")
        input_mode = self.config.section("input").get("mode", "always_on")
        frame_samples = int(audio_settings.get("frame_samples", 512))
        frame_seconds = frame_samples / int(self.config.section("input").get("target_sample_rate", 16000))
        pre_roll = deque(maxlen=max(1, round(int(settings.get("preroll_ms", 300)) / 1000 / frame_seconds)))
        active: list[np.ndarray] = []
        active_start = 0.0
        speech_frames = 0
        silence_frames = 0
        armed = input_mode != "wake_word"
        speech_active = False
        max_frames = max(1, int(float(settings.get("max_utterance_s", 30)) / frame_seconds))
        min_frames = max(1, int(int(settings.get("min_speech_ms", 300)) / 1000 / frame_seconds))
        end_silence = max(1, int(int(settings.get("min_silence_ms", 700)) / 1000 / frame_seconds))
        try:
            for frame in self.audio_input.frames():
                if not self._running.is_set():
                    break
                now = time.monotonic()
                samples = np.asarray(frame.samples, dtype=np.float32).reshape(-1)
                if samples.size != frame_samples:
                    samples = self._pad_or_trim(samples, frame_samples)
                if self.current_state in {VoiceState.THINKING, VoiceState.TRANSCRIBING}:
                    continue
                if self._speaking.is_set() and not bool(self.config.values.get("barge_in", False)):
                    continue
                if self._speaking.is_set() and bool(self.config.values.get("barge_in", False)):
                    if self.vad.probability(samples) > float(settings.get("threshold", 0.5)):
                        self.interrupt()
                if input_mode == "push_to_talk" and not self._ptt_active.is_set():
                    continue
                if input_mode == "wake_word" and not armed:
                    if self.wakeword.detected(samples):
                        armed = True
                        pre_roll.clear()
                        LOG.info("Wake word detected")
                    continue
                probability = self.vad.probability(samples)
                threshold = float(settings.get("threshold", 0.5))
                in_speech = probability > threshold
                if in_speech and not speech_active:
                    LOG.info("Silero VAD speech started: probability=%.3f threshold=%.3f", probability, threshold)
                    speech_active = True
                if not active:
                    pre_roll.append(samples.copy())
                    if in_speech:
                        active = list(pre_roll)
                        active_start = now - frame_seconds * max(0, len(active) - 1)
                        speech_frames = 1
                        silence_frames = 0
                else:
                    active.append(samples.copy())
                    if in_speech:
                        speech_frames += 1
                        silence_frames = 0
                    else:
                        silence_frames += 1
                    if silence_frames >= end_silence or len(active) >= max_frames:
                        LOG.info("Silero VAD utterance ended: speech_frames=%s min_frames=%s elapsed_s=%.2f", speech_frames, min_frames, now - active_start)
                        if speech_frames >= min_frames:
                            turn = _Turn(np.concatenate(active), active_start, now)
                            try:
                                self._stt_queue.put(turn, timeout=1.0)
                                self._set_state(VoiceState.TRANSCRIBING)
                            except queue.Full:
                                LOG.warning("Dropping utterance because the bounded STT queue is full")
                        active = []
                        speech_frames = silence_frames = 0
                        speech_active = False
                        pre_roll.clear()
                        self.vad.reset()
                        if input_mode == "wake_word":
                            armed = False
                            self.wakeword.reset()
        except Exception:
            if self._running.is_set():
                LOG.exception("Microphone capture recovered after an error")
                self._set_state(VoiceState.ERROR)
                self._speak_error(self._last_language)
                time.sleep(0.5)
                self._set_state(VoiceState.LISTENING)
                if self._running.is_set():
                    threading.Thread(target=self._capture_loop, name="jarvis-capture-retry", daemon=True).start()

    def _stt_loop(self) -> None:
        while self._running.is_set():
            turn = self._stt_queue.get()
            try:
                if turn is None:
                    return
                started = time.perf_counter()
                result: TranscriptResult | None = self.stt.transcribe(turn.samples, sample_rate=16000)
                if result is None or not result.text.strip():
                    LOG.info("STT rejected silent, low-confidence, or hallucinated audio")
                    self._set_state(VoiceState.LISTENING)
                    continue
                self._last_language = result.lang
                LOG.info(
                    "Transcript language=%s confidence=%.3f vad_end_to_stt_done_ms=%.1f",
                    result.lang,
                    result.confidence,
                    (time.monotonic() - turn.ended_at) * 1000,
                )
                LOG.info("Final transcript [%s]: %s", result.lang, result.text)
                self._set_state(VoiceState.THINKING)
                for callback in tuple(self._callbacks):
                    try:
                        callback_result = callback(result.text, result.lang)
                        if not self._tts_enabled and isinstance(callback_result, Iterable) and not isinstance(callback_result, (str, bytes)):
                            # Drain the bridge iterator so its async brain request
                            # completes and releases its per-request state.
                            for _item in callback_result:
                                pass
                        elif isinstance(callback_result, str) and callback_result.strip():
                            self.speak(callback_result, result.lang)
                        elif callback_result is not None and isinstance(callback_result, Iterable):
                            self.speak_stream(callback_result, result.lang)
                    except Exception:
                        LOG.exception("Brain transcript callback failed")
                        self._speak_error(result.lang)
                if not self._tts_enabled and self.current_state == VoiceState.THINKING:
                    self._set_state(VoiceState.LISTENING)
            except Exception:
                LOG.exception("STT worker recovered from a turn failure")
                self._set_state(VoiceState.LISTENING)
                self._speak_error(self._last_language)
            finally:
                self._stt_queue.task_done()

    def _enqueue_speech(self, text: str, lang: str, generation: int, *, add_end: bool = True) -> None:
        language = "ur" if str(lang).lower().startswith("ur") else "en"
        sentences = split_sentences(str(text), language)
        if not sentences:
            if add_end:
                self._queue_end(generation)
            return
        self._speaking.set()
        self._set_state(VoiceState.SPEAKING)
        for sentence in sentences:
            self._tts_queue.put(_SpeechJob(sentence, language, generation))
        if add_end:
            self._queue_end(generation)

    def _queue_end(self, generation: int) -> None:
        self._tts_queue.put(_SpeechJob("", self._last_language, generation, end_of_stream=True))

    def _tts_loop(self) -> None:
        while self._running.is_set():
            job = self._tts_queue.get()
            try:
                if job is None:
                    return
                if job.end_of_stream:
                    self.audio_output.wait_empty(timeout=float(self.config.section("brain").get("qalb", {}).get("timeout_s", 30)) + 120.0)
                    self._speaking.clear()
                    self._set_state(VoiceState.LISTENING)
                    continue
                if job.generation != self.audio_output.generation:
                    continue
                started = time.perf_counter()
                waveform = self.tts.synthesize(job.text, job.lang)
                LOG.info("sentence_to_audio_ready_ms=%.1f lang=%s", (time.perf_counter() - started) * 1000, job.lang)
                if job.generation != self.audio_output.generation:
                    continue
                self.audio_output.enqueue(waveform, int(self.tts.sample_rate), job.generation)
                silence_samples = int(int(self.config.section("audio").get("sentence_silence_ms", 150)) * self.tts.sample_rate / 1000)
                if silence_samples:
                    self.audio_output.enqueue(np.zeros(silence_samples, dtype=np.float32), int(self.tts.sample_rate), job.generation)
            except Exception:
                LOG.exception("TTS worker recovered from a sentence failure")
            finally:
                self._tts_queue.task_done()

    def _speak_error(self, lang: str) -> None:
        if not self._tts_enabled:
            return
        notice = "معذرت، آواز سمجھنے میں مسئلہ ہوا۔ دوبارہ کہیں۔" if lang == "ur" else "Sorry, I had trouble understanding that. Please try again."
        try:
            self._enqueue_speech(notice, lang, self.audio_output.generation)
        except Exception:
            LOG.exception("Could not enqueue the voice error notice")

    @staticmethod
    def _pad_or_trim(values: np.ndarray, length: int) -> np.ndarray:
        return np.pad(values[:length], (0, max(0, length - values.size))).astype(np.float32)
