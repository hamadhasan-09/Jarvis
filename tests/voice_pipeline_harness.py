"""Synthetic speech/device edges around the existing voice architecture.

No network providers, model loading or audio devices. The Socket.IO transport
in the integration tests is real HTTP to an isolated ephemeral loopback server.
"""
from copy import deepcopy
from pathlib import Path
import os
import threading
import time
import wave
from types import SimpleNamespace

import numpy as np

from voice.audio_output import AudioOutput
from voice.config import VoiceConfig, load_config
from voice.local_tts import PiperTTS
from voice.service import VoiceService
from voice.stt import WhisperEngine


class SyntheticProvider:
    is_configured = True
    model = "fixture"
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.cancelled = threading.Event()
        self.fail = False
    def status(self):
        return {"provider": "synthetic", "model": "fixture", "configured": True}
    def stream(self, *, messages, cancel_event=None, **kwargs):
        self.calls.append(messages)
        self.started.set()
        yield "The authenticated brain replied. "
        if self.block:
            while not self.release.wait(.01):
                if cancel_event is not None and cancel_event.is_set():
                    self.cancelled.set()
                    return
        if self.fail:
            raise RuntimeError("synthetic provider failure")
        yield "Playback is isolated."


class FixtureAudio:
    def __init__(self, samples):
        self.samples = samples
        self.closed = threading.Event()
    def frames(self):
        try:
            values = np.concatenate((self.samples, np.zeros(16000, dtype=np.float32)))
            for offset in range(0, len(values), 512):
                if self.closed.is_set():
                    break
                yield SimpleNamespace(samples=values[offset:offset+512], captured_at=time.perf_counter())
        finally:
            self.closed.set()
    def stop(self):
        self.closed.set()


class MemorySpeaker:
    latency = 0.
    samplerate = 22050
    def __init__(self):
        self.writes = []
        self.closed = threading.Event()
        self.written = threading.Event()
        self.gate = None
        self.fail = False
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.closed.set()
    def write(self, samples):
        if self.fail:
            raise RuntimeError("synthetic output failure")
        self.writes.append(samples.copy())
        self.written.set()
        if self.gate is not None:
            self.gate.wait(3)
    def abort(self):
        if self.gate is not None:
            self.gate.set()
    stop = abort


def build_pipeline(tmp_path, bridge, monkeypatch):
    original = load_config()
    values = deepcopy(original.values)
    values["barge_in"] = False
    values["audio"]["sentence_silence_ms"] = 0
    values["stt"].update(provider="faster-whisper", model="base", language="auto", device="cpu", compute_type="int8", warmup_on_startup=False)
    values["tts"].update(provider="piper", warmup_on_startup=False)
    values["tts"]["local"] = {"models": {}}
    for language in ("en", "ur"):
        path = tmp_path / (language + ".onnx")
        path.write_bytes(b"synthetic; never loaded")
        path.with_suffix(".onnx.json").write_text("{}")
        values["tts"]["local"]["models"][language] = str(path)
    cfg = VoiceConfig(values, original.path)
    fixture = os.getenv("NEXUS_VOICE_FIXTURE_WAV")
    if fixture:
        with wave.open(str(Path(fixture)), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise ValueError("Optional voice fixture must be mono 16-bit 16 kHz PCM WAV")
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32)/32768
    else:
        samples = np.full(8192, .05, dtype=np.float32)
    audio = FixtureAudio(samples)
    speaker = MemorySpeaker()
    output = AudioOutput(cfg, stream_factory=lambda rate: speaker)
    transcript = "Explain a simple greeting"
    stt_calls, tts_calls = [], []
    class Model:
        def transcribe(self, samples, **kwargs):
            assert kwargs["language"] is None
            stt_calls.append(len(samples))
            return [SimpleNamespace(text=transcript, avg_logprob=-.1)], SimpleNamespace(language="en", language_probability=.99)
    def voice_factory(*args, **kwargs):
        def synthesize(text):
            tts_calls.append(text)
            yield SimpleNamespace(audio_float_array=np.ones(2205, dtype=np.float32)*.1, sample_rate=22050)
        return SimpleNamespace(synthesize=synthesize)
    tts = PiperTTS(cfg, voice_factory=voice_factory)
    vad = SimpleNamespace(load=lambda: None, reset=lambda: None, probability=lambda samples: .9 if np.max(np.abs(samples))>.001 else 0.)
    monkeypatch.setattr("voice.service.configure_voice_logging", lambda cfg: None)
    service = VoiceService(cfg, audio_input=audio, vad=vad, wakeword=SimpleNamespace(enabled=False, load=lambda: None),
                           stt=WhisperEngine(cfg, model=Model()), tts=tts, audio_output=output)
    service.on_transcript(bridge.submit)
    diagnostics = []
    service.on_diagnostic(diagnostics.append)
    return SimpleNamespace(service=service, diagnostics=diagnostics, audio=audio, speaker=speaker, output=output, tts_calls=tts_calls, stt_calls=stt_calls)
