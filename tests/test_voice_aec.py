from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from brain.router import QalbRouter
from voice.aec import PlaybackReference, WebRTCAEC
from voice.config import VoiceConfig, load_config
from voice.service import VoiceService


class _SyntheticAEC3:
    """Deterministic reference canceller for testing sample/event wiring."""

    def __init__(self, **_kwargs):
        pass

    def process(self, near, far):
        near = np.asarray(near, dtype=np.float32)
        far = np.asarray(far, dtype=np.float32)
        power = float(np.dot(far, far))
        gain = float(np.dot(near, far) / power) if power > 1e-8 else 0.0
        return near - np.clip(gain, -2.0, 2.0) * far

    def reset(self):
        pass


def test_reference_aec_suppresses_echo_and_keeps_new_speech():
    sample_rate = 16000
    frame_size = 160
    frame_count = 100
    base_time = time.perf_counter()
    rng = np.random.default_rng(20261003)
    reference_signal = rng.normal(0.0, 0.08, frame_size * frame_count).astype(np.float32)
    reference = PlaybackReference(sample_rate)
    reference.publish(reference_signal, sample_rate, base_time)
    aec = WebRTCAEC(reference, processor_factory=_SyntheticAEC3)
    aec.load()

    speech_started = False
    echo_only_vad_scores = []
    speech_frame = 70
    for index in range(frame_count):
        start = index * frame_size
        far = reference_signal[start : start + frame_size]
        user = np.zeros(frame_size, dtype=np.float32)
        if index >= speech_frame:
            time_axis = np.arange(frame_size, dtype=np.float32) / sample_rate
            user = 0.22 * np.sin(2 * np.pi * 310 * (index * frame_size / sample_rate + time_axis))
        microphone = 0.62 * far + user
        capture_end = base_time + (start + frame_size) / sample_rate
        cleaned = aec.process(microphone, capture_end)
        # Lightweight deterministic VAD stand-in; this test targets reference
        # alignment and AEC/VAD event wiring, not acoustic room performance.
        probability = 1.0 if float(np.sqrt(np.mean(cleaned**2))) > 0.06 else 0.0
        if index < speech_frame:
            echo_only_vad_scores.append(probability)
        elif probability:
            speech_started = True

    assert echo_only_vad_scores and not any(echo_only_vad_scores)
    assert speech_started


def test_playback_reference_returns_only_samples_for_requested_time():
    sample_rate = 16000
    now = time.perf_counter()
    reference = PlaybackReference(sample_rate)
    samples = np.arange(320, dtype=np.float32) / 1000.0
    reference.publish(samples, sample_rate, now)

    frame = reference.read(now + 0.015, 160)

    np.testing.assert_allclose(frame, samples[80:240])


def test_aec_initialization_failure_keeps_barge_in_disabled():
    values = deepcopy(load_config().values)
    values["barge_in"] = True
    values["tts"]["enabled"] = False
    config = VoiceConfig(values, load_config().path)

    class BrokenAEC:
        def load(self):
            raise ImportError("native wheel unavailable")

    class Provider:
        enabled = False

        def load(self):
            pass

        def warmup(self):
            pass

        def close(self):
            pass

    class AudioInputStub:
        def frames(self):
            return iter(())

        def stop(self):
            pass

    class AudioOutputStub:
        generation = 0

        def close(self):
            pass

    service = VoiceService(
        config,
        audio_input=AudioInputStub(),
        vad=Provider(),
        wakeword=Provider(),
        stt=Provider(),
        audio_output=AudioOutputStub(),
        aec=BrokenAEC(),
    )
    try:
        service.start()
        assert service._barge_in_requested is True
        assert service._barge_in_active is False
    finally:
        service.stop()


def test_voice_interrupt_cancels_reply_with_only_completed_sentences():
    class AudioOutputStub:
        generation = 0

        def interrupt(self):
            self.generation += 1
            return self.generation

    class ReplyStreamStub:
        request_id = "reply-1"

        def __init__(self):
            self.cancelled_with = None

        def cancel(self, heard_text):
            self.cancelled_with = heard_text

    service = VoiceService(audio_output=AudioOutputStub())
    reply_stream = ReplyStreamStub()
    service._active_reply_stream = reply_stream
    service._played_sentences[reply_stream.request_id] = ["First sentence.", "Second sentence."]
    service._tts_queue.put(SimpleNamespace())

    service.interrupt()

    assert reply_stream.cancelled_with == "First sentence. Second sentence."
    assert service._active_reply_stream is None
    assert service._tts_queue.empty()


def test_qalb_stream_closes_on_voice_cancel_without_retry_or_memory_write():
    cancel_event = __import__("threading").Event()

    class FakeQalbClient:
        def __init__(self):
            self.stream_closed = False

        def health_check(self):
            return True

        def stream_chat(self, _messages, max_tokens=None):
            try:
                yield "آپ کی درخواست مکمل ہو گئی۔ "
                cancel_event.set()
                yield "یہ جملہ نہیں سنایا جائے گا۔"
            finally:
                self.stream_closed = True

    client = FakeQalbClient()
    config_values = deepcopy(load_config().values)
    config_values["brain"]["qalb"]["mode"] = "direct"
    router = QalbRouter(VoiceConfig(config_values, load_config().path), client=client)
    with pytest.raises(InterruptedError, match="cancelled by voice barge-in"):
        router.route_urdu("سلام", on_sentence=lambda _text: None, cancel_event=cancel_event)
    assert client.stream_closed is True
    assert router.history == []
