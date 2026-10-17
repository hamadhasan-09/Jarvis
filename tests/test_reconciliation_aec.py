"""Synthetic coverage for AEC merged with connection-owned turn cancellation."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from core.contracts import begin_turn, end_turn, is_turn_current
from voice.config import VoiceConfig, load_config
from voice.service import VoiceService, VoiceState


@pytest.mark.parametrize("aec_fails", [False, True])
def test_aec_capture_preserves_turn_ownership_and_fails_closed(aec_fails):
    values = deepcopy(load_config().values)
    values["barge_in"] = True
    values["tts"]["enabled"] = False
    config = VoiceConfig(values, load_config().path)
    owner, other = "synthetic-aec-owner", "synthetic-aec-other"
    turn, other_turn = begin_turn(owner), begin_turn(other)

    class Capture:
        def frames(self):
            yield SimpleNamespace(samples=np.ones(512, dtype=np.float32), captured_at=123.0)

    class Output:
        generation = 0

        def set_reference_sink(self, reference):
            self.reference = reference

        def interrupt(self):
            self.generation += 1
            return self.generation

    class AEC:
        calls = []

        def process(self, samples, captured_at):
            self.calls.append(captured_at)
            if aec_fails:
                raise RuntimeError("synthetic AEC failure")
            return samples

    class Reply:
        request_id = "synthetic-aec-reply"
        session_id, turn_id = owner, turn
        cancelled = None

        def cancel(self, heard_text):
            self.cancelled = heard_text

    output, aec, reply = Output(), AEC(), Reply()
    service = VoiceService(
        config, audio_input=Capture(), audio_output=output, aec=aec,
        vad=SimpleNamespace(probability=lambda _: 1.0),
        wakeword=SimpleNamespace(), stt=SimpleNamespace(),
    )
    states = []
    service.on_state(states.append)
    service._active_reply_stream = reply
    service._played_sentences[reply.request_id] = ["Completed sentence."]
    service._barge_in_active = True
    service._running.set()
    service._speaking.set()
    service._set_state(VoiceState.SPEAKING)
    try:
        service._capture_loop()
        assert aec.calls == [123.0]
        assert is_turn_current(other, other_turn)
        if aec_fails:
            assert not service._barge_in_active
            assert output.generation == 0
            assert reply.cancelled is None
            assert is_turn_current(owner, turn)
            assert states == [VoiceState.SPEAKING]
        else:
            assert output.generation == 1
            assert reply.cancelled == "Completed sentence."
            assert not is_turn_current(owner, turn)
            assert states == [VoiceState.SPEAKING, VoiceState.LISTENING]
    finally:
        service._running.clear()
        end_turn(owner, turn)
        end_turn(other, other_turn)


def test_capture_timestamps_account_for_latency_and_buffered_frames(monkeypatch):
    from voice import audio_input as capture_module

    base = load_config()
    values = deepcopy(base.values)
    values["input"]["test_native_sample_rate"] = 16000
    values["audio"]["frame_samples"] = 512
    config = VoiceConfig(values, base.path)
    raw = np.linspace(-0.25, 0.25, 1024, dtype=np.float32)
    monkeypatch.setattr(capture_module, "time", SimpleNamespace(perf_counter=lambda: 10.0))

    class Stream:
        latency = 0.05

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, block_size):
            assert block_size == 512
            capture.stop()
            return raw.copy(), False

    capture = capture_module.AudioInput(config, stream_factory=Stream)
    frames = list(capture.frames())
    assert len(frames) == 2
    assert [frame.captured_at for frame in frames] == pytest.approx([9.918, 9.95])
    np.testing.assert_array_equal(np.concatenate([frame.samples for frame in frames]), raw)
